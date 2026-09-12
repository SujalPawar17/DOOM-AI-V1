"""Bounded in-process V8.6 task registry. SQL lives in the V8.7 ledger, not here."""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

from orchestration.executor import ExecutionResult
from orchestration.goal.plan_types import GoalPlan
from orchestration.goal.types import GoalSpec
from orchestration.recovery.types import RecoveryExecutionResult
from orchestration.task.errors import (
    MAX_TASKS,
    MAX_TRANSITIONS_PER_TASK,
    TaskAlreadyTerminal,
    TaskCapacityExceeded,
    TaskNotFound,
    TaskTransitionLimitExceeded,
)
from orchestration.task.state import ensure_transition
from orchestration.task.types import (
    TERMINAL_STATES,
    TaskSnapshot,
    TaskState,
    TaskTransition,
)


class _Entry:
    __slots__ = (
        "snapshot", "plan", "goal", "execution_result", "recovery_result",
        "created_mono", "pending_recovery",
    )

    def __init__(self, snapshot: TaskSnapshot, plan: Optional[GoalPlan], goal: Optional[GoalSpec], created_mono: float) -> None:
        self.snapshot = snapshot
        self.plan = plan
        self.goal = goal
        self.execution_result: Optional[ExecutionResult] = None
        self.recovery_result: Optional[RecoveryExecutionResult] = None
        self.created_mono = created_mono
        self.pending_recovery = False


_LOCK = threading.Lock()
_REGISTRY: Dict[str, _Entry] = {}


def reset_task_registry_for_tests() -> None:
    with _LOCK:
        _REGISTRY.clear()


def _require(task_id: str) -> _Entry:
    row = _REGISTRY.get(task_id)
    if row is None:
        raise TaskNotFound()
    return row


def put_created(snapshot: TaskSnapshot, plan: Optional[GoalPlan], goal: Optional[GoalSpec], created_mono: float) -> TaskSnapshot:
    from orchestration.task.ledger import try_persist_create
    from orchestration.task.ledger_errors import TaskAlreadyExists
    with _LOCK:
        if len(_REGISTRY) >= MAX_TASKS:
            raise TaskCapacityExceeded()
        if snapshot.task_id in _REGISTRY:
            raise TaskAlreadyExists()
        try_persist_create(snapshot)
        _REGISTRY[snapshot.task_id] = _Entry(snapshot, plan, goal, created_mono)
        return snapshot


def load_snapshot_for_inspection(snapshot: TaskSnapshot) -> TaskSnapshot:
    """Registry insert without a GoalPlan. Cannot execute. Inspection only."""
    with _LOCK:
        if len(_REGISTRY) >= MAX_TASKS:
            raise TaskCapacityExceeded()
        _REGISTRY[snapshot.task_id] = _Entry(snapshot, None, None, 0.0)
        return snapshot


def get_entry(task_id: str) -> _Entry:
    with _LOCK:
        return _require(task_id)


def get_task(task_id: str) -> TaskSnapshot:
    with _LOCK:
        return _require(task_id).snapshot


def list_tasks() -> List[TaskSnapshot]:
    with _LOCK:
        return [row.snapshot for row in _REGISTRY.values()]


def apply_transition(
    task_id: str,
    nxt: TaskState,
    *,
    reason: str,
    now_ms: int,
    plan_hash: str = "",
    recovery_plan_hash: str = "",
    started_unix_ms: int = -1,
    completed_unix_ms: int = -1,
    attempts_used: int = -1,
    execution_started: Optional[bool] = None,
    pending_recovery: Optional[bool] = None,
    execution_result: Optional[ExecutionResult] = None,
    recovery_result: Optional[RecoveryExecutionResult] = None,
) -> TaskSnapshot:
    with _LOCK:
        row = _require(task_id)
        snap = row.snapshot
        ensure_transition(snap.state, nxt)
        if len(snap.transitions) >= MAX_TRANSITIONS_PER_TASK:
            raise TaskTransitionLimitExceeded()
        if execution_result is not None:
            row.execution_result = execution_result
        if recovery_result is not None:
            row.recovery_result = recovery_result
        from orchestration.task.ledger import try_persist_transition
        persisted = try_persist_transition(
            snap, nxt, reason=reason, now_ms=now_ms, plan_hash=plan_hash or snap.plan_hash,
            recovery_plan_hash=recovery_plan_hash, started_unix_ms=started_unix_ms,
            completed_unix_ms=completed_unix_ms, attempts_used=attempts_used,
            execution_started=execution_started, pending_recovery=pending_recovery,
        )
        event = TaskTransition(
            from_state=snap.state.value,
            to_state=nxt.value,
            timestamp_unix_ms=now_ms,
            reason_code=reason[:64],
            plan_hash=(plan_hash or snap.plan_hash)[:128],
        )
        history = snap.transitions + (event,)
        done = snap.completed_unix_ms
        if nxt in TERMINAL_STATES:
            done = completed_unix_ms if completed_unix_ms >= 0 else now_ms
        started = snap.started_unix_ms if started_unix_ms < 0 else started_unix_ms
        rec_hash = snap.recovery_plan_hash if not recovery_plan_hash else recovery_plan_hash
        attempts = snap.attempts_used if attempts_used < 0 else attempts_used
        exec_flag = snap.execution_started if execution_started is None else execution_started
        row.snapshot = TaskSnapshot(
            task_id=snap.task_id,
            goal_id=snap.goal_id,
            owner_id=snap.owner_id,
            session_id=snap.session_id,
            computer_session_id=snap.computer_session_id,
            plan_hash=snap.plan_hash,
            recovery_plan_hash=rec_hash[:128],
            state=nxt,
            created_unix_ms=snap.created_unix_ms,
            started_unix_ms=started,
            completed_unix_ms=done,
            attempts_used=attempts,
            transition_count=len(history),
            failure_reason=reason[:64],
            execution_started=exec_flag,
            transitions=history,
            version=int(persisted.version) if persisted is not None else int(snap.version) + 1,
        )
        if pending_recovery is not None:
            row.pending_recovery = pending_recovery
        return row.snapshot


def remove_task(task_id: str) -> None:
    with _LOCK:
        row = _require(task_id)
        if row.snapshot.state not in TERMINAL_STATES:
            raise TaskAlreadyTerminal()
        del _REGISTRY[task_id]


def registry_size() -> int:
    with _LOCK:
        return len(_REGISTRY)
