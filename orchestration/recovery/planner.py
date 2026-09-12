"""Deterministic V8.5 recovery planner. Untrusted proposals. Zero execution."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from orchestration.goal.plan_errors import PlanValidationError
from orchestration.goal.plan_registry import RISK_RANK, VERIFY_PARAMS, allowed_params
from orchestration.goal.plan_types import GoalPlan, PlanStep
from orchestration.goal.plan_validator import build_goal_plan, hash_goal_plan
from orchestration.goal.types import GoalSpec
from orchestration.recovery.classifier import classify_failure
from orchestration.recovery.errors import (
    MAX_RECOVERY_ATTEMPTS,
    RecoveryAction,
    RecoveryFailureClass,
    RecoveryStatus,
)
from orchestration.recovery.policy import failed_step, recovery_action_for
from orchestration.recovery.types import RecoveryFailure, RecoveryProposal
from proactive.config import is_v8_enabled


def _empty(
    status: RecoveryStatus,
    code: str,
    *,
    plan: Optional[GoalPlan] = None,
    failure: Optional[RecoveryFailure] = None,
    action: RecoveryAction = RecoveryAction.NONE,
) -> RecoveryProposal:
    fail = failure or RecoveryFailure(
        failure_class=RecoveryFailureClass.UNKNOWN,
        reason_code=code,
        recoverable=False,
    )
    return RecoveryProposal(
        status=status,
        reason_code=code,
        original_plan_hash="" if plan is None else plan.plan_hash,
        original_goal_id="" if plan is None else plan.goal_id,
        failed_step_id=fail.failed_step_id,
        failure_class=fail.failure_class,
        recovery_action=action,
        attempt_number=0,
        candidate_plan=None,
        authorization_required=False,
    )


def _draft_from_step(step: PlanStep, step_id: str) -> Dict[str, Any]:
    return {
        "step_id": step_id,
        "capability_id": step.capability_id,
        "action": step.action,
        "parameters": dict(step.parameters),
        "dependencies": (),
        "verification_required": step.verification_required,
        "verification_type": step.verification_type,
        "retry_count": step.retry_count,
        "timeout_ms": step.timeout_ms,
    }


def _verification_draft(step: PlanStep, step_id: str) -> Dict[str, Any]:
    vtype = step.action if step.capability_id == "verification" else step.verification_type
    allowed = allowed_params("verification", vtype)
    params = {k: v for k, v in dict(step.parameters).items() if k in allowed and k in VERIFY_PARAMS}
    if "domain" not in params:
        if step.capability_id in ("filesystem", "computer", "browser"):
            params["domain"] = step.capability_id
        else:
            params["domain"] = "filesystem"
    return {
        "step_id": step_id,
        "capability_id": "verification",
        "action": vtype,
        "parameters": params,
        "dependencies": (),
        "verification_required": False,
        "verification_type": "",
        "retry_count": 0,
        "timeout_ms": step.timeout_ms,
    }


def propose_recovery(plan: Any, execution_result: Any, goal: Any) -> RecoveryProposal:
    """Propose at most one new GoalPlan. Does not authorize or execute."""
    if not is_v8_enabled():
        return _empty(RecoveryStatus.V8_DISABLED, "V8_DISABLED", plan=plan if isinstance(plan, GoalPlan) else None)
    if type(plan) is not GoalPlan or type(goal) is not GoalSpec:
        return _empty(RecoveryStatus.INVALID_INPUT, "INVALID_INPUT")
    if goal.goal_id != plan.goal_id or goal.owner_id != plan.owner_id:
        return _empty(RecoveryStatus.INVALID_INPUT, "GOAL_PLAN_MISMATCH", plan=plan)
    if goal.session_id != plan.session_id or goal.computer_session_id != plan.computer_session_id:
        return _empty(RecoveryStatus.INVALID_INPUT, "GOAL_PLAN_MISMATCH", plan=plan)
    failure = classify_failure(execution_result)
    if failure.failure_class is RecoveryFailureClass.SUCCESS:
        return _empty(RecoveryStatus.NO_RECOVERY, "ORIGINAL_SUCCESS", plan=plan, failure=failure)
    if not failure.recoverable:
        return _empty(RecoveryStatus.TERMINAL, failure.reason_code, plan=plan, failure=failure)
    step = failed_step(plan, failure.failed_step_id)
    action = recovery_action_for(failure, step)
    if action is RecoveryAction.NONE or step is None:
        return _empty(RecoveryStatus.NO_RECOVERY, "NO_SAFE_RECOVERY", plan=plan, failure=failure)
    rec_id = ("rec-" + step.step_id)[:64]
    if action is RecoveryAction.RETRY_VERIFICATION:
        drafts: List[Dict[str, Any]] = [_verification_draft(step, rec_id)]
    else:
        drafts = [_draft_from_step(step, rec_id)]
    try:
        candidate = build_goal_plan(goal, drafts, claimed_plan_risk=plan.plan_risk)
    except PlanValidationError as exc:
        return _empty(RecoveryStatus.TERMINAL, exc.code, plan=plan, failure=failure)
    if RISK_RANK.get(candidate.plan_risk, 0) < RISK_RANK.get(plan.plan_risk, 0):
        return _empty(RecoveryStatus.NO_RECOVERY, "RISK_DOWNGRADE_BLOCKED", plan=plan, failure=failure)
    if candidate.plan_hash == plan.plan_hash:
        return _empty(RecoveryStatus.NO_RECOVERY, "RECOVERY_HASH_COLLISION", plan=plan, failure=failure)
    if hash_goal_plan(candidate) != candidate.plan_hash:
        return _empty(RecoveryStatus.TERMINAL, "PLAN_HASH_MISMATCH", plan=plan, failure=failure)
    if candidate.execution_permitted or candidate.approved:
        return _empty(RecoveryStatus.TERMINAL, "EXECUTION_CLAIM_REJECTED", plan=plan, failure=failure)
    return RecoveryProposal(
        status=RecoveryStatus.PROPOSED,
        reason_code="RECOVERY_PROPOSED",
        original_plan_hash=plan.plan_hash,
        original_goal_id=plan.goal_id,
        failed_step_id=step.step_id,
        failure_class=failure.failure_class,
        recovery_action=action,
        attempt_number=MAX_RECOVERY_ATTEMPTS,
        candidate_plan=candidate,
        authorization_required=candidate.approval_required,
    )
