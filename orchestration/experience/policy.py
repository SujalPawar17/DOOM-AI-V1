"""V8.28 Goal Experience validation, sanitization, and sensitive-data policy."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from orchestration.experience.types import (
    EXPERIENCE_ID_PREFIX,
    MAX_BLOCKER_CHARS,
    MAX_BLOCKERS,
    MAX_CONTENT_CHARS,
    MAX_EXPERIENCE_ID_CHARS,
    MAX_OWNER_CHARS,
    MAX_SOURCE_GOAL_ID_CHARS,
    MAX_STEP_SUMMARY_CHARS,
    MAX_STEPS,
    MAX_TAG_CHARS,
    MAX_TAGS,
    MAX_TITLE_CHARS,
    MAX_USER_NOTE_CHARS,
    SCHEMA_VERSION,
    REGISTRY_GOAL_ID_PREFIX,
    SOURCE_GOAL_ID_PREFIX,
    ExperienceResultStatus,
    ExperienceStatus,
    Outcome,
)

_TAG_RE = re.compile(r"^[a-z0-9_]{1,32}$")

# Aligned with V8.27 User Model / V8 personal memory sensitive policy.
_SECRETISH = re.compile(
    r"(?i)("
    r"api[_-]?key|bearer\b|password|passwd|\bpwd\b|"
    r"cookie|csrf|private[_ -]?key|authorization|"
    r"session_id|session_id_hash|ask_session|doom_ask|"
    r"executionidentity|owner_id\s*[:=]|plan_hash|"
    r"authorized_plan_hash|computer_session|browser_session|\bbws_|"
    r"csrf_token|postgres|database.{0,12}(user|pass|cred)|"
    r"-----BEGIN|hidden system prompt|internal (security )?policy|"
    r"\bsecrets?\b|supersecret|sk-[a-z0-9]{8,}|"
    r"\b(aws|gcp|azure)[_-]?secret\b|\bconnection\s*string\b|"
    r"\b\.env\b|\benv\s*file\b|"
    r"\b(credit\s*card|card\s*number|cvv|ssn|social\s*security)\b|"
    r"\b(passport|driver'?s?\s*license|national\s*id)\b|"
    r"\b(home\s*address|street\s*address)\b|"
    r"\b(diagnos(?:is|ed)|medic(?:al|ation)|mental\s*health)\b|"
    r"\b(democrat|republican|liberal|conservative)\b|"
    r"\b(christian|muslim|hindu|buddhist|atheist|jewish)\b"
    r")"
)
_SQLISH = re.compile(r"(?i)\b(select|insert|update|delete)\b.+\bfrom\b|\bcreate table\b")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Historical status transitions (not Goal Registry lifecycle).
_VALID_TRANSITIONS = frozenset(
    {
        (ExperienceStatus.ACTIVE_RECORD, ExperienceStatus.SUPERSEDED),
        (ExperienceStatus.ACTIVE_RECORD, ExperienceStatus.FORGOTTEN),
    }
)


def normalize_owner_id(owner_id: str) -> str:
    return str(owner_id or "").strip()[:MAX_OWNER_CHARS]


def is_valid_owner_id(owner_id: str) -> bool:
    owner = normalize_owner_id(owner_id)
    if not owner:
        return False
    if any(ord(ch) < 32 for ch in owner):
        return False
    return True


def is_sensitive_experience_content(text: str) -> bool:
    """True when content must never be stored. Empty is not sensitive."""
    blob = str(text or "")
    if not blob.strip():
        return False
    if "\x00" in blob:
        return True
    if _SECRETISH.search(blob):
        return True
    if _SQLISH.search(blob):
        return True
    return False


def _clip_words(text: str, limit: int) -> str:
    """Collapse whitespace and clip at word boundary when possible."""
    cleaned = " ".join(str(text or "").replace("\x00", "").split())
    cleaned = _CTRL.sub("", cleaned)
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut[:limit].rstrip()


def sanitize_title(title: str) -> str:
    return _clip_words(title, MAX_TITLE_CHARS)


def sanitize_user_note(note: str) -> str:
    return _clip_words(note, MAX_USER_NOTE_CHARS)


def sanitize_step(step: str) -> str:
    return _clip_words(step, MAX_STEP_SUMMARY_CHARS)


def sanitize_blocker(blocker: str) -> str:
    return _clip_words(blocker, MAX_BLOCKER_CHARS)


def sanitize_tag(tag: str) -> str:
    raw = str(tag or "").strip().lower().replace("-", "_").replace(" ", "_")
    raw = re.sub(r"[^a-z0-9_]", "", raw)
    return raw[:MAX_TAG_CHARS]


def parse_outcome(raw: Any) -> Optional[Outcome]:
    if isinstance(raw, Outcome):
        return raw
    text = str(raw or "").strip().upper()
    try:
        return Outcome(text)
    except ValueError:
        return None


def parse_status(raw: Any) -> Optional[ExperienceStatus]:
    if isinstance(raw, ExperienceStatus):
        return raw
    text = str(raw or "").strip().upper()
    try:
        return ExperienceStatus(text)
    except ValueError:
        return None


def is_valid_transition(from_status: ExperienceStatus, to_status: ExperienceStatus) -> bool:
    return (from_status, to_status) in _VALID_TRANSITIONS


def normalize_source_goal_id(raw: str) -> str:
    """Return sanitized source_goal_id or empty. Does not invent IDs.

    Accepts Goal Registry IDs (`rg_…`) and legacy `ag_…` forms.
    """
    sid = str(raw or "").strip()[:MAX_SOURCE_GOAL_ID_CHARS]
    if not sid:
        return ""
    if any(ord(ch) < 32 for ch in sid):
        return ""
    if len(sid) > MAX_SOURCE_GOAL_ID_CHARS:
        return ""
    if sid.startswith(REGISTRY_GOAL_ID_PREFIX) or sid.startswith(SOURCE_GOAL_ID_PREFIX):
        return sid
    return ""


def _normalize_string_tuple(
    items: Any,
    *,
    max_items: int,
    sanitizer,
    sensitive_check: bool = True,
) -> Tuple[Optional[Tuple[str, ...]], ExperienceResultStatus]:
    if items is None:
        return (), ExperienceResultStatus.OK
    if isinstance(items, str):
        # Single string → one-item tuple after sanitize
        seq: Sequence[Any] = (items,)
    elif isinstance(items, (list, tuple)):
        seq = items
    else:
        return None, ExperienceResultStatus.REJECTED
    out: List[str] = []
    for raw in seq:
        if len(out) >= max_items:
            break
        text = sanitizer(raw)
        if not text:
            continue
        if sensitive_check and is_sensitive_experience_content(text):
            return None, ExperienceResultStatus.SENSITIVE_REJECTED
        if text not in out:
            out.append(text)
    return tuple(out), ExperienceResultStatus.OK


def validate_experience_fields(
    *,
    owner_id: str,
    title: str,
    outcome: Any,
    step_summary: Any = (),
    blockers: Any = (),
    user_note: str = "",
    tags: Any = (),
    source_goal_id: str = "",
    schema_version: int = SCHEMA_VERSION,
    experience_id: str = "",
    version: int = 1,
    status: Any = ExperienceStatus.ACTIVE_RECORD,
    expires_at: Optional[float] = None,
) -> Tuple[Optional[dict], ExperienceResultStatus]:
    """Validate and normalize fields for create/update. Fail closed."""
    if int(schema_version) != SCHEMA_VERSION:
        return None, ExperienceResultStatus.REJECTED
    if not is_valid_owner_id(owner_id):
        return None, ExperienceResultStatus.REJECTED

    outc = parse_outcome(outcome)
    if outc is None:
        return None, ExperienceResultStatus.REJECTED

    st = parse_status(status) if status is not None else ExperienceStatus.ACTIVE_RECORD
    if st is None:
        return None, ExperienceResultStatus.REJECTED

    if int(version) < 1:
        return None, ExperienceResultStatus.REJECTED

    if is_sensitive_experience_content(title) or is_sensitive_experience_content(user_note):
        return None, ExperienceResultStatus.SENSITIVE_REJECTED

    clean_title = sanitize_title(title)
    if not clean_title:
        return None, ExperienceResultStatus.REJECTED

    clean_note = sanitize_user_note(user_note)
    if clean_note and is_sensitive_experience_content(clean_note):
        return None, ExperienceResultStatus.SENSITIVE_REJECTED

    steps, step_st = _normalize_string_tuple(
        step_summary, max_items=MAX_STEPS, sanitizer=sanitize_step
    )
    if step_st is not ExperienceResultStatus.OK or steps is None:
        return None, step_st

    blocks, block_st = _normalize_string_tuple(
        blockers, max_items=MAX_BLOCKERS, sanitizer=sanitize_blocker
    )
    if block_st is not ExperienceResultStatus.OK or blocks is None:
        return None, block_st

    tag_items, tag_st = _normalize_string_tuple(
        tags, max_items=MAX_TAGS, sanitizer=sanitize_tag, sensitive_check=True
    )
    if tag_st is not ExperienceResultStatus.OK or tag_items is None:
        return None, tag_st
    # Tags must match strict pattern after sanitize.
    clean_tags: List[str] = []
    for t in tag_items:
        if not _TAG_RE.match(t):
            continue
        clean_tags.append(t)

    # Bound total textual content (title + note + steps + blockers).
    content_budget = (
        len(clean_title)
        + len(clean_note)
        + sum(len(s) for s in steps)
        + sum(len(b) for b in blocks)
    )
    if content_budget > MAX_CONTENT_CHARS:
        # Prefer keeping title; trim note then steps/blockers from the end.
        overflow = content_budget - MAX_CONTENT_CHARS
        if clean_note and overflow > 0:
            if len(clean_note) <= overflow:
                overflow -= len(clean_note)
                clean_note = ""
            else:
                clean_note = sanitize_user_note(clean_note[: max(0, len(clean_note) - overflow)])
                overflow = 0
        while overflow > 0 and steps:
            last = steps[-1]
            steps = steps[:-1]
            overflow -= len(last)
        while overflow > 0 and blocks:
            last = blocks[-1]
            blocks = blocks[:-1]
            overflow -= len(last)

    src = normalize_source_goal_id(source_goal_id)
    eid = str(experience_id or "").strip()[:MAX_EXPERIENCE_ID_CHARS]
    if eid and (
        not eid.startswith(EXPERIENCE_ID_PREFIX) or len(eid) > MAX_EXPERIENCE_ID_CHARS
    ):
        return None, ExperienceResultStatus.REJECTED

    if expires_at is not None:
        try:
            expires_at = float(expires_at)
        except (TypeError, ValueError):
            return None, ExperienceResultStatus.REJECTED

    return {
        "schema_version": SCHEMA_VERSION,
        "owner_id": normalize_owner_id(owner_id),
        "experience_id": eid,
        "source_goal_id": src,
        "title": clean_title,
        "outcome": outc,
        "step_summary": tuple(steps),
        "blockers": tuple(blocks),
        "user_note": clean_note,
        "tags": tuple(clean_tags),
        "status": st,
        "version": int(version),
        "expires_at": expires_at,
    }, ExperienceResultStatus.OK
