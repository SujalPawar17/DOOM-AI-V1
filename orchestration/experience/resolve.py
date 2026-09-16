"""V8.28 Phase 3 — read-only Goal Experience resolver for PLAN/DECISION.

Deterministic. Bounded. No writes. No model calls. No Goal Registry mutation.
Consumers call get_relevant_goal_experiences; never query the table directly.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from orchestration.experience.config import is_v828_goal_experience_enabled
from orchestration.experience.policy import (
    is_sensitive_experience_content,
    is_valid_owner_id,
    normalize_owner_id,
)
from orchestration.experience.types import (
    MAX_CONTENT_CHARS,
    MAX_LIST_RESULTS,
    ExperienceResult,
    ExperienceResultStatus,
    ExperienceStatus,
    GoalExperience,
    Outcome,
)

# Consumer hard caps (PLAN / DECISION).
MAX_CONSUMER_EXPERIENCES = 4
MAX_PREFERENCE_CHARS_CONSUMER = 160

_SUPPORTED_CONTEXTS = frozenset({"PLAN", "DECISION"})
_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")


def normalize_context_type(context_type: Any) -> Optional[str]:
    raw = str(context_type or "").strip().upper()
    if raw in _SUPPORTED_CONTEXTS:
        return raw
    return None


def _tokenize(text: str) -> frozenset:
    return frozenset(_TOKEN_RE.findall(str(text or "").lower()))


def _outcome_label(outcome: Any) -> str:
    try:
        if isinstance(outcome, Outcome):
            return outcome.value
        return str(outcome or "").strip().upper() or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _entry_usable(exp: Optional[GoalExperience]) -> bool:
    if exp is None:
        return False
    try:
        if exp.status is not ExperienceStatus.ACTIVE_RECORD:
            return False
        title = str(exp.title or "").strip()
        if not title:
            return False
        if is_sensitive_experience_content(title):
            return False
        if not isinstance(exp.outcome, Outcome):
            # Tolerate string outcomes from malformed rows.
            try:
                Outcome(str(exp.outcome))
            except Exception:
                return False
        return True
    except Exception:
        return False


def relevance_score(exp: GoalExperience, query: str) -> int:
    """Deterministic explainable relevance. Higher is better. 0 = irrelevant."""
    q = " ".join(str(query or "").lower().split())
    if not q:
        return 0
    title = " ".join(str(exp.title or "").lower().split())
    score = 0
    if title and title == q:
        score += 100
    elif title and (title in q or q in title):
        score += 50

    q_toks = _tokenize(q)
    if not q_toks:
        return score

    t_toks = _tokenize(title)
    score += len(q_toks & t_toks) * 10

    for step in exp.step_summary or ():
        score += len(q_toks & _tokenize(step)) * 3

    for blocker in exp.blockers or ():
        score += len(q_toks & _tokenize(blocker)) * 2

    return int(score)


def format_experience_for_consumer(exp: GoalExperience) -> str:
    """Bounded consumer string. Never includes owner_id or internals."""
    if exp is None:
        return ""
    try:
        title = str(exp.title or "").strip()
        if not title or is_sensitive_experience_content(title):
            return ""
        outcome = _outcome_label(exp.outcome)
        parts: List[str] = [f"[past:{outcome}] {title}"]

        safe_steps: List[str] = []
        for step in exp.step_summary or ():
            s = " ".join(str(step or "").split())
            if not s or is_sensitive_experience_content(s):
                continue
            safe_steps.append(s[:60])
            if len(safe_steps) >= 2:
                break
        if safe_steps:
            parts.append("steps: " + "; ".join(safe_steps))

        safe_blockers: List[str] = []
        for blocker in exp.blockers or ():
            b = " ".join(str(blocker or "").split())
            if not b or is_sensitive_experience_content(b):
                continue
            safe_blockers.append(b[:60])
            if len(safe_blockers) >= 1:
                break
        if safe_blockers:
            parts.append("blocker: " + safe_blockers[0])

        note = " ".join(str(exp.user_note or "").split())
        if note and not is_sensitive_experience_content(note):
            parts.append("note: " + note[:80])

        text = " | ".join(parts)
        limit = MAX_PREFERENCE_CHARS_CONSUMER
        if len(text) > limit:
            text = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[:limit]
        return text[: min(limit, MAX_CONTENT_CHARS)]
    except Exception:
        return ""


def get_relevant_goal_experiences(
    owner_id: str,
    context_type: Any,
    query: str = "",
    limit: int = MAX_CONSUMER_EXPERIENCES,
) -> ExperienceResult:
    """Return bounded ACTIVE historical experiences for PLAN/DECISION.

    Never raises. Never writes. Fail-closed on errors.
    """
    try:
        if not is_v828_goal_experience_enabled():
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        ctx = normalize_context_type(context_type)
        if ctx is None:
            return ExperienceResult(ExperienceResultStatus.REJECTED)
        if not is_valid_owner_id(owner_id):
            return ExperienceResult(ExperienceResultStatus.REJECTED)
        owner = normalize_owner_id(owner_id)

        try:
            requested = int(limit) if limit is not None else MAX_CONSUMER_EXPERIENCES
        except (TypeError, ValueError):
            requested = MAX_CONSUMER_EXPERIENCES
        lim = max(0, min(requested, MAX_CONSUMER_EXPERIENCES))
        if lim <= 0:
            return ExperienceResult(ExperienceResultStatus.OK, experiences=())

        from orchestration.experience.store import list_experiences

        result = list_experiences(
            owner,
            include_inactive=False,
            limit=MAX_LIST_RESULTS,
        )
        if result.status is ExperienceResultStatus.UNAVAILABLE:
            return result
        if result.status is not ExperienceResultStatus.OK:
            return ExperienceResult(ExperienceResultStatus.OK, experiences=())

        scored: List[Tuple[int, float, str, GoalExperience]] = []
        for exp in result.experiences or ():
            try:
                if not _entry_usable(exp):
                    continue
                score = relevance_score(exp, query)
                if score <= 0:
                    continue
                scored.append(
                    (
                        score,
                        float(exp.updated_at or 0.0),
                        str(exp.experience_id or ""),
                        exp,
                    )
                )
            except Exception:
                continue

        # Stronger relevance, then newest updated_at, then stable experience_id.
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
        chosen = tuple(item[3] for item in scored[:lim])
        return ExperienceResult(ExperienceResultStatus.OK, experiences=chosen)
    except Exception:
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)


def experience_strings_for_consumer(
    owner_id: str,
    context_type: Any,
    query: str = "",
    limit: int = MAX_CONSUMER_EXPERIENCES,
) -> Tuple[str, ...]:
    """Convenience: formatted consumer strings, or empty on any failure."""
    try:
        result = get_relevant_goal_experiences(
            owner_id, context_type, query=query, limit=limit
        )
        if result.status is not ExperienceResultStatus.OK:
            return ()
        out: List[str] = []
        hard = MAX_CONSUMER_EXPERIENCES
        try:
            requested = int(limit) if limit is not None else hard
        except (TypeError, ValueError):
            requested = hard
        cap = max(0, min(requested, hard))
        for exp in result.experiences or ():
            text = format_experience_for_consumer(exp)
            if text:
                out.append(text)
            if len(out) >= cap:
                break
        return tuple(out)
    except Exception:
        return ()


def _overlaps_prior(text: str, prior: Sequence[str]) -> bool:
    t = " ".join(str(text or "").lower().split())
    if not t:
        return True
    for p in prior:
        body = re.sub(r"^\[(?:\w+|past:[^\]]+)\]\s*", "", str(p or "").lower()).strip()
        if not body:
            continue
        if body in t or t in body:
            return True
        if len(body.split()) <= 3 and re.search(rf"(?i)\b{re.escape(body)}\b", t):
            return True
    return False


def merge_profile_experience_memory(
    profile_texts: Sequence[str],
    experience_texts: Sequence[str],
    memory_texts: Sequence[str],
    *,
    limit: int,
) -> List[str]:
    """Precedence: User Model → Goal Experiences → personal memory. Cap at limit."""
    out: List[str] = []
    for source in (profile_texts, experience_texts, memory_texts):
        for raw in source:
            text = str(raw or "").strip()
            if not text:
                continue
            if _overlaps_prior(text, out):
                continue
            out.append(text)
            if len(out) >= limit:
                return out
    return out
