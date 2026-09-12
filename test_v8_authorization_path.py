"""ASK UI authorization path for MEDIUM computer plans. No real OS/GUI/cloud."""
from __future__ import annotations

import ast
import os
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_CLICK_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_TYPE_ENABLED", "false")

from orchestration.authorization import (
    AUTHORIZATION_TTL_SECONDS,
    claim_authorization,
    expire_pending_for_tests,
    medium_computer_approvable,
    pending_display,
    replace_pending_plan_for_tests,
    reset_authorization_store_for_tests,
    revoke_pending,
    safe_plan_display,
    stash_pending_plan,
)
from orchestration.executor import ExecutionIdentity, execute_plan, use_test_execution_hooks
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.kernel import process_goal
from orchestration.goal.plan_errors import PlanValidationError
from orchestration.goal.plan_validator import build_goal_plan, hash_goal_plan
from orchestration.goal.planner import _type_payload, plan_goal
from orchestration.production import handle_v8_enabled_request, prepare_v8_request
from test_v8_harness import exec_plan
from core.orchestrator import DOOMCore

ROOT = Path(__file__).resolve().parent
AUTH = ROOT / "orchestration" / "authorization.py"
DASH = ROOT / "dashboard" / "server.py"
ORIGIN = "http://127.0.0.1:8000"


def _v8_on():
    os.environ["PROACTIVE_V8_ENABLED"] = "true"


def _v8_off():
    os.environ["PROACTIVE_V8_ENABLED"] = "false"


def _caps_on():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_TYPE_ENABLED"] = "true"


def _ident(**kw):
    return ExecutionIdentity(
        owner_id=kw.get("owner_id", "alice"),
        session_id=kw.get("session_id", "ask-sess"),
        computer_session_id=kw.get("computer_session_id", "cs-1"),
    )


def _goal(text, ident):
    return process_goal(text, context={
        "owner_id": ident.owner_id,
        "session_id": ident.session_id,
        "computer_session_id": ident.computer_session_id,
    }).goal


def _click_plan(ident, *, name="OK", oh="a" * 64, extra=None):
    params = {
        "automation_id": "btnOK",
        "runtime_id": "1.2.3",
        "control_type": "Button",
        "name": name,
        "session_id": ident.computer_session_id,
        "precondition_observation_hash": oh,
    }
    if extra:
        params.update(extra)
    return build_goal_plan(_goal("Click the OK button.", ident), [{
        "step_id": "s1",
        "capability_id": "computer",
        "action": "CLICK",
        "parameters": params,
        "dependencies": (),
        "verification_required": True,
        "verification_type": "TARGET_STATE_MATCH",
        "retry_count": 0,
        "timeout_ms": 8000,
    }])


def _type_plan(ident, *, text="hello", name="Search", oh="a" * 64):
    return build_goal_plan(_goal('Type "hello" into the Search field.', ident), [{
        "step_id": "s1",
        "capability_id": "computer",
        "action": "TYPE",
        "parameters": {
            "automation_id": "txtSearch",
            "runtime_id": "9.9.9",
            "control_type": "Edit",
            "name": name,
            "text": text,
            "session_id": ident.computer_session_id,
            "precondition_observation_hash": oh,
        },
        "dependencies": (),
        "verification_required": True,
        "verification_type": "TARGET_STATE_MATCH",
        "retry_count": 0,
        "timeout_ms": 8000,
    }])


def _high_plan(ident):
    return build_goal_plan(_goal("delete a file", ident), [{
        "step_id": "s1",
        "capability_id": "filesystem",
        "action": "DELETE_FILE",
        "parameters": {"path": "C:\\tmp\\x.txt"},
        "dependencies": (),
        "verification_required": False,
        "verification_type": "",
        "retry_count": 0,
        "timeout_ms": 8000,
        "risk": "HIGH",
    }])


class _Spy:
    def __init__(self, status="SUCCESS"):
        self.n = 0
        self.status = status
        self.last = None

    def __call__(self, step, plan):
        self.n += 1
        self.last = (step, plan)
        return self.status


def _pg():
    from database.postgres_db import postgres_manager
    return postgres_manager.is_connected()


class TestV8AuthorizationPath(unittest.TestCase):
    def setUp(self):
        _v8_off()
        reset_authorization_store_for_tests()
        self.core = DOOMCore()
        self._live = patch(
            "orchestration.authorization.verified_computer_session_id",
            return_value=("cs-1", "OK"),
        )
        self._live.start()

    def tearDown(self):
        self._live.stop()
        _v8_off()
        reset_authorization_store_for_tests()

    def test_01_valid_approval_creates_authorization(self):
        _v8_on()
        ident = _ident()
        plan = _click_plan(ident)
        pid, st = stash_pending_plan(plan, ident)
        self.assertEqual(st, "OK")
        display, dst = pending_display(pid, ident)
        self.assertEqual(dst, "OK")
        self.assertEqual(display["action"], "CLICK")
        self.assertEqual(display["name"], "OK")
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

    def test_02_type_approval_binds_exact_text(self):
        _v8_on()
        ident = _ident()
        plan = _type_plan(ident, text="hello")
        pid, _ = stash_pending_plan(plan, ident)
        disp, _ = pending_display(pid, ident)
        self.assertEqual(disp["text"], "hello")
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, "OK")
        self.assertEqual(dict(claim.plan.steps[0].parameters)["text"], "hello")

    def test_03_missing_identity_rejected(self):
        _v8_on()
        plan = _click_plan(_ident())
        pid, st = stash_pending_plan(plan, None)  # type: ignore[arg-type]
        self.assertIsNone(pid)
        self.assertEqual(st, ExecutionStatus.IDENTITY_REQUIRED.value)

    def test_04_wrong_owner_cannot_claim(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        other = _ident(owner_id="bob")
        claim, code = claim_authorization(pid, other)
        self.assertIsNone(claim)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_INVALID.value)

    def test_05_wrong_ask_session_cannot_claim(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        other = _ident(session_id="other-ask")
        claim, code = claim_authorization(pid, other)
        self.assertIsNone(claim)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_INVALID.value)

    def test_06_forged_computer_session_in_claim_rejected(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        claim, code = claim_authorization(pid, ident, requested_computer_session_id="cs-forged")
        self.assertIsNone(claim)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_INVALID.value)

    def test_07_altered_plan_hash_rejected(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        mutated = replace(plan, plan_hash="deadbeef" * 8)
        replace_pending_plan_for_tests(pid, mutated)
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.PLAN_HASH_MISMATCH.value)

    def test_08_changed_type_payload_is_new_plan(self):
        ident = _ident()
        a = _type_plan(ident, text="hello")
        b = _type_plan(ident, text="world")
        self.assertNotEqual(a.plan_hash, b.plan_hash)
        _v8_on()
        out = execute_plan(b, identity=ident, authorized_plan_hash=a.plan_hash)
        self.assertEqual(out.status, ExecutionStatus.APPROVAL_REQUIRED)

    def test_09_changed_click_target_changes_hash(self):
        ident = _ident()
        a = _click_plan(ident, name="OK")
        b = _click_plan(ident, name="Cancel")
        self.assertNotEqual(a.plan_hash, b.plan_hash)

    def test_10_changed_observation_hash_changes_plan_hash(self):
        ident = _ident()
        a = _click_plan(ident, oh="a" * 64)
        b = _click_plan(ident, oh="b" * 64)
        self.assertNotEqual(a.plan_hash, b.plan_hash)

    def test_11_changed_risk_rejected(self):
        ident = _ident()
        plan = _click_plan(ident)
        lowered = replace(plan, plan_risk="LOW", plan_hash=plan.plan_hash)
        pid, st = stash_pending_plan(lowered, ident)
        self.assertIsNone(pid)
        self.assertIn(st, (ExecutionStatus.RISK_NOT_APPROVABLE.value, ExecutionStatus.PLAN_HASH_MISMATCH.value))

    def test_12_replay_after_consumption(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        first, code = claim_authorization(pid, ident)
        self.assertEqual(code, "OK")
        second, code2 = claim_authorization(pid, ident)
        self.assertIsNone(second)
        self.assertEqual(code2, ExecutionStatus.AUTHORIZATION_CONSUMED.value)

    def test_13_expired_authorization(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        expire_pending_for_tests(pid, now=1)
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_EXPIRED.value)

    def test_14_revoked_authorization(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        self.assertEqual(revoke_pending(pid, ident), "OK")
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_REVOKED.value)

    def test_15_concurrent_consume_single_winner(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        results = []

        def worker():
            results.append(claim_authorization(pid, ident)[1])

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("OK"), 1)
        self.assertEqual(results.count(ExecutionStatus.AUTHORIZATION_CONSUMED.value), 7)

    def test_16_plan_approved_flag_is_not_authorization(self):
        _v8_on()
        ident = _ident()
        plan = replace(_click_plan(ident), approved=True, execution_permitted=True)
        out = execute_plan(plan, identity=ident, authorized_plan_hash="")
        self.assertEqual(out.status, ExecutionStatus.APPROVAL_REQUIRED)

    def test_17_low_plan_does_not_use_medium_path(self):
        _v8_on()
        ident = _ident(computer_session_id="")
        goal = process_goal("hello", context={"owner_id": ident.owner_id, "session_id": ident.session_id}).goal
        plan = plan_goal(goal).plan
        ok, code = medium_computer_approvable(plan)
        self.assertFalse(ok)
        out = execute_plan(plan, identity=ident, authorized_plan_hash="")
        self.assertEqual(out.status, ExecutionStatus.SUCCESS)

    def test_18_medium_requires_explicit_approval(self):
        _v8_on()
        ident = _ident()
        plan = _click_plan(ident)
        self.assertTrue(medium_computer_approvable(plan)[0])
        out = execute_plan(plan, identity=ident, authorized_plan_hash="")
        self.assertEqual(out.status, ExecutionStatus.APPROVAL_REQUIRED)

    def test_19_high_cannot_be_stashed(self):
        ident = _ident()
        plan = _high_plan(ident)
        self.assertEqual(plan.plan_risk, "HIGH")
        pid, code = stash_pending_plan(plan, ident)
        self.assertIsNone(pid)
        self.assertEqual(code, ExecutionStatus.RISK_NOT_APPROVABLE.value)

    def test_20_no_computer_session_fails_closed(self):
        ident2 = _ident()
        plan = replace(_click_plan(ident2), computer_session_id="")
        ok, code = medium_computer_approvable(plan)
        self.assertFalse(ok)
        self.assertEqual(code, ExecutionStatus.COMPUTER_SESSION_REQUIRED.value)

    def test_21_stopped_computer_session_rejected(self):
        ident = _ident()
        plan = _click_plan(ident)
        self._live.stop()
        with patch("orchestration.authorization.verified_computer_session_id", return_value=(None, ExecutionStatus.SESSION_UNAVAILABLE.value)):
            pid, code = stash_pending_plan(plan, ident)
        self._live.start()
        self.assertIsNone(pid)
        self.assertEqual(code, ExecutionStatus.SESSION_UNAVAILABLE.value)

    def test_22_session_a_auth_cannot_bind_session_b(self):
        a = _ident(computer_session_id="cs-1")
        b = _ident(computer_session_id="cs-2")
        plan_a = _click_plan(a)
        pid, _ = stash_pending_plan(plan_a, a)
        with patch("orchestration.authorization.verified_computer_session_id", return_value=("cs-2", "OK")):
            claim, code = claim_authorization(pid, b)
        self.assertIsNone(claim)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_INVALID.value)

    def test_23_observation_toctou_still_enforced(self):
        _v8_on()
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        spy = _Spy("PRECONDITION_FAILED")
        out = exec_plan(
            claim.plan,
            identity=claim.identity,
            authorized_plan_hash=claim.authorized_plan_hash,
            adapters={"computer": spy, "verification": _Spy()},
        )
        self.assertEqual(out.status, ExecutionStatus.PRECONDITION_FAILED)
        self.assertEqual(spy.n, 1)

    def test_24_auth_failure_does_not_invoke_cognitive_engine(self):
        _v8_on()
        ident = _ident()
        with patch.object(self.core.cognition, "process") as proc:
            out = self.core.process_request("Click the OK button.", identity=ident)
        proc.assert_not_called()
        self.assertIn("[V8]", out)
        self.assertNotIn("legacy", out.lower())

    def test_25_v8_off_skips_authorization(self):
        _v8_off()
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
            out = self.core.process_request("Click the OK button.")
        proc.assert_called_once()
        self.assertIn("legacy-ok", out)

    def test_26_ttl_is_short(self):
        self.assertLessEqual(AUTHORIZATION_TTL_SECONDS, 300)
        self.assertGreaterEqual(AUTHORIZATION_TTL_SECONDS, 30)

    def test_27_password_type_text_not_displayed(self):
        ident = _ident()
        plan = _type_plan(ident, text="hunter2", name="Password")
        disp = safe_plan_display(plan)
        self.assertEqual(disp["text"], "")
        self.assertEqual(disp["name"], "Password")

    def test_28_dangerous_type_payload_rejected_by_planner(self):
        _v8_on()
        _caps_on()
        self.assertIsNone(_type_payload('type "eval(1)"'))
        self.assertIsNone(_type_payload('type "rm -rf /"'))
        ident = _ident()
        out = plan_goal(_goal('type "eval(1)" into the Search field', ident), {
            "structured_targets": [{
                "automation_id": "t", "runtime_id": "1", "control_type": "Edit",
                "name": "Search", "selected": "true",
            }],
            "observation_hash": "a" * 64,
        })
        self.assertIsNone(out.plan)

    def test_29_oversized_type_parameter_rejected(self):
        ident = _ident()
        with self.assertRaises(PlanValidationError):
            _type_plan(ident, text="x" * 513)

    def test_30_bounded_type_accepted(self):
        ident = _ident()
        plan = _type_plan(ident, text="hello world")
        self.assertEqual(plan.plan_risk, "MEDIUM")
        pid, st = stash_pending_plan(plan, ident)
        self.assertEqual(st, "OK")
        self.assertTrue(pid)

    def test_31_same_goal_different_plan_needs_new_approval(self):
        ident = _ident()
        a = _click_plan(ident, name="OK")
        b = _click_plan(ident, name="Submit")
        pid, _ = stash_pending_plan(a, ident)
        claim, _ = claim_authorization(pid, ident)
        self.assertEqual(claim.plan.plan_hash, a.plan_hash)
        pid2, st = stash_pending_plan(b, ident)
        self.assertEqual(st, "OK")
        self.assertNotEqual(pid, pid2)

    def test_32_missing_pending_id(self):
        claim, code = claim_authorization("", _ident())
        self.assertEqual(code, ExecutionStatus.PLAN_NOT_FOUND.value)
        claim2, code2 = claim_authorization("missing", _ident())
        self.assertEqual(code2, ExecutionStatus.PLAN_NOT_FOUND.value)

    def test_33_prepare_does_not_execute(self):
        _v8_on()
        ident = _ident()
        with patch("orchestration.production.execute_plan") as ex:
            prep = prepare_v8_request("hello", identity=ident)
        ex.assert_not_called()
        self.assertEqual(prep.status, "OK")
        self.assertIsNotNone(prep.plan)

    def test_34_command_source_does_not_read_body_hash(self):
        src = DASH.read_text(encoding="utf-8")
        fn = src.split("async def v8_authenticated_command")[1].split("async def ")[0]
        self.assertNotIn('body.get("authorized_plan_hash")', fn)
        self.assertNotIn('body.get("owner_id")', fn)
        self.assertNotIn('body.get("session_id")', fn)
        auth = src.split("async def v8_authorize_plan")[1].split("async def ")[0]
        self.assertNotIn('body.get("owner_id")', auth)
        self.assertNotIn('body.get("session_id")', auth)
        self.assertNotIn("authorized_plan_hash=str(body", auth)

    def test_35_static_authorization_module(self):
        src = AUTH.read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden_mod = {"subprocess", "importlib", "ctypes", "pyautogui", "playwright", "selenium"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], forbidden_mod)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], forbidden_mod)
                self.assertFalse(node.module.startswith("proactive.computer"))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("eval", "exec"))
        self.assertNotIn("execute_computer_action", src)
        self.assertNotIn("execute_plan", src)
        self.assertNotIn("CognitiveEngine", src)
        self.assertNotIn("ALL_TOOLS", src)
        self.assertNotIn("pyautogui", src)

    def test_36_hash_goal_plan_must_match_before_claim(self):
        ident = _ident()
        plan = _click_plan(ident)
        self.assertEqual(hash_goal_plan(plan), plan.plan_hash)

    def test_37_browser_medium_not_approvable_here(self):
        ident = _ident()
        plan = build_goal_plan(_goal("open https://example.com", ident), [{
            "step_id": "s1",
            "capability_id": "browser",
            "action": "NAVIGATE",
            "parameters": {"session_id": ident.computer_session_id, "url": "https://example.com"},
            "dependencies": (),
            "verification_required": False,
            "verification_type": "",
            "retry_count": 0,
            "timeout_ms": 8000,
        }])
        self.assertEqual(plan.plan_risk, "MEDIUM")
        ok, code = medium_computer_approvable(plan)
        self.assertFalse(ok)
        self.assertEqual(code, ExecutionStatus.RISK_NOT_APPROVABLE.value)

    def test_38_voice_path_does_not_stash(self):
        _v8_on()
        ident = _ident()
        with patch("orchestration.observation.computer_planner_context", return_value=(None, ExecutionStatus.SESSION_UNAVAILABLE.value)):
            text = handle_v8_enabled_request("Click the OK button.", identity=ident)
        self.assertIn("SESSION_UNAVAILABLE", text)

    def test_39_mismatch_computer_owner_rejected_by_identity_helper(self):
        from orchestration.identity import verified_computer_session_id
        with patch("orchestration.identity._verified_computer_session", return_value=None):
            found, code = verified_computer_session_id("alice", "cs-1")
        self.assertIsNone(found)
        self.assertEqual(code, ExecutionStatus.SESSION_UNAVAILABLE.value)

    def test_40_empty_computer_session_required(self):
        from orchestration.identity import verified_computer_session_id
        found, code = verified_computer_session_id("alice", "")
        self.assertEqual(code, ExecutionStatus.COMPUTER_SESSION_REQUIRED.value)

    def test_41_approved_plan_unchanged_observation_executes(self):
        _v8_on()
        ident = _ident()
        plan = _click_plan(ident, oh="a" * 64)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        spy = _Spy("SUCCESS")
        out = exec_plan(
            claim.plan,
            identity=claim.identity,
            authorized_plan_hash=claim.authorized_plan_hash,
            adapters={"computer": spy, "verification": _Spy("VERIFIED")},
        )
        self.assertEqual(out.status, ExecutionStatus.SUCCESS)
        self.assertEqual(dict(claim.plan.steps[0].parameters)["precondition_observation_hash"], "a" * 64)

    def test_42_client_cannot_mark_plan_approved_in_stash(self):
        ident = _ident()
        plan = replace(_click_plan(ident), approved=True)
        pid, st = stash_pending_plan(plan, ident)
        self.assertEqual(st, "OK")
        claim, _ = claim_authorization(pid, ident)
        self.assertFalse(claim.plan.approved)

    def test_43_no_all_tools_or_pyautogui_in_auth_route(self):
        src = DASH.read_text(encoding="utf-8")
        fn = src.split("async def v8_authorize_plan")[1].split("async def ")[0]
        self.assertNotIn("ALL_TOOLS", fn)
        self.assertNotIn("pyautogui", fn)
        self.assertNotIn("execute_computer_action", fn)
        self.assertIn("execute_plan", fn)
        self.assertIn("claim_authorization", fn)

    def test_44_production_auth_failure_text_is_v8(self):
        _v8_on()
        ident = _ident()
        text = handle_v8_enabled_request("Click the OK button.", identity=ident)
        self.assertTrue(text.startswith("[V8]"))
        self.assertNotIn("CognitiveEngine", text)

    def test_45_claim_recomputes_canonical_hash(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, "OK")
        self.assertEqual(hash_goal_plan(claim.plan), claim.authorized_plan_hash)


class _Req:
    def __init__(self, *, origin="", csrf="", cookie="", method="POST", site=""):
        self.headers = {}
        if origin:
            self.headers["origin"] = origin
        if csrf:
            self.headers["X-DOOM-CSRF"] = csrf
        if site:
            self.headers["sec-fetch-site"] = site
        self.cookies = {}
        if cookie:
            self.cookies["doom_ask_sid"] = cookie
        self.method = method
        self.client = None


class TestV8AuthorizationHTTP(unittest.TestCase):
    def setUp(self):
        reset_authorization_store_for_tests()
        self._live = patch(
            "orchestration.authorization.verified_computer_session_id",
            return_value=("cs-1", "OK"),
        )
        self._live.start()

    def tearDown(self):
        self._live.stop()
        reset_authorization_store_for_tests()

    def test_46_unauthenticated_without_cookie(self):
        from dashboard.ask_session import load_session, require_ask_session
        sess = load_session(_Req())
        self.assertIsNone(sess)
        row, err = require_ask_session(_Req(origin=ORIGIN, csrf="x"), need_csrf=True)
        self.assertIsNone(row)
        self.assertEqual(err.status_code, 401)

    def test_47_missing_csrf(self):
        from dashboard.ask_session import csrf_ok
        self.assertFalse(csrf_ok(_Req(origin=ORIGIN), {"csrf_token": "stored-token"}))

    def test_48_invalid_csrf(self):
        from dashboard.ask_session import csrf_ok
        self.assertFalse(csrf_ok(_Req(origin=ORIGIN, csrf="wrong-csrf-token-value-xxx"), {"csrf_token": "stored-token"}))

    def test_49_invalid_origin(self):
        from dashboard.ask_session import origin_allowed
        self.assertFalse(origin_allowed(_Req(origin="http://evil.example", csrf="x")))
        self.assertTrue(origin_allowed(_Req(origin=ORIGIN, csrf="x")))

    def test_50_api_command_cannot_authorize(self):
        src = DASH.read_text(encoding="utf-8")
        cmd = src.split("async def execute_command")[1].split("async def ")[0]
        self.assertNotIn("claim_authorization", cmd)
        self.assertNotIn("stash_pending_plan", cmd)
        self.assertNotIn("/api/proactive/v8/authorize", cmd)
        ident = _ident()
        plan = _click_plan(ident)
        pid, st = stash_pending_plan(plan, ident)
        self.assertEqual(st, "OK")
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, "OK")

    def test_51_forged_owner_body_ignored(self):
        src = DASH.read_text(encoding="utf-8")
        auth = src.split("async def v8_authorize_plan")[1].split("async def ")[0]
        self.assertNotIn('body.get("owner_id")', auth)
        self.assertNotIn('body.get("session_id")', auth)
        cmd = src.split("async def v8_authenticated_command")[1].split("async def ")[0]
        self.assertNotIn('body.get("authorized_plan_hash")', cmd)
        self.assertIn("CSRF_FAILURE", src)
        self.assertIn("ORIGIN_FAILURE", src)

    def test_52_authorize_calls_execute_plan_not_v7(self):
        src = DASH.read_text(encoding="utf-8")
        auth = src.split("async def v8_authorize_plan")[1].split("async def ")[0]
        self.assertIn("execute_plan(", auth)
        self.assertIn("authorized_plan_hash=claim.authorized_plan_hash", auth)
        self.assertNotIn("execute_computer_action", auth)

    def test_53_ui_contains_explicit_buttons(self):
        html = (ROOT / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Approve this action", html)
        self.assertIn("v8-ask-cancel", html)
        js = (ROOT / "dashboard" / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/proactive/v8/authorize", js)
        self.assertNotIn('if (text === "yes")', js.lower())


if __name__ == "__main__":
    unittest.main()
