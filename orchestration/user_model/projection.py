"""V8.27 Phase 3 — deterministic personal-memory → User Model projection.

Only invoked after successful explicit MEMORY_SAVE.
Never infers from conversation. Failures are nonfatal to MEMORY_SAVE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from orchestration.user_model.config import is_v827_user_model_enabled
from orchestration.user_model.policy import (
    is_sensitive_profile_content,
    is_valid_owner_id,
    normalize_owner_id,
    sanitize_profile_key,
    sanitize_profile_value,
)
from orchestration.user_model.types import (
    Category,
    Confidence,
    ProfileResult,
    ProfileResultStatus,
    Provenance,
)

_CONCISE = re.compile(r"(?i)\bprefer\s+concise\s+answers?\b|\bconcise\s+answers?\b")
_ROLE = re.compile(
    r"(?i)^i\s+am\s+(?:a\s+|an\s+)?"
    r"(software\s+developer|developer|engineer|backend\s+engineer|"
    r"frontend\s+engineer|student|researcher)\.?$"
)
_PREFER_CONTENT = re.compile(r"(?i)^i\s+prefer\s+(.+?)\.?$")
_PREFERRED_LANG = re.compile(
    r"(?i)^my\s+preferred\s+(?:programming\s+)?language\s+is\s+(.+?)\.?$"
)
_PROJECT_IS = re.compile(
    r"(?i)^(?:my\s+(?:current\s+)?project\s+is(?:\s+called)?\s+|i(?:'m| am)\s+building\s+)(.+?)\.?$"
)
_TEMP_EXAM = re.compile(
    r"(?i)\b(?:currently\s+)?preparing\s+for\s+(?:an\s+)?exam\b|"
    r"\bi\s+am\s+preparing\s+for\s+(?:an\s+)?exam\b"
)


@dataclass(frozen=True)
class MemoryProjectionMap:
    category: Category
    key: str
    value: str
    confidence: Confidence


def map_memory_to_profile(
    fact_key: str,
    content: str,
) -> Optional[MemoryProjectionMap]:
    """Deterministic mapper. Returns None when unmapped (no profile write)."""
    fk = str(fact_key or "").strip().lower()
    raw = " ".join(str(content or "").strip().split())
    if not fk or not raw:
        return None
    if is_sensitive_profile_content(raw) or is_sensitive_profile_content(fk):
        return None

    # --- Communication (explicit) ---
    if _CONCISE.search(raw):
        return MemoryProjectionMap(
            Category.COMMUNICATION, "style", "concise", Confidence.HIGH
        )

    # --- Temporary fact ---
    if _TEMP_EXAM.search(raw):
        return MemoryProjectionMap(
            Category.TEMPORARY_FACT,
            "exam_prep",
            sanitize_profile_value(raw) or "Preparing for exam",
            Confidence.MEDIUM,
        )

    # --- Stable role ---
    m_role = _ROLE.match(raw)
    if m_role:
        role = sanitize_profile_value(m_role.group(1))
        if role:
            return MemoryProjectionMap(
                Category.STABLE_FACT, "role", role, Confidence.HIGH
            )

    # --- Project by fact_key ---
    if fk in ("building_project", "project_name"):
        val = _extract_project_value(raw)
        if val:
            return MemoryProjectionMap(
                Category.PROJECT, "current_project", val, Confidence.HIGH
            )

    m_proj = _PROJECT_IS.match(raw)
    if m_proj and fk.startswith("project"):
        val = sanitize_profile_value(m_proj.group(1))
        if val:
            return MemoryProjectionMap(
                Category.PROJECT, "current_project", val, Confidence.HIGH
            )

    # --- Preference by fact_key ---
    if fk.startswith("prefer_") or fk.startswith("favorite_"):
        val = _extract_preference_value(raw, fk)
        if val:
            key = "prefer_language" if _looks_like_language_key(fk, val) else sanitize_profile_key(fk)
            if not key:
                return None
            # Normalize favorite_* → prefer_* style key when language-like
            if key.startswith("favorite_"):
                key = "prefer_" + key[len("favorite_"):]
            if _looks_like_language_key(fk, val):
                key = "prefer_language"
            return MemoryProjectionMap(
                Category.PREFERENCE, key[:96], val, Confidence.HIGH
            )

    # --- Preference from explicit content patterns (still MEMORY_SAVE content) ---
    m_lang = _PREFERRED_LANG.match(raw)
    if m_lang:
        val = sanitize_profile_value(m_lang.group(1))
        if val:
            return MemoryProjectionMap(
                Category.PREFERENCE, "prefer_language", val, Confidence.HIGH
            )
    m_pref = _PREFER_CONTENT.match(raw)
    if m_pref:
        body = " ".join((m_pref.group(1) or "").split())
        if body.lower() in ("concise answers", "concise answer", "concise"):
            return MemoryProjectionMap(
                Category.COMMUNICATION, "style", "concise", Confidence.HIGH
            )
        val = sanitize_profile_value(body)
        if val and len(val.split()) <= 3:
            return MemoryProjectionMap(
                Category.PREFERENCE, "prefer_language", val, Confidence.HIGH
            )

    return None


def _looks_like_language_key(fk: str, val: str) -> bool:
    if "language" in fk or "lang" in fk.split("_"):
        return True
    # Short token preferences from prefer_* keys map to prefer_language
    if fk.startswith("prefer_") and len(val.split()) <= 2:
        return True
    if fk.startswith("favorite_") and "language" in fk:
        return True
    return False


def _extract_preference_value(content: str, fact_key: str) -> str:
    raw = " ".join(str(content or "").strip().split())
    m = _PREFER_CONTENT.match(raw)
    if m:
        return sanitize_profile_value(m.group(1))
    m = _PREFERRED_LANG.match(raw)
    if m:
        return sanitize_profile_value(m.group(1))
    # "My favorite programming language is Python."
    m2 = re.search(r"(?i)\bis\s+(.+?)\.?$", raw)
    if m2 and ("favorite" in fact_key or "prefer" in fact_key):
        return sanitize_profile_value(m2.group(1))
    return sanitize_profile_value(raw)


def _extract_project_value(content: str) -> str:
    raw = " ".join(str(content or "").strip().split())
    m = _PROJECT_IS.match(raw)
    if m:
        return sanitize_profile_value(m.group(1))
    # "I am building DOOM." / "My project is called DOOM."
    m2 = re.search(r"(?i)(?:called|building|is)\s+(.+?)\.?$", raw)
    if m2:
        return sanitize_profile_value(m2.group(1))
    return sanitize_profile_value(raw)


def project_memory_to_profile(
    owner_id: str,
    memory_id: str,
    fact_key: str,
    content: str,
) -> ProfileResult:
    """Project explicit MEMORY_SAVE content into User Model. Never raises."""
    try:
        if not is_v827_user_model_enabled():
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        if not is_valid_owner_id(owner_id):
            return ProfileResult(ProfileResultStatus.REJECTED)
        owner = normalize_owner_id(owner_id)
        mid = str(memory_id or "").strip()[:64]
        if not mid:
            return ProfileResult(ProfileResultStatus.REJECTED)
        if is_sensitive_profile_content(content) or is_sensitive_profile_content(fact_key):
            return ProfileResult(ProfileResultStatus.SENSITIVE_REJECTED)

        mapped = map_memory_to_profile(fact_key, content)
        if mapped is None:
            return ProfileResult(ProfileResultStatus.NOT_FOUND)

        from orchestration.user_model.store import get_profile_entry, upsert_profile_entry

        existing = get_profile_entry(owner, mapped.category, mapped.key)
        expected = None
        if existing.status is ProfileResultStatus.OK and existing.entry is not None:
            # Idempotent: same value + same source → OK without bump requirement fight
            if (
                existing.entry.value == mapped.value
                and existing.entry.source_memory_id == mid
                and existing.entry.provenance is Provenance.PROJECTED_MEMORY
            ):
                return ProfileResult(ProfileResultStatus.OK, entry=existing.entry)
            expected = int(existing.entry.version)

        fields = {
            "category": mapped.category,
            "key": mapped.key,
            "value": mapped.value,
            "confidence": mapped.confidence,
            "provenance": Provenance.PROJECTED_MEMORY,
            "source_memory_id": mid,
        }
        if expected is None:
            return upsert_profile_entry(owner, fields)
        return upsert_profile_entry(owner, fields, expected_version=expected)
    except Exception:
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)


def project_after_memory_save(owner_id: str, raw_intent: str) -> ProfileResult:
    """Best-effort projection hook after successful MEMORY_SAVE. Never raises."""
    try:
        if not is_v827_user_model_enabled():
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        from orchestration.conversation.personal_memory import (
            derive_fact_key,
            extract_memory_content,
            lookup_personal_memory_id,
        )

        content = extract_memory_content(raw_intent)
        if not content:
            return ProfileResult(ProfileResultStatus.REJECTED)
        fact_key = derive_fact_key(content)
        mid = lookup_personal_memory_id(owner_id, fact_key)
        if not mid:
            return ProfileResult(ProfileResultStatus.NOT_FOUND)
        return project_memory_to_profile(owner_id, mid, fact_key, content)
    except Exception:
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)
