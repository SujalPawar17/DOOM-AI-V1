"""Explicit V8.5 recovery policy. Unknown/unsafe failures are terminal."""

from __future__ import annotations

from typing import Optional

from orchestration.goal.plan_registry import IDEMPOTENT_RETRY, MUTATION_ACTIONS, VERIFY_ACTIONS
from orchestration.goal.plan_types import GoalPlan, PlanStep
from orchestration.recovery.errors import RecoveryAction, RecoveryFailureClass
from orchestration.recovery.types import RecoveryFailure


def recovery_action_for(failure: RecoveryFailure, step: Optional[PlanStep]) -> RecoveryAction:
    """Return a single allowed recovery action or NONE. Never invents capabilities."""
    if step is None or not failure.recoverable:
        return RecoveryAction.NONE
    pair = (step.capability_id, step.action)
    klass = failure.failure_class
    if klass is RecoveryFailureClass.TIMEOUT:
        if pair in MUTATION_ACTIONS:
            return RecoveryAction.NONE
        if pair in IDEMPOTENT_RETRY:
            return RecoveryAction.RETRY_IDEMPOTENT_STEP
        return RecoveryAction.NONE
    if klass is RecoveryFailureClass.PRECONDITION_FAILED:
        if pair in MUTATION_ACTIONS:
            return RecoveryAction.NONE
        if pair in IDEMPOTENT_RETRY:
            return RecoveryAction.RETRY_IDEMPOTENT_STEP
        return RecoveryAction.NONE
    if klass in (RecoveryFailureClass.NOT_VERIFIED, RecoveryFailureClass.VERIFICATION_FAILED):
        if step.capability_id == "verification" and step.action in VERIFY_ACTIONS:
            return RecoveryAction.RETRY_VERIFICATION
        if step.verification_required and step.verification_type in VERIFY_ACTIONS:
            return RecoveryAction.RETRY_VERIFICATION
        return RecoveryAction.NONE
    return RecoveryAction.NONE


def failed_step(plan: GoalPlan, failed_step_id: str) -> Optional[PlanStep]:
    if not failed_step_id:
        return None
    for step in plan.steps:
        if step.step_id == failed_step_id:
            return step
    return None
