"""Trusted window binding: opaque candidates + persistent identity. No live click."""
from __future__ import annotations

import ast
import os
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_OBSERVE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_CLICK_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_TYPE_ENABLED", "false")

from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.kernel import process_goal
from orchestration.goal.plan_errors import PlanValidationError
from orchestration.goal.planner import plan_goal
from orchestration.goal.planner_errors import PlannerStatus
from orchestration.goal.plan_validator import build_goal_plan
from orchestration.observation import computer_planner_context
from proactive.computer.actions.kernel import execute_computer_action
from proactive.computer.actions.adapter import MemoryUiaAdapter
from proactive.computer.actions.target import ActionNode
from proactive.computer.actions.types import (
    ActionType,
    ApprovalState,
    ComputerActionRequest,
    PATTERN_INVOKE,
    Status,
    TargetIdentity,
)
from proactive.computer.drivers.uia_win import UIA_UNAVAILABLE, UiaWalkNode
from proactive.computer.drivers.win32_id import OK, WINDOW_GONE, Win32Identity
from proactive.computer.hash import observation_hash
from proactive.computer.observe import ComputerObservation, capture_structured_observation
from proactive.computer.policy import CAPABILITY_ID, SCHEMA_VERSION
from proactive.computer.session import (
    bind_session_window,
    identities_match,
    list_bind_candidates,
    public_computer_session,
    reset_bind_candidates_for_tests,
    seed_bind_candidate_for_tests,
    validate_bound_identity,
)
from test_v8_harness import exec_plan

ROOT = Path(__file__).resolve().parent
ORIGIN = "http://127.0.0.1:8000"
BTN = {
    "automation_id": "doom_test_button",
    "runtime_id": "1.2.3",
    "control_type": "Button",
    "name": "DOOM_TEST_BUTTON",
}


def _ident(**kw):
    return ExecutionIdentity(
        owner_id=kw.get("owner_id", "alice"),
        session_id=kw.get("session_id", "ask-sess"),
        computer_session_id=kw.get("computer_session_id", "cs-1"),
    )


def _win(**kw) -> Win32Identity:
    return Win32Identity(
        hwnd=int(kw.get("hwnd", 42)),
        pid=int(kw.get("pid", 1001)),
        exe_path_norm=str(kw.get("exe_path_norm", r"c:\python.exe")),
        window_class=str(kw.get("window_class", "TkTopLevel")),
        title_advisory=str(kw.get("title_advisory", "DOOM Safe Test Window")),
        outcome=str(kw.get("outcome", OK)),
    )


def _sess(owner="alice", sid="cs-1", status="OBSERVING", bound=False, **kw):
    row = {
        "owner_id": owner,
        "session_id": sid,
        "status": status,
        "bound_hwnd": 42 if bound else 0,
        "bound_pid": 1001 if bound else 0,
        "bound_exe_path_norm": r"c:\python.exe" if bound else "",
        "bound_window_class": "TkTopLevel" if bound else "",
        "bound_title_advisory": "DOOM Safe Test Window" if bound else "",
    }
    row.update(kw)
    return row


def _caps_on():
    os.environ["PROACTIVE_V8_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "true"


def _obs_rec(targets=(), code="OK"):
    rec = MagicMock()
    rec.observation_hash = "a" * 64
    rec.capture_unix_ms = int(time.time() * 1000)
    rec.outcome_code = code
    rec.hwnd = 42
    return rec, code, tuple(targets)


class TestOpaqueCandidatesAndBind(unittest.TestCase):
    def setUp(self):
        reset_bind_candidates_for_tests()
        _caps_on()

    def tearDown(self):
        reset_bind_candidates_for_tests()

    def test_candidates_are_opaque_no_hwnd(self):
        ident = _win()
        with patch("proactive.computer.session.get_session", return_value=_sess()):
            rows, code = list_bind_candidates("alice", "cs-1", enumerator=lambda **k: [ident])
        self.assertEqual(code, "OK")
        self.assertEqual(len(rows), 1)
        self.assertNotIn("hwnd", rows[0])
        self.assertNotIn("exe_path_norm", rows[0])
        cid = rows[0]["candidate_id"]
        self.assertFalse(cid.isdigit())
        self.assertNotIn(str(ident.hwnd), cid)
        self.assertEqual(rows[0]["title"], "DOOM Safe Test Window")
        self.assertEqual(rows[0]["exe"], "python.exe")

    def test_raw_hwnd_candidate_rejected(self):
        with patch("proactive.computer.session.get_session", return_value=_sess()):
            bound, code = bind_session_window("alice", "cs-1", "424242")
        self.assertIsNone(bound)
        self.assertEqual(code, "INVALID_CANDIDATE")
        with patch("proactive.computer.session.get_session", return_value=_sess()):
            bound2, code2 = bind_session_window("alice", "cs-1", "hwnd:42")
        self.assertEqual(code2, "INVALID_CANDIDATE")

    def test_candidate_expires(self):
        ident = _win()
        cid = seed_bind_candidate_for_tests("alice", "cs-1", ident, expires_at=time.time() - 1)
        with patch("proactive.computer.session.get_session", return_value=_sess()):
            bound, code = bind_session_window("alice", "cs-1", cid, now=time.time())
        self.assertEqual(code, "CANDIDATE_EXPIRED")
        self.assertIsNone(bound)

    def test_window_gone_at_bind(self):
        ident = _win()
        cid = seed_bind_candidate_for_tests("alice", "cs-1", ident)
        gone = Win32Identity(outcome=WINDOW_GONE)
        with patch("proactive.computer.session.get_session", return_value=_sess()):
            bound, code = bind_session_window(
                "alice", "cs-1", cid, identity_fn=lambda hwnd, **k: gone,
            )
        self.assertEqual(code, "WINDOW_GONE")
        self.assertIsNone(bound)

    def test_pid_exe_class_hwnd_reuse(self):
        ident = _win()
        for field, code_want, live_kw in (
            ("PID_MISMATCH", "PID_MISMATCH", {"pid": 9999}),
            ("EXE_MISMATCH", "EXE_MISMATCH", {"exe_path_norm": r"c:\evil.exe"}),
            ("CLASS_MISMATCH", "CLASS_MISMATCH", {"window_class": "Chrome_WidgetWin_1"}),
            ("HWND_REUSE", "HWND_REUSE", {"hwnd": 99}),
        ):
            reset_bind_candidates_for_tests()
            cid = seed_bind_candidate_for_tests("alice", "cs-1", ident)
            live = _win(**live_kw)
            with patch("proactive.computer.session.get_session", return_value=_sess()):
                bound, code = bind_session_window(
                    "alice", "cs-1", cid, identity_fn=lambda hwnd, **k: live,
                )
            self.assertIsNone(bound, field)
            self.assertEqual(code, code_want, field)

    def test_owner_and_session_mismatch(self):
        ident = _win()
        cid = seed_bind_candidate_for_tests("alice", "cs-1", ident)
        with patch("proactive.computer.session.get_session", return_value=None):
            bound, code = bind_session_window("bob", "cs-1", cid)
        self.assertEqual(code, "SESSION_UNAVAILABLE")
        with patch("proactive.computer.session.get_session", return_value=_sess(sid="cs-2")):
            bound2, code2 = bind_session_window("alice", "cs-2", cid)
        self.assertEqual(code2, "INVALID_CANDIDATE")

    def test_dead_session_rejected(self):
        ident = _win()
        cid = seed_bind_candidate_for_tests("alice", "cs-1", ident)
        with patch("proactive.computer.session.get_session", return_value=_sess(status="STOPPED")):
            bound, code = bind_session_window("alice", "cs-1", cid)
            rows, lcode = list_bind_candidates("alice", "cs-1")
        self.assertEqual(code, "SESSION_UNAVAILABLE")
        self.assertEqual(lcode, "SESSION_UNAVAILABLE")

    def test_already_bound_not_silent_rebind(self):
        ident = _win()
        cid = seed_bind_candidate_for_tests("alice", "cs-1", ident)
        with patch("proactive.computer.session.get_session", return_value=_sess(bound=True)):
            bound, code = bind_session_window(
                "alice", "cs-1", cid, identity_fn=lambda hwnd, **k: ident,
            )
        self.assertEqual(code, "ALREADY_BOUND")
        self.assertIsNone(bound)

    def test_concurrent_bind_one_winner(self):
        ident = _win()
        c1 = seed_bind_candidate_for_tests("alice", "cs-1", ident)
        c2 = seed_bind_candidate_for_tests("alice", "cs-1", _win(hwnd=43))
        state = {"n": 0}
        lock = threading.Lock()
        bound_row = _sess(bound=True)

        def get_session(sid, owner):
            with lock:
                return bound_row if state["n"] else _sess()

        def store_bind(*a, **k):
            with lock:
                if state["n"]:
                    return None
                state["n"] += 1
                return bound_row

        results = []

        def _run(cid):
            results.append(bind_session_window(
                "alice", "cs-1", cid, identity_fn=lambda hwnd, **k: ident if hwnd == 42 else _win(hwnd=43),
            ))

        with patch("proactive.computer.session.get_session", side_effect=get_session), \
             patch("proactive.computer.session.proactive_store.bind_computer_session", side_effect=store_bind):
            t1 = threading.Thread(target=_run, args=(c1,))
            t2 = threading.Thread(target=_run, args=(c2,))
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        oks = [r for r in results if r[1] == "OK"]
        fails = [r for r in results if r[1] != "OK"]
        self.assertEqual(len(oks), 1)
        self.assertEqual(len(fails), 1)
        self.assertIn(fails[0][1], ("ALREADY_BOUND", "SESSION_UNAVAILABLE", "HWND_REUSE"))

    def test_public_session_strips_hwnd(self):
        pub = public_computer_session(_sess(bound=True))
        self.assertTrue(pub["bound"])
        self.assertNotIn("bound_hwnd", pub)
        self.assertEqual(pub["bound_title"], "DOOM Safe Test Window")


class TestAskCandidateRoutes(unittest.TestCase):
    def test_list_bind_routes_require_ask_csrf_origin(self):
        from dashboard.ask_session import csrf_ok, origin_allowed, require_ask_session

        class _Req:
            def __init__(self, origin="", csrf="", cookies=None):
                self.headers = {}
                if origin:
                    self.headers["origin"] = origin
                if csrf:
                    self.headers["X-DOOM-CSRF"] = csrf
                self.cookies = cookies or {}
                self.method = "GET"

        self.assertFalse(origin_allowed(_Req(origin="http://evil.example")))
        self.assertTrue(origin_allowed(_Req(origin=ORIGIN)))
        self.assertFalse(csrf_ok(_Req(origin=ORIGIN), {"csrf_token": "stored"}))
        row, err = require_ask_session(_Req(origin=ORIGIN, csrf="x"), need_csrf=True)
        self.assertIsNone(row)
        self.assertIsNotNone(err)
        src = (ROOT / "dashboard" / "server.py").read_text(encoding="utf-8")
        cand = src.split("async def computer_window_candidates")[1].split("async def ")[0]
        bind = src.split("async def computer_bind_window")[1].split("async def ")[0]
        self.assertIn("need_csrf=True", cand)
        self.assertIn("need_csrf=True", bind)
        self.assertIn('if "hwnd" in body', bind)
        self.assertNotIn("execute_computer_action", bind)
        self.assertNotIn("execute_plan", bind)
        self.assertNotIn("plan_goal", bind)
        js = (ROOT / "dashboard" / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("candidate_id", js)
        self.assertNotIn("hwnd:", js.split("v8CsBindBtn")[1].split("v8CsStop")[0] if "v8CsBindBtn" in js else js)

    def test_bind_rejects_raw_hwnd_body_unit(self):
        ident = _win()
        with patch("proactive.computer.session.get_session", return_value=_sess()):
            bound, code = bind_session_window("alice", "cs-1", "12345")
        self.assertEqual(code, "INVALID_CANDIDATE")
        self.assertIsNone(bound)


class TestBoundObservationAndPlanner(unittest.TestCase):
    def setUp(self):
        _caps_on()

    def test_bound_observation_uses_hwnd_not_foreground(self):
        live = _win()
        root = UiaWalkNode(
            runtime_id="1", automation_id="root", control_type="Window",
            name="DOOM Safe Test Window",
            children=[UiaWalkNode(
                runtime_id="1.2.3", automation_id="doom_test_button",
                control_type="Button", name="DOOM_TEST_BUTTON",
            )],
        )
        with patch("proactive.computer.session.get_session", return_value=_sess(bound=True)), \
             patch("proactive.computer.observe.read_foreground_win32") as fg, \
             patch("proactive.computer.observe.read_foreground_uia_tree", return_value=(MagicMock(outcome="OK", runtime_id="1", automation_id="root", control_type="Window", tree_digest="x", node_count=2, sensitive_hit=False), root)):
            obs, code, targets = capture_structured_observation(
                "alice", computer_session_id="cs-1", identity_fn=lambda hwnd, **k: live,
            )
        fg.assert_not_called()
        self.assertIsNotNone(obs)
        self.assertEqual(obs.hwnd, 42)
        self.assertTrue(any(t.get("name") == "DOOM_TEST_BUTTON" for t in targets))

    def test_unbound_v8_click_fails_closed(self):
        with patch("proactive.computer.session.get_session", return_value=_sess(bound=False)):
            ctx, status = computer_planner_context(_ident())
        self.assertIsNone(ctx)
        self.assertEqual(status, ExecutionStatus.SESSION_UNAVAILABLE.value)

    def test_uia_unavailable_not_plannable(self):
        rec = MagicMock()
        rec.observation_hash = "b" * 64
        rec.capture_unix_ms = int(time.time() * 1000)
        rec.outcome_code = UIA_UNAVAILABLE
        with patch("proactive.computer.session.get_session", return_value=_sess(bound=True)):
            with patch(
                "proactive.computer.observe.capture_structured_observation",
                return_value=(rec, UIA_UNAVAILABLE, ()),
            ):
                ctx, status = computer_planner_context(_ident())
        self.assertIsNone(ctx)
        self.assertEqual(status, ExecutionStatus.SESSION_UNAVAILABLE.value)

    def test_empty_uia_failure_not_success_context(self):
        rec = MagicMock()
        rec.observation_hash = "c" * 64
        rec.capture_unix_ms = int(time.time() * 1000)
        rec.outcome_code = UIA_UNAVAILABLE
        with patch("proactive.computer.session.get_session", return_value=_sess(bound=True)):
            with patch(
                "proactive.computer.observe.capture_structured_observation",
                return_value=(rec, "OK", ()),
            ):
                ctx, status = computer_planner_context(_ident())
        self.assertIsNone(ctx)

    def test_planner_resolves_doom_test_button_without_hwnd(self):
        goal = process_goal(
            "click DOOM_TEST_BUTTON",
            context={"owner_id": "alice", "session_id": "ask-sess", "computer_session_id": "cs-1"},
        ).goal
        ctx = {
            "structured_targets": [BTN],
            "observation_hash": "d" * 64,
        }
        proposal = plan_goal(goal, ctx)
        self.assertEqual(proposal.status, PlannerStatus.SUCCESS)
        params = dict(proposal.plan.steps[0].parameters)
        self.assertEqual(params["name"], "DOOM_TEST_BUTTON")
        self.assertEqual(params["control_type"], "Button")
        self.assertNotIn("hwnd", params)
        self.assertNotIn("pid", params)
        self.assertTrue(proposal.plan.steps[0].verification_required)

    def test_planner_cannot_supply_hwnd(self):
        goal = process_goal("click DOOM_TEST_BUTTON").goal
        with self.assertRaises(PlanValidationError) as ctx:
            build_goal_plan(goal, [{
                "step_id": "s1",
                "capability_id": "computer",
                "action": "CLICK",
                "parameters": {
                    "automation_id": "doom_test_button",
                    "runtime_id": "1",
                    "control_type": "Button",
                    "name": "DOOM_TEST_BUTTON",
                    "session_id": "cs-1",
                    "hwnd": "12345",
                },
                "dependencies": (),
                "verification_required": True,
                "verification_type": "TARGET_STATE_MATCH",
                "retry_count": 0,
                "timeout_ms": 8000,
            }])
        self.assertEqual(ctx.exception.code, "FORBIDDEN_PARAMETER")

    def test_ask_still_required_and_single_use_and_verify(self):
        goal = process_goal(
            "click DOOM_TEST_BUTTON",
            context={"owner_id": "alice", "session_id": "ask-sess", "computer_session_id": "cs-1"},
        ).goal
        proposal = plan_goal(goal, {"structured_targets": [BTN], "observation_hash": "e" * 64})
        ident = _ident()
        pending = execute_plan(proposal.plan, identity=ident, authorized_plan_hash="")
        self.assertEqual(pending.status, ExecutionStatus.APPROVAL_REQUIRED)
        spy = MagicMock(return_value="SUCCESS")
        vspy = MagicMock(return_value="NOT_VERIFIED")
        done = exec_plan(
            proposal.plan,
            adapters={"computer": spy, "verification": vspy},
            authorized_plan_hash=proposal.plan.plan_hash,
        )
        self.assertEqual(done.status, ExecutionStatus.NOT_VERIFIED)
        from orchestration.authorization import claim_authorization, stash_pending_plan
        with patch("orchestration.authorization.verified_computer_session_id", return_value=("cs-1", "OK")):
            pid, stash_code = stash_pending_plan(proposal.plan, ident)
            self.assertEqual(stash_code, "OK")
            first, first_code = claim_authorization(pid, ident)
            again, code = claim_authorization(pid, ident)
        self.assertEqual(first_code, "OK")
        self.assertIsNotNone(first)
        self.assertIsNone(again)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_CONSUMED.value)


class TestActionRevalidation(unittest.TestCase):
    def setUp(self):
        _caps_on()
        os.environ["PROACTIVE_COMPUTER_TYPE_ENABLED"] = "false"

    def test_click_revalidates_bound_session_capture(self):
        obs = ComputerObservation(
            owner_id="alice", capability_id=CAPABILITY_ID, hwnd=42, pid=1001,
            exe_path_norm=r"c:\python.exe", window_class="TkTopLevel",
            schema_version=SCHEMA_VERSION,
        )
        obs.observation_hash = observation_hash(obs.as_authoritative())
        req = ComputerActionRequest(
            action_id="a1", action_type=ActionType.CLICK,
            target=TargetIdentity(automation_id="doom_test_button", runtime_id="1.2.3", control_type="Button", name="DOOM_TEST_BUTTON"),
            owner_id="alice", session_id="cs-1",
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash=obs.observation_hash,
        )
        with patch("proactive.computer.actions.kernel._cost_ok", return_value=True), \
             patch("proactive.computer.actions.kernel._emergency_stop", return_value=False), \
             patch("proactive.computer.actions.kernel.capture_observation", return_value=(obs, "OK")) as cap:
            out = execute_computer_action(
                req,
                adapter=MemoryUiaAdapter(ActionNode(
                    automation_id="root", runtime_id="1", control_type="50032",
                    children=[ActionNode(
                        automation_id="doom_test_button", runtime_id="1.2.3",
                        control_type="50000", name="DOOM_TEST_BUTTON", patterns=(PATTERN_INVOKE,),
                    )],
                )),
                capture_after=False,
            )
        cap.assert_called()
        self.assertEqual(cap.call_args.kwargs.get("computer_session_id"), "cs-1")
        self.assertEqual(out.status, Status.SUCCESS)

    def test_identity_change_fails_closed(self):
        req = ComputerActionRequest(
            action_id="a2", action_type=ActionType.CLICK,
            target=TargetIdentity(automation_id="doom_test_button", runtime_id="1", control_type="Button"),
            owner_id="alice", session_id="cs-1",
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash="f" * 64,
        )
        with patch("proactive.computer.actions.kernel._cost_ok", return_value=True), \
             patch("proactive.computer.actions.kernel._emergency_stop", return_value=False), \
             patch("proactive.computer.actions.kernel.capture_observation", return_value=(None, "PID_MISMATCH")):
            out = execute_computer_action(req, capture_after=False)
        self.assertEqual(out.status, Status.PRECONDITION_FAILED)

    def test_stale_hash_still_enforced(self):
        obs = ComputerObservation(
            owner_id="alice", capability_id=CAPABILITY_ID, hwnd=42, pid=1001,
            exe_path_norm=r"c:\python.exe", window_class="TkTopLevel",
            schema_version=SCHEMA_VERSION,
        )
        obs.observation_hash = observation_hash(obs.as_authoritative())
        req = ComputerActionRequest(
            action_id="a3", action_type=ActionType.CLICK,
            target=TargetIdentity(automation_id="x", runtime_id="1", control_type="Button"),
            owner_id="alice", session_id="cs-1",
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash="0" * 64,
        )
        out = execute_computer_action(req, current_observation=obs, capture_after=False)
        self.assertEqual(out.status, Status.PRECONDITION_FAILED)
        self.assertEqual(out.error_code, "STALE_OBSERVATION_HASH")

    def test_validate_bound_identity_mismatch(self):
        live = _win(pid=2)
        ident, code = validate_bound_identity(_sess(bound=True), identity_fn=lambda hwnd, **k: live)
        self.assertIsNone(ident)
        self.assertEqual(code, "PID_MISMATCH")
        self.assertTrue(identities_match(_sess(bound=True), _win()))


class TestSourceContracts(unittest.TestCase):
    def test_read_hwnd_never_foreground(self):
        src = (ROOT / "proactive" / "computer" / "drivers" / "win32_id.py").read_text(encoding="utf-8")
        fn = src.split("def read_hwnd_win32")[1].split("def read_foreground_win32")[0]
        self.assertNotIn("user32.GetForegroundWindow(", fn)
        self.assertIn("Does not call GetForegroundWindow", fn)
        uia = (ROOT / "proactive" / "computer" / "drivers" / "uia_win.py").read_text(encoding="utf-8")
        read_com = uia.split("def _read_com")[1].split("def read_foreground_uia_meta")[0]
        self.assertIn("create_uia_client", read_com)
        planner = (ROOT / "orchestration" / "goal" / "planner.py").read_text(encoding="utf-8")
        self.assertIn("_CLICK_NAME", planner)
        tree = ast.parse(planner)
        self.assertTrue(any(isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_CLICK_NAME" for t in getattr(n, "targets", [])) for n in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main()
