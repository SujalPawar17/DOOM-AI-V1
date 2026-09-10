"""Typed V7.2 computer-action contract. No generic execute(command)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class ActionType(str, Enum):
    CLICK = "CLICK"
    TYPE = "TYPE"


class ApprovalState(str, Enum):
    NONE = "NONE"
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class Status(str, Enum):
    SUCCESS = "SUCCESS"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    CONTROL_NOT_CAPABLE = "CONTROL_NOT_CAPABLE"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    RISK_BLOCKED = "RISK_BLOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    EXECUTION_FAILED = "EXECUTION_FAILED"


REDACTED = "[REDACTED]"

PATTERN_INVOKE = "invoke"
PATTERN_SELECTION_ITEM = "selection_item"
PATTERN_TOGGLE = "toggle"
PATTERN_VALUE = "value"


@dataclass(frozen=True)
class AncestryNode:
    automation_id: str = ""
    control_type: str = ""
    runtime_id: str = ""


@dataclass(frozen=True)
class TargetIdentity:
    """Structured UIA identity. Coordinates are never identity."""

    automation_id: str = ""
    runtime_id: str = ""
    control_type: str = ""
    name: str = ""
    framework_id: str = ""
    class_name: str = ""
    ancestry: Tuple[AncestryNode, ...] = ()
    exe_path_norm: str = ""
    window_class: str = ""
    is_password: bool = False

    def is_deterministic(self) -> bool:
        has_id = bool(str(self.automation_id or "").strip() or str(self.runtime_id or "").strip())
        has_type = bool(str(self.control_type or "").strip())
        return has_id and has_type

    def as_public(self) -> Dict[str, Any]:
        return {
            "automation_id": str(self.automation_id or "")[:128],
            "runtime_id": str(self.runtime_id or "")[:256],
            "control_type": str(self.control_type or "")[:64],
            "name": str(self.name or "")[:80],
            "framework_id": str(self.framework_id or "")[:64],
            "class_name": str(self.class_name or "")[:64],
            "ancestry": [
                {
                    "automation_id": n.automation_id[:128],
                    "control_type": n.control_type[:64],
                    "runtime_id": n.runtime_id[:256],
                }
                for n in (self.ancestry or ())
            ],
            "exe_path_norm": str(self.exe_path_norm or "")[:512],
            "window_class": str(self.window_class or "")[:64],
            "is_password": bool(self.is_password),
        }


@dataclass
class ComputerActionRequest:
    action_id: str
    action_type: ActionType
    target: TargetIdentity
    precondition_observation_hash: str
    owner_id: str = ""
    session_id: str = ""
    approval_state: ApprovalState = ApprovalState.NONE
    timeout_ms: int = 800
    valid_until_unix_ms: int = 0
    text: str = ""
    sensitive: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def capability_id(self) -> str:
        if self.action_type == ActionType.CLICK:
            from proactive.computer.policy import CAPABILITY_CLICK
            return CAPABILITY_CLICK
        from proactive.computer.policy import CAPABILITY_TYPE
        return CAPABILITY_TYPE


@dataclass
class ComputerActionResult:
    action_id: str
    action_type: ActionType
    status: Status
    target_identity: Dict[str, Any]
    precondition_status: str
    execution_status: str
    error_code: str = ""
    before_observation_hash: str = ""
    after_observation_hash: str = ""
    timestamp_unix_ms: int = 0
    telemetry: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type.value,
            "status": self.status.value,
            "target_identity": self.target_identity,
            "precondition_status": self.precondition_status,
            "execution_status": self.execution_status,
            "error_code": self.error_code,
            "before_observation_hash": self.before_observation_hash,
            "after_observation_hash": self.after_observation_hash,
            "timestamp_unix_ms": self.timestamp_unix_ms,
            "telemetry": dict(self.telemetry or {}),
        }


@dataclass
class ResolvedTarget:
    handle: Any
    identity: TargetIdentity
    patterns: Tuple[str, ...] = ()
    is_password: bool = False
    is_enabled: bool = True
    value_readonly: bool = False
