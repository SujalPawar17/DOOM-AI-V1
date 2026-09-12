"""Post-V8.10 computer CLICK/TYPE hardening. No real OS mutation. No cloud."""
from __future__ import annotations

import ast
import os
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_CLICK_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_TYPE_ENABLED", "false")

from orchestration.authorization import (
    AUTHORIZATION_TTL_SECONDS,
    claim_authorization,
    expire_pending_for_tests,
    pending_display,
    replace_pending_plan_for_tests,
    reset_authorization_store_for_tests,
    revoke_pending,
    safe_plan_display,
    stash_pending_plan,
)
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.kernel import process_goal
from orchestration.goal.plan_validator import build_goal_plan, hash_goal_plan
from orchestration.goal.planner import _type_payload, plan_goal
from orchestration.goal.planner_errors import PlannerStatus
from orchestration.identity import identity_from_ask_session_row, verified_computer_session_id
from orchestration.observation import MAX_AGE_MS, computer_planner_context
from orchestration.production import handle_v8_enabled_request, prepare_v8_request
from test_v8_harness import exec_plan
from core.orchestrator import DOOMCore

ROOT = Path(__file__).resolve().parent
AUTH = ROOT / "orchestration" / "authorization.py"
DASH = ROOT / "dashboard" / "server.py"
EXEC = ROOT / "orchestration" / "executor.py"
HTML = ROOT / "dashboard" / "static" / "index.html"
JS = ROOT / "dashboard" / "static" / "js" / "app.js"
ORIGIN = "http://127.0.0.1:8000"

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


def _goal(text, ident):
    return process_goal(text, context={
        "owner_id": ident.owner_id,
        "session_id": ident.session_id,
        "computer_session_id": ident.computer_session_id,
    }).goal


def _click_plan(ident, *, name="OK", oh="a" * 64, extra=None, control="Button"):
    params = {
        "automation_id": "btnOK",
        "runtime_id": "1.2.3",
        "control_type": control,
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


def _plan_text(text, targets, *, oh="a" * 64, ident=None):
    ident = ident or _ident()
    return plan_goal(_goal(text, ident), {
        "structured_targets": list(targets),
        "observation_hash": oh,
    })


class _Spy:
    def __init__(self, status="SUCCESS"):
        self.n = 0
        self.status = status
        self.steps = []

    def __call__(self, step, plan):
        self.n += 1
        self.steps.append(step)
        return self.status


class _Req:
    def __init__(self, *, origin="", csrf="", cookie="", method="POST"):
        self.headers = {}
        self.cookies = {}
        self.method = method
        if origin:
            self.headers["origin"] = origin
        if csrf:
            self.headers["X-DOOM-CSRF"] = csrf
        if cookie:
            self.cookies["doom_ask_sid"] = cookie


class TestComputerControlHardening(unittest.TestCase):
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

    def test_01_valid_authorization_consume(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, st = stash_pending_plan(plan, ident)
        self.assertEqual(st, "OK")
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, "OK")
        self.assertEqual(claim.authorized_plan_hash, plan.plan_hash)
        again, code2 = claim_authorization(pid, ident)
        self.assertIsNone(again)
        self.assertEqual(code2, ExecutionStatus.AUTHORIZATION_CONSUMED.value)

    def test_02_exact_hash_required(self):
        ident = _ident()
        plan = _click_plan(ident)
        self.assertEqual(hash_goal_plan(plan), plan.plan_hash)
        _v8_on()
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        out = exec_plan(
            claim.plan,
            identity=claim.identity,
            authorized_plan_hash=claim.authorized_plan_hash,
            adapters={"computer": _Spy(), "verification": _Spy("VERIFIED")},
        )
        self.assertEqual(out.status, ExecutionStatus.SUCCESS)

    def test_03_changed_plan_rejected(self):
        ident = _ident()
        a = _click_plan(ident, name="OK")
        b = _click_plan(ident, name="Submit")
        pid, _ = stash_pending_plan(a, ident)
        replace_pending_plan_for_tests(pid, b)
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.PLAN_HASH_MISMATCH.value)
        self.assertIsNone(claim)

    def test_04_changed_payload_new_hash(self):
        ident = _ident()
        a = _type_plan(ident, text="hello")
        b = _type_plan(ident, text="hello DOOM")
        self.assertNotEqual(a.plan_hash, b.plan_hash)

    def test_05_expired_authorization(self):
        ident = _ident()
        pid, _ = stash_pending_plan(_click_plan(ident), ident)
        expire_pending_for_tests(pid, now=1)
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_EXPIRED.value)
        self.assertLessEqual(AUTHORIZATION_TTL_SECONDS, 120)

    def test_06_consumed_not_reusable(self):
        ident = _ident()
        pid, _ = stash_pending_plan(_click_plan(ident), ident)
        claim_authorization(pid, ident)
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_CONSUMED.value)
        self.assertIsNone(claim)

    def test_07_revoked_authorization(self):
        ident = _ident()
        pid, _ = stash_pending_plan(_click_plan(ident), ident)
        self.assertEqual(revoke_pending(pid, ident), "OK")
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_REVOKED.value)

    def test_08_replay_after_success(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        _v8_on()
        first = exec_plan(
            claim.plan, identity=claim.identity,
            authorized_plan_hash=claim.authorized_plan_hash,
            adapters={"computer": _Spy(), "verification": _Spy("VERIFIED")},
        )
        self.assertEqual(first.status, ExecutionStatus.SUCCESS)
        again, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_CONSUMED.value)
        self.assertIsNone(again)

    def test_09_concurrent_replay_single_winner(self):
        ident = _ident()
        pid, _ = stash_pending_plan(_click_plan(ident), ident)
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

    def test_10_wrong_owner(self):
        ident = _ident()
        pid, _ = stash_pending_plan(_click_plan(ident), ident)
        other = _ident(owner_id="mallory")
        claim, code = claim_authorization(pid, other)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_INVALID.value)
        self.assertIsNone(claim)

    def test_11_missing_ask_identity(self):
        ident, status = identity_from_ask_session_row(None)
        self.assertIsNone(ident)
        self.assertEqual(status, ExecutionStatus.IDENTITY_REQUIRED.value)

    def test_12_invalid_ask_row(self):
        ident, status = identity_from_ask_session_row({
            "owner_id": "",
            "session_id_hash": "",
            "expires_at": time.time() + 3600,
        })
        self.assertIsNone(ident)
        self.assertEqual(status, ExecutionStatus.IDENTITY_REQUIRED.value)

    def test_13_wrong_ask_session(self):
        ident = _ident()
        pid, _ = stash_pending_plan(_click_plan(ident), ident)
        other = _ident(session_id="other-ask")
        claim, code = claim_authorization(pid, other)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_INVALID.value)

    def test_14_body_owner_ignored_in_goal(self):
        ident = _ident(owner_id="alice")
        goal = _goal("click DOOM_TEST_BUTTON owner_id=attacker", ident)
        self.assertEqual(goal.owner_id, "alice")

    def test_15_body_session_ignored_in_routes(self):
        src = DASH.read_text(encoding="utf-8")
        cmd = src.split("async def v8_authenticated_command")[1].split("async def ")[0]
        auth = src.split("async def v8_authorize_plan")[1].split("async def ")[0]
        self.assertNotIn('body.get("owner_id")', cmd)
        self.assertNotIn('body.get("session_id")', cmd)
        self.assertNotIn('body.get("owner_id")', auth)
        self.assertNotIn('body.get("session_id")', auth)

    def test_16_missing_computer_session(self):
        empty = _ident(computer_session_id="")
        live, err = verified_computer_session_id(empty.owner_id, "")
        self.assertIsNone(live)
        self.assertEqual(err, "COMPUTER_SESSION_REQUIRED")

    def test_17_computer_session_wrong_owner(self):
        with patch("proactive.computer.session.get_session", return_value=None):
            live, err = verified_computer_session_id("alice", "cs-1")
        self.assertIsNone(live)
        self.assertEqual(err, ExecutionStatus.SESSION_UNAVAILABLE.value)

    def test_18_computer_session_expired(self):
        row = {"owner_id": "alice", "session_id": "cs-1", "status": "EXPIRED"}
        with patch("proactive.computer.session.get_session", return_value=row):
            live, err = verified_computer_session_id("alice", "cs-1")
        self.assertIsNone(live)
        self.assertEqual(err, ExecutionStatus.SESSION_UNAVAILABLE.value)

    def test_19_computer_session_stopped(self):
        row = {"owner_id": "alice", "session_id": "cs-1", "status": "STOPPED"}
        with patch("proactive.computer.session.get_session", return_value=row):
            live, err = verified_computer_session_id("alice", "cs-1")
        self.assertIsNone(live)

    def test_20_computer_session_revoked(self):
        row = {"owner_id": "alice", "session_id": "cs-1", "status": "REVOKED"}
        with patch("proactive.computer.session.get_session", return_value=row):
            live, err = verified_computer_session_id("alice", "cs-1")
        self.assertIsNone(live)

    def test_21_computer_session_cancelled(self):
        row = {"owner_id": "alice", "session_id": "cs-1", "status": "CANCELLED"}
        with patch("proactive.computer.session.get_session", return_value=row):
            live, err = verified_computer_session_id("alice", "cs-1")
        self.assertIsNone(live)
        self.assertEqual(err, ExecutionStatus.SESSION_UNAVAILABLE.value)

    def test_22_session_a_cannot_execute_session_b(self):
        a = _ident(computer_session_id="cs-a")
        b = _ident(computer_session_id="cs-b")
        plan = _click_plan(a)
        pid, _ = stash_pending_plan(plan, a)
        claim, code = claim_authorization(pid, b, requested_computer_session_id="cs-b")
        self.assertIsNone(claim)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_INVALID.value)

    def test_23_fresh_observation_plans(self):
        _v8_on()
        _caps_on()
        out = _plan_text("click DOOM_TEST_BUTTON", [BTN])
        self.assertEqual(out.status, PlannerStatus.SUCCESS)
        self.assertEqual(dict(out.plan.steps[0].parameters)["precondition_observation_hash"], "a" * 64)

    def test_24_stale_observation_fails_closed(self):
        ident = _ident()
        plan = _click_plan(ident, oh="a" * 64)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        _v8_on()
        spy = _Spy(ExecutionStatus.STALE_OBSERVATION_HASH.value)
        out = exec_plan(
            claim.plan, identity=claim.identity,
            authorized_plan_hash=claim.authorized_plan_hash,
            adapters={"computer": spy, "verification": _Spy("VERIFIED")},
        )
        self.assertEqual(out.status, ExecutionStatus.STALE_OBSERVATION_HASH)
        self.assertEqual(spy.n, 1)

    def test_25_changed_target_new_plan_hash(self):
        ident = _ident()
        a = _click_plan(ident, name="DOOM_TEST_BUTTON")
        b = _click_plan(ident, name="OTHER")
        self.assertNotEqual(a.plan_hash, b.plan_hash)

    def test_26_disappeared_target(self):
        _v8_on()
        _caps_on()
        out = _plan_text("click DOOM_TEST_BUTTON", [])
        self.assertIsNone(out.plan)
        self.assertEqual(out.status, PlannerStatus.PLANNING_UNAVAILABLE)

    def test_27_changed_target_type(self):
        _v8_on()
        _caps_on()
        edit_named = dict(BTN, control_type="Edit")
        out = _plan_text("click DOOM_TEST_BUTTON", [edit_named])
        self.assertIsNone(out.plan)
        self.assertEqual(out.status, PlannerStatus.PLANNING_UNAVAILABLE)

    def test_28_changed_target_name(self):
        _v8_on()
        _caps_on()
        renamed = dict(BTN, name="OTHER_BUTTON")
        out = _plan_text("click DOOM_TEST_BUTTON", [renamed])
        self.assertIsNone(out.plan)

    def test_29_window_identity_fail_closed(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        _v8_on()
        out = exec_plan(
            claim.plan, identity=claim.identity,
            authorized_plan_hash=claim.authorized_plan_hash,
            adapters={"computer": _Spy(ExecutionStatus.PRECONDITION_FAILED.value), "verification": _Spy("VERIFIED")},
        )
        self.assertEqual(out.status, ExecutionStatus.PRECONDITION_FAILED)

    def test_30_observation_hash_mismatch_material(self):
        ident = _ident()
        a = _click_plan(ident, oh="a" * 64)
        b = _click_plan(ident, oh="b" * 64)
        self.assertNotEqual(a.plan_hash, b.plan_hash)
        _v8_on()
        blocked = execute_plan(b, identity=ident, authorized_plan_hash=a.plan_hash)
        self.assertEqual(blocked.status, ExecutionStatus.APPROVAL_REQUIRED)

    def test_31_unique_button(self):
        _v8_on()
        _caps_on()
        out = _plan_text("click DOOM_TEST_BUTTON", [BTN, EDIT])
        self.assertEqual(out.status, PlannerStatus.SUCCESS)
        self.assertEqual(out.plan.steps[0].action, "CLICK")

    def test_32_duplicate_button(self):
        _v8_on()
        _caps_on()
        dup = dict(BTN, automation_id="other", runtime_id="9.9")
        out = _plan_text("click DOOM_TEST_BUTTON", [BTN, dup])
        self.assertEqual(out.status, PlannerStatus.AMBIGUOUS_TARGET)
        self.assertIsNone(out.plan)

    def test_33_zero_button(self):
        _v8_on()
        _caps_on()
        out = _plan_text("click DOOM_TEST_BUTTON", [EDIT])
        self.assertEqual(out.status, PlannerStatus.PLANNING_UNAVAILABLE)

    def test_34_similar_names_no_fuzzy(self):
        _v8_on()
        _caps_on()
        similar = dict(BTN, name="DOOM_TEST_BUTTON2", automation_id="x", runtime_id="2")
        out = _plan_text("click DOOM_TEST_BUTTON", [similar])
        self.assertIsNone(out.plan)
        self.assertEqual(out.status, PlannerStatus.PLANNING_UNAVAILABLE)

    def test_35_unique_edit(self):
        _v8_on()
        _caps_on()
        only = dict(EDIT)
        only.pop("selected", None)
        out = _plan_text('Type "hello DOOM" into DOOM_TEST_INPUT', [only])
        self.assertEqual(out.status, PlannerStatus.SUCCESS)
        self.assertEqual(out.plan.steps[0].action, "TYPE")

    def test_36_duplicate_edit(self):
        _v8_on()
        _caps_on()
        a = dict(EDIT)
        a.pop("selected", None)
        b = dict(EDIT, automation_id="other", runtime_id="3")
        b.pop("selected", None)
        out = _plan_text('Type "hello DOOM" into DOOM_TEST_INPUT', [a, b])
        self.assertEqual(out.status, PlannerStatus.AMBIGUOUS_TARGET)

    def test_37_focused_edit(self):
        _v8_on()
        _caps_on()
        other = {
            "automation_id": "other",
            "runtime_id": "3",
            "control_type": "Edit",
            "name": "OTHER",
        }
        out = _plan_text("Type hello into the selected field.", [EDIT, other])
        self.assertEqual(out.status, PlannerStatus.SUCCESS)
        self.assertEqual(dict(out.plan.steps[0].parameters)["name"], "DOOM_TEST_INPUT")

    def test_38_missing_name_not_planned(self):
        _v8_on()
        _caps_on()
        nameless = dict(BTN, name="")
        out = _plan_text("click DOOM_TEST_BUTTON", [nameless])
        self.assertIsNone(out.plan)

    def test_39_valid_type_payloads(self):
        self.assertEqual(_type_payload('type "hello DOOM"'), "hello DOOM")
        self.assertEqual(_type_payload('type "Hello, world!"'), "Hello, world!")
        self.assertEqual(_type_payload('type "test@example.com"'), "test@example.com")
        self.assertEqual(_type_payload("type 12345"), "12345")

    def test_40_type_256_boundary(self):
        payload = "a" * 256
        self.assertEqual(_type_payload(f'type "{payload}"'), payload)

    def test_41_type_257_rejected(self):
        self.assertIsNone(_type_payload('type "' + ("a" * 257) + '"'))

    def test_42_dangerous_type_patterns(self):
        bad = (
            "eval", "exec", "subprocess", "shell", "powershell", "cmd.exe",
            "python", "bash", "cmd /c", "rm -rf", "<script", "`whoami`", "$(id)",
        )
        for item in bad:
            self.assertIsNone(_type_payload(f'type "{item}"'), item)

    def test_43_ordinary_punctuation_allowed(self):
        self.assertEqual(_type_payload('type "Hello, world!"'), "Hello, world!")
        self.assertEqual(_type_payload("type hello DOOM"), "hello DOOM")

    def test_44_explicit_approval_ui(self):
        html = HTML.read_text(encoding="utf-8")
        js = JS.read_text(encoding="utf-8")
        self.assertIn("Approve this action", html)
        self.assertNotIn("Allow computer control", html)
        self.assertIn("this exact action", html.lower())
        self.assertIn("Target:", js)
        self.assertIn("Type:", js)
        self.assertIn("Approve this action", html)

    def test_45_cancel_revokes_without_execute(self):
        ident = _ident()
        pid, _ = stash_pending_plan(_click_plan(ident), ident)
        self.assertEqual(revoke_pending(pid, ident), "OK")
        _v8_on()
        spy = _Spy()
        disp = pending_display(pid, ident)
        self.assertIsNone(disp[0])
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_REVOKED.value)
        self.assertEqual(spy.n, 0)

    def test_46_missing_csrf(self):
        from dashboard.ask_session import csrf_ok
        self.assertFalse(csrf_ok(_Req(origin=ORIGIN), {"csrf_token": "stored-token"}))

    def test_47_invalid_csrf(self):
        from dashboard.ask_session import csrf_ok
        self.assertFalse(csrf_ok(
            _Req(origin=ORIGIN, csrf="wrong-csrf-token-value-xxx"),
            {"csrf_token": "stored-token"},
        ))

    def test_48_invalid_origin(self):
        from dashboard.ask_session import origin_allowed
        self.assertFalse(origin_allowed(_Req(origin="http://evil.example", csrf="x")))
        self.assertTrue(origin_allowed(_Req(origin=ORIGIN, csrf="x")))

    def test_49_unauthenticated_ask(self):
        from dashboard.ask_session import require_ask_session
        row, err = require_ask_session(_Req(origin=ORIGIN, csrf="x"), need_csrf=True)
        self.assertIsNone(row)
        self.assertIsNotNone(err)

    def test_50_api_command_cannot_authorize(self):
        src = DASH.read_text(encoding="utf-8")
        fn = src.split("async def execute_command")[1].split("async def ")[0]
        self.assertNotIn("claim_authorization", fn)
        self.assertNotIn("stash_pending_plan", fn)
        self.assertNotIn("execute_plan", fn)

    def test_51_execute_plan_is_authorize_mutation_path(self):
        src = DASH.read_text(encoding="utf-8")
        auth = src.split("async def v8_authorize_plan")[1].split("async def ")[0]
        self.assertIn("execute_plan(", auth)
        self.assertIn("claim_authorization", auth)

    def test_52_no_direct_v7_from_dashboard_authorize(self):
        src = DASH.read_text(encoding="utf-8")
        auth = src.split("async def v8_authorize_plan")[1].split("async def ")[0]
        self.assertNotIn("execute_computer_action", auth)
        cmd = src.split("async def v8_authenticated_command")[1].split("async def ")[0]
        self.assertNotIn("execute_computer_action", cmd)

    def test_53_v7_approval_state_approved_only_in_default_computer(self):
        src = EXEC.read_text(encoding="utf-8")
        fn = src.split("def _default_computer")[1].split("def _default_browser")[0]
        self.assertIn("ApprovalState.APPROVED", fn)

    def test_54_toctou_hash_is_on_click_plan(self):
        ident = _ident()
        plan = _click_plan(ident, oh="c" * 64)
        self.assertEqual(dict(plan.steps[0].parameters)["precondition_observation_hash"], "c" * 64)

    def test_55_verification_required_not_success_without_verified(self):
        ident = _ident()
        plan = _click_plan(ident)
        self.assertTrue(plan.steps[0].verification_required)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        _v8_on()
        out = exec_plan(
            claim.plan, identity=claim.identity,
            authorized_plan_hash=claim.authorized_plan_hash,
            adapters={"computer": _Spy("SUCCESS"), "verification": _Spy("NOPE")},
        )
        self.assertEqual(out.status, ExecutionStatus.NOT_VERIFIED)
        claim2, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_CONSUMED.value)

    def test_56_v8_on_no_cognitive_engine_fallback(self):
        _v8_on()
        ident = _ident()
        with patch.object(self.core.cognition, "process") as proc:
            out = self.core.process_request("click DOOM_TEST_BUTTON", identity=ident)
        proc.assert_not_called()
        self.assertIn("[V8]", out)

    def test_57_v8_on_no_all_tools_fallback(self):
        src = (ROOT / "orchestration" / "production.py").read_text(encoding="utf-8")
        self.assertNotIn("ALL_TOOLS", src)
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            if isinstance(node, ast.Import):
                imported.update(n.name.split(".")[0] for n in node.names)
        self.assertNotIn("core", imported)

    def test_58_v8_on_no_pyautogui_fallback(self):
        for path in (AUTH, EXEC, ROOT / "orchestration" / "production.py"):
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("pyautogui", src)
            tree = ast.parse(src)
            mods = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    mods.update(n.name.split(".")[0] for n in node.names)
                if isinstance(node, ast.ImportFrom) and node.module:
                    mods.add(node.module.split(".")[0])
            self.assertFalse(mods & {"pyautogui", "playwright", "selenium", "subprocess"})

    def test_59_restart_clears_in_memory_authorization(self):
        ident = _ident()
        pid, _ = stash_pending_plan(_click_plan(ident), ident)
        reset_authorization_store_for_tests()
        claim, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.PLAN_NOT_FOUND.value)
        self.assertIsNone(claim)

    def test_60_stale_authorization_object_cannot_execute_after_restart(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        reset_authorization_store_for_tests()
        _v8_on()
        spy = _Spy()
        out = exec_plan(plan, identity=ident, authorized_plan_hash="", adapters={"computer": spy})
        self.assertEqual(out.status, ExecutionStatus.APPROVAL_REQUIRED)
        self.assertEqual(spy.n, 0)

    def test_61_v8_off_uses_legacy_cognition(self):
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
            out = self.core.process_request("click DOOM_TEST_BUTTON")
        proc.assert_called_once()
        self.assertIn("legacy-ok", out)

    def test_62_estop_fail_closed(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        _v8_on()
        spy = _Spy()
        out = exec_plan(
            claim.plan, identity=claim.identity,
            authorized_plan_hash=claim.authorized_plan_hash,
            adapters={"computer": spy, "verification": _Spy("VERIFIED")},
            emergency_stop_fn=lambda *_a, **_k: True,
        )
        self.assertEqual(out.status, ExecutionStatus.EMERGENCY_STOPPED)
        self.assertEqual(spy.n, 0)

    def test_63_verification_failed_not_success(self):
        ident = _ident()
        plan = _click_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        _v8_on()
        out = exec_plan(
            claim.plan, identity=claim.identity,
            authorized_plan_hash=claim.authorized_plan_hash,
            adapters={"computer": _Spy("SUCCESS"), "verification": _Spy(ExecutionStatus.VERIFICATION_FAILED.value)},
        )
        self.assertEqual(out.status, ExecutionStatus.VERIFICATION_FAILED)

    def test_64_consume_before_execute_survives_adapter_failure(self):
        ident = _ident()
        pid, _ = stash_pending_plan(_click_plan(ident), ident)
        claim, _ = claim_authorization(pid, ident)
        _v8_on()
        exec_plan(
            claim.plan, identity=claim.identity,
            authorized_plan_hash=claim.authorized_plan_hash,
            adapters={"computer": _Spy(ExecutionStatus.STEP_FAILED.value)},
        )
        _, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_CONSUMED.value)

    def test_65_material_plan_fields_change_hash(self):
        ident = _ident()
        base = _click_plan(ident)
        cases = [
            _click_plan(ident, extra={"automation_id": "other"}),
            _click_plan(ident, extra={"runtime_id": "9.9.9"}),
            _click_plan(ident, control="Edit"),
            _click_plan(ident, name="Submit"),
        ]
        self.assertNotEqual(base.plan_hash, cases[0].plan_hash)
        self.assertNotEqual(base.plan_hash, cases[1].plan_hash)
        self.assertNotEqual(base.plan_hash, cases[2].plan_hash)
        self.assertNotEqual(base.plan_hash, cases[3].plan_hash)

    def test_66_retry_and_timeout_change_hash(self):
        ident = _ident()
        a = build_goal_plan(_goal("Click the OK button.", ident), [{
            "step_id": "s1", "capability_id": "computer", "action": "CLICK",
            "parameters": {
                "automation_id": "btnOK", "runtime_id": "1", "control_type": "Button",
                "name": "OK", "session_id": ident.computer_session_id,
                "precondition_observation_hash": "a" * 64,
            },
            "dependencies": (), "verification_required": True,
            "verification_type": "TARGET_STATE_MATCH", "retry_count": 0, "timeout_ms": 8000,
        }])
        c = build_goal_plan(_goal("Click the OK button.", ident), [{
            "step_id": "s1", "capability_id": "computer", "action": "CLICK",
            "parameters": {
                "automation_id": "btnOK", "runtime_id": "1", "control_type": "Button",
                "name": "OK", "session_id": ident.computer_session_id,
                "precondition_observation_hash": "a" * 64,
            },
            "dependencies": (), "verification_required": True,
            "verification_type": "TARGET_STATE_MATCH", "retry_count": 0, "timeout_ms": 9000,
        }])
        self.assertNotEqual(a.plan_hash, c.plan_hash)
        from orchestration.goal.plan_errors import PlanValidationError
        with self.assertRaises(PlanValidationError):
            build_goal_plan(_goal("Click the OK button.", ident), [{
                "step_id": "s1", "capability_id": "computer", "action": "CLICK",
                "parameters": {
                    "automation_id": "btnOK", "runtime_id": "1", "control_type": "Button",
                    "name": "OK", "session_id": ident.computer_session_id,
                    "precondition_observation_hash": "a" * 64,
                },
                "dependencies": (), "verification_required": True,
                "verification_type": "TARGET_STATE_MATCH", "retry_count": 1, "timeout_ms": 8000,
            }])

    def test_67_verification_spec_change_hash(self):
        ident = _ident()
        a = _click_plan(ident)
        mutated = replace(a, steps=(replace(a.steps[0], verification_required=False),))
        self.assertNotEqual(hash_goal_plan(mutated), a.plan_hash)

    def test_68_default_computer_maps_stale_error_code(self):
        from orchestration.executor import _default_computer
        ident = _ident()
        plan = _click_plan(ident)
        fake = SimpleNamespace(status=SimpleNamespace(value="PRECONDITION_FAILED"), error_code="STALE_OBSERVATION_HASH")
        with patch("orchestration.identity.verified_computer_session_id", return_value=("cs-1", "OK")):
            with patch("proactive.computer.actions.kernel.execute_computer_action", return_value=fake):
                code = _default_computer(plan.steps[0], plan)
        self.assertEqual(code, ExecutionStatus.STALE_OBSERVATION_HASH.value)

    def test_69_observation_expired(self):
        _caps_on()
        rec = MagicMock()
        rec.observation_hash = "a" * 64
        rec.capture_unix_ms = int(time.time() * 1000) - (MAX_AGE_MS + 50)
        with patch("proactive.computer.session.get_session", return_value={"owner_id": "alice", "session_id": "cs-1", "status": "OBSERVING", "bound_hwnd": 1, "bound_pid": 1, "bound_exe_path_norm": r"c:\python.exe", "bound_window_class": "TkTopLevel"}):
            with patch("proactive.computer.observe.capture_structured_observation", return_value=(rec, "OK", (BTN,))):
                ctx, status = computer_planner_context(_ident())
        self.assertIsNone(ctx)
        self.assertEqual(status, ExecutionStatus.SESSION_UNAVAILABLE.value)

    def test_70_focused_non_edit_not_typed(self):
        _v8_on()
        _caps_on()
        focused_btn = dict(BTN, selected="true")
        out = _plan_text("Type hello into the selected field.", [focused_btn])
        self.assertIsNone(out.plan)

    def test_71_prepare_does_not_authorize(self):
        _v8_on()
        with patch("orchestration.production.execute_plan") as ex:
            prep = prepare_v8_request("hello", identity=_ident())
        ex.assert_not_called()
        self.assertEqual(prep.status, "OK")

    def test_72_handle_v8_never_calls_cognition(self):
        _v8_on()
        with patch("core.orchestrator.DOOMCore") as unused:
            unused.process = MagicMock()
            text = handle_v8_enabled_request("hello", identity=_ident())
        self.assertTrue(text.startswith("[V8]"))

    def test_73_computer_verify_not_filesystem(self):
        src = EXEC.read_text(encoding="utf-8")
        fn = src.split("def _default_verify")[1].split("def _default_world")[0]
        self.assertIn('capability_id == "computer"', fn)
        self.assertIn("TARGET_STATE_MATCH", fn)
        computer_branch = fn.split("spec = VerificationSpec")[0]
        self.assertNotIn('capability="filesystem"', computer_branch)
        self.assertNotIn("filesystem", computer_branch)

    def test_74_display_shows_exact_type_text(self):
        ident = _ident()
        plan = _type_plan(ident, text="hello DOOM", name="DOOM_TEST_INPUT")
        disp = safe_plan_display(plan)
        self.assertEqual(disp["action"], "TYPE")
        self.assertEqual(disp["name"], "DOOM_TEST_INPUT")
        self.assertEqual(disp["control_type"], "Edit")
        self.assertEqual(disp["text"], "hello DOOM")

    def test_75_ask_session_not_substituted_as_computer_session(self):
        ident = _ident(session_id="ask-sess", computer_session_id="cs-1")
        plan = _click_plan(ident)
        self.assertEqual(plan.session_id, "ask-sess")
        self.assertEqual(plan.computer_session_id, "cs-1")
        self.assertNotEqual(plan.session_id, plan.computer_session_id)


if __name__ == "__main__":
    unittest.main()
