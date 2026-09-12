"""V8.6 in-process task state tests. No real OS/browser/FS mutation."""
from __future__ import annotations

import ast
import os
import time
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")

from orchestration.executor import reset_execution_ledger_for_tests
from orchestration.goal.kernel import process_goal
from orchestration.goal.plan_validator import build_goal_plan
from orchestration.recovery.engine import reset_recovery_attempts_for_tests
from orchestration.recovery.planner import propose_recovery
from orchestration.task.engine import force_transition_for_tests, start_task, task_result
from orchestration.task.errors import (
    InvalidTaskPlan,
    InvalidTaskTransition,
    TaskAlreadyStarted,
    TaskAlreadyTerminal,
    TaskCapacityExceeded,
    TaskNotFound,
    TaskTimeout,
)
from orchestration.task.registry import get_entry, get_task, list_tasks, remove_task, reset_task_registry_for_tests
from orchestration.task.state import ALLOWED_TRANSITIONS
from orchestration.task.types import TaskState
from orchestration.task.engine import cancel_task, create_task, poll_task
from test_v8_harness import start

ROOT = Path(__file__).resolve().parent
TASK_PKG = ROOT / "orchestration" / "task"


def _v8_on():
    os.environ["PROACTIVE_V8_ENABLED"] = "true"


def _v8_off():
    os.environ["PROACTIVE_V8_ENABLED"] = "false"


def _step(step_id="s1", capability_id="conversation", action="RESPOND", **kw):
    d = {
        "step_id": step_id,
        "capability_id": capability_id,
        "action": action,
        "parameters": kw.pop("parameters", {}),
        "dependencies": kw.pop("dependencies", ()),
        "verification_required": kw.pop("verification_required", False),
        "verification_type": kw.pop("verification_type", ""),
        "retry_count": kw.pop("retry_count", 0),
        "timeout_ms": kw.pop("timeout_ms", 8000),
    }
    d.update(kw)
    return d


def _goal(**ctx):
    _v8_on()
    base = {"owner_id": "owner_A", "session_id": "sess"}
    base.update(ctx)
    return process_goal("hello", context=base).goal


class _Spy:
    def __init__(self, status="SUCCESS"):
        self.n = 0
        self.status = status

    def __call__(self, step, plan):
        self.n += 1
        return self.status


class TestV86TaskState(unittest.TestCase):
    def setUp(self):
        _v8_off()
        reset_task_registry_for_tests()
        reset_execution_ledger_for_tests()
        reset_recovery_attempts_for_tests()

    def tearDown(self):
        _v8_off()
        reset_task_registry_for_tests()
        reset_execution_ledger_for_tests()
        reset_recovery_attempts_for_tests()

    def test_create_valid_and_reject_invalid(self):
        _v8_on()
        goal = _goal()
        plan = build_goal_plan(goal, [_step()])
        snap = create_task(plan, goal)
        self.assertEqual(snap.state, TaskState.READY)
        self.assertEqual(snap.plan_hash, plan.plan_hash)
        self.assertFalse(snap.as_public()["durable"])
        with self.assertRaises(InvalidTaskPlan):
            create_task({"steps": []})
        with self.assertRaises(InvalidTaskPlan):
            create_task(None)
        with self.assertRaises(InvalidTaskPlan):
            create_task(lambda: None)
        with self.assertRaises(InvalidTaskPlan):
            create_task(replace(plan, plan_hash="0" * 64))
        with self.assertRaises(TaskNotFound):
            get_task("missing")

    def test_transitions_and_invalid(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        snap = create_task(plan)
        self.assertEqual(snap.state, TaskState.READY)
        with self.assertRaises(InvalidTaskTransition):
            force_transition_for_tests(snap.task_id, TaskState.COMPLETED)
        with self.assertRaises(InvalidTaskTransition):
            force_transition_for_tests(snap.task_id, TaskState.CREATED)
        running = force_transition_for_tests(snap.task_id, TaskState.RUNNING)
        self.assertEqual(running.state, TaskState.RUNNING)
        done = force_transition_for_tests(snap.task_id, TaskState.COMPLETED)
        self.assertEqual(done.state, TaskState.COMPLETED)
        with self.assertRaises(InvalidTaskTransition):
            force_transition_for_tests(snap.task_id, TaskState.READY)
        with self.assertRaises(TaskAlreadyTerminal):
            start(snap.task_id, adapters={"conversation": _Spy()})

    def test_start_complete_poll_idempotent(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        snap = create_task(plan)
        spy = _Spy()
        first = start(snap.task_id, adapters={"conversation": spy})
        self.assertEqual(first.state, TaskState.COMPLETED)
        self.assertEqual(spy.n, 1)
        poll_task(snap.task_id)
        poll_task(snap.task_id)
        self.assertEqual(spy.n, 1)
        with self.assertRaises(TaskAlreadyTerminal):
            start(snap.task_id, adapters={"conversation": spy})
        self.assertEqual(spy.n, 1)
        self.assertIs(plan.steps, plan.steps)

    def test_failed_cancelled_aborted(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        snap = create_task(plan)
        fail = start(snap.task_id, adapters={"conversation": _Spy("STEP_FAILED")})
        self.assertEqual(fail.state, TaskState.FAILED)
        snap2 = create_task(build_goal_plan(_goal(), [_step()]))
        cancelled = cancel_task(snap2.task_id)
        self.assertEqual(cancelled.state, TaskState.CANCELLED)
        snap3 = create_task(build_goal_plan(_goal(), [_step()]))
        aborted = start(snap3.task_id, adapters={"conversation": _Spy()}, emergency_stop_fn=lambda *_: True)
        self.assertEqual(aborted.state, TaskState.ABORTED)

    def test_waiting_approval_then_start(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="browser", action="BACK", parameters={"session_id": "cs1"},
        )])
        snap = create_task(plan, goal)
        spy = _Spy()
        waiting = start(snap.task_id, adapters={"browser": spy})
        self.assertEqual(waiting.state, TaskState.WAITING)
        self.assertEqual(spy.n, 0)
        again = start(snap.task_id, authorized_plan_hash=plan.plan_hash, adapters={"browser": spy})
        self.assertEqual(again.state, TaskState.COMPLETED)
        self.assertEqual(spy.n, 1)

    def test_recovery_lifecycle_and_no_auth_inherit(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="READ_FILE",
            parameters={"path": "a.txt"}, retry_count=1,
        )])
        snap = create_task(plan, goal)
        spy = _Spy()
        from orchestration.executor import ExecutionResult
        from orchestration.executor_errors import ExecutionStatus

        def timeout_then_ok(step, live):
            spy.n += 1
            if spy.n == 1:
                return ExecutionStatus.TIMEOUT.value
            return ExecutionStatus.SUCCESS.value

        out = start(
            snap.task_id, adapters={"filesystem": timeout_then_ok}, allow_recovery=True,
            authorized_plan_hash=plan.plan_hash,
        )
        self.assertIn(out.state, (TaskState.WAITING, TaskState.COMPLETED, TaskState.FAILED))
        if out.state is TaskState.WAITING:
            self.assertNotEqual(out.recovery_plan_hash, plan.plan_hash)
            stolen = start(
                snap.task_id, authorized_plan_hash=plan.plan_hash, adapters={"filesystem": timeout_then_ok},
                allow_recovery=True,
            )
            self.assertEqual(stolen.state, TaskState.WAITING)
            ok = start(
                snap.task_id, authorized_plan_hash=out.recovery_plan_hash,
                adapters={"filesystem": timeout_then_ok}, allow_recovery=True,
            )
            self.assertEqual(ok.state, TaskState.COMPLETED)
        self.assertNotEqual(get_task(snap.task_id).plan_hash, "")
        self.assertEqual(plan.plan_hash, snap.plan_hash)

    def test_not_verified_not_completed(self):
        _v8_on()
        goal = _goal()
        plan = build_goal_plan(goal, [_step(
            verification_required=True, verification_type="TARGET_EXISTS",
        )])
        snap = create_task(plan, goal)
        out = start(snap.task_id, adapters={
            "conversation": _Spy(),
            "verification": _Spy("NOT_VERIFIED"),
        })
        self.assertEqual(out.state, TaskState.FAILED)
        self.assertEqual(out.failure_reason, "NOT_VERIFIED")

    def test_v8_disabled_capacity_history_lifetime(self):
        plan = build_goal_plan(_goal(), [_step()])
        _v8_off()
        blocked = create_task(plan)
        self.assertEqual(blocked.state, TaskState.BLOCKED)
        _v8_on()
        with patch("orchestration.task.registry.MAX_TASKS", 1):
            reset_task_registry_for_tests()
            create_task(build_goal_plan(_goal(), [_step()]))
            with self.assertRaises(TaskCapacityExceeded):
                create_task(build_goal_plan(_goal(), [_step()]))
        reset_task_registry_for_tests()
        snap = create_task(build_goal_plan(_goal(), [_step()]))
        force_transition_for_tests(snap.task_id, TaskState.RUNNING)
        for _ in range(31):
            force_transition_for_tests(snap.task_id, TaskState.WAITING, "WAIT")
            force_transition_for_tests(snap.task_id, TaskState.RUNNING, "RUN")
        with self.assertRaises(Exception):
            force_transition_for_tests(snap.task_id, TaskState.WAITING, "WAIT")
        snap2 = create_task(build_goal_plan(_goal(), [_step()]))
        get_entry(snap2.task_id).created_mono = time.monotonic() - 400.0
        with patch("orchestration.task.engine.MAX_TASK_LIFETIME_MS", 1):
            with self.assertRaises(TaskTimeout):
                start(snap2.task_id, adapters={"conversation": _Spy()})
        self.assertIn(get_task(snap2.task_id).state, (TaskState.FAILED, TaskState.BLOCKED))

    def test_immutable_snapshots_and_no_secrets(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        snap = create_task(plan)
        with self.assertRaises(FrozenInstanceError):
            snap.state = TaskState.COMPLETED  # type: ignore[misc]
        done = start(snap.task_id, adapters={"conversation": _Spy()})
        with self.assertRaises(FrozenInstanceError):
            done.transitions[0].reason_code = "x"  # type: ignore[misc]
        blob = str(done.as_public()).lower()
        for token in ("screenshot", "password", "secret", "credential"):
            self.assertNotIn(token, blob)
        listed = list_tasks()
        self.assertEqual(len(listed), 1)
        remove_task(done.task_id)
        self.assertEqual(list_tasks(), [])

    def test_ast_isolation(self):
        forbidden_mod = {
            "subprocess", "importlib", "ctypes", "pyautogui", "playwright", "selenium",
            "requests", "httpx", "urllib", "sqlite3", "psycopg2", "redis",
        }
        forbidden_names = {"eval", "exec", "__import__", "ALL_TOOLS", "TaskEngine"}
        for path in TASK_PKG.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], forbidden_mod, msg=str(path))
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], forbidden_mod, msg=str(path))
                    self.assertFalse(node.module.startswith("proactive.computer"))
                    self.assertNotEqual(node.module, "tools")
                    self.assertFalse("computer_tools" in node.module)
                    self.assertNotIn("execute_computer_action", [a.name for a in node.names])
                if isinstance(node, ast.Name) and node.id in forbidden_names:
                    self.fail(f"{path} uses {node.id}")
                if isinstance(node, ast.ClassDef) and node.name in forbidden_names:
                    self.fail(f"{path} defines {node.name}")


if __name__ == "__main__":
    unittest.main()
