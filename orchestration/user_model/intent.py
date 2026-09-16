"""V8.27 User Model intent detection + Phase 2 UX orchestration.

Deterministic. Explicit confirmation required for durable asserts.
Does not touch Goal Registry, Continuity, or MEMORY_SAVE semantics.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional, Tuple

from orchestration.user_model.config import is_v827_user_model_enabled
from orchestration.user_model.confirm import (
    ACTION_CLEAR,
    ACTION_CLEAR_PREFERENCES,
    ACTION_FORGET,
    ACTION_FORGET_CATEGORY,
    ACTION_UPSERT,
    clear_profile_confirm,
    get_profile_confirm,
    make_profile_confirm,
    set_profile_confirm,
)
from orchestration.user_model.format import (
    format_assert_confirm,
    format_cancelled,
    format_clarify_assertion,
    format_clear_confirm,
    format_clear_success,
    format_conflict,
    format_expired,
    format_forget_category_confirm,
    format_forget_confirm,
    format_forget_success,
    format_need_yes_no,
    format_nothing_known,
    format_rejected,
    format_sensitive_rejected,
    format_transparency,
    format_unavailable,
    format_upsert_success,
    format_why,
)
from orchestration.user_model.policy import (
    is_sensitive_profile_content,
    sanitize_profile_key,
    sanitize_profile_value,
)
from orchestration.user_model.types import (
    Category,
    Confidence,
    ProfileResultStatus,
    Provenance,
)


class ProfileIntent(str, Enum):
    NONE = "NONE"
    CONFIRM_YES = "CONFIRM_YES"
    CONFIRM_NO = "CONFIRM_NO"
    CONFIRM_AMBIGUOUS = "CONFIRM_AMBIGUOUS"
    PROFILE_ASSERT = "PROFILE_ASSERT"
    PROFILE_FORGET = "PROFILE_FORGET"
    PROFILE_CLEAR = "PROFILE_CLEAR"
    PROFILE_QUERY = "PROFILE_QUERY"
    PROFILE_WHY = "PROFILE_WHY"


_YES = re.compile(
    r"(?i)^(yes|y|yeah|yep|confirm|do it|please do|ok|okay|sure)[\s?.!]*$"
)
_NO = re.compile(
    r"(?i)^(no|n|nope|cancel|never mind|nevermind|don'?t|do not)[\s?.!]*$"
)
_AMBIGUOUS = re.compile(
    r"(?i)^(maybe|not sure|idk|i don't know)[\s?.!]*$"
)

# MEMORY_SAVE must stay exclusive — never steal remember/save/store.
_MEMORY_SAVE_PREFIX = re.compile(
    r"(?i)^\s*(please\s+)?(remember|save this|store this|save that|store that)\b"
)

_QUERY_ALL = re.compile(
    r"(?i)^(?:what do you know about me|what have you stored about me|"
    r"show (?:me )?my profile|what'?s? in my profile)[\s?.!]*$"
)
_QUERY_PREFS = re.compile(
    r"(?i)^(?:what are my preferences|show (?:me )?my preferences|"
    r"what preferences do you know)[\s?.!]*$"
)
_QUERY_WORK = re.compile(
    r"(?i)^(?:what do you remember about my work|"
    r"what(?:'s| is) my (?:current )?project|"
    r"show (?:me )?my (?:work|projects?))[\s?.!]*$"
)

_WHY = re.compile(
    r"(?i)^why do you (?:think|know)(?: that)?\s+(?:i\s+)?(.+?)[\s?.!]*$"
)

_CLEAR_PREFS = re.compile(
    r"(?i)^(?:please\s+)?(?:clear|forget|erase)\s+(?:all\s+)?my\s+preferences[\s?.!]*$"
)
_CLEAR_ALL = re.compile(
    r"(?i)^(?:please\s+)?(?:"
    r"clear my profile|"
    r"forget what you know about me|"
    r"erase what you know about me|"
    r"clear what you know about me"
    r")[\s?.!]*$"
)

_FORGET_PREF = re.compile(
    r"(?i)^(?:please\s+)?forget\s+(?:that\s+)?(?:i\s+prefer\s+|my\s+preference\s+for\s+)(.+?)[\s?.!]*$"
)
_FORGET_PROJECT = re.compile(
    r"(?i)^(?:please\s+)?forget\s+(?:my\s+)?(?:current\s+)?project[\s?.!]*$"
)
_FORGET_STYLE = re.compile(
    r"(?i)^(?:please\s+)?forget\s+(?:that\s+)?(?:i\s+prefer\s+)?concise\s+answers[\s?.!]*$"
)

_ASSERT_PREFER = re.compile(
    r"(?i)^(?:please\s+)?i\s+prefer\s+(.+?)[\s?.!]*$"
)
_ASSERT_PREFERRED_LANG = re.compile(
    r"(?i)^(?:please\s+)?my\s+preferred\s+(?:programming\s+)?language\s+is\s+(.+?)[\s?.!]*$"
)
_ASSERT_PROJECT = re.compile(
    r"(?i)^(?:please\s+)?my\s+current\s+project\s+is\s+(.+?)[\s?.!]*$"
)
_ASSERT_ROLE = re.compile(
    r"(?i)^(?:please\s+)?i\s+am\s+(?:a\s+|an\s+)?(.+?)[\s?.!]*$"
)

_CONCISE = re.compile(r"(?i)^concise\s+answers?$")
_ROLE_OK = re.compile(
    r"(?i)^(software\s+developer|developer|engineer|backend\s+engineer|"
    r"frontend\s+engineer|student|researcher)$"
)

# Steal guards — never claim these as profile UX.
_DECISIONISH = re.compile(r"(?i)\bor\b|\bvs\.?\b|\bversus\b|\bwhich\b|\bshould i\b")
_PLANISH = re.compile(r"(?i)\b(make|create|build)\s+(me\s+)?a\s+plan\b|\bplan for\b")
_COMPUTERISH = re.compile(r"(?i)^\s*(click|press|type|open)\b")


def _norm(query: str) -> str:
    return " ".join(str(query or "").strip().split())


def detect_profile_intent(query: str) -> Tuple[ProfileIntent, str]:
    """Return (intent, residual topic/value text). Narrow and fail closed."""
    q = _norm(query)
    if not q:
        return ProfileIntent.NONE, ""
    if _MEMORY_SAVE_PREFIX.match(q):
        return ProfileIntent.NONE, ""
    if _YES.match(q):
        return ProfileIntent.CONFIRM_YES, ""
    if _NO.match(q):
        return ProfileIntent.CONFIRM_NO, ""
    if _AMBIGUOUS.match(q):
        return ProfileIntent.CONFIRM_AMBIGUOUS, ""
    if _COMPUTERISH.match(q) or _PLANISH.search(q) or _DECISIONISH.search(q):
        # Allow forget/query phrasing that contains "or"? rare — keep steal-safe.
        if not (
            _QUERY_ALL.match(q)
            or _QUERY_PREFS.match(q)
            or _QUERY_WORK.match(q)
            or _CLEAR_ALL.match(q)
            or _CLEAR_PREFS.match(q)
            or _FORGET_PREF.match(q)
            or _FORGET_PROJECT.match(q)
            or _WHY.match(q)
        ):
            return ProfileIntent.NONE, ""

    if _CLEAR_ALL.match(q):
        return ProfileIntent.PROFILE_CLEAR, "all"
    if _CLEAR_PREFS.match(q):
        return ProfileIntent.PROFILE_CLEAR, "preferences"
    if _QUERY_ALL.match(q):
        return ProfileIntent.PROFILE_QUERY, "all"
    if _QUERY_PREFS.match(q):
        return ProfileIntent.PROFILE_QUERY, "preferences"
    if _QUERY_WORK.match(q):
        return ProfileIntent.PROFILE_QUERY, "work"
    m = _WHY.match(q)
    if m:
        return ProfileIntent.PROFILE_WHY, (m.group(1) or "").strip()[:160]
    if _FORGET_STYLE.match(q):
        return ProfileIntent.PROFILE_FORGET, "concise answers"
    if _FORGET_PROJECT.match(q):
        return ProfileIntent.PROFILE_FORGET, "project"
    m = _FORGET_PREF.match(q)
    if m:
        return ProfileIntent.PROFILE_FORGET, (m.group(1) or "").strip()[:160]

    m = _ASSERT_PREFERRED_LANG.match(q)
    if m:
        return ProfileIntent.PROFILE_ASSERT, f"language:{(m.group(1) or '').strip()}"
    m = _ASSERT_PROJECT.match(q)
    if m:
        return ProfileIntent.PROFILE_ASSERT, f"project:{(m.group(1) or '').strip()}"
    m = _ASSERT_PREFER.match(q)
    if m:
        return ProfileIntent.PROFILE_ASSERT, f"prefer:{(m.group(1) or '').strip()}"
    m = _ASSERT_ROLE.match(q)
    if m:
        role = (m.group(1) or "").strip()
        if _ROLE_OK.match(role):
            return ProfileIntent.PROFILE_ASSERT, f"role:{role}"
    return ProfileIntent.NONE, ""


def is_profile_ux_utterance(query: str) -> bool:
    intent, _ = detect_profile_intent(query)
    return intent not in (
        ProfileIntent.NONE,
        ProfileIntent.CONFIRM_YES,
        ProfileIntent.CONFIRM_NO,
        ProfileIntent.CONFIRM_AMBIGUOUS,
    )


def should_route_user_model(
    query: str,
    owner_id: str,
    session_id: str,
) -> bool:
    if not is_v827_user_model_enabled():
        return False
    owner = str(owner_id or "").strip()
    session = str(session_id or "").strip()
    if not owner or not session:
        return False
    try:
        pending = get_profile_confirm(owner, session)
        intent, _ = detect_profile_intent(query)
        if pending is not None and intent in (
            ProfileIntent.CONFIRM_YES,
            ProfileIntent.CONFIRM_NO,
            ProfileIntent.CONFIRM_AMBIGUOUS,
        ):
            return True
        return is_profile_ux_utterance(query)
    except Exception:
        return False


def map_assertion(topic: str) -> Optional[Tuple[Category, str, str]]:
    """Deterministic category/key/value mapping. None if unclear."""
    raw = _norm(topic)
    if not raw:
        return None
    if raw.startswith("language:"):
        val = sanitize_profile_value(raw.split(":", 1)[1])
        if not val:
            return None
        return Category.PREFERENCE, "prefer_language", val
    if raw.startswith("project:"):
        val = sanitize_profile_value(raw.split(":", 1)[1])
        if not val:
            return None
        return Category.PROJECT, "current_project", val
    if raw.startswith("role:"):
        val = sanitize_profile_value(raw.split(":", 1)[1])
        key = sanitize_profile_key(val) or "role"
        if not val:
            return None
        return Category.STABLE_FACT, "role", val
    if raw.startswith("prefer:"):
        body = _norm(raw.split(":", 1)[1])
        if _CONCISE.match(body):
            return Category.COMMUNICATION, "style", "concise"
        # Prefer X as language-like preference when short token
        val = sanitize_profile_value(body)
        if not val:
            return None
        if len(val.split()) <= 3:
            return Category.PREFERENCE, "prefer_language", val
        return None
    return None


def map_forget_target(topic: str) -> Optional[Tuple[Category, str]]:
    raw = _norm(topic)
    if not raw:
        return None
    if raw == "project":
        return Category.PROJECT, "current_project"
    if _CONCISE.match(raw) or raw in ("concise", "concise answers"):
        return Category.COMMUNICATION, "style"
    # "Forget that I prefer Python" → topic Python
    val = sanitize_profile_value(raw)
    if not val:
        return None
    return Category.PREFERENCE, "prefer_language"


def _why_topic_to_key(topic: str) -> Tuple[Optional[Category], str]:
    t = _norm(topic).lower()
    if "concise" in t:
        return Category.COMMUNICATION, "style"
    if "project" in t:
        return Category.PROJECT, "current_project"
    if "prefer" in t or "python" in t or "typescript" in t or "language" in t:
        # Extract trailing token after prefer
        m = re.search(r"(?i)prefer\s+(.+)$", t)
        if m:
            return Category.PREFERENCE, "prefer_language"
        return Category.PREFERENCE, "prefer_language"
    return None, ""


def handle_user_model_request(
    owner_id: str,
    session_id: str,
    user_text: str,
) -> Optional[str]:
    """Handle Phase 2 UX. Returns response text or None if not applicable."""
    owner = str(owner_id or "").strip()[:64]
    session = str(session_id or "").strip()[:64]
    if not is_v827_user_model_enabled():
        # Fail closed: drop stale pending so re-enable cannot apply old YES.
        if owner and session:
            try:
                clear_profile_confirm(owner, session)
            except Exception:
                pass
        return None
    if not owner or not session:
        return None

    from orchestration.user_model.store import (
        clear_profile,
        forget_profile_category,
        forget_profile_entry,
        get_profile_entry,
        list_profile_entries,
        upsert_profile_entry,
    )

    intent, topic = detect_profile_intent(user_text)
    pending = get_profile_confirm(owner, session)

    # --- Confirmation apply / cancel ---
    if pending is not None and intent in (
        ProfileIntent.CONFIRM_YES,
        ProfileIntent.CONFIRM_NO,
        ProfileIntent.CONFIRM_AMBIGUOUS,
    ):
        if intent is ProfileIntent.CONFIRM_AMBIGUOUS:
            return format_need_yes_no()
        if intent is ProfileIntent.CONFIRM_NO:
            clear_profile_confirm(owner, session)
            return format_cancelled()
        return _apply_confirm(owner, session, pending)

    if pending is not None and intent is ProfileIntent.NONE:
        if len(_norm(user_text)) <= 40:
            return format_need_yes_no()

    if intent is ProfileIntent.NONE:
        return None

    if intent is ProfileIntent.PROFILE_QUERY:
        return _handle_query(owner, topic)

    if intent is ProfileIntent.PROFILE_WHY:
        return _handle_why(owner, topic)

    if intent is ProfileIntent.PROFILE_ASSERT:
        mapped = map_assertion(topic)
        if mapped is None:
            return format_clarify_assertion()
        category, key, value = mapped
        # Reject sensitive before any confirmation state is created.
        if (
            is_sensitive_profile_content(value)
            or is_sensitive_profile_content(key)
            or is_sensitive_profile_content(topic)
            or is_sensitive_profile_content(user_text)
        ):
            clear_profile_confirm(owner, session)
            return format_sensitive_rejected()
        existing = get_profile_entry(owner, category, key)
        old_value = ""
        expected = 0
        if existing.status is ProfileResultStatus.OK and existing.entry is not None:
            old_value = existing.entry.value
            expected = int(existing.entry.version)
            if old_value == value:
                return f"Your profile already has that: {_label(category, key, value)}"[:2048]
        conf = make_profile_confirm(
            owner_id=owner,
            session_id=session,
            action=ACTION_UPSERT,
            category=category.value,
            key=key,
            value=value,
            expected_version=expected,
            old_value=old_value,
        )
        if conf is None:
            return format_rejected()
        set_profile_confirm(conf)
        return format_assert_confirm(
            category=category, key=key, value=value, old_value=old_value
        )

    if intent is ProfileIntent.PROFILE_FORGET:
        target = map_forget_target(topic)
        if target is None:
            return format_clarify_assertion()
        category, key = target
        existing = get_profile_entry(owner, category, key)
        if existing.status is not ProfileResultStatus.OK or existing.entry is None:
            return format_nothing_known()
        conf = make_profile_confirm(
            owner_id=owner,
            session_id=session,
            action=ACTION_FORGET,
            category=category.value,
            key=key,
            value=existing.entry.value,
            expected_version=int(existing.entry.version),
        )
        if conf is None:
            return format_rejected()
        set_profile_confirm(conf)
        return format_forget_confirm(
            category=category, key=key, value=existing.entry.value
        )

    if intent is ProfileIntent.PROFILE_CLEAR:
        if topic == "preferences":
            listed = list_profile_entries(
                owner, categories=(Category.PREFERENCE,), limit=12
            )
            if listed.status is ProfileResultStatus.UNAVAILABLE:
                return format_unavailable()
            if not listed.entries:
                return format_nothing_known()
            conf = make_profile_confirm(
                owner_id=owner,
                session_id=session,
                action=ACTION_CLEAR_PREFERENCES,
                category=Category.PREFERENCE.value,
            )
            if conf is None:
                return format_rejected()
            set_profile_confirm(conf)
            return format_clear_confirm(preferences_only=True)
        listed = list_profile_entries(owner, limit=12)
        if listed.status is ProfileResultStatus.UNAVAILABLE:
            return format_unavailable()
        if not listed.entries:
            return format_nothing_known()
        conf = make_profile_confirm(
            owner_id=owner,
            session_id=session,
            action=ACTION_CLEAR,
        )
        if conf is None:
            return format_rejected()
        set_profile_confirm(conf)
        return format_clear_confirm(preferences_only=False)

    return None


def _label(category: Category, key: str, value: str) -> str:
    return f"{category.value} — {key.replace('_', ' ')}: {value}"


def _handle_query(owner: str, topic: str) -> str:
    from orchestration.user_model.store import list_profile_entries

    if topic == "preferences":
        res = list_profile_entries(owner, categories=(Category.PREFERENCE,), limit=12)
        title = "Your preferences"
    elif topic == "work":
        res = list_profile_entries(
            owner,
            categories=(Category.PROJECT, Category.CONSTRAINT),
            limit=12,
        )
        title = "Your work profile"
    else:
        res = list_profile_entries(owner, limit=12)
        title = "What I know about you"
    if res.status is ProfileResultStatus.UNAVAILABLE:
        return format_unavailable()
    safe = tuple(
        e
        for e in (res.entries or ())
        if e is not None and not is_sensitive_profile_content(e.value)
        and not is_sensitive_profile_content(e.key)
    )
    return format_transparency(safe, title=title)


def _handle_why(owner: str, topic: str) -> str:
    from orchestration.user_model.store import get_profile_entry, list_profile_entries

    cat, key = _why_topic_to_key(topic)
    if cat is not None and key:
        res = get_profile_entry(owner, cat, key)
        if res.status is ProfileResultStatus.OK:
            if res.entry and (
                is_sensitive_profile_content(res.entry.value)
                or is_sensitive_profile_content(res.entry.key)
            ):
                return format_nothing_known()
            return format_why(res.entry, topic=topic)
        if res.status is ProfileResultStatus.UNAVAILABLE:
            return format_unavailable()
    # Fallback: search listed entries by value token overlap
    listed = list_profile_entries(owner, limit=12)
    if listed.status is ProfileResultStatus.UNAVAILABLE:
        return format_unavailable()
    needle = sanitize_profile_value(topic).casefold()
    for e in listed.entries:
        if is_sensitive_profile_content(e.value) or is_sensitive_profile_content(e.key):
            continue
        if needle and (
            needle in e.value.casefold()
            or e.key.replace("_", " ") in needle
            or any(tok and tok in e.value.casefold() for tok in needle.split() if len(tok) > 2)
        ):
            return format_why(e, topic=topic)
    return format_why(None, topic=topic)


def _apply_confirm(owner: str, session: str, pending: Any) -> str:
    from orchestration.user_model.store import (
        clear_profile,
        forget_profile_category,
        forget_profile_entry,
        get_profile_entry,
        upsert_profile_entry,
    )

    if not is_v827_user_model_enabled():
        clear_profile_confirm(owner, session)
        return format_unavailable()
    if pending.owner_id != owner or pending.session_id != session:
        clear_profile_confirm(owner, session)
        return format_expired()
    # Nonce must remain bound to the live pending object.
    fresh = get_profile_confirm(owner, session)
    if (
        fresh is None
        or not getattr(fresh, "nonce", "")
        or fresh.nonce != getattr(pending, "nonce", None)
    ):
        clear_profile_confirm(owner, session)
        return format_expired()
    if float(pending.expires_at) <= __import__("time").time():
        clear_profile_confirm(owner, session)
        return format_expired()

    action = str(pending.action or "")
    if action == ACTION_UPSERT:
        if (
            is_sensitive_profile_content(pending.value)
            or is_sensitive_profile_content(pending.key)
        ):
            clear_profile_confirm(owner, session)
            return format_sensitive_rejected()
        fields = {
            "category": pending.category,
            "key": pending.key,
            "value": pending.value,
            "provenance": Provenance.USER_EXPLICIT,
            "confidence": Confidence.HIGH,
        }
        expected_raw = int(pending.expected_version or 0)
        if expected_raw <= 0:
            # Create-only confirm: conflict if an ACTIVE row appeared meanwhile.
            existing = get_profile_entry(owner, pending.category, pending.key)
            if existing.status is ProfileResultStatus.OK and existing.entry is not None:
                clear_profile_confirm(owner, session)
                return format_conflict()
            res = upsert_profile_entry(owner, fields)
        else:
            res = upsert_profile_entry(
                owner, fields, expected_version=expected_raw
            )
        clear_profile_confirm(owner, session)
        if res.status is ProfileResultStatus.CONFLICT:
            return format_conflict()
        if res.status is ProfileResultStatus.SENSITIVE_REJECTED:
            return format_sensitive_rejected()
        if res.status is ProfileResultStatus.UNAVAILABLE:
            return format_unavailable()
        if res.status is not ProfileResultStatus.OK or res.entry is None:
            return format_rejected()
        return format_upsert_success(
            category=res.entry.category,
            key=res.entry.key,
            value=res.entry.value,
        )

    if action == ACTION_FORGET:
        res = forget_profile_entry(
            owner,
            pending.category,
            pending.key,
            int(pending.expected_version),
        )
        clear_profile_confirm(owner, session)
        if res.status is ProfileResultStatus.CONFLICT:
            return format_conflict()
        if res.status is ProfileResultStatus.NOT_FOUND:
            return format_nothing_known()
        if res.status is not ProfileResultStatus.OK:
            return format_rejected()
        return format_forget_success(category=pending.category, key=pending.key)

    if action == ACTION_CLEAR_PREFERENCES:
        res = forget_profile_category(owner, Category.PREFERENCE)
        clear_profile_confirm(owner, session)
        if res.status is ProfileResultStatus.UNAVAILABLE:
            return format_unavailable()
        if res.status is not ProfileResultStatus.OK:
            return format_rejected()
        return format_clear_success(preferences_only=True)

    if action == ACTION_CLEAR:
        res = clear_profile(owner)
        clear_profile_confirm(owner, session)
        if res.status is ProfileResultStatus.UNAVAILABLE:
            return format_unavailable()
        if res.status is not ProfileResultStatus.OK:
            return format_rejected()
        return format_clear_success(preferences_only=False)

    if action == ACTION_FORGET_CATEGORY:
        res = forget_profile_category(owner, pending.category)
        clear_profile_confirm(owner, session)
        if res.status is not ProfileResultStatus.OK:
            return format_rejected()
        return format_clear_success(preferences_only=False)

    clear_profile_confirm(owner, session)
    return format_expired()
