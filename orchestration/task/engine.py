"""V8.6 in-process task orchestration. Calls V8.4/V8.5 only.

External execute_plan mutations and V8.7 ledger writes are separate systems.
A ledger failure MUST NOT replay execution. Uncertain crash state is ABORTED
on rehydrate, never continued.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from orchestration.executor import execute_plan, validate_execution_identity
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.plan_registry import PLAN_SCHEMA_VERSION
from orchestration.goal.plan_types import GoalPlan
from orchestration.goal.plan_validator import hash_goal_plan
from orchestration.goal.types import GoalSpec
from orchestration.recovery.engine import recover_execution
from orchestration.recovery.errors import RecoveryStatus
from orchestration.task.errors import (
    MAX_REASON_CHARS,
    MAX_TASK_LIFETIME_MS,
    InvalidTaskPlan,
    TaskAlreadyStarted,
    TaskAlreadyTerminal,
    TaskTimeout,
)
from orchestration.task.registry import (
    apply_transition,
    get_entry,
    get_task,
    put_created,
)
from orchestration.task.types import (
    TERMINAL_STATES,
    TaskResult,
    TaskSnapshot,
    TaskState,
)
from proactive.config import is_v8_enabled


def _now_ms() -> int:
    return int(time.time() * 1000)


def _reason(code: str) -> str:
    return str(code or "")[:MAX_REASON_CHARS]


def _map_status(status: ExecutionStatus) -> TaskState:
    if status is ExecutionStatus.SUCCESS:
        return TaskState.COMPLETED
    if status is ExecutionStatus.APPROVAL_REQUIRED:
        return TaskState.WAITING
    if status in (ExecutionStatus.ABORTED, ExecutionStatus.CANCELLED, ExecutionStatus.EMERGENCY_STOPPED):
        return TaskState.ABORTED
    if status in (
        ExecutionStatus.PLAN_HASH_MISMATCH,
        ExecutionStatus.INVALID_PLAN,
        ExecutionStatus.CAPABILITY_UNAVAILABLE,
        ExecutionStatus.ACTION_UNAVAILABLE,
        ExecutionStatus.SESSION_UNAVAILABLE,
        ExecutionStatus.IDENTITY_REQUIRED,
        ExecutionStatus.V8_DISABLED,
        ExecutionStatus.BLOCKED,
    ):
        return TaskState.BLOCKED
    return TaskState.FAILED


def create_task(plan: Any, goal: Any = None) -> TaskSnapshot:
    """Create a CREATED->READY in-process task bound to a validated GoalPlan."""
    if type(plan) is not GoalPlan:
        raise InvalidTaskPlan("INVALID_TASK_PLAN")
    if goal is not None and type(goal) is not GoalSpec:
        raise InvalidTaskPlan("INVALID_GOAL")
    if plan.schema_version != PLAN_SCHEMA_VERSION:
        raise InvalidTaskPlan("INVALID_PLAN")
    try:
        digest = hash_goal_plan(plan)
    except Exception:
        raise InvalidTaskPlan("INVALID_PLAN")
    if digest != plan.plan_hash:
        raise InvalidTaskPlan("PLAN_HASH_MISMATCH")
    if goal is not None and (
        goal.goal_id != plan.goal_id
        or goal.owner_id != plan.owner_id
        or goal.session_id != plan.session_id
        or goal.computer_session_id != plan.computer_session_id
    ):
        raise InvalidTaskPlan("GOAL_PLAN_MISMATCH")
    now = _now_ms()
    snap = TaskSnapshot(
        task_id=str(uuid.uuid4())[:64],
        goal_id=plan.goal_id[:64],
        owner_id=plan.owner_id[:64],
        session_id=plan.session_id[:64],
        computer_session_id=plan.computer_session_id[:64],
        plan_hash=plan.plan_hash,
        recovery_plan_hash="",
        state=TaskState.CREATED,
        created_unix_ms=now,
        started_unix_ms=0,
        completed_unix_ms=0,
        attempts_used=0,
        transition_count=0,
        failure_reason="",
        execution_started=False,
        transitions=(),
    )
    put_created(snap, plan, goal, time.monotonic())
    if not is_v8_enabled():
        return apply_transition(snap.task_id, TaskState.BLOCKED, reason="V8_DISABLED", now_ms=_now_ms())
    return apply_transition(snap.task_id, TaskState.READY, reason="READY", now_ms=_now_ms())


def poll_task(task_id: str) -> TaskSnapshot:
    """Return an immutable snapshot. Never executes."""
    return get_task(task_id)


def cancel_task(task_id: str) -> TaskSnapshot:
    row = get_entry(task_id)
    snap = row.snapshot
    if snap.state in TERMINAL_STATES:
        raise TaskAlreadyTerminal()
    return apply_transition(task_id, TaskState.CANCELLED, reason="CANCELLED", now_ms=_now_ms())


def _lifetime_exceeded(row: Any) -> bool:
    elapsed_ms = int((time.monotonic() - row.created_mono) * 1000.0)
    wall = _now_ms() - row.snapshot.created_unix_ms
    return elapsed_ms > MAX_TASK_LIFETIME_MS or wall > MAX_TASK_LIFETIME_MS


def _finish_from_execution(task_id: str, result, started: int) -> TaskSnapshot:
    mapped = _map_status(result.status)
    reason = _reason(result.status.value)
    exec_started = result.status is not ExecutionStatus.APPROVAL_REQUIRED
    if mapped is TaskState.WAITING:
        return apply_transition(
            task_id, TaskState.WAITING, reason=reason, now_ms=_now_ms(),
            started_unix_ms=started, execution_started=False,
            execution_result=result,
        )
    if mapped is TaskState.COMPLETED:
        return apply_transition(
            task_id, TaskState.COMPLETED, reason=reason, now_ms=_now_ms(),
            started_unix_ms=started, execution_started=True,
            execution_result=result, pending_recovery=False,
        )
    return apply_transition(
        task_id, mapped, reason=reason, now_ms=_now_ms(),
        started_unix_ms=started, execution_started=exec_started,
        execution_result=result,
    )


def _control_hooks():
    from orchestration.executor import _HOOK_LOCK, _TEST_HOOKS
    with _HOOK_LOCK:
        return _TEST_HOOKS.cancelled_fn, _TEST_HOOKS.emergency_stop_fn


def start_task(
    task_id: str,
    *,
    identity: Any = None,
    authorized_plan_hash: str = "",
    allow_recovery: bool = False,
) -> TaskSnapshot:
    """READY/WAITING -> RUNNING and execute once via V8.4. Polling never calls this path."""
    row = get_entry(task_id)
    snap = row.snapshot
    if validate_execution_identity(identity) is not None:
        raise InvalidTaskPlan("IDENTITY_REQUIRED")
    if identity.owner_id != snap.owner_id or identity.session_id != snap.session_id:
        raise InvalidTaskPlan("SESSION_UNAVAILABLE")
    if snap.computer_session_id and identity.computer_session_id != snap.computer_session_id:
        raise InvalidTaskPlan("SESSION_UNAVAILABLE")
    if row.plan is None:
        raise InvalidTaskPlan("PLAN_NOT_BOUND")
    if snap.state in TERMINAL_STATES:
        raise TaskAlreadyTerminal()
    if snap.execution_started and not row.pending_recovery:
        raise TaskAlreadyStarted()
    if _lifetime_exceeded(row):
        if snap.state is TaskState.READY:
            apply_transition(task_id, TaskState.RUNNING, reason="TIMEOUT", now_ms=_now_ms())
            apply_transition(task_id, TaskState.FAILED, reason="TIMEOUT", now_ms=_now_ms())
        elif snap.state is TaskState.WAITING:
            apply_transition(task_id, TaskState.FAILED, reason="TIMEOUT", now_ms=_now_ms())
        else:
            apply_transition(task_id, TaskState.BLOCKED, reason="TIMEOUT", now_ms=_now_ms())
        raise TaskTimeout()
    cancel, stop = _control_hooks()
    if cancel and cancel():
        return apply_transition(task_id, TaskState.CANCELLED, reason="CANCELLED", now_ms=_now_ms())
    if stop and stop(snap.owner_id, snap.computer_session_id or snap.session_id):
        if snap.state is TaskState.READY:
            apply_transition(task_id, TaskState.RUNNING, reason="EMERGENCY_STOPPED", now_ms=_now_ms())
        return apply_transition(task_id, TaskState.ABORTED, reason="EMERGENCY_STOPPED", now_ms=_now_ms())

    started = _now_ms()
    if row.pending_recovery:
        if snap.state is TaskState.WAITING:
            apply_transition(task_id, TaskState.RECOVERING, reason="RECOVERING", now_ms=_now_ms(), started_unix_ms=started)
        return _run_recovery(task_id, identity, authorized_plan_hash, started)

    if snap.state is TaskState.READY:
        apply_transition(task_id, TaskState.RUNNING, reason="RUNNING", now_ms=_now_ms(), started_unix_ms=started)
    elif snap.state is TaskState.WAITING:
        apply_transition(task_id, TaskState.RUNNING, reason="RUNNING", now_ms=_now_ms(), started_unix_ms=started)

    result = execute_plan(
        row.plan,
        identity=identity,
        authorized_plan_hash=str(authorized_plan_hash or ""),
    )
    if result.status is ExecutionStatus.SUCCESS:
        return _finish_from_execution(task_id, result, started)
    if result.status is ExecutionStatus.APPROVAL_REQUIRED:
        return _finish_from_execution(task_id, result, started)
    if result.status in (ExecutionStatus.ABORTED, ExecutionStatus.CANCELLED, ExecutionStatus.EMERGENCY_STOPPED):
        return _finish_from_execution(task_id, result, started)

    mapped = _map_status(result.status)
    if allow_recovery and row.goal is not None and mapped is TaskState.FAILED:
        apply_transition(
            task_id, TaskState.RECOVERING, reason=_reason(result.status.value), now_ms=_now_ms(),
            started_unix_ms=started, execution_started=True, execution_result=result,
        )
        rec = recover_execution(
            row.plan, result, row.goal,
            identity=identity,
            authorized_plan_hash=str(authorized_plan_hash or ""),
        )
        return _apply_recovery(task_id, rec, started)
    return _finish_from_execution(task_id, result, started)


def _run_recovery(
    task_id: str,
    identity: Any,
    authorized_plan_hash: str,
    started: int,
) -> TaskSnapshot:
    row = get_entry(task_id)
    if row.execution_result is None or row.goal is None:
        return apply_transition(task_id, TaskState.FAILED, reason="NO_RECOVERY", now_ms=_now_ms(), started_unix_ms=started)
    rec = recover_execution(
        row.plan, row.execution_result, row.goal,
        identity=identity,
        authorized_plan_hash=str(authorized_plan_hash or ""),
    )
    return _apply_recovery(task_id, rec, started)


def _apply_recovery(task_id: str, rec, started: int) -> TaskSnapshot:
    rec_hash = rec.recovery_plan_hash
    attempts = rec.attempts_used
    if rec.final_status == ExecutionStatus.SUCCESS.value:
        return apply_transition(
            task_id, TaskState.COMPLETED, reason="RECOVERY_SUCCESS", now_ms=_now_ms(),
            started_unix_ms=started, attempts_used=attempts, execution_started=True,
            recovery_plan_hash=rec_hash, recovery_result=rec, pending_recovery=False,
        )
    if rec.final_status == ExecutionStatus.APPROVAL_REQUIRED.value:
        return apply_transition(
            task_id, TaskState.WAITING, reason="APPROVAL_REQUIRED", now_ms=_now_ms(),
            started_unix_ms=started, attempts_used=attempts, execution_started=True,
            recovery_plan_hash=rec_hash, recovery_result=rec, pending_recovery=True,
        )
    if rec.final_status in (ExecutionStatus.ABORTED.value, ExecutionStatus.EMERGENCY_STOPPED.value):
        return apply_transition(
            task_id, TaskState.ABORTED, reason=_reason(rec.final_status), now_ms=_now_ms(),
            started_unix_ms=started, attempts_used=attempts, execution_started=True,
            recovery_plan_hash=rec_hash, recovery_result=rec, pending_recovery=False,
        )
    if rec.recovery_proposal.status is RecoveryStatus.RECURSIVE_RECOVERY:
        return apply_transition(
            task_id, TaskState.FAILED, reason="RECURSIVE_RECOVERY", now_ms=_now_ms(),
            started_unix_ms=started, attempts_used=attempts, execution_started=True,
            recovery_result=rec, pending_recovery=False,
        )
    mapped = TaskState.FAILED
    if rec.final_status in (
        ExecutionStatus.PLAN_HASH_MISMATCH.value,
        ExecutionStatus.CAPABILITY_UNAVAILABLE.value,
        ExecutionStatus.INVALID_PLAN.value,
        ExecutionStatus.V8_DISABLED.value,
        ExecutionStatus.SESSION_UNAVAILABLE.value,
        ExecutionStatus.IDENTITY_REQUIRED.value,
        ExecutionStatus.BLOCKED.value,
    ):
        mapped = TaskState.BLOCKED
    return apply_transition(
        task_id, mapped, reason=_reason(rec.final_status), now_ms=_now_ms(),
        started_unix_ms=started, attempts_used=attempts, execution_started=True,
        recovery_plan_hash=rec_hash, recovery_result=rec, pending_recovery=False,
    )


def task_result(task_id: str) -> TaskResult:
    row = get_entry(task_id)
    snap = row.snapshot
    return TaskResult(
        task_id=snap.task_id,
        goal_id=snap.goal_id,
        final_state=snap.state,
        plan_hash=snap.plan_hash,
        recovery_plan_hash=snap.recovery_plan_hash,
        execution_result=row.execution_result,
        recovery_result=row.recovery_result,
        attempts_used=snap.attempts_used,
        started_unix_ms=snap.started_unix_ms,
        completed_unix_ms=snap.completed_unix_ms,
        reason_code=snap.failure_reason,
    )


def force_transition_for_tests(task_id: str, nxt: TaskState, reason: str = "TEST") -> TaskSnapshot:
    """Test helper that still uses the central transition table."""
    return apply_transition(task_id, nxt, reason=reason, now_ms=_now_ms())
