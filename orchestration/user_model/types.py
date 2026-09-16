"""V8.27 User Model — typed profile foundation.

Structured owner-scoped cognitive profile entries. Informational only.
Zero execution authority. Not personal memory, goals, ledger, or V5 memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

SCHEMA_VERSION = 1

MAX_OWNER_CHARS = 64
MAX_ENTRY_ID_CHARS = 64
MAX_CATEGORY_CHARS = 32
MAX_PROFILE_KEY_LENGTH = 96
MAX_PROFILE_VALUE_LENGTH = 160
MAX_SOURCE_MEMORY_ID_CHARS = 64

MAX_PROFILE_ENTRIES = 32
MAX_ENTRIES_PER_CATEGORY = 8
MAX_CONSUMER_RESULTS = 4
MAX_TRANSPARENCY_RESULTS = 12
MAX_LIST_RESULTS = 12

PROJECT_EXPIRY_SEC = 90 * 24 * 3600
TEMPORARY_FACT_EXPIRY_SEC = 14 * 24 * 3600

ENTRY_ID_PREFIX = "up_"


class Category(str, Enum):
    PREFERENCE = "preference"
    PROJECT = "project"
    CONSTRAINT = "constraint"
    COMMUNICATION = "communication"
    STABLE_FACT = "stable_fact"
    TEMPORARY_FACT = "temporary_fact"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Provenance(str, Enum):
    USER_EXPLICIT = "USER_EXPLICIT"
    PROJECTED_MEMORY = "PROJECTED_MEMORY"
    USER_CONFIRMED = "USER_CONFIRMED"


class ProfileStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"


class ProfileResultStatus(str, Enum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    SENSITIVE_REJECTED = "SENSITIVE_REJECTED"


@dataclass(frozen=True)
class ProfileEntry:
    schema_version: int
    entry_id: str
    owner_id: str
    category: Category
    key: str
    value: str
    confidence: Confidence
    provenance: Provenance
    source_memory_id: str
    version: int
    created_at: float
    updated_at: float
    confirmed_at: float
    expires_at: Optional[float]
    status: ProfileStatus


@dataclass(frozen=True)
class ProfileResult:
    status: ProfileResultStatus
    entry: Optional[ProfileEntry] = None
    entries: tuple = ()
