"""V7.7 hashed experience contracts. Recording only. No lessons."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Tuple


class ExperienceOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    NOT_VERIFIED = "NOT_VERIFIED"
    TIMEOUT = "TIMEOUT"
    ABORTED = "ABORTED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"


class ExperienceProvenance(str, Enum):
    DIRECT_ACTION = "DIRECT_ACTION"
    SEQUENCE_STEP = "SEQUENCE_STEP"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    SEQUENCE_SUMMARY = "SEQUENCE_SUMMARY"
    MANUAL_TEST = "MANUAL_TEST"


class ExperienceStatus(str, Enum):
    RECORDED = "RECORDED"
    REJECTED = "REJECTED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    COST_GUARD_BLOCKED = "COST_GUARD_BLOCKED"


@dataclass(frozen=True)
class ExperienceRecord:
    experience_id: str
    schema_version: str
    timestamp_unix_ms: int
    capability: str
    action: str
    sequence_id: str
    step_id: str
    action_id: str
    sequence_spec_hash: str
    before_observation_hash: str
    after_observation_hash: str
    verification_status: str
    verification_type: str
    outcome: ExperienceOutcome
    risk_level: str
    approval_state: str
    duration_ms: int
    failure_code: str
    provenance: ExperienceProvenance
    sensitive: bool
    previous_experience_hash: str
    content_hash: str
    experience_hash: str
    duplicate_of: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "schema_version": self.schema_version,
            "timestamp_unix_ms": int(self.timestamp_unix_ms),
            "capability": self.capability,
            "action": self.action,
            "sequence_id": self.sequence_id,
            "step_id": self.step_id,
            "action_id": self.action_id,
            "sequence_spec_hash": self.sequence_spec_hash,
            "before_observation_hash": self.before_observation_hash,
            "after_observation_hash": self.after_observation_hash,
            "verification_status": self.verification_status,
            "verification_type": self.verification_type,
            "outcome": self.outcome.value,
            "risk_level": self.risk_level,
            "approval_state": self.approval_state,
            "duration_ms": int(self.duration_ms),
            "failure_code": self.failure_code,
            "provenance": self.provenance.value,
            "sensitive": bool(self.sensitive),
            "previous_experience_hash": self.previous_experience_hash,
            "content_hash": self.content_hash,
            "experience_hash": self.experience_hash,
            "duplicate_of": self.duplicate_of,
        }

    def as_telemetry(self) -> Dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "sequence_id": self.sequence_id,
            "step_id": self.step_id,
            "capability": self.capability,
            "action": self.action,
            "outcome": self.outcome.value,
            "verification_status": self.verification_status,
            "risk": self.risk_level,
            "duration_ms": int(self.duration_ms),
            "before_hash": self.before_observation_hash,
            "after_hash": self.after_observation_hash,
            "experience_hash": self.experience_hash,
            "content_hash": self.content_hash,
            "provenance": self.provenance.value,
        }


@dataclass(frozen=True)
class ExperienceDraft:
    """Inputs from V7 kernels only. No payload values."""

    capability: str
    action: str
    outcome_status: str
    owner_id: str = ""
    sequence_id: str = ""
    step_id: str = ""
    action_id: str = ""
    sequence_spec_hash: str = ""
    before_observation_hash: str = ""
    after_observation_hash: str = ""
    verification_status: str = ""
    verification_type: str = ""
    risk_level: str = ""
    approval_state: str = "NONE"
    duration_ms: int = 0
    failure_code: str = ""
    provenance: ExperienceProvenance = ExperienceProvenance.DIRECT_ACTION
    sensitive: bool = False
    experience_id: str = ""
    timestamp_unix_ms: int = 0
    stream_id: str = ""
