"""V8.27 Phase 4 — read-only User Model consumer resolver.

Deterministic. Bounded. No writes. No model calls. No Goal Registry.
Consumers call get_relevant_user_profile; never query the table directly.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from orchestration.user_model.config import is_v827_user_model_enabled
from orchestration.user_model.policy import (
    is_sensitive_profile_content,
    is_valid_owner_id,
    normalize_owner_id,
    sanitize_profile_value,
)
from orchestration.user_model.types import (
    MAX_CONSUMER_RESULTS,
    MAX_PROFILE_VALUE_LENGTH,
    Category,
    Confidence,
    ProfileEntry,
    ProfileResult,
    ProfileResultStatus,
    ProfileStatus,
)

# Context-type category bias (approved V8.27 order).
_CONTEXT_BIAS: dict = {
    "DECISION": (
        Category.PREFERENCE,
        Category.CONSTRAINT,
        Category.PROJECT,
    ),
    "PLAN": (
        Category.PROJECT,
        Category.PREFERENCE,
        Category.CONSTRAINT,
        Category.COMMUNICATION,
    ),
    "SITUATION": (
        Category.CONSTRAINT,
        Category.TEMPORARY_FACT,
    ),
    "CONVERSATION": (
        Category.COMMUNICATION,
        Category.PREFERENCE,
    ),
}

_SITUATION_MAX = 2

_CONSUMER_CONFIDENCE = frozenset({Confidence.HIGH, Confidence.MEDIUM})

_LANG_Q = re.compile(
    r"(?i)\b(?:preferred|favourite|favorite)?\s*(?:programming\s+)?language\b|"
    r"\bprefer(?:red)?\b"
)
_PROJECT_Q = re.compile(r"(?i)\bproject\b|\bbuilding\b|\bworking\s+on\b")
_STYLE_Q = re.compile(r"(?i)\bconcise\b|\bdetailed\b|\bstyle\b|\bcommunication\b")


def normalize_context_type(context_type: Any) -> Optional[str]:
    raw = str(context_type or "").strip().upper()
    if raw in _CONTEXT_BIAS:
        return raw
    return None


def format_profile_for_consumer(entry: ProfileEntry) -> str:
    """Bounded consumer string: `[category] value`."""
    if entry is None:
        return ""
    try:
        cat = entry.category.value if isinstance(entry.category, Category) else str(entry.category)
        val = sanitize_profile_value(str(entry.value or ""))
        if not val or is_sensitive_profile_content(val):
            return ""
        text = f"[{cat}] {val}"
        return text[:MAX_PROFILE_VALUE_LENGTH]
    except Exception:
        return ""


def _entry_usable(entry: Optional[ProfileEntry]) -> bool:
    if entry is None:
        return False
    try:
        if entry.status is not ProfileStatus.ACTIVE:
            return False
        if entry.confidence not in _CONSUMER_CONFIDENCE:
            return False
        if not str(entry.value or "").strip():
            return False
        if is_sensitive_profile_content(entry.value) or is_sensitive_profile_content(entry.key):
            return False
        return True
    except Exception:
        return False


def _query_boost(entry: ProfileEntry, query: str) -> int:
    """Deterministic soft rank boost from lexical overlap (0–3)."""
    q = " ".join(str(query or "").lower().split())
    if not q:
        return 0
    boost = 0
    val = str(entry.value or "").lower()
    key = str(entry.key or "").lower()
    if val and val in q:
        boost += 2
    elif val and any(tok and tok in q for tok in val.split() if len(tok) > 2):
        boost += 1
    if key and key.replace("_", " ") in q:
        boost += 1
    if entry.category is Category.PREFERENCE and _LANG_Q.search(q):
        if "language" in key or "lang" in key or len(val.split()) <= 2:
            boost += 1
    if entry.category is Category.PROJECT and _PROJECT_Q.search(q):
        boost += 1
    if entry.category is Category.COMMUNICATION and _STYLE_Q.search(q):
        boost += 1
    return boost


def _category_rank(cat: Category, bias: Sequence[Category]) -> int:
    try:
        return list(bias).index(cat)
    except ValueError:
        return 99


def get_relevant_user_profile(
    owner_id: str,
    context_type: Any,
    query: str = "",
    limit: int = MAX_CONSUMER_RESULTS,
) -> ProfileResult:
    """Return bounded ACTIVE HIGH/MEDIUM profile entries for a consumer context.

    Never raises. Never writes. Empty/unavailable → UNAVAILABLE or OK+empty.
    """
    try:
        if not is_v827_user_model_enabled():
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        ctx = normalize_context_type(context_type)
        if ctx is None:
            return ProfileResult(ProfileResultStatus.REJECTED)
        if not is_valid_owner_id(owner_id):
            return ProfileResult(ProfileResultStatus.REJECTED)
        owner = normalize_owner_id(owner_id)
        bias = _CONTEXT_BIAS[ctx]
        hard_cap = _SITUATION_MAX if ctx == "SITUATION" else MAX_CONSUMER_RESULTS
        lim = max(1, min(int(limit or hard_cap), hard_cap))

        from orchestration.user_model.store import list_profile_entries

        result = list_profile_entries(
            owner,
            categories=list(bias),
            include_expired=False,
            limit=MAX_CONSUMER_RESULTS * 2,
        )
        if result.status is ProfileResultStatus.UNAVAILABLE:
            return result
        if result.status is not ProfileResultStatus.OK:
            return ProfileResult(ProfileResultStatus.OK, entries=())

        usable: List[ProfileEntry] = []
        for entry in result.entries or ():
            if not _entry_usable(entry):
                continue
            usable.append(entry)

        usable.sort(
            key=lambda e: (
                -_query_boost(e, query),
                _category_rank(e.category, bias),
                0 if e.confidence is Confidence.HIGH else 1,
                -float(e.updated_at or 0.0),
            )
        )
        return ProfileResult(
            ProfileResultStatus.OK,
            entries=tuple(usable[:lim]),
        )
    except Exception:
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)


def profile_strings_for_consumer(
    owner_id: str,
    context_type: Any,
    query: str = "",
    limit: int = MAX_CONSUMER_RESULTS,
) -> Tuple[str, ...]:
    """Convenience: formatted consumer strings, or empty on any failure."""
    try:
        result = get_relevant_user_profile(owner_id, context_type, query=query, limit=limit)
        if result.status is not ProfileResultStatus.OK:
            return ()
        out: List[str] = []
        for entry in result.entries or ():
            text = format_profile_for_consumer(entry)
            if text:
                out.append(text)
            if len(out) >= max(1, int(limit or MAX_CONSUMER_RESULTS)):
                break
        return tuple(out)
    except Exception:
        return ()


def _overlaps_profile(text: str, profile_texts: Sequence[str]) -> bool:
    """True when memory text clearly duplicates an already-injected profile value."""
    t = " ".join(str(text or "").lower().split())
    if not t:
        return True
    for p in profile_texts:
        # Strip [category] prefix for overlap check.
        body = re.sub(r"^\[\w+\]\s*", "", str(p or "").lower()).strip()
        if not body:
            continue
        if body in t or t in body:
            return True
        # Short token values (e.g. Python) as whole-word presence.
        if len(body.split()) <= 2 and re.search(
            rf"(?i)\b{re.escape(body)}\b", t
        ):
            return True
    return False


def merge_profile_then_memory(
    profile_texts: Sequence[str],
    memory_texts: Sequence[str],
    *,
    limit: int,
) -> List[str]:
    """Profile first, then memory fillers with simple dedup. Cap at limit."""
    out: List[str] = []
    for p in profile_texts:
        text = str(p or "").strip()
        if not text:
            continue
        out.append(text)
        if len(out) >= limit:
            return out
    for m in memory_texts:
        text = str(m or "").strip()
        if not text:
            continue
        if _overlaps_profile(text, out):
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out


def try_direct_profile_fact_answer(owner_id: str, query: str) -> Optional[str]:
    """Deterministic direct fact from User Model. None → caller may fall back."""
    try:
        if not is_v827_user_model_enabled():
            return None
        from orchestration.conversation.personal_memory import (
            is_simple_personal_fact_question,
        )

        if not is_simple_personal_fact_question(query):
            return None
        if not is_valid_owner_id(owner_id):
            return None
        q = " ".join(str(query or "").split())
        ql = q.lower()

        # Prefer DECISION-style prefs/projects for fact questions.
        result = get_relevant_user_profile(
            owner_id, "CONVERSATION", query=q, limit=MAX_CONSUMER_RESULTS
        )
        # Also pull project for "what am I building"
        if _PROJECT_Q.search(ql):
            proj = get_relevant_user_profile(
                owner_id, "PLAN", query=q, limit=MAX_CONSUMER_RESULTS
            )
            if proj.status is ProfileResultStatus.OK and proj.entries:
                merged = list(proj.entries) + list(result.entries or ())
                # de-dupe by (category, key)
                seen = set()
                entries = []
                for e in merged:
                    k = (e.category, e.key)
                    if k in seen:
                        continue
                    seen.add(k)
                    entries.append(e)
                result = ProfileResult(ProfileResultStatus.OK, entries=tuple(entries))

        if result.status is not ProfileResultStatus.OK:
            return None
        for entry in result.entries or ():
            if not _entry_usable(entry):
                continue
            key = str(entry.key or "").lower()
            val = sanitize_profile_value(entry.value)
            if not val:
                continue
            if entry.category is Category.PREFERENCE and (
                "language" in key or "lang" in key or _LANG_Q.search(ql)
            ):
                if "language" in ql or "prefer" in ql or "favourite" in ql or "favorite" in ql:
                    return f"You prefer {val}."[:MAX_PROFILE_VALUE_LENGTH]
            if entry.category is Category.PROJECT and _PROJECT_Q.search(ql):
                return f"You are building {val}."[:MAX_PROFILE_VALUE_LENGTH]
            if entry.category is Category.COMMUNICATION and key == "style":
                if "style" in ql or "concise" in ql or "detailed" in ql:
                    return f"You prefer {val} answers."[:MAX_PROFILE_VALUE_LENGTH]
            if entry.category is Category.PREFERENCE and (
                "colour" in key or "color" in key or "theme" in key
            ):
                if any(w in ql for w in ("colour", "color", "theme", "ui")):
                    return f"Your preferred {key.replace('prefer_', '').replace('_', ' ')} is {val}."[
                        :MAX_PROFILE_VALUE_LENGTH
                    ]
        return None
    except Exception:
        return None
