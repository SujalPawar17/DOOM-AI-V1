"""Immutable V8.9 audit records. Descriptive only. Not authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from orchestration.audit.codes import AuditEventCode, AuditPhase, AuditReasonCode

MAX_AUDIT_EVENTS = 1024
MAX_QUERY_RESULTS = 100
MAX_METADATA_KEYS = 16
MAX_METADATA_VALUE_CHARS = 256
MAX_ID_CHARS = 128
MAX_HASH_CHARS = 256


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    sequence: int
    timestamp_unix_ms: int
    owner_id: str
    session_id: str
    goal_id: str
    task_id: str
    plan_hash: str
    recovery_plan_hash: str
    step_id: str
    phase: AuditPhase
    event_code: AuditEventCode
    outcome: str
    reason_code: AuditReasonCode
    explanation: str
    metadata: Tuple[Tuple[str, str], ...]

    def as_public(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "timestamp_unix_ms": self.timestamp_unix_ms,
            "owner_id": self.owner_id,
            "session_id": self.session_id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "plan_hash": self.plan_hash,
            "recovery_plan_hash": self.recovery_plan_hash,
            "step_id": self.step_id,
            "phase": self.phase.value,
            "event_code": self.event_code.value,
            "outcome": self.outcome,
            "reason_code": self.reason_code.value,
            "explanation": self.explanation,
            "metadata": dict(self.metadata),
            "authorizes_execution": False,
            "approved": False,
            "execution_permitted": False,
            "verification_override": False,
            "risk_override": False,
        }
