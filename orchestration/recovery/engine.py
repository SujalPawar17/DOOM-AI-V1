"""V8.5 bounded recovery engine. One new GoalPlan, new hash, new authorization, V8.4 only."""

from __future__ import annotations

import threading
from typing import Any, Dict

from orchestration.executor import (
    ExecutionResult,
    bind_execution_identity,
    execute_plan,
    validate_execution_identity,
)
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.plan_types import GoalPlan
from orchestration.goal.plan_validator import hash_goal_plan
from orchestration.goal.types import GoalSpec
from orchestration.recovery.errors import MAX_RECOVERY_ATTEMPTS, RecoveryFailureClass, RecoveryStatus
from orchestration.recovery.planner import propose_recovery
from orchestration.recovery.types import RecoveryExecutionResult, RecoveryProposal
from proactive.config import is_v8_enabled

_LOCK = threading.Lock()
_ATTEMPTS: Dict[str, int] = {}


def reset_recovery_attempts_for_tests() -> None:
    with _LOCK:
        _ATTEMPTS.clear()


def _attempt_key(plan: GoalPlan) -> str:
    return "|".join((plan.owner_id, plan.goal_id, plan.plan_hash))


def _wrap(
    original: Optional[ExecutionResult],
    proposal: RecoveryProposal,
    recovery: Optional[ExecutionResult],
    final_status: str,
    *,
    attempts: int = 0,
    auth_status: str = "NONE",
) -> RecoveryExecutionResult:
    rec_hash = "" if proposal.candidate_plan is None else proposal.candidate_plan.plan_hash
    return RecoveryExecutionResult(
        original_result=original,
        recovery_proposal=proposal,
        recovery_result=recovery,
        final_status=final_status,
        attempts_used=attempts,
        original_plan_hash=proposal.original_plan_hash,
        recovery_plan_hash=rec_hash,
        failure_class=proposal.failure_class.value,
        recovery_reason=proposal.reason_code,
        attempt_number=attempts,
        authorization_required=proposal.authorization_required,
        authorization_status=auth_status,
    )


def _control_hooks():
    from orchestration.executor import _HOOK_LOCK, _TEST_HOOKS
    with _HOOK_LOCK:
        return _TEST_HOOKS.cancelled_fn, _TEST_HOOKS.emergency_stop_fn


def recover_execution(
    plan: Any,
    execution_result: Any,
    goal: Any,
    *,
    identity: Any = None,
    authorized_plan_hash: str = "",
    recovery_attempt: bool = False,
) -> RecoveryExecutionResult:
    """Recover a failed V8.4 execution with at most one new authorized GoalPlan."""
    original = execution_result if type(execution_result) is ExecutionResult else None
    if not is_v8_enabled():
        proposal = propose_recovery(plan, execution_result, goal)
        return _wrap(original, proposal, None, ExecutionStatus.V8_DISABLED.value, auth_status="NONE")
    ident_status = validate_execution_identity(identity)
    if ident_status is not None:
        proposal = propose_recovery(plan, execution_result, goal)
        return _wrap(original, proposal, None, ident_status.value, auth_status="DENIED")
    if recovery_attempt:
        proposal = propose_recovery(plan, execution_result, goal)
        proposal = RecoveryProposal(
            status=RecoveryStatus.RECURSIVE_RECOVERY,
            reason_code="RECURSIVE_RECOVERY",
            original_plan_hash=proposal.original_plan_hash,
            original_goal_id=proposal.original_goal_id,
            failed_step_id=proposal.failed_step_id,
            failure_class=proposal.failure_class,
            recovery_action=proposal.recovery_action,
            attempt_number=0,
            candidate_plan=None,
            authorization_required=False,
        )
        final = original.status.value if original is not None else RecoveryStatus.RECURSIVE_RECOVERY.value
        return _wrap(original, proposal, None, final, auth_status="DENIED")
    if type(plan) is not GoalPlan or type(goal) is not GoalSpec or original is None:
        proposal = propose_recovery(plan, execution_result, goal)
        return _wrap(None, proposal, None, RecoveryStatus.INVALID_INPUT.value, auth_status="NONE")
    bind_status = bind_execution_identity(identity, plan)
    if bind_status is not None:
        proposal = propose_recovery(plan, execution_result, goal)
        return _wrap(original, proposal, None, bind_status.value, auth_status="DENIED")
    try:
        if hash_goal_plan(plan) != plan.plan_hash:
            proposal = propose_recovery(plan, execution_result, goal)
            return _wrap(original, proposal, None, ExecutionStatus.PLAN_HASH_MISMATCH.value, auth_status="DENIED")
    except Exception:
        proposal = propose_recovery(plan, execution_result, goal)
        return _wrap(original, proposal, None, ExecutionStatus.INVALID_PLAN.value, auth_status="DENIED")
    if original.plan_hash != plan.plan_hash or original.goal_id != plan.goal_id:
        proposal = propose_recovery(plan, execution_result, goal)
        return _wrap(original, proposal, None, ExecutionStatus.PLAN_HASH_MISMATCH.value, auth_status="DENIED")

    key = _attempt_key(plan)
    with _LOCK:
        used = _ATTEMPTS.get(key, 0)
        if used >= MAX_RECOVERY_ATTEMPTS:
            proposal = propose_recovery(plan, execution_result, goal)
            return _wrap(original, proposal, None, original.status.value, attempts=used, auth_status="DENIED")

    cancel, stop = _control_hooks()
    if (cancel and cancel()) or original.status in (ExecutionStatus.ABORTED, ExecutionStatus.CANCELLED):
        proposal = propose_recovery(plan, execution_result, goal)
        return _wrap(original, proposal, None, ExecutionStatus.ABORTED.value, auth_status="DENIED")

    if (stop and stop(plan.owner_id, plan.computer_session_id or plan.session_id)) or original.status is ExecutionStatus.EMERGENCY_STOPPED:
        proposal = propose_recovery(plan, execution_result, goal)
        return _wrap(original, proposal, None, ExecutionStatus.EMERGENCY_STOPPED.value, auth_status="DENIED")

    proposal = propose_recovery(plan, execution_result, goal)
    if proposal.status is RecoveryStatus.V8_DISABLED:
        return _wrap(original, proposal, None, ExecutionStatus.V8_DISABLED.value)
    if proposal.status is not RecoveryStatus.PROPOSED or proposal.candidate_plan is None:
        final = original.status.value
        if proposal.failure_class is RecoveryFailureClass.SUCCESS:
            final = ExecutionStatus.SUCCESS.value
        return _wrap(original, proposal, None, final, auth_status="NONE")

    recovery_plan = proposal.candidate_plan
    if recovery_plan.plan_hash == plan.plan_hash:
        return _wrap(original, proposal, None, original.status.value, auth_status="DENIED")
    cand_bind = bind_execution_identity(identity, recovery_plan)
    if cand_bind is not None:
        return _wrap(original, proposal, None, cand_bind.value, auth_status="DENIED")
    if (
        recovery_plan.owner_id != plan.owner_id
        or recovery_plan.session_id != plan.session_id
        or recovery_plan.computer_session_id != plan.computer_session_id
    ):
        return _wrap(original, proposal, None, ExecutionStatus.SESSION_UNAVAILABLE.value, auth_status="DENIED")

    needs_auth = proposal.authorization_required
    auth_ok = str(authorized_plan_hash) == recovery_plan.plan_hash
    if needs_auth and not auth_ok:
        return _wrap(
            original, proposal, None, ExecutionStatus.APPROVAL_REQUIRED.value,
            auth_status="APPROVAL_REQUIRED",
        )
    auth_status = "GRANTED" if auth_ok else "NOT_REQUIRED"

    with _LOCK:
        if _ATTEMPTS.get(key, 0) >= MAX_RECOVERY_ATTEMPTS:
            return _wrap(original, proposal, None, original.status.value, attempts=_ATTEMPTS.get(key, 0), auth_status="DENIED")
        _ATTEMPTS[key] = _ATTEMPTS.get(key, 0) + 1

    recovery = execute_plan(
        recovery_plan,
        identity=identity,
        authorized_plan_hash=str(authorized_plan_hash or ""),
    )
    return _wrap(
        original, proposal, recovery, recovery.status.value,
        attempts=1, auth_status=auth_status,
    )
