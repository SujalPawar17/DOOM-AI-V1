"""V8.5 bounded recovery tests. No real OS/browser/FS mutation."""
from __future__ import annotations

import ast
import os
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")

from orchestration.executor import ExecutionResult, execute_plan, reset_execution_ledger_for_tests
from test_v8_harness import exec_plan, rec_exec
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.kernel import process_goal
from orchestration.goal.plan_registry import MAX_DEPENDENCY_DEPTH, MAX_STEPS, RISK_RANK
from orchestration.goal.plan_validator import build_goal_plan
from orchestration.recovery.classifier import classify_failure
from orchestration.recovery.engine import recover_execution, reset_recovery_attempts_for_tests
from orchestration.recovery.errors import (
    MAX_RECOVERY_ATTEMPTS,
    RecoveryAction,
    RecoveryFailureClass,
    RecoveryStatus,
)
from orchestration.recovery.planner import propose_recovery

ROOT = Path(__file__).resolve().parent
RECOVERY = ROOT / "orchestration" / "recovery"


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


def _result(plan, status, failed="s1", verification="", eid="exec-1"):
    return ExecutionResult(
        execution_id=eid,
        goal_id=plan.goal_id,
        plan_hash=plan.plan_hash,
        status=status,
        plan_risk=plan.plan_risk,
        approval_required=plan.approval_required,
        approval_granted=False,
        completed_step_ids=(),
        failed_step_id=failed,
        steps=(),
        verification_state=verification,
        started_unix_ms=1,
        ended_unix_ms=2,
    )


class _Spy:
    def __init__(self, status=ExecutionStatus.SUCCESS.value):
        self.n = 0
        self.status = status
        self.steps = []

    def __call__(self, step, plan):
        self.n += 1
        self.steps.append(step.step_id)
        return self.status


class TestV85VerificationRecovery(unittest.TestCase):
    def setUp(self):
        _v8_off()
        reset_execution_ledger_for_tests()
        reset_recovery_attempts_for_tests()

    def tearDown(self):
        _v8_off()
        reset_execution_ledger_for_tests()
        reset_recovery_attempts_for_tests()

    def test_successful_original_skips_recovery(self):
        _v8_on()
        goal = _goal()
        plan = build_goal_plan(goal, [_step()])
        spy = _Spy()
        original = exec_plan(plan, adapters={"conversation": spy})
        out = rec_exec(plan, original, goal, adapters={"conversation": spy})
        self.assertEqual(original.status, ExecutionStatus.SUCCESS)
        self.assertEqual(out.final_status, ExecutionStatus.SUCCESS.value)
        self.assertEqual(out.attempts_used, 0)
        self.assertIsNone(out.recovery_result)
        self.assertEqual(spy.n, 1)

    def test_timeout_recoverable_idempotent(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="READ_FILE",
            parameters={"path": "a.txt"}, retry_count=1,
        )])
        original = _result(plan, ExecutionStatus.TIMEOUT)
        spy = _Spy()
        prop = propose_recovery(plan, original, goal)
        self.assertEqual(prop.status, RecoveryStatus.PROPOSED)
        self.assertEqual(prop.recovery_action, RecoveryAction.RETRY_IDEMPOTENT_STEP)
        self.assertIsNotNone(prop.candidate_plan)
        self.assertNotEqual(prop.candidate_plan.plan_hash, plan.plan_hash)
        out = rec_exec(
            plan, original, goal,
            authorized_plan_hash=prop.candidate_plan.plan_hash,
            adapters={"filesystem": spy},
        )
        self.assertEqual(out.final_status, ExecutionStatus.SUCCESS.value)
        self.assertEqual(out.attempts_used, 1)
        self.assertEqual(spy.n, 1)
        self.assertTrue(spy.steps[0].startswith("rec-"))

    def test_precondition_failed_recoverable(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="OBSERVE_PATH",
            parameters={"path": "a"},
        )])
        original = _result(plan, ExecutionStatus.PRECONDITION_FAILED)
        spy = _Spy()
        prop = propose_recovery(plan, original, goal)
        out = rec_exec(
            plan, original, goal,
            authorized_plan_hash="" if not prop.authorization_required else prop.candidate_plan.plan_hash,
            adapters={"filesystem": spy},
        )
        self.assertEqual(out.final_status, ExecutionStatus.SUCCESS.value)
        self.assertEqual(spy.n, 1)

    def test_not_verified_only_when_policy_permits(self):
        _v8_on()
        goal = _goal()
        blocked = build_goal_plan(goal, [_step()])
        original = _result(blocked, ExecutionStatus.NOT_VERIFIED)
        spy = _Spy()
        out = rec_exec(blocked, original, goal, adapters={"conversation": spy, "verification": spy})
        self.assertEqual(out.final_status, ExecutionStatus.NOT_VERIFIED.value)
        self.assertEqual(out.attempts_used, 0)
        self.assertEqual(spy.n, 0)

        session_goal = _goal(computer_session_id="cs1")
        allowed = build_goal_plan(session_goal, [_step(
            verification_required=True, verification_type="TARGET_EXISTS",
        )])
        original = _result(allowed, ExecutionStatus.NOT_VERIFIED)
        verify = _Spy(status="VERIFIED")
        conv = _Spy()
        prop = propose_recovery(allowed, original, session_goal)
        self.assertEqual(prop.recovery_action, RecoveryAction.RETRY_VERIFICATION)
        out = rec_exec(
            allowed, original, session_goal,
            adapters={"verification": verify, "conversation": conv},
        )
        self.assertEqual(out.final_status, ExecutionStatus.SUCCESS.value)
        self.assertEqual(verify.n, 1)
        self.assertEqual(conv.n, 0)

    def test_verification_failed_policy(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="verification", action="TARGET_EXISTS",
            parameters={"domain": "filesystem", "path": "a"},
        )])
        original = _result(plan, ExecutionStatus.NOT_VERIFIED, verification="TARGET_MISSING")
        klass = classify_failure(original)
        self.assertEqual(klass.failure_class, RecoveryFailureClass.VERIFICATION_FAILED)
        spy = _Spy(status="VERIFIED")
        prop = propose_recovery(plan, original, goal)
        out = rec_exec(
            plan, original, goal,
            authorized_plan_hash="" if not prop.authorization_required else prop.candidate_plan.plan_hash,
            adapters={"verification": spy},
        )
        self.assertEqual(out.final_status, ExecutionStatus.SUCCESS.value)
        self.assertEqual(spy.n, 1)

    def test_unsupported_and_security_failures_are_terminal(self):
        _v8_on()
        goal = _goal()
        plan = build_goal_plan(goal, [_step()])
        spy = _Spy()
        for status in (
            ExecutionStatus.STEP_FAILED,
            ExecutionStatus.PLAN_HASH_MISMATCH,
            ExecutionStatus.INVALID_PLAN,
            ExecutionStatus.APPROVAL_REQUIRED,
            ExecutionStatus.CAPABILITY_UNAVAILABLE,
            ExecutionStatus.EMERGENCY_STOPPED,
            ExecutionStatus.ABORTED,
        ):
            reset_recovery_attempts_for_tests()
            original = _result(plan, status, eid=status.value)
            out = rec_exec(plan, original, goal, adapters={"conversation": spy})
            self.assertEqual(out.attempts_used, 0, msg=status.value)
            self.assertIsNone(out.recovery_result)
        self.assertEqual(spy.n, 0)

    def test_emergency_stop_blocks_recovery(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="READ_FILE", parameters={"path": "a.txt"},
        )])
        original = _result(plan, ExecutionStatus.TIMEOUT)
        spy = _Spy()
        out = rec_exec(
            plan, original, goal, adapters={"filesystem": spy},
            emergency_stop_fn=lambda *_: True,
        )
        self.assertEqual(out.final_status, ExecutionStatus.EMERGENCY_STOPPED.value)
        self.assertEqual(spy.n, 0)

    def test_cancellation_blocks_recovery(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="READ_FILE", parameters={"path": "a.txt"},
        )])
        original = _result(plan, ExecutionStatus.TIMEOUT)
        spy = _Spy()
        out = rec_exec(
            plan, original, goal, adapters={"filesystem": spy},
            cancelled_fn=lambda: True,
        )
        self.assertEqual(out.final_status, ExecutionStatus.ABORTED.value)
        self.assertEqual(spy.n, 0)

    def test_original_plan_immutable_and_new_hash(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="READ_FILE",
            parameters={"path": "a.txt"}, retry_count=1,
        )])
        digest = plan.plan_hash
        steps = plan.steps
        original = _result(plan, ExecutionStatus.TIMEOUT)
        prop = propose_recovery(plan, original, goal)
        rec_exec(
            plan, original, goal,
            authorized_plan_hash=prop.candidate_plan.plan_hash,
            adapters={"filesystem": _Spy()},
        )
        self.assertEqual(plan.plan_hash, digest)
        self.assertIs(plan.steps, steps)
        self.assertNotEqual(prop.candidate_plan.plan_hash, digest)
        with self.assertRaises(FrozenInstanceError):
            plan.plan_risk = "LOW"  # type: ignore[misc]

    def test_original_approval_cannot_authorize_recovery(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="browser", action="BACK",
            parameters={"session_id": "cs1"},
        )])
        self.assertTrue(plan.approval_required)
        original = _result(plan, ExecutionStatus.TIMEOUT)
        spy = _Spy()
        out = rec_exec(
            plan, original, goal,
            authorized_plan_hash=plan.plan_hash,
            adapters={"browser": spy},
        )
        self.assertEqual(out.final_status, ExecutionStatus.APPROVAL_REQUIRED.value)
        self.assertEqual(spy.n, 0)
        flagged = replace(plan, approved=True)
        out = rec_exec(
            flagged, original, goal,
            authorized_plan_hash=plan.plan_hash,
            adapters={"browser": spy},
        )
        self.assertEqual(out.final_status, ExecutionStatus.APPROVAL_REQUIRED.value)
        self.assertEqual(spy.n, 0)

    def test_valid_recovery_authorization_and_wrong_hash(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="browser", action="BACK",
            parameters={"session_id": "cs1"},
        )])
        original = _result(plan, ExecutionStatus.TIMEOUT)
        prop = propose_recovery(plan, original, goal)
        spy = _Spy()
        blocked = rec_exec(
            plan, original, goal, authorized_plan_hash="deadbeef", adapters={"browser": spy},
        )
        self.assertEqual(blocked.final_status, ExecutionStatus.APPROVAL_REQUIRED.value)
        self.assertEqual(spy.n, 0)
        reset_recovery_attempts_for_tests()
        ok = rec_exec(
            plan, original, goal,
            authorized_plan_hash=prop.candidate_plan.plan_hash,
            adapters={"browser": spy},
        )
        self.assertEqual(ok.final_status, ExecutionStatus.SUCCESS.value)
        self.assertEqual(spy.n, 1)
        self.assertGreaterEqual(RISK_RANK[prop.candidate_plan.plan_risk], RISK_RANK[plan.plan_risk])

    def test_attempt_limit_and_no_nested_recovery(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="READ_FILE", parameters={"path": "a.txt"},
        )])
        original = _result(plan, ExecutionStatus.TIMEOUT)
        spy = _Spy(status=ExecutionStatus.TIMEOUT.value)
        prop = propose_recovery(plan, original, goal)
        first = rec_exec(
            plan, original, goal,
            authorized_plan_hash=prop.candidate_plan.plan_hash,
            adapters={"filesystem": spy},
        )
        self.assertEqual(first.attempts_used, 1)
        self.assertEqual(spy.n, 1)
        second = rec_exec(
            plan, original, goal,
            authorized_plan_hash=prop.candidate_plan.plan_hash,
            adapters={"filesystem": spy},
        )
        self.assertEqual(second.attempts_used, MAX_RECOVERY_ATTEMPTS)
        self.assertIsNone(second.recovery_result)
        self.assertEqual(spy.n, 1)
        nested = rec_exec(
            plan, original, goal, recovery_attempt=True, adapters={"filesystem": spy},
        )
        self.assertEqual(nested.recovery_proposal.status, RecoveryStatus.RECURSIVE_RECOVERY)
        self.assertIsNone(nested.recovery_proposal.candidate_plan)
        self.assertEqual(spy.n, 1)

    def test_no_dynamic_expansion_and_no_retry_escalation(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="READ_FILE",
            parameters={"path": "a.txt"}, retry_count=1, timeout_ms=1000,
        )])
        original = _result(plan, ExecutionStatus.TIMEOUT)
        prop = propose_recovery(plan, original, goal)
        cand = prop.candidate_plan
        self.assertEqual(len(cand.steps), 1)
        self.assertLessEqual(len(cand.steps), MAX_STEPS)
        self.assertEqual(cand.steps[0].retry_count, 1)
        self.assertEqual(cand.steps[0].timeout_ms, 1000)
        self.assertEqual(cand.steps[0].parameters, plan.steps[0].parameters)

    def test_risk_cannot_be_lowered(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="browser", action="BACK",
            parameters={"session_id": "cs1"},
        )])
        original = _result(plan, ExecutionStatus.TIMEOUT)
        prop = propose_recovery(plan, original, goal)
        self.assertGreaterEqual(RISK_RANK[prop.candidate_plan.plan_risk], RISK_RANK[plan.plan_risk])
        self.assertTrue(prop.candidate_plan.approval_required)

    def test_v8_disabled_malformed_and_unknown_capability(self):
        _v8_on()
        goal = _goal()
        plan = build_goal_plan(goal, [_step()])
        original = _result(plan, ExecutionStatus.TIMEOUT)
        _v8_off()
        spy = _Spy()
        out = rec_exec(plan, original, goal, adapters={"conversation": spy})
        self.assertEqual(out.final_status, ExecutionStatus.V8_DISABLED.value)
        self.assertEqual(spy.n, 0)
        _v8_on()
        bad = rec_exec(plan, {"status": "TIMEOUT"}, goal, adapters={"conversation": spy})
        self.assertEqual(bad.final_status, RecoveryStatus.INVALID_INPUT.value)
        self.assertEqual(spy.n, 0)
        text = rec_exec("plan", original, goal)
        self.assertEqual(text.final_status, RecoveryStatus.INVALID_INPUT.value)

    def test_adversarial_forbidden_recovery_shapes(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="READ_FILE", parameters={"path": "a.txt"},
        )])
        original = _result(plan, ExecutionStatus.TIMEOUT)
        prop = propose_recovery(plan, original, goal)
        cand = prop.candidate_plan
        keys = set(dict(cand.steps[0].parameters))
        for token in ("eval", "exec", "shell", "tool_name", "all_tools", "subprocess", "command"):
            self.assertNotIn(token, keys)
        self.assertEqual(cand.steps[0].capability_id, "filesystem")
        self.assertEqual(cand.execution_permitted, False)
        self.assertEqual(cand.approved, False)
        self.assertLessEqual(len(cand.steps), MAX_STEPS)
        self.assertLessEqual(MAX_DEPENDENCY_DEPTH, 4)

    def test_mutation_timeout_is_not_recovered(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="DELETE_FILE", parameters={"path": "a.txt"},
        )])
        original = _result(plan, ExecutionStatus.TIMEOUT)
        spy = _Spy()
        out = rec_exec(
            plan, original, goal, authorized_plan_hash=plan.plan_hash, adapters={"filesystem": spy},
        )
        self.assertEqual(out.attempts_used, 0)
        self.assertEqual(spy.n, 0)
        self.assertEqual(out.final_status, ExecutionStatus.TIMEOUT.value)

    def test_not_verified_never_becomes_success_without_verify(self):
        _v8_on()
        goal = _goal(computer_session_id="cs1")
        plan = build_goal_plan(goal, [_step(
            verification_required=True, verification_type="TARGET_EXISTS",
        )])
        original = _result(plan, ExecutionStatus.NOT_VERIFIED)
        verify = _Spy(status="NOT_VERIFIED")
        out = rec_exec(plan, original, goal, adapters={"verification": verify})
        self.assertEqual(out.final_status, ExecutionStatus.NOT_VERIFIED.value)
        self.assertNotEqual(out.final_status, ExecutionStatus.SUCCESS.value)

    def test_result_immutable_and_no_secrets(self):
        _v8_on()
        goal = _goal()
        plan = build_goal_plan(goal, [_step()])
        original = exec_plan(plan, adapters={"conversation": _Spy()})
        out = rec_exec(plan, original, goal, adapters={"conversation": _Spy()})
        with self.assertRaises(FrozenInstanceError):
            out.final_status = "SUCCESS"  # type: ignore[misc]
        blob = str(out.as_public()).lower()
        for token in ("screenshot", "password", "credential", "microphone", "secret"):
            self.assertNotIn(token, blob)

    def test_ast_isolation(self):
        forbidden_mod = {
            "subprocess", "importlib", "ctypes", "pyautogui", "playwright", "selenium",
            "requests", "httpx", "urllib",
        }
        forbidden_names = {"eval", "exec", "__import__", "ALL_TOOLS", "TaskEngine"}
        for path in RECOVERY.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], forbidden_mod, msg=str(path))
                if isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".")[0]
                    self.assertNotIn(root, forbidden_mod, msg=str(path))
                    self.assertFalse(node.module.startswith("proactive.computer"))
                    self.assertNotEqual(node.module, "tools")
                    self.assertFalse("computer_tools" in node.module)
                    self.assertNotEqual(node.module, "core.tool_registry")
                    self.assertNotIn("execute_computer_action", [a.name for a in node.names])
                if isinstance(node, ast.Name) and node.id in forbidden_names:
                    self.fail(f"{path} uses {node.id}")


if __name__ == "__main__":
    unittest.main()
