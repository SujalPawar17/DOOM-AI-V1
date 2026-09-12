"""Test-only V8 execution helpers. Not a production API. Not imported by doom.py."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from orchestration.executor import ExecutionIdentity, execute_plan, use_test_execution_hooks
from orchestration.goal.plan_types import GoalPlan
from orchestration.recovery.engine import recover_execution
from orchestration.task.engine import start_task
from orchestration.task.registry import get_task


def ident_for(plan: Any, **overrides: str) -> ExecutionIdentity:
    if type(plan) is GoalPlan:
        fields = {
            "owner_id": plan.owner_id,
            "session_id": plan.session_id,
            "computer_session_id": plan.computer_session_id,
        }
    else:
        fields = {"owner_id": "owner_A", "session_id": "sess", "computer_session_id": ""}
    fields.update(overrides)
    return ExecutionIdentity(
        owner_id=str(fields.get("owner_id") or ""),
        session_id=str(fields.get("session_id") or ""),
        computer_session_id=str(fields.get("computer_session_id") or ""),
    )


def ident_for_task(task_id: str) -> ExecutionIdentity:
    snap = get_task(task_id)
    return ExecutionIdentity(
        owner_id=snap.owner_id,
        session_id=snap.session_id,
        computer_session_id=snap.computer_session_id,
    )


def exec_plan(
    plan: Any,
    *,
    adapters: Optional[Dict[str, Callable]] = None,
    emergency_stop_fn: Optional[Callable] = None,
    cancelled_fn: Optional[Callable] = None,
    authorized_plan_hash: str = "",
    identity: Any = None,
    expected_owner_id: Optional[str] = None,
    expected_session_id: Optional[str] = None,
    expected_computer_session_id: Optional[str] = None,
) -> Any:
    ident = identity
    if ident is None:
        extra = {}
        if expected_owner_id is not None:
            extra["owner_id"] = expected_owner_id
        if expected_session_id is not None:
            extra["session_id"] = expected_session_id
        if expected_computer_session_id is not None:
            extra["computer_session_id"] = expected_computer_session_id
        ident = ident_for(plan, **extra)
    with use_test_execution_hooks(
        adapters=adapters,
        emergency_stop_fn=emergency_stop_fn,
        cancelled_fn=cancelled_fn,
    ):
        return execute_plan(plan, identity=ident, authorized_plan_hash=authorized_plan_hash)


def rec_exec(
    plan: Any,
    execution_result: Any,
    goal: Any,
    *,
    adapters: Optional[Dict[str, Callable]] = None,
    emergency_stop_fn: Optional[Callable] = None,
    cancelled_fn: Optional[Callable] = None,
    authorized_plan_hash: str = "",
    identity: Any = None,
    recovery_attempt: bool = False,
    expected_owner_id: Optional[str] = None,
    expected_session_id: Optional[str] = None,
    expected_computer_session_id: Optional[str] = None,
) -> Any:
    ident = identity
    if ident is None:
        extra = {}
        if expected_owner_id is not None:
            extra["owner_id"] = expected_owner_id
        if expected_session_id is not None:
            extra["session_id"] = expected_session_id
        if expected_computer_session_id is not None:
            extra["computer_session_id"] = expected_computer_session_id
        ident = ident_for(plan, **extra)
    with use_test_execution_hooks(
        adapters=adapters,
        emergency_stop_fn=emergency_stop_fn,
        cancelled_fn=cancelled_fn,
    ):
        return recover_execution(
            plan,
            execution_result,
            goal,
            identity=ident,
            authorized_plan_hash=authorized_plan_hash,
            recovery_attempt=recovery_attempt,
        )


def start(
    task_id: str,
    *,
    adapters: Optional[Dict[str, Callable]] = None,
    emergency_stop_fn: Optional[Callable] = None,
    cancelled_fn: Optional[Callable] = None,
    authorized_plan_hash: str = "",
    identity: Any = None,
    allow_recovery: bool = False,
) -> Any:
    ident = identity
    if ident is None:
        try:
            ident = ident_for_task(task_id)
        except Exception:
            ident = ExecutionIdentity(owner_id="owner_A", session_id="sess")
    with use_test_execution_hooks(
        adapters=adapters,
        emergency_stop_fn=emergency_stop_fn,
        cancelled_fn=cancelled_fn,
    ):
        return start_task(
            task_id,
            identity=ident,
            authorized_plan_hash=authorized_plan_hash,
            allow_recovery=allow_recovery,
        )
