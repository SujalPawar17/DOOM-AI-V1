"""V8.28 Goal Experience intent detection + Phase 4 UX orchestration.

Deterministic. Explicit confirmation required for forget.
Does not touch Goal Registry, Continuity, User Model, or MEMORY_SAVE.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, List, Optional, Sequence, Tuple

from orchestration.experience.config import is_v828_goal_experience_enabled
from orchestration.experience.confirm import (
    ACTION_FORGET,
    clear_experience_confirm,
    get_experience_confirm,
    make_experience_confirm,
    set_experience_confirm,
)
from orchestration.experience.format import (
    format_already_forgotten,
    format_ambiguous_forget,
    format_cancelled,
    format_conflict,
    format_expired,
    format_experience_list,
    format_forget_confirm,
    format_forget_success,
    format_need_yes_no,
    format_none_found,
    format_rejected,
    format_unavailable,
    format_why,
)
from orchestration.experience.policy import (
    is_sensitive_experience_content,
    is_valid_owner_id,
    normalize_owner_id,
)
from orchestration.experience.resolve import relevance_score
from orchestration.experience.types import (
    ExperienceResultStatus,
    ExperienceStatus,
    GoalExperience,
)


class ExperienceIntent(str, Enum):
    NONE = "NONE"
    CONFIRM_YES = "CONFIRM_YES"
    CONFIRM_NO = "CONFIRM_NO"
    CONFIRM_AMBIGUOUS = "CONFIRM_AMBIGUOUS"
    EXPERIENCE_QUERY = "EXPERIENCE_QUERY"
    EXPERIENCE_WHY = "EXPERIENCE_WHY"
    EXPERIENCE_FORGET = "EXPERIENCE_FORGET"
    EXPERIENCE_LIST = "EXPERIENCE_LIST"


_YES = re.compile(
    r"(?i)^(yes|y|yeah|yep|confirm|do it|please do|ok|okay|sure|forget it)[\s?.!]*$"
)
_NO = re.compile(
    r"(?i)^(no|n|nope|cancel|never mind|nevermind|don'?t|do not)[\s?.!]*$"
)
_AMBIGUOUS = re.compile(
    r"(?i)^(maybe|not sure|idk|i don't know)[\s?.!]*$"
)

# MEMORY_SAVE / profile assert must stay exclusive.
_MEMORY_SAVE_PREFIX = re.compile(
    r"(?i)^\s*(please\s+)?(remember|save this|store this|save that|store that)\b"
)
_PROFILE_PREFER = re.compile(r"(?i)^\s*(please\s+)?i\s+prefer\b")
_PROFILE_FORGET = re.compile(
    r"(?i)^\s*(please\s+)?forget\s+(?:that\s+)?(?:i\s+prefer|my\s+preference|what you know about me|my\s+preferences)\b"
)

_DECISIONISH = re.compile(r"(?i)\bor\b|\bvs\.?\b|\bversus\b|\bwhich\b|\bshould i\b")
_PLANISH = re.compile(
    r"(?i)\b(make|create|build)\s+(me\s+)?a\s+plan\b|\bplan for\b|\bnext steps for\b"
)
_COMPUTERISH = re.compile(r"(?i)^\s*(click|press|type|open)\b")

_LIST = re.compile(
    r"(?i)^(?:please\s+)?(?:"
    r"show (?:me )?(?:my )?(?:recent )?(?:goal )?experiences?|"
    r"list (?:my )?(?:recent )?(?:goal )?experiences?|"
    r"show (?:me )?recent goal experiences?"
    r")[\s?.!]*$"
)

_QUERY = re.compile(
    r"(?i)^(?:please\s+)?(?:"
    r"what did we learn from (?:my )?(?:previous |prior |past )?(?:goals?|that goal)|"
    r"what did we learn from (?:my )?(?:previous |prior |past )?(.+?)\s+goal|"
    r"what happened with (?:my )?(?:previous |prior |past )?(.+?)\s+goal|"
    r"what happened (?:to|with) (?:my )?(?:previous |prior |past )?goal|"
    r"tell me about (?:my )?(?:previous |prior |past )?(?:goal )?experiences?"
    r")[\s?.!]*$"
)

_WHY = re.compile(
    r"(?i)^(?:please\s+)?(?:"
    r"why do you remember (?:this|that)(?:\s+experience|\s+goal)?|"
    r"why is this experience stored|"
    r"why did you remember (?:that|this)(?:\s+goal|\s+experience)?|"
    r"why do you (?:still )?remember (?:my )?(.+?)(?:\s+goal|\s+experience)?"
    r")[\s?.!]*$"
)

_FORGET_BARE = re.compile(
    r"(?i)^(?:please\s+)?forget\s+(?:that|this|the)\s+(?:goal\s+)?experience(?:s)?[\s?.!]*$"
)
_FORGET_TOPIC = re.compile(
    r"(?i)^(?:please\s+)?forget\s+(?:my\s+|the\s+)?(.+?)\s+"
    r"(?:goal\s+)?experience(?:s)?[\s?.!]*$"
)


def _norm(query: str) -> str:
    return " ".join(str(query or "").strip().split())


def detect_experience_intent(query: str) -> Tuple[ExperienceIntent, str]:
    """Return (intent, residual topic). Narrow and fail closed."""
    q = _norm(query)
    if not q:
        return ExperienceIntent.NONE, ""
    if _MEMORY_SAVE_PREFIX.match(q) or _PROFILE_PREFER.match(q) or _PROFILE_FORGET.match(q):
        return ExperienceIntent.NONE, ""
    if _YES.match(q):
        return ExperienceIntent.CONFIRM_YES, ""
    if _NO.match(q):
        return ExperienceIntent.CONFIRM_NO, ""
    if _AMBIGUOUS.match(q):
        return ExperienceIntent.CONFIRM_AMBIGUOUS, ""
    if _COMPUTERISH.match(q) or _PLANISH.search(q) or _DECISIONISH.search(q):
        return ExperienceIntent.NONE, ""

    if _LIST.match(q):
        return ExperienceIntent.EXPERIENCE_LIST, ""

    if _FORGET_BARE.match(q):
        return ExperienceIntent.EXPERIENCE_FORGET, ""
    m = _FORGET_TOPIC.match(q)
    if m:
        topic = (m.group(1) or "").strip()[:160]
        if topic.lower() in ("that", "this", "my", "the", "a", "an", "goal"):
            topic = ""
        # Avoid stealing profile-style forget without experience keyword (already required).
        return ExperienceIntent.EXPERIENCE_FORGET, topic

    m = _WHY.match(q)
    if m:
        topic = ""
        if m.lastindex:
            topic = (m.group(m.lastindex) or "").strip()[:160]
        if topic.lower() in ("this", "that", "experience", "goal"):
            topic = ""
        return ExperienceIntent.EXPERIENCE_WHY, topic

    m = _QUERY.match(q)
    if m:
        topic = ""
        if m.lastindex:
            for i in range(1, m.lastindex + 1):
                g = m.group(i)
                if g:
                    topic = g.strip()[:160]
                    break
        topic = re.sub(
            r"(?i)^(previous|prior|past|my|the|that)\s+",
            "",
            topic,
        ).strip()
        topic = re.sub(r"(?i)\s+goal$", "", topic).strip()
        if topic.lower() in ("goals", "goal", "previous goals", "that goal", ""):
            topic = ""
        return ExperienceIntent.EXPERIENCE_QUERY, topic

    return ExperienceIntent.NONE, ""


def is_experience_ux_utterance(query: str) -> bool:
    intent, _ = detect_experience_intent(query)
    return intent not in (
        ExperienceIntent.NONE,
        ExperienceIntent.CONFIRM_YES,
        ExperienceIntent.CONFIRM_NO,
        ExperienceIntent.CONFIRM_AMBIGUOUS,
    )


def should_route_experience(
    query: str,
    owner_id: str,
    session_id: str,
) -> bool:
    if not is_v828_goal_experience_enabled():
        return False
    owner = str(owner_id or "").strip()
    session = str(session_id or "").strip()
    if not owner or not session:
        return False
    try:
        pending = get_experience_confirm(owner, session)
        intent, _ = detect_experience_intent(query)
        if pending is not None and intent in (
            ExperienceIntent.CONFIRM_YES,
            ExperienceIntent.CONFIRM_NO,
            ExperienceIntent.CONFIRM_AMBIGUOUS,
        ):
            return True
        return is_experience_ux_utterance(query)
    except Exception:
        return False


def _usable(exp: Optional[GoalExperience]) -> bool:
    if exp is None:
        return False
    try:
        if exp.status is not ExperienceStatus.ACTIVE_RECORD:
            return False
        title = str(exp.title or "").strip()
        if not title or is_sensitive_experience_content(title):
            return False
        return True
    except Exception:
        return False


def _load_active(owner: str) -> Tuple[Optional[str], List[GoalExperience]]:
    """Return (error_kind, experiences). error_kind: unavailable|None."""
    from orchestration.experience.store import list_experiences

    result = list_experiences(owner, include_inactive=False, limit=16)
    if result.status is ExperienceResultStatus.UNAVAILABLE:
        return "unavailable", []
    if result.status is not ExperienceResultStatus.OK:
        return None, []
    out: List[GoalExperience] = []
    for exp in result.experiences or ():
        if _usable(exp):
            out.append(exp)
    return None, out


def _match_experiences(
    experiences: Sequence[GoalExperience],
    topic: str,
    *,
    require_relevance: bool,
) -> List[GoalExperience]:
    q = _norm(topic)
    if not q:
        # Recent-first (list_experiences already sorts by updated_at desc).
        return list(experiences)[:4]
    scored: List[Tuple[int, float, str, GoalExperience]] = []
    for exp in experiences:
        try:
            score = relevance_score(exp, q)
            if require_relevance and score <= 0:
                # Soft fallback: substring title match.
                title = str(exp.title or "").lower()
                if q.lower() not in title and not any(
                    tok and tok in title for tok in q.lower().split() if len(tok) > 2
                ):
                    continue
                score = max(score, 1)
            if score <= 0 and require_relevance:
                continue
            scored.append(
                (score, float(exp.updated_at or 0.0), str(exp.experience_id or ""), exp)
            )
        except Exception:
            continue
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return [item[3] for item in scored[:4]]


def handle_experience_request(
    owner_id: str,
    session_id: str,
    user_text: str,
) -> Optional[str]:
    """Handle Phase 4 UX. Returns response text or None if not applicable."""
    owner_raw = str(owner_id or "").strip()[:64]
    session = str(session_id or "").strip()[:64]
    if not is_v828_goal_experience_enabled():
        if owner_raw and session:
            try:
                clear_experience_confirm(owner_raw, session)
            except Exception:
                pass
        return None
    if not is_valid_owner_id(owner_raw) or not session:
        return None
    owner = normalize_owner_id(owner_raw)

    intent, topic = detect_experience_intent(user_text)
    pending = get_experience_confirm(owner, session)

    if pending is not None and intent in (
        ExperienceIntent.CONFIRM_YES,
        ExperienceIntent.CONFIRM_NO,
        ExperienceIntent.CONFIRM_AMBIGUOUS,
    ):
        if intent is ExperienceIntent.CONFIRM_AMBIGUOUS:
            return format_need_yes_no()
        if intent is ExperienceIntent.CONFIRM_NO:
            clear_experience_confirm(owner, session)
            return format_cancelled()
        return _apply_confirm(owner, session, pending)

    if pending is not None and intent is ExperienceIntent.NONE:
        if len(_norm(user_text)) <= 40:
            return format_need_yes_no()

    if intent is ExperienceIntent.NONE:
        return None

    if intent in (
        ExperienceIntent.EXPERIENCE_QUERY,
        ExperienceIntent.EXPERIENCE_LIST,
    ):
        return _handle_query(owner, topic, list_mode=(intent is ExperienceIntent.EXPERIENCE_LIST))

    if intent is ExperienceIntent.EXPERIENCE_WHY:
        return _handle_why(owner, topic)

    if intent is ExperienceIntent.EXPERIENCE_FORGET:
        return _handle_forget(owner, session, topic)

    return None


def _handle_query(owner: str, topic: str, *, list_mode: bool) -> str:
    try:
        err, experiences = _load_active(owner)
        if err == "unavailable":
            return format_unavailable()
        if not experiences:
            return format_none_found(topic)
        if list_mode or not topic:
            return format_experience_list(experiences[:4])
        matched = _match_experiences(experiences, topic, require_relevance=True)
        if not matched:
            return format_none_found(topic)
        return format_experience_list(
            matched,
            title=f'Goal experiences related to "{topic[:60]}"',
        )
    except Exception:
        return format_unavailable()


def _handle_why(owner: str, topic: str) -> str:
    try:
        err, experiences = _load_active(owner)
        if err == "unavailable":
            return format_unavailable()
        if not experiences:
            return format_none_found(topic)
        if topic:
            matched = _match_experiences(experiences, topic, require_relevance=True)
        else:
            matched = list(experiences)[:1]
        if not matched:
            return format_none_found(topic)
        return format_why(matched[0], topic=topic)
    except Exception:
        return format_unavailable()


def _handle_forget(owner: str, session: str, topic: str) -> str:
    try:
        err, experiences = _load_active(owner)
        if err == "unavailable":
            return format_unavailable()
        if not experiences:
            return format_none_found(topic)

        if topic:
            matched = _match_experiences(experiences, topic, require_relevance=True)
            if not matched:
                return format_none_found(topic)
            if len(matched) > 1:
                return format_ambiguous_forget(matched)
        else:
            if len(experiences) > 1:
                return format_ambiguous_forget(experiences[:4])
            matched = experiences[:1]

        exp = matched[0]
        conf = make_experience_confirm(
            owner_id=owner,
            session_id=session,
            action=ACTION_FORGET,
            experience_id=str(exp.experience_id),
            expected_version=int(exp.version),
            title=str(exp.title or ""),
            outcome=getattr(exp.outcome, "value", str(exp.outcome or "")),
        )
        if conf is None:
            return format_rejected()
        set_experience_confirm(conf)
        return format_forget_confirm(
            title=str(exp.title or ""),
            outcome=getattr(exp.outcome, "value", str(exp.outcome or "")),
        )
    except Exception:
        return format_unavailable()


def _apply_confirm(owner: str, session: str, pending: Any) -> str:
    from orchestration.experience.store import forget_experience, get_experience

    # Re-validate binding against live pending (anti-stale / anti-replay).
    if pending.owner_id != owner or pending.session_id != session:
        clear_experience_confirm(owner, session)
        return format_expired()
    fresh = get_experience_confirm(owner, session)
    if (
        fresh is None
        or not getattr(fresh, "nonce", "")
        or fresh.nonce != getattr(pending, "nonce", None)
    ):
        clear_experience_confirm(owner, session)
        return format_expired()
    if float(pending.expires_at) <= __import__("time").time():
        clear_experience_confirm(owner, session)
        return format_expired()
    if pending.action != ACTION_FORGET:
        clear_experience_confirm(owner, session)
        return format_rejected()

    # Consume confirmation before mutate (one-shot / anti-replay).
    clear_experience_confirm(owner, session)

    existing = get_experience(owner, pending.experience_id)
    if existing.status is ExperienceResultStatus.NOT_FOUND:
        return format_already_forgotten()
    if existing.status is ExperienceResultStatus.UNAVAILABLE:
        return format_unavailable()
    if existing.status is not ExperienceResultStatus.OK or existing.experience is None:
        return format_already_forgotten()

    res = forget_experience(
        owner,
        pending.experience_id,
        expected_version=int(pending.expected_version),
    )
    if res.status is ExperienceResultStatus.CONFLICT:
        return format_conflict()
    if res.status is ExperienceResultStatus.NOT_FOUND:
        return format_already_forgotten()
    if res.status is ExperienceResultStatus.UNAVAILABLE:
        return format_unavailable()
    if res.status is not ExperienceResultStatus.OK:
        return format_rejected()
    return format_forget_success(title=str(pending.title or ""))
