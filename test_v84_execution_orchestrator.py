"""V8.4 execution orchestrator tests. Routing only. No real OS/browser/FS mutation."""
from __future__ import annotations

import ast
import os
import time
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")

from orchestration.executor import execute_plan, reset_execution_ledger_for_tests
from orchestration.executor_errors import ExecutionStatus
from test_v8_harness import exec_plan
from orchestration.goal.kernel import process_goal
from orchestration.goal.plan_validator import build_goal_plan, hash_goal_plan

ROOT = Path(__file__).resolve().parent
ORCH = ROOT / "orchestration"
EXECUTOR = ORCH / "executor.py"


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


def _ok(step, plan):
    return ExecutionStatus.SUCCESS.value


class _Spy:
    def __init__(self, status=ExecutionStatus.SUCCESS.value, fail_until=0):
        self.calls = []
        self.status = status
        self.fail_until = fail_until
        self.n = 0

    def __call__(self, step, plan):
        self.n += 1
        self.calls.append((step.capability_id, step.action, step.step_id))
        if self.n <= self.fail_until:
            return ExecutionStatus.STEP_FAILED.value
        return self.status


class TestV84ExecutionOrchestrator(unittest.TestCase):
    def setUp(self):
        _v8_off()
        reset_execution_ledger_for_tests()

    def tearDown(self):
        _v8_off()
        reset_execution_ledger_for_tests()

    def test_v8_disabled_zero_execution(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        _v8_off()
        spy = _Spy()
        out = exec_plan(plan, adapters={"conversation": spy})
        self.assertEqual(out.status, ExecutionStatus.V8_DISABLED)
        self.assertEqual(spy.n, 0)
        self.assertEqual(out.completed_step_ids, ())

    def test_invalid_plan_zero_execution(self):
        _v8_on()
        spy = _Spy()
        out = exec_plan(None, adapters={"conversation": spy})
        self.assertEqual(out.status, ExecutionStatus.INVALID_PLAN)
        self.assertEqual(spy.n, 0)
        out = exec_plan({"steps": []}, adapters={"conversation": spy})
        self.assertEqual(out.status, ExecutionStatus.INVALID_PLAN)
        self.assertEqual(spy.n, 0)
        out = exec_plan("hello", adapters={"conversation": spy})
        self.assertEqual(out.status, ExecutionStatus.INVALID_PLAN)
        self.assertEqual(spy.n, 0)
        out = exec_plan(_goal(), adapters={"conversation": spy})
        self.assertEqual(out.status, ExecutionStatus.INVALID_PLAN)
        self.assertEqual(spy.n, 0)
        out = exec_plan(lambda: None, adapters={"conversation": spy})
        self.assertEqual(out.status, ExecutionStatus.INVALID_PLAN)
        self.assertEqual(spy.n, 0)

    def test_plan_hash_mismatch_zero_execution(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        bad = replace(plan, plan_hash="0" * 64)
        spy = _Spy()
        out = exec_plan(bad, adapters={"conversation": spy})
        self.assertEqual(out.status, ExecutionStatus.PLAN_HASH_MISMATCH)
        self.assertEqual(spy.n, 0)

    def test_unknown_capability_zero_execution(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        step = replace(plan.steps[0], capability_id="shell")
        tmp = replace(plan, steps=(step,))
        bad = replace(tmp, plan_hash=hash_goal_plan(tmp))
        spy = _Spy()
        out = exec_plan(bad, adapters={"conversation": spy, "shell": spy})
        self.assertEqual(out.status, ExecutionStatus.CAPABILITY_UNAVAILABLE)
        self.assertEqual(spy.n, 0)

    def test_unknown_action_zero_execution(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        step = replace(plan.steps[0], action="CLICK")
        tmp = replace(plan, steps=(step,))
        bad = replace(tmp, plan_hash=hash_goal_plan(tmp))
        spy = _Spy()
        out = exec_plan(bad, adapters={"conversation": spy})
        self.assertEqual(out.status, ExecutionStatus.ACTION_UNAVAILABLE)
        self.assertEqual(spy.n, 0)

    def test_missing_approval_zero_execution(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1", session_id="sess")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="DELETE_FILE",
            parameters={"path": "a.txt"},
        )])
        self.assertTrue(plan.approval_required)
        flagged = replace(plan, approved=True)
        spy = _Spy()
        out = exec_plan(flagged, adapters={"filesystem": spy})
        self.assertEqual(out.status, ExecutionStatus.APPROVAL_REQUIRED)
        self.assertEqual(spy.n, 0)

    def test_policy_approval_not_suppressed(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="DELETE_FILE",
            parameters={"path": "a.txt"},
        )])
        step = replace(plan.steps[0], approval_required=False)
        tmp = replace(plan, steps=(step,), approval_required=False)
        bad = replace(tmp, plan_hash=hash_goal_plan(tmp))
        spy = _Spy()
        out = exec_plan(bad, adapters={"filesystem": spy})
        self.assertEqual(out.status, ExecutionStatus.APPROVAL_REQUIRED)
        self.assertEqual(spy.n, 0)

    def test_invalid_session_zero_execution(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        spy = _Spy()
        out = exec_plan(
            plan, expected_owner_id="other-owner", adapters={"conversation": spy},
        )
        self.assertEqual(out.status, ExecutionStatus.SESSION_UNAVAILABLE)
        self.assertEqual(spy.n, 0)
        fs = build_goal_plan(_goal(), [_step(
            capability_id="filesystem", action="OBSERVE_PATH",
            parameters={"path": "a"},
        )])
        out = exec_plan(
            fs, authorized_plan_hash=fs.plan_hash, adapters={"filesystem": spy},
        )
        self.assertEqual(out.status, ExecutionStatus.SESSION_UNAVAILABLE)
        self.assertEqual(spy.n, 0)

    def test_emergency_stop_before_first_step(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        spy = _Spy()
        out = exec_plan(
            plan, adapters={"conversation": spy},
            emergency_stop_fn=lambda *_: True,
        )
        self.assertEqual(out.status, ExecutionStatus.EMERGENCY_STOPPED)
        self.assertEqual(spy.n, 0)

    def test_emergency_stop_between_steps(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [
            _step("a"),
            _step("b", dependencies=("a",)),
        ])
        fired = []

        def conv(step, _plan):
            fired.append(step.step_id)
            return ExecutionStatus.SUCCESS.value

        out = exec_plan(
            plan,
            adapters={"conversation": conv},
            emergency_stop_fn=lambda *_: len(fired) >= 1,
        )
        self.assertEqual(out.status, ExecutionStatus.EMERGENCY_STOPPED)
        self.assertEqual(fired, ["a"])
        self.assertEqual(out.completed_step_ids, ("a",))

    def test_dependency_order(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [
            _step("b", dependencies=("a",)),
            _step("a"),
        ])
        order = []

        def conv(step, _plan):
            order.append(step.step_id)
            return ExecutionStatus.SUCCESS.value

        out = exec_plan(plan, adapters={"conversation": conv})
        self.assertEqual(out.status, ExecutionStatus.SUCCESS)
        self.assertEqual(order, ["a", "b"])

    def test_failed_dependency_blocks_dependent(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [
            _step("a"),
            _step("b", dependencies=("a",)),
        ])
        fired = []

        def conv(step, _plan):
            fired.append(step.step_id)
            if step.step_id == "a":
                return ExecutionStatus.STEP_FAILED.value
            return ExecutionStatus.SUCCESS.value

        out = exec_plan(plan, adapters={"conversation": conv})
        self.assertEqual(out.status, ExecutionStatus.STEP_FAILED)
        self.assertEqual(fired, ["a"])
        self.assertEqual(out.failed_step_id, "a")

    def test_cycle_not_executed(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step("a"), _step("b")])
        a = replace(plan.steps[0], dependencies=("b",))
        b = replace(plan.steps[1], dependencies=("a",))
        tmp = replace(plan, steps=(a, b))
        bad = replace(tmp, plan_hash=hash_goal_plan(tmp))
        spy = _Spy()
        out = exec_plan(bad, adapters={"conversation": spy})
        self.assertEqual(out.status, ExecutionStatus.INVALID_PLAN)
        self.assertEqual(spy.n, 0)

    def test_retry_limit_respected(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="OBSERVE_PATH",
            parameters={"path": "a"},
            retry_count=2,
        )])
        spy = _Spy(fail_until=2)
        out = exec_plan(
            plan, authorized_plan_hash=plan.plan_hash, adapters={"filesystem": spy},
        )
        self.assertEqual(out.status, ExecutionStatus.SUCCESS)
        self.assertEqual(spy.n, 3)

    def test_mutation_not_retried(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="DELETE_FILE",
            parameters={"path": "a.txt"},
        )])
        spy = _Spy(status=ExecutionStatus.STEP_FAILED.value)
        out = exec_plan(
            plan, authorized_plan_hash=plan.plan_hash, adapters={"filesystem": spy},
        )
        self.assertEqual(out.status, ExecutionStatus.STEP_FAILED)
        self.assertEqual(spy.n, 1)

    def test_timeout_respected(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step(timeout_ms=1)])

        def slow(step, _plan):
            time.sleep(0.05)
            return ExecutionStatus.SUCCESS.value

        out = exec_plan(plan, adapters={"conversation": slow})
        self.assertEqual(out.status, ExecutionStatus.TIMEOUT)

    def test_verification_required_blocks_success(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step(
            verification_required=True, verification_type="TARGET_EXISTS",
        )])
        conv = _Spy()
        verify = _Spy(status="NOT_VERIFIED")
        out = exec_plan(plan, adapters={"conversation": conv, "verification": verify})
        self.assertEqual(out.status, ExecutionStatus.NOT_VERIFIED)
        self.assertEqual(conv.n, 1)
        self.assertEqual(verify.n, 1)

    def test_not_verified_remains_not_verified(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step(
            verification_required=True, verification_type="TARGET_EXISTS",
        )])
        out = exec_plan(plan, adapters={
            "conversation": _ok,
            "verification": lambda *_: "NOT_VERIFIED",
        })
        self.assertEqual(out.status, ExecutionStatus.NOT_VERIFIED)
        self.assertNotEqual(out.status, ExecutionStatus.SUCCESS)

    def test_computer_routes_to_computer_adapter_only(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="computer", action="CLICK",
            parameters={"name": "OK", "session_id": "cs1"},
        )])
        computer = _Spy()
        other = _Spy()
        out = exec_plan(plan, authorized_plan_hash=plan.plan_hash, adapters={
            "computer": computer,
            "browser": other,
            "filesystem": other,
        })
        self.assertEqual(out.status, ExecutionStatus.SUCCESS)
        self.assertEqual(computer.n, 1)
        self.assertEqual(other.n, 0)

    def test_browser_filesystem_sequence_verification_world_memory(self):
        _v8_on()
        cases = [
            ("browser", "NAVIGATE", {"url": "https://example.com", "session_id": "cs1"}, True),
            ("filesystem", "READ_FILE", {"path": "a.txt"}, True),
            ("sequence", "DECLARE", {"label": "open"}, True),
            ("verification", "TARGET_EXISTS", {"domain": "filesystem", "path": "a"}, True),
            ("world_act", "INTERNAL_NOTE", {"note": "n1"}, True),
            ("memory_read", "RETRIEVE", {"query": "hello"}, False),
        ]
        for cap, action, params, need_session in cases:
            with self.subTest(cap=cap):
                reset_execution_ledger_for_tests()
                ctx = {"computer_session_id": "cs1"} if need_session else {}
                plan = build_goal_plan(_goal(**ctx), [_step(
                    capability_id=cap, action=action, parameters=params,
                )])
                spy = _Spy(status="VERIFIED" if cap == "verification" else ExecutionStatus.SUCCESS.value)
                others = _Spy()
                table = {k: others for k in (
                    "computer", "browser", "filesystem", "sequence",
                    "verification", "world_act", "memory_read", "conversation",
                )}
                table[cap] = spy
                auth = plan.plan_hash if plan.approval_required else ""
                out = exec_plan(plan, authorized_plan_hash=auth, adapters=table)
                self.assertEqual(out.status, ExecutionStatus.SUCCESS, msg=cap)
                self.assertEqual(spy.n, 1, msg=cap)
                self.assertEqual(others.n, 0, msg=cap)

    def test_conversation_no_mutation(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        mut = _Spy()
        out = exec_plan(plan, adapters={
            "conversation": _ok,
            "computer": mut,
            "browser": mut,
            "filesystem": mut,
            "world_act": mut,
        })
        self.assertEqual(out.status, ExecutionStatus.SUCCESS)
        self.assertEqual(mut.n, 0)

    def test_no_dynamic_plan_expansion(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        before = plan.steps

        def conv(step, live):
            self.assertIs(live, plan)
            self.assertEqual(live.steps, before)
            return ExecutionStatus.SUCCESS.value

        out = exec_plan(plan, adapters={"conversation": conv})
        self.assertEqual(out.status, ExecutionStatus.SUCCESS)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps, before)

    def test_idempotent_replay_skips_mutation(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="DELETE_FILE",
            parameters={"path": "a.txt"},
        )])
        spy = _Spy()
        first = exec_plan(plan, authorized_plan_hash=plan.plan_hash, adapters={"filesystem": spy})
        second = exec_plan(plan, authorized_plan_hash=plan.plan_hash, adapters={"filesystem": spy})
        self.assertEqual(first.status, ExecutionStatus.SUCCESS)
        self.assertEqual(second.status, ExecutionStatus.SUCCESS)
        self.assertEqual(spy.n, 1)

    def test_cancellation_stops_future_steps(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [
            _step("a"),
            _step("b", dependencies=("a",)),
        ])
        fired = []

        def conv(step, _plan):
            fired.append(step.step_id)
            return ExecutionStatus.SUCCESS.value

        out = exec_plan(
            plan,
            adapters={"conversation": conv},
            cancelled_fn=lambda: len(fired) >= 1,
        )
        self.assertEqual(out.status, ExecutionStatus.ABORTED)
        self.assertEqual(out.status, ExecutionStatus.CANCELLED)
        self.assertEqual(fired, ["a"])
        self.assertEqual(out.completed_step_ids, ("a",))

    def test_result_immutable_and_no_secrets(self):
        _v8_on()
        plan = build_goal_plan(_goal(), [_step()])
        out = exec_plan(plan, adapters={"conversation": _ok})
        with self.assertRaises(FrozenInstanceError):
            out.status = ExecutionStatus.FAILED  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            out.steps[0].status = "FAILED"  # type: ignore[misc]
        public = out.as_public()
        blob = str(public).lower()
        for token in ("screenshot", "password", "credential", "microphone", "audio", "secret"):
            self.assertNotIn(token, blob)
        self.assertNotIn("raw_intent", public)

    def test_ast_isolation(self):
        src = EXECUTOR.read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden_mod = {
            "subprocess", "importlib", "requests", "httpx", "urllib",
            "playwright", "selenium", "pyautogui",
        }
        forbidden_names = {"eval", "exec", "__import__"}
        legacy = {"ALL_TOOLS", "TaskEngine"}
        imports = set()
        froms = set()
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            if isinstance(node, ast.ImportFrom) and node.module:
                froms.add(node.module)
                for alias in node.names:
                    names.add(alias.name)
            if isinstance(node, ast.Name):
                names.add(node.id)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "system"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
            ):
                self.fail("os.system call")
        self.assertTrue(imports.isdisjoint(forbidden_mod))
        self.assertTrue({m.split(".")[0] for m in froms}.isdisjoint(forbidden_mod))
        self.assertTrue(names.isdisjoint(forbidden_names | legacy))
        self.assertIn("proactive.computer.actions.kernel", froms)
        self.assertIn("proactive.computer.browser.kernel", froms)
        self.assertIn("proactive.computer.fs.kernel", froms)
        self.assertIn("proactive.computer.sequence.kernel", froms)
        self.assertIn("proactive.computer.verify.kernel", froms)
        self.assertIn("proactive.act_engine", froms)
        self.assertIn("memory.manager", froms)
        self.assertIn("execute_computer_action", names)
        self.assertIn("execute_browser_action", names)
        self.assertIn("execute_fs_action", names)
        self.assertIn("execute_sequence", names)
        self.assertIn("execute_verification", names)
        self.assertIn("request_run", names)
        self.assertNotIn("tools", froms)
        self.assertNotIn("core.tool_registry", froms)
        self.assertFalse(any("computer_tools" in m for m in froms))
        self.assertFalse(any(m.startswith("tools.") for m in froms))


if __name__ == "__main__":
    unittest.main()
