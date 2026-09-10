"""Typed V7.3 browser contracts. No generic command executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Tuple

from proactive.computer.actions.types import ApprovalState

REDACTED = "[REDACTED]"


class BrowserActionType(str, Enum):
    NAVIGATE = "NAVIGATE"
    CLICK = "CLICK"
    TYPE = "TYPE"
    BACK = "BACK"
    FORWARD = "FORWARD"
    REFRESH = "REFRESH"
    CLOSE = "CLOSE"


class BrowserSessionState(str, Enum):
    CREATED = "CREATED"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class Status(str, Enum):
    SUCCESS = "SUCCESS"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_CLOSED = "SESSION_CLOSED"
    NAVIGATION_BLOCKED = "NAVIGATION_BLOCKED"
    INVALID_URL = "INVALID_URL"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    RISK_BLOCKED = "RISK_BLOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    EXECUTION_FAILED = "EXECUTION_FAILED"


@dataclass(frozen=True)
class AncestryNode:
    role: str = ""
    name: str = ""
    element_id: str = ""


@dataclass(frozen=True)
class BrowserTarget:
    role: str = ""
    name: str = ""
    element_id: str = ""
    test_id: str = ""
    input_type: str = ""
    origin: str = ""
    ancestry: Tuple[AncestryNode, ...] = ()
    is_password: bool = False

    def is_deterministic(self) -> bool:
        has_id = bool(str(self.element_id or "").strip() or str(self.test_id or "").strip())
        has_role_name = bool(str(self.role or "").strip() and str(self.name or "").strip())
        return bool(has_id or has_role_name)

    def as_public(self) -> Dict[str, Any]:
        return {
            "role": str(self.role or "")[:64],
            "name": str(self.name or "")[:80],
            "element_id": str(self.element_id or "")[:128],
            "test_id": str(self.test_id or "")[:128],
            "input_type": str(self.input_type or "")[:32],
            "origin": str(self.origin or "")[:256],
            "is_password": bool(self.is_password),
            "ancestry": [
                {"role": n.role[:64], "name": n.name[:80], "element_id": n.element_id[:128]}
                for n in (self.ancestry or ())
            ],
        }


@dataclass
class BrowserElement:
    role: str = ""
    name: str = ""
    element_id: str = ""
    test_id: str = ""
    input_type: str = ""
    disabled: bool = False
    is_password: bool = False
    href: str = ""
    value: str = ""
    editable: bool = False
    download: bool = False
    ancestry: Tuple[AncestryNode, ...] = ()
    handle: str = ""


@dataclass
class BrowserObservation:
    session_id: str = ""
    url: str = ""
    origin: str = ""
    title_advisory: str = ""
    page_identity: str = ""
    elements: List[BrowserElement] = field(default_factory=list)
    schema_version: str = ""
    observation_hash: str = ""
    download_attempted: bool = False
    node_count: int = 0

    def as_authoritative(self) -> Dict[str, Any]:
        rows = []
        for el in self.elements:
            rows.append({
                "e": str(el.element_id or "")[:128],
                "i": str(el.input_type or "")[:32],
                "n": str(el.name or "")[:80],
                "p": bool(el.is_password),
                "r": str(el.role or "")[:64],
                "t": str(el.test_id or "")[:128],
            })
        return {
            "elements": rows,
            "origin": str(self.origin or "")[:256],
            "page_identity": str(self.page_identity or ""),
            "schema_version": str(self.schema_version or ""),
            "session_id": str(self.session_id or ""),
            "url": str(self.url or "")[:512],
        }


@dataclass
class BrowserActionRequest:
    action_id: str
    action_type: BrowserActionType
    session_id: str
    owner_id: str = ""
    precondition_observation_hash: str = ""
    target: BrowserTarget = field(default_factory=BrowserTarget)
    url: str = ""
    text: str = ""
    sensitive: bool = False
    approval_state: ApprovalState = ApprovalState.NONE
    timeout_ms: int = 8000


@dataclass
class BrowserActionResult:
    action_id: str
    session_id: str
    action_type: BrowserActionType
    status: Status
    precondition_status: str = ""
    execution_status: str = ""
    error_code: str = ""
    before_observation_hash: str = ""
    after_observation_hash: str = ""
    target_identity: Dict[str, Any] = field(default_factory=dict)
    telemetry: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "session_id": self.session_id,
            "action_type": self.action_type.value,
            "status": self.status.value,
            "precondition_status": self.precondition_status,
            "execution_status": self.execution_status,
            "error_code": self.error_code,
            "before_observation_hash": self.before_observation_hash,
            "after_observation_hash": self.after_observation_hash,
            "target_identity": dict(self.target_identity or {}),
            "telemetry": dict(self.telemetry or {}),
        }
