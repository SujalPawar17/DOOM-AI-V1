"""V7.4 typed filesystem contracts. No generic file command executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from proactive.computer.actions.types import ApprovalState

REDACTED = "[REDACTED]"


class FsActionType(str, Enum):
    OBSERVE_PATH = "OBSERVE_PATH"
    LIST_DIRECTORY = "LIST_DIRECTORY"
    READ_FILE = "READ_FILE"
    CREATE_DIRECTORY = "CREATE_DIRECTORY"
    WRITE_FILE = "WRITE_FILE"
    COPY_FILE = "COPY_FILE"
    MOVE_FILE = "MOVE_FILE"
    DELETE_FILE = "DELETE_FILE"


class ObjectType(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    MISSING = "missing"
    OTHER = "other"


class Status(str, Enum):
    SUCCESS = "SUCCESS"
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    PATH_INVALID = "PATH_INVALID"
    PATH_ESCAPE_BLOCKED = "PATH_ESCAPE_BLOCKED"
    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
    PATH_DENIED = "PATH_DENIED"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    RISK_BLOCKED = "RISK_BLOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    DIRECTORY_TOO_LARGE = "DIRECTORY_TOO_LARGE"
    DIRECTORY_DELETE_BLOCKED = "DIRECTORY_DELETE_BLOCKED"
    SENSITIVE_FILE_BLOCKED = "SENSITIVE_FILE_BLOCKED"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    EXECUTION_FAILED = "EXECUTION_FAILED"


@dataclass(frozen=True)
class PathIdentity:
    canonical_path: str = ""
    object_type: ObjectType = ObjectType.MISSING
    exists: bool = False
    size_bytes: int = 0
    mtime_ns: int = 0
    file_index: str = ""

    def as_public(self) -> Dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "object_type": self.object_type.value,
            "exists": self.exists,
            "size_bytes": int(self.size_bytes),
            "mtime_ns": int(self.mtime_ns),
            "file_index": self.file_index[:64],
        }


@dataclass
class FsChild:
    name: str
    object_type: str
    size_bytes: int = 0


@dataclass
class FsObservation:
    canonical_path: str = ""
    object_type: str = ObjectType.MISSING.value
    exists: bool = False
    size_bytes: int = 0
    mtime_ns: int = 0
    file_index: str = ""
    children: List[FsChild] = field(default_factory=list)
    truncated: bool = False
    schema_version: str = ""
    observation_hash: str = ""

    def as_authoritative(self) -> Dict[str, Any]:
        kids = [
            {"n": c.name[:128], "s": int(c.size_bytes), "t": c.object_type[:16]}
            for c in (self.children or [])
        ]
        return {
            "canonical_path": self.canonical_path,
            "children": kids,
            "exists": bool(self.exists),
            "file_index": self.file_index[:64],
            "mtime_ns": int(self.mtime_ns),
            "object_type": self.object_type,
            "schema_version": self.schema_version,
            "size_bytes": int(self.size_bytes),
            "truncated": bool(self.truncated),
        }


@dataclass
class FsActionRequest:
    action_id: str
    action_type: FsActionType
    path: str
    owner_id: str = ""
    dest_path: str = ""
    content: bytes = b""
    precondition_observation_hash: str = ""
    expected_exists: Optional[bool] = None
    expected_type: Optional[ObjectType] = None
    approval_state: ApprovalState = ApprovalState.NONE
    computer_session_id: str = ""


@dataclass
class FsActionResult:
    action_id: str
    action_type: FsActionType
    status: Status
    canonical_path: str = ""
    dest_canonical_path: str = ""
    precondition_status: str = ""
    execution_status: str = ""
    error_code: str = ""
    before_observation_hash: str = ""
    after_observation_hash: str = ""
    identity: Dict[str, Any] = field(default_factory=dict)
    listing: List[Dict[str, Any]] = field(default_factory=list)
    content: bytes = b""
    truncated: bool = False
    telemetry: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type.value,
            "status": self.status.value,
            "canonical_path": self.canonical_path,
            "dest_canonical_path": self.dest_canonical_path,
            "precondition_status": self.precondition_status,
            "execution_status": self.execution_status,
            "error_code": self.error_code,
            "before_observation_hash": self.before_observation_hash,
            "after_observation_hash": self.after_observation_hash,
            "identity": dict(self.identity or {}),
            "listing": list(self.listing or []),
            "content_bytes": len(self.content or b""),
            "truncated": bool(self.truncated),
            "telemetry": dict(self.telemetry or {}),
        }
