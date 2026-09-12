"""Immutable V8.1 GoalSpec and classification result. Data only; not executable."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional


GOAL_SCHEMA_VERSION = "v81.1"
MAX_RAW_INTENT = 2048
ALLOWED_CONTEXT_KEYS = frozenset({"owner_id", "session_id", "computer_session_id"})
FORBIDDEN_CONTEXT_KEYS = frozenset({
    "callable", "function", "code", "command", "shell", "import", "module",
    "eval", "exec", "tool", "tool_name", "callback", "lambda",
    "hwnd", "pid", "exe_path", "exe_path_norm", "window_handle",
})


class IntentClass(str, Enum):
    COMPUTER = "COMPUTER"
    BROWSER = "BROWSER"
    FILESYSTEM = "FILESYSTEM"
    MEMORY_READ = "MEMORY_READ"
    WORLD_ACTION = "WORLD_ACTION"
    CONVERSATION = "CONVERSATION"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"


class CapabilityClass(str, Enum):
    COMPUTER = "computer"
    BROWSER = "browser"
    FILESYSTEM = "filesystem"
    SEQUENCE = "sequence"
    VERIFICATION = "verification"
    WORLD_ACT = "world_act"
    MEMORY_READ = "memory_read"
    CONVERSATION = "conversation"
    NONE = "none"


class AvailabilityStatus(str, Enum):
    V8_DISABLED = "V8_DISABLED"
    CAPABILITY_AVAILABLE = "CAPABILITY_AVAILABLE"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"


class Provenance(str, Enum):
    USER_TEXT = "USER_TEXT"


INTENT_TO_CAPABILITY = {
    IntentClass.COMPUTER: CapabilityClass.COMPUTER,
    IntentClass.BROWSER: CapabilityClass.BROWSER,
    IntentClass.FILESYSTEM: CapabilityClass.FILESYSTEM,
    IntentClass.MEMORY_READ: CapabilityClass.MEMORY_READ,
    IntentClass.WORLD_ACTION: CapabilityClass.WORLD_ACT,
    IntentClass.CONVERSATION: CapabilityClass.CONVERSATION,
    IntentClass.UNKNOWN: CapabilityClass.NONE,
    IntentClass.AMBIGUOUS: CapabilityClass.NONE,
}


def sanitize_context(context: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    if not context:
        return {}
    for key in context.keys():
        name = str(key).strip().lower()
        if name in FORBIDDEN_CONTEXT_KEYS or name not in ALLOWED_CONTEXT_KEYS:
            raise ValueError("INVALID_CONTEXT")
        val = context[key]
        if callable(val):
            raise ValueError("INVALID_CONTEXT")
    out: Dict[str, str] = {}
    for key in ("owner_id", "session_id", "computer_session_id"):
        if key in context and context[key] is not None:
            out[key] = str(context[key])[:64]
    return out


def truncate_intent(raw: str) -> str:
    text = str(raw or "").replace("\x00", "")
    return text[:MAX_RAW_INTENT]


@dataclass(frozen=True)
class GoalSpec:
    goal_id: str
    schema_version: str
    owner_id: str
    session_id: str
    computer_session_id: str
    raw_intent: str
    normalized_intent: IntentClass
    capability_class: CapabilityClass
    provenance: Provenance
    requested_unix_ms: int
    goal_hash: str

    def as_public(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "schema_version": self.schema_version,
            "owner_id": self.owner_id,
            "session_id": self.session_id,
            "computer_session_id": self.computer_session_id,
            "raw_intent": self.raw_intent,
            "normalized_intent": self.normalized_intent.value,
            "capability_class": self.capability_class.value,
            "provenance": self.provenance.value,
            "requested_unix_ms": self.requested_unix_ms,
            "goal_hash": self.goal_hash,
        }


@dataclass(frozen=True)
class CapabilityRecord:
    capability_class: CapabilityClass
    owner_plane: str
    exists: bool
    enabled: bool
    available: bool
    execution_permitted: bool


@dataclass(frozen=True)
class GoalClassificationResult:
    status: AvailabilityStatus
    reason_code: str
    goal: GoalSpec
    capability: CapabilityRecord
    execution_permitted: bool

    def as_public(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "reason_code": self.reason_code,
            "execution_permitted": False,
            "goal": self.goal.as_public(),
            "capability": {
                "capability_class": self.capability.capability_class.value,
                "owner_plane": self.capability.owner_plane,
                "exists": self.capability.exists,
                "enabled": self.capability.enabled,
                "available": self.capability.available,
                "execution_permitted": False,
            },
        }
