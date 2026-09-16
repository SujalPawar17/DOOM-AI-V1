"""V8.28 Goal Experience / Outcome Memory — typed foundation.

Historical, owner-scoped records of finished informational goal trajectories.
Informational only. Zero execution authority.
Not Goal Registry, Continuity, User Model, Task Ledger, or V5 memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

SCHEMA_VERSION = 1

MAX_OWNER_CHARS = 64
MAX_EXPERIENCE_ID_CHARS = 64
MAX_SOURCE_GOAL_ID_CHARS = 64
MAX_TITLE_CHARS = 160
MAX_CONTENT_CHARS = 400
MAX_STEP_SUMMARY_CHARS = 80
MAX_STEPS = 8
MAX_BLOCKER_CHARS = 120
MAX_BLOCKERS = 4
MAX_USER_NOTE_CHARS = 200
MAX_TAG_CHARS = 32
MAX_TAGS = 6
MAX_EXPERIENCES_PER_OWNER = 64
MAX_LIST_RESULTS = 16

EXPERIENCE_ID_PREFIX = "ge_"
SOURCE_GOAL_ID_PREFIX = "ag_"
REGISTRY_GOAL_ID_PREFIX = "rg_"


class Outcome(str, Enum):
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
    STALE = "STALE"
    PARTIAL = "PARTIAL"


class ExperienceStatus(str, Enum):
    ACTIVE_RECORD = "ACTIVE_RECORD"
    SUPERSEDED = "SUPERSEDED"
    FORGOTTEN = "FORGOTTEN"


class ExperienceResultStatus(str, Enum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    SENSITIVE_REJECTED = "SENSITIVE_REJECTED"


@dataclass(frozen=True)
class GoalExperience:
    schema_version: int
    experience_id: str
    owner_id: str
    source_goal_id: str
    title: str
    outcome: Outcome
    step_summary: Tuple[str, ...]
    blockers: Tuple[str, ...]
    user_note: str
    tags: Tuple[str, ...]
    status: ExperienceStatus
    version: int
    created_at: float
    updated_at: float
    expires_at: Optional[float]


@dataclass(frozen=True)
class ExperienceResult:
    status: ExperienceResultStatus
    experience: Optional[GoalExperience] = None
    experiences: tuple = ()
