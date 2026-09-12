"""Immutable V8.2 GoalPlan / PlanStep. Valid representation is not execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple, Union

ParamValue = Union[str, int, bool]
ParamTuple = Tuple[Tuple[str, ParamValue], ...]


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    capability_id: str
    action: str
    parameters: ParamTuple
    dependencies: Tuple[str, ...]
    verification_required: bool
    verification_type: str
    risk: str
    approval_required: bool
    retry_count: int
    timeout_ms: int

    def as_public(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "capability_id": self.capability_id,
            "action": self.action,
            "parameters": dict(self.parameters),
            "dependencies": list(self.dependencies),
            "verification_required": self.verification_required,
            "verification_type": self.verification_type,
            "risk": self.risk,
            "approval_required": self.approval_required,
            "retry_count": self.retry_count,
            "timeout_ms": self.timeout_ms,
        }


@dataclass(frozen=True)
class GoalPlan:
    """Typed plan. plan_hash is integrity only, not authorization or execution."""

    plan_id: str
    goal_id: str
    schema_version: str
    owner_id: str
    session_id: str
    computer_session_id: str
    steps: Tuple[PlanStep, ...]
    plan_risk: str
    approval_required: bool
    provenance: str
    plan_hash: str
    execution_permitted: bool = False
    approved: bool = False

    def as_public(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal_id": self.goal_id,
            "schema_version": self.schema_version,
            "owner_id": self.owner_id,
            "session_id": self.session_id,
            "computer_session_id": self.computer_session_id,
            "steps": [s.as_public() for s in self.steps],
            "plan_risk": self.plan_risk,
            "approval_required": self.approval_required,
            "provenance": self.provenance,
            "plan_hash": self.plan_hash,
            "execution_permitted": False,
            "approved": False,
        }
