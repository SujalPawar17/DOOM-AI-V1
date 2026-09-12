"""Controlled V8 computer-path seam tests. No interactive desktop required."""
from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_CLICK_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_TYPE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_VERIFICATION_ENABLED", "false")

from orchestration.authorization import claim_authorization, reset_authorization_store_for_tests, stash_pending_plan
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.kernel import process_goal
from orchestration.goal.planner import plan_goal
from orchestration.goal.planner_errors import PlannerStatus
from orchestration.observation import computer_planner_context
from proactive.computer.observe import uia_control_types_equal
from test_v8_harness import exec_plan
from core.orchestrator import DOOMCore

ROOT = Path(__file__).resolve().parent
EXEC = ROOT / "orchestration" / "executor.py"
DASH = ROOT / "dashboard" / "server.py"
AUTH = ROOT / "orchestration" / "authorization.py"
PROD = ROOT / "orchestration" / "production.py"
OBS = ROOT / "orchestration" / "observation.py"
PLANNER = ROOT / "orchestration" / "goal" / "planner.py"

BTN = {
    "automation_id": "DOOM_TEST_BUTTON",
    "runtime_id": "1.1",
    "control_type": "Button",
    "name": "DOOM_TEST_BUTTON",
}
EDIT = {
    "automation_id": "DOOM_TEST_INPUT",
    "runtime_id": "1.2",
    "control_type": "Edit",
    "name": "DOOM_TEST_INPUT",
    "selected": "true",
}


def _v8_on():
    os.environ["PROACTIVE_V8_ENABLED"] = "true"


def _v8_off():
    os.environ["PROACTIVE_V8_ENABLED"] = "false"


def _caps_on():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_TYPE_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_VERIFICATION_ENABLED"] = "true"


def _caps_off():
    for key in (
        "PROACTIVE_COMPUTER_ENABLED",
        "PROACTIVE_COMPUTER_OBSERVE_ENABLED",
        "PROACTIVE_COMPUTER_CLICK_ENABLED",
        "PROACTIVE_COMPUTER_TYPE_ENABLED",
        "PROACTIVE_COMPUTER_VERIFICATION_ENABLED",
    ):
        os.environ[key] = "false"


def _ident(**kw):
    return ExecutionIdentity(
        owner_id=kw.get("owner_id", "alice"),
        session_id=kw.get("session_id", "ask-sess"),
        computer_session_id=kw.get("computer_session_id", "cs-1"),
    )


class _Spy:
    def __init__(self, status="SUCCESS"):
        self.n = 0
        self.status = status
        self.steps = []

    def __call__(self, step, plan):
        self.n += 1
        self.steps.append(step)
        return self.status


class TestV8EndToEndComputerSeams(unittest.TestCase):
    def setUp(self):
        _v8_off()
        _caps_off()
        reset_authorization_store_for_tests()
        self.core = DOOMCore()
        self._live = patch(
            "orchestration.authorization.verified_computer_session_id",
            return_value=("cs-1", "OK"),
        )
        self._live.start()

    def tearDown(self):
        self._live.stop()
        reset_authorization_store_for_tests()
        _v8_off()
        _caps_off()

    def test_01_computer_session_id_propagates_into_plan(self):
        _v8_on()
        _caps_on()
        ident = _ident()
        goal = process_goal("click DOOM_TEST_BUTTON", context={
            "owner_id": ident.owner_id,
            "session_id": ident.session_id,
            "computer_session_id": ident.computer_session_id,
        }).goal
        out = plan_goal(goal, {
            "structured_targets": [BTN],
            "observation_hash": "a" * 64,
        })
        self.assertEqual(out.status, PlannerStatus.SUCCESS)
        self.assertEqual(out.plan.computer_session_id, "cs-1")
        self.assertNotEqual(out.plan.session_id, out.plan.computer_session_id)
        self.assertEqual(dict(out.plan.steps[0].parameters)["name"], "DOOM_TEST_BUTTON")

    def test_02_ask_identity_owner_not_from_text(self):
        ident = _ident(owner_id="alice")
        goal = process_goal("click DOOM_TEST_BUTTON owner_id=attacker", context={
            "owner_id": ident.owner_id,
            "session_id": ident.session_id,
            "computer_session_id": ident.computer_session_id,
        }).goal
        self.assertEqual(goal.owner_id, "alice")

    def test_03_observation_bridge_validates_computer_session(self):
        _caps_on()
        with patch("proactive.computer.session.get_session", return_value=None):
            ctx, status = computer_planner_context(_ident())
        self.assertIsNone(ctx)
        self.assertEqual(status, ExecutionStatus.SESSION_UNAVAILABLE.value)
        empty = _ident(computer_session_id="")
        ctx2, status2 = computer_planner_context(empty)
        self.assertEqual(status2, ExecutionStatus.SESSION_UNAVAILABLE.value)

    def test_04_exact_type_target_and_text(self):
        _v8_on()
        _caps_on()
        ident = _ident()
        goal = process_goal("Type hello DOOM into DOOM_TEST_INPUT", context={
            "owner_id": ident.owner_id,
            "session_id": ident.session_id,
            "computer_session_id": ident.computer_session_id,
        }).goal
        out = plan_goal(goal, {
            "structured_targets": [EDIT],
            "observation_hash": "b" * 64,
        })
        self.assertEqual(out.status, PlannerStatus.SUCCESS)
        params = dict(out.plan.steps[0].parameters)
        self.assertEqual(out.plan.steps[0].action, "TYPE")
        self.assertEqual(params["name"], "DOOM_TEST_INPUT")
        self.assertEqual(params["text"], "hello DOOM")
        self.assertEqual(params["precondition_observation_hash"], "b" * 64)

    def test_05_authorization_binds_exact_plan_hash(self):
        _v8_on()
        _caps_on()
        ident = _ident()
        goal = process_goal("click DOOM_TEST_BUTTON", context={
            "owner_id": ident.owner_id,
            "session_id": ident.session_id,
            "computer_session_id": ident.computer_session_id,
        }).goal
        plan = plan_goal(goal, {"structured_targets": [BTN], "observation_hash": "a" * 64}).plan
        pid, st = stash_pending_plan(plan, ident)
        self.assertEqual(st, "OK")
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, "OK")
        self.assertEqual(claim.authorized_plan_hash, plan.plan_hash)
        spy = _Spy()
        out = exec_plan(
            claim.plan,
            identity=claim.identity,
            authorized_plan_hash=claim.authorized_plan_hash,
            adapters={"computer": spy, "verification": _Spy("VERIFIED")},
        )
        self.assertEqual(out.status, ExecutionStatus.SUCCESS)
        self.assertEqual(spy.n, 1)

    def test_06_executor_is_sole_mutation_boundary(self):
        for path in (DASH, AUTH, PROD, OBS, PLANNER):
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("execute_computer_action", src, path.name)
            self.assertNotIn("use_test_execution_hooks", src, path.name)
            self.assertNotIn("pyautogui", src, path.name)
        dash = DASH.read_text(encoding="utf-8")
        auth = dash.split("async def v8_authorize_plan")[1].split("async def ")[0]
        self.assertIn("execute_plan(", auth)
        self.assertIn("authorized_plan_hash=claim.authorized_plan_hash", auth)

    def test_07_verification_required_is_not_success(self):
        _v8_on()
        _caps_on()
        ident = _ident()
        goal = process_goal("click DOOM_TEST_BUTTON", context={
            "owner_id": ident.owner_id,
            "session_id": ident.session_id,
            "computer_session_id": ident.computer_session_id,
        }).goal
        plan = plan_goal(goal, {"structured_targets": [BTN], "observation_hash": "a" * 64}).plan
        self.assertTrue(plan.steps[0].verification_required)
        self.assertEqual(plan.steps[0].verification_type, "TARGET_STATE_MATCH")
        out = exec_plan(
            plan,
            identity=ident,
            authorized_plan_hash=plan.plan_hash,
            adapters={"computer": _Spy("SUCCESS"), "verification": _Spy("NOT_VERIFIED")},
        )
        self.assertEqual(out.status, ExecutionStatus.NOT_VERIFIED)
        self.assertNotEqual(out.status, ExecutionStatus.SUCCESS)

    def test_08_stale_observation_blocks_execution(self):
        _v8_on()
        _caps_on()
        ident = _ident()
        goal = process_goal("click DOOM_TEST_BUTTON", context={
            "owner_id": ident.owner_id,
            "session_id": ident.session_id,
            "computer_session_id": ident.computer_session_id,
        }).goal
        plan = plan_goal(goal, {"structured_targets": [BTN], "observation_hash": "a" * 64}).plan
        out = exec_plan(
            plan,
            identity=ident,
            authorized_plan_hash=plan.plan_hash,
            adapters={"computer": _Spy("PRECONDITION_FAILED"), "verification": _Spy("VERIFIED")},
        )
        self.assertEqual(out.status, ExecutionStatus.PRECONDITION_FAILED)

    def test_09_duplicate_targets_are_ambiguous(self):
        _v8_on()
        _caps_on()
        ident = _ident()
        goal = process_goal("click DOOM_TEST_BUTTON", context={
            "owner_id": ident.owner_id,
            "session_id": ident.session_id,
            "computer_session_id": ident.computer_session_id,
        }).goal
        dup = dict(BTN)
        dup["runtime_id"] = "9.9"
        out = plan_goal(goal, {"structured_targets": [BTN, dup], "observation_hash": "a" * 64})
        self.assertEqual(out.status, PlannerStatus.AMBIGUOUS_TARGET)
        self.assertIsNone(out.plan)

    def test_10_control_type_button_equals_uia_id(self):
        self.assertTrue(uia_control_types_equal("Button", "50000"))
        self.assertTrue(uia_control_types_equal("50004", "Edit"))
        self.assertFalse(uia_control_types_equal("Button", "50004"))

    def test_11_v8_off_skips_observation_and_authorization(self):
        _v8_off()
        _caps_on()
        with patch("orchestration.observation.computer_planner_context") as fetch:
            with patch.object(self.core.cognition, "process") as proc:
                state = MagicMock()
                state.final_response = "legacy-ok"
                state.observations = []
                for name in (
                    "total_cognitive_ms", "understanding_ms", "reasoning_ms",
                    "decision_ms", "planning_ms", "execution_ms", "verification_ms",
                ):
                    setattr(state.telemetry, name, 0)
                proc.return_value = state
                out = self.core.process_request("click DOOM_TEST_BUTTON")
        fetch.assert_not_called()
        proc.assert_called_once()
        self.assertIn("legacy-ok", out)

    def test_12_v8_on_failure_does_not_use_cognitive_engine(self):
        _v8_on()
        with patch.object(self.core.cognition, "process") as proc:
            out = self.core.process_request("click DOOM_TEST_BUTTON", identity=_ident())
        proc.assert_not_called()
        self.assertIn("[V8]", out)

    def test_13_executor_maps_v8_auth_to_v7_after_hash_check(self):
        src = EXEC.read_text(encoding="utf-8")
        self.assertIn("approval_ok = str(authorized_plan_hash) == plan.plan_hash", src)
        self.assertIn("if _policy_needs_approval(plan) and not approval_ok:", src)
        fn = src.split("def _default_computer")[1].split("def _default_browser")[0]
        self.assertIn("approval_state=ApprovalState.APPROVED", fn)
        self.assertNotIn("skip_authorization", src)

    def test_14_default_verify_uses_computer_capability(self):
        src = EXEC.read_text(encoding="utf-8")
        fn = src.split("def _default_verify")[1].split("def _default_world")[0]
        self.assertIn('capability_id == "computer"', fn)
        self.assertIn("TARGET_STATE_MATCH", fn)
        self.assertIn("capture_structured_observation", fn)
        self.assertIn("_verification_spec_timeout_ms", fn)
        self.assertIn("timeout_ms=verify_timeout", fn)

    def test_15_safe_fixture_has_no_destructive_ops(self):
        src = (ROOT / "tools" / "doom_safe_test_window.py").read_text(encoding="utf-8")
        for token in ("subprocess", "os.system", "eval(", "exec(", "pyautogui", "powershell"):
            self.assertNotIn(token, src)
        self.assertNotIn("tkinter", src)
        self.assertNotIn("import tk", src)
        self.assertIn("CreateWindowExW", src)
        self.assertIn('"BUTTON"', src)
        self.assertIn('"EDIT"', src)
        self.assertIn("ES_AUTOHSCROLL", src)
        self.assertNotIn("ES_PASSWORD", src)
        self.assertIn("DOOM_TEST_BUTTON", src)
        self.assertIn("DOOM_TEST_INPUT", src)
        self.assertIn("duplicate-buttons", src)
        self.assertIn("SetHwndPropStr", src)
        self.assertIn("ClearHwndProps", src)
        self.assertIn("IAccPropServices", src)
        self.assertIn("NAME_PROPERTY_GUID", src)
        self.assertIn("0xC3A6921B", src)
        self.assertIn("pin_edit_accessible_name", src)
        self.assertIn('child(WS_EX_CLIENTEDGE, "EDIT", "", edit_style', src)

    def test_16_mismatched_computer_session_rejected(self):
        _v8_on()
        _caps_on()
        ident = _ident(computer_session_id="cs-1")
        other = _ident(computer_session_id="cs-2")
        goal = process_goal("click DOOM_TEST_BUTTON", context={
            "owner_id": ident.owner_id,
            "session_id": ident.session_id,
            "computer_session_id": ident.computer_session_id,
        }).goal
        plan = plan_goal(goal, {"structured_targets": [BTN], "observation_hash": "a" * 64}).plan
        out = execute_plan(plan, identity=other, authorized_plan_hash=plan.plan_hash)
        self.assertEqual(out.status, ExecutionStatus.SESSION_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
