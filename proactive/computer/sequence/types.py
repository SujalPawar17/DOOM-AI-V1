"""Frozen sequence specification and results. Data only. No callables."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple


class SequenceStatus(str, Enum):
    SUCCESS = "SUCCESS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    RISK_BLOCKED = "RISK_BLOCKED"
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    SEQUENCE_TIMEOUT = "SEQUENCE_TIMEOUT"
    STEP_FAILED = "STEP_FAILED"
    SEQUENCE_ABORTED = "SEQUENCE_ABORTED"
    SEQUENCE_LIMIT_EXCEEDED = "SEQUENCE_LIMIT_EXCEEDED"


class FailurePolicy(str, Enum):
    FAIL_CLOSED = "FAIL_CLOSED"


def freeze_params(raw: Optional[Mapping[str, Any]]) -> Tuple[Tuple[str, str], ...]:
    if not raw:
        return ()
    items = []
    for key in sorted(str(k) for k in raw.keys()):
        val = raw[key]
        if val is None:
            text = ""
        elif isinstance(val, bool):
            text = "true" if val else "false"
        elif isinstance(val, bytes):
            text = val.decode("utf-8", errors="replace")
        else:
            text = str(val)
        items.append((str(key), text))
    return tuple(items)


def params_map(pairs: Tuple[Tuple[str, str], ...]) -> Dict[str, str]:
    return {k: v for k, v in pairs}


@dataclass(frozen=True)
class SequenceStep:
    step_id: str
    capability: str
    action: str
    parameters: Tuple[Tuple[str, str], ...] = ()
    expected_result: str = ""

    def param(self, key: str, default: str = "") -> str:
        for k, v in self.parameters:
            if k == key:
                return v
        return default


@dataclass(frozen=True)
class SequenceSpec:
    sequence_id: str
    owner_id: str
    steps: Tuple[SequenceStep, ...]
    max_steps: int = 8
    timeout_ms: int = 30000
    retry_count: int = 0
    failure_policy: FailurePolicy = FailurePolicy.FAIL_CLOSED
    computer_session_id: str = ""
    schema_version: str = "v75.1"

    def canonical_dict(self) -> Dict[str, Any]:
        return {
            "computer_session_id": str(self.computer_session_id or ""),
            "failure_policy": self.failure_policy.value,
            "max_steps": int(self.max_steps),
            "owner_id": str(self.owner_id or ""),
            "retry_count": int(self.retry_count),
            "schema_version": str(self.schema_version or ""),
            "sequence_id": str(self.sequence_id or ""),
            "steps": [
                {
                    "action": str(s.action or ""),
                    "capability": str(s.capability or ""),
                    "expected_result": str(s.expected_result or ""),
                    "parameters": [[k, v] for k, v in s.parameters],
                    "step_id": str(s.step_id or ""),
                }
                for s in self.steps
            ],
            "timeout_ms": int(self.timeout_ms),
        }

    def spec_hash(self) -> str:
        raw = json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SequenceResult:
    sequence_id: str
    status: SequenceStatus
    total_steps: int
    completed_step_count: int
    failed_step_id: str = ""
    failed_action: str = ""
    failure_code: str = ""
    aggregate_risk: str = ""
    spec_hash: str = ""
    last_before_hash: str = ""
    last_after_hash: str = ""
    telemetry: Tuple[Dict[str, Any], ...] = ()
    experiences: Tuple[Any, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "sequence_id": self.sequence_id,
            "status": self.status.value,
            "total_steps": self.total_steps,
            "completed_step_count": self.completed_step_count,
            "failed_step_id": self.failed_step_id,
            "failed_action": self.failed_action,
            "failure_code": self.failure_code,
            "aggregate_risk": self.aggregate_risk,
            "spec_hash": self.spec_hash,
            "last_before_hash": self.last_before_hash,
            "last_after_hash": self.last_after_hash,
            "telemetry": [dict(t) for t in self.telemetry],
            "experiences": [
                r.as_telemetry() if hasattr(r, "as_telemetry") else {}
                for r in self.experiences
            ],
        }
