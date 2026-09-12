"""Immutable V8.5 recovery models. A proposal is not authorization or execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from orchestration.executor import ExecutionResult
from orchestration.goal.plan_types import GoalPlan
from orchestration.recovery.errors import (
    RecoveryAction,
    RecoveryFailureClass,
    RecoveryStatus,
)


@dataclass(frozen=True)
class RecoveryFailure:
    failure_class: RecoveryFailureClass
    reason_code: str
    failed_step_id: str = ""
    recoverable: bool = False

    def as_public(self) -> Dict[str, Any]:
        return {
            "failure_class": self.failure_class.value,
            "reason_code": self.reason_code,
            "failed_step_id": self.failed_step_id,
            "recoverable": self.recoverable,
        }


@dataclass(frozen=True)
class RecoveryProposal:
    status: RecoveryStatus
    reason_code: str
    original_plan_hash: str
    original_goal_id: str
    failed_step_id: str
    failure_class: RecoveryFailureClass
    recovery_action: RecoveryAction
    attempt_number: int
    candidate_plan: Optional[GoalPlan]
    authorization_required: bool

    def as_public(self) -> Dict[str, Any]:
        plan = None if self.candidate_plan is None else self.candidate_plan.as_public()
        return {
            "status": self.status.value,
            "reason_code": self.reason_code,
            "original_plan_hash": self.original_plan_hash,
            "original_goal_id": self.original_goal_id,
            "failed_step_id": self.failed_step_id,
            "failure_class": self.failure_class.value,
            "recovery_action": self.recovery_action.value,
            "attempt_number": self.attempt_number,
            "authorization_required": self.authorization_required,
            "execution_permitted": False,
            "approved": False,
            "candidate_plan": plan,
            "recovery_plan_hash": "" if self.candidate_plan is None else self.candidate_plan.plan_hash,
        }


@dataclass(frozen=True)
class RecoveryExecutionResult:
    original_result: Optional[ExecutionResult]
    recovery_proposal: RecoveryProposal
    recovery_result: Optional[ExecutionResult]
    final_status: str
    attempts_used: int
    original_plan_hash: str
    recovery_plan_hash: str
    failure_class: str
    recovery_reason: str
    attempt_number: int
    authorization_required: bool
    authorization_status: str

    def as_public(self) -> Dict[str, Any]:
        return {
            "final_status": self.final_status,
            "attempts_used": self.attempts_used,
            "attempt_number": self.attempt_number,
            "original_plan_hash": self.original_plan_hash,
            "recovery_plan_hash": self.recovery_plan_hash,
            "failure_class": self.failure_class,
            "recovery_reason": self.recovery_reason,
            "authorization_required": self.authorization_required,
            "authorization_status": self.authorization_status,
            "original_result": None if self.original_result is None else self.original_result.as_public(),
            "recovery_proposal": self.recovery_proposal.as_public(),
            "recovery_result": None if self.recovery_result is None else self.recovery_result.as_public(),
        }
