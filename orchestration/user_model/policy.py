"""V8.27 User Model validation, sanitization, and sensitive-data policy."""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from orchestration.user_model.types import (
    MAX_ENTRY_ID_CHARS,
    MAX_OWNER_CHARS,
    MAX_PROFILE_KEY_LENGTH,
    MAX_PROFILE_VALUE_LENGTH,
    MAX_SOURCE_MEMORY_ID_CHARS,
    SCHEMA_VERSION,
    Category,
    Confidence,
    ENTRY_ID_PREFIX,
    ProfileResultStatus,
    ProfileStatus,
    Provenance,
)

_KEY_RE = re.compile(r"^[a-z0-9_]+$")

# Secrets / credentials / session material (aligned with V8 personal memory).
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


def normalize_owner_id(owner_id: str) -> str:
    return str(owner_id or "").strip()[:MAX_OWNER_CHARS]


def is_valid_owner_id(owner_id: str) -> bool:
    owner = normalize_owner_id(owner_id)
    if not owner:
        return False
    if any(ord(ch) < 32 for ch in owner):
        return False
    return True


def is_sensitive_profile_content(text: str) -> bool:
    """True when content must never be stored as a profile value.

    Empty/whitespace-only is not sensitive — callers reject it as REJECTED.
    """
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


def sanitize_profile_value(value: str) -> str:
    """Strip control chars and collapse whitespace; clip to max length."""
    raw = str(value or "").replace("\x00", "")
    raw = _CTRL.sub("", raw)
    cleaned = " ".join(raw.strip().split())
    return cleaned[:MAX_PROFILE_VALUE_LENGTH]


def sanitize_profile_key(key: str) -> str:
    raw = str(key or "").strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    raw = re.sub(r"[^a-z0-9_]", "", raw)
    return raw[:MAX_PROFILE_KEY_LENGTH]


def parse_category(raw: Any) -> Optional[Category]:
    if isinstance(raw, Category):
        return raw
    text = str(raw or "").strip()
    try:
        return Category(text)
    except ValueError:
        return None


def parse_confidence(raw: Any) -> Optional[Confidence]:
    if isinstance(raw, Confidence):
        return raw
    text = str(raw or "").strip().upper()
    try:
        return Confidence(text)
    except ValueError:
        return None


def parse_provenance(raw: Any) -> Optional[Provenance]:
    if isinstance(raw, Provenance):
        return raw
    text = str(raw or "").strip().upper()
    try:
        return Provenance(text)
    except ValueError:
        return None


def parse_status(raw: Any) -> Optional[ProfileStatus]:
    if isinstance(raw, ProfileStatus):
        return raw
    text = str(raw or "").strip().upper()
    try:
        return ProfileStatus(text)
    except ValueError:
        return None


def default_confidence_for_category(category: Category) -> Confidence:
    if category is Category.TEMPORARY_FACT:
        return Confidence.MEDIUM
    return Confidence.HIGH


def default_expiry_for_category(category: Category, now: float) -> Optional[float]:
    from orchestration.user_model.types import (
        PROJECT_EXPIRY_SEC,
        TEMPORARY_FACT_EXPIRY_SEC,
    )

    if category is Category.PROJECT:
        return float(now) + float(PROJECT_EXPIRY_SEC)
    if category is Category.TEMPORARY_FACT:
        return float(now) + float(TEMPORARY_FACT_EXPIRY_SEC)
    return None


def validate_entry_fields(
    *,
    owner_id: str,
    category: Any,
    key: str,
    value: str,
    confidence: Any = None,
    provenance: Any = None,
    source_memory_id: str = "",
    schema_version: int = SCHEMA_VERSION,
    entry_id: str = "",
    version: int = 1,
    status: Any = ProfileStatus.ACTIVE,
    expires_at: Optional[float] = None,
) -> Tuple[Optional[dict], ProfileResultStatus]:
    """Validate and normalize fields for upsert. Fail closed."""
    if int(schema_version) != SCHEMA_VERSION:
        return None, ProfileResultStatus.REJECTED
    if not is_valid_owner_id(owner_id):
        return None, ProfileResultStatus.REJECTED

    cat = parse_category(category)
    if cat is None:
        return None, ProfileResultStatus.REJECTED

    clean_key = sanitize_profile_key(key)
    if not clean_key or not _KEY_RE.match(clean_key):
        return None, ProfileResultStatus.REJECTED
    if len(clean_key) > MAX_PROFILE_KEY_LENGTH:
        return None, ProfileResultStatus.REJECTED

    if is_sensitive_profile_content(value) or is_sensitive_profile_content(str(key or "")):
        return None, ProfileResultStatus.SENSITIVE_REJECTED

    clean_value = sanitize_profile_value(value)
    if not clean_value:
        return None, ProfileResultStatus.REJECTED
    if is_sensitive_profile_content(clean_value):
        return None, ProfileResultStatus.SENSITIVE_REJECTED

    conf = parse_confidence(confidence) if confidence is not None else default_confidence_for_category(cat)
    if conf is None:
        return None, ProfileResultStatus.REJECTED

    prov = parse_provenance(provenance) if provenance is not None else Provenance.USER_EXPLICIT
    if prov is None:
        return None, ProfileResultStatus.REJECTED

    st = parse_status(status) if status is not None else ProfileStatus.ACTIVE
    if st is None:
        return None, ProfileResultStatus.REJECTED

    if int(version) < 1:
        return None, ProfileResultStatus.REJECTED

    src = str(source_memory_id or "").strip()[:MAX_SOURCE_MEMORY_ID_CHARS]
    eid = str(entry_id or "").strip()[:MAX_ENTRY_ID_CHARS]
    if eid and (not eid.startswith(ENTRY_ID_PREFIX) or len(eid) > MAX_ENTRY_ID_CHARS):
        return None, ProfileResultStatus.REJECTED

    if expires_at is not None:
        try:
            expires_at = float(expires_at)
        except (TypeError, ValueError):
            return None, ProfileResultStatus.REJECTED

    return {
        "schema_version": SCHEMA_VERSION,
        "entry_id": eid,
        "owner_id": normalize_owner_id(owner_id),
        "category": cat,
        "key": clean_key,
        "value": clean_value[:MAX_PROFILE_VALUE_LENGTH],
        "confidence": conf,
        "provenance": prov,
        "source_memory_id": src,
        "version": int(version),
        "status": st,
        "expires_at": expires_at,
    }, ProfileResultStatus.OK
