"""Trusted ASK-session → ExecutionIdentity tests. No real computer actions."""
from __future__ import annotations

import ast
import os
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")

from dashboard.ask_session import create_session, hash_session_token
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.executor_errors import ExecutionStatus
from orchestration.identity import identity_from_ask_session_row, session_lifecycle
from proactive.store import proactive_store
from core.orchestrator import DOOMCore

ROOT = Path(__file__).resolve().parent
IDENT = ROOT / "orchestration" / "identity.py"
DASH = ROOT / "dashboard" / "server.py"


def _v8_on():
    os.environ["PROACTIVE_V8_ENABLED"] = "true"


def _v8_off():
    os.environ["PROACTIVE_V8_ENABLED"] = "false"


def _row_from_create(owner: str):
    raw, csrf, exp = create_session(owner)
    if not raw:
        return None, "", ""
    row = proactive_store.get_ask_session(hash_session_token(raw))
    return row, raw, csrf


def _cog_state(text="legacy-ok"):
    state = MagicMock()
    state.final_response = text
    state.observations = []
    state.telemetry.total_cognitive_ms = 0
    state.telemetry.understanding_ms = 0
    state.telemetry.reasoning_ms = 0
    state.telemetry.decision_ms = 0
    state.telemetry.planning_ms = 0
    state.telemetry.execution_ms = 0
    state.telemetry.verification_ms = 0
    return state


class TestV8TrustedSession(unittest.TestCase):
    def setUp(self):
        _v8_off()
        self.core = DOOMCore()

    def tearDown(self):
        _v8_off()

    def test_session_create_unique_and_owner_bound(self):
        a, raw_a, csrf_a = _row_from_create("alice")
        b, raw_b, csrf_b = _row_from_create("bob")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertNotEqual(raw_a, raw_b)
        self.assertNotEqual(a["session_id_hash"], b["session_id_hash"])
        self.assertNotEqual(a["session_id_hash"], raw_a)
        ident, st = identity_from_ask_session_row(a)
        self.assertEqual(st, "OK")
        self.assertEqual(ident.owner_id, "alice")
        self.assertEqual(ident.session_id, a["session_id_hash"])
        self.assertEqual(ident.computer_session_id, "")
        self.assertNotEqual(ident.session_id, raw_a)
        self.assertNotEqual(ident.session_id, csrf_a)

    def test_expiration_and_revocation(self):
        row, raw, _ = _row_from_create("alice")
        self.assertEqual(session_lifecycle(row), "ACTIVE")
        expired = dict(row)
        expired["expires_at"] = time.time() - 10
        ident, st = identity_from_ask_session_row(expired)
        self.assertIsNone(ident)
        self.assertEqual(st, ExecutionStatus.SESSION_UNAVAILABLE.value)
        revoked = dict(row)
        revoked["revoked_at"] = time.time()
        ident2, st2 = identity_from_ask_session_row(revoked)
        self.assertIsNone(ident2)
        self.assertEqual(st2, ExecutionStatus.SESSION_UNAVAILABLE.value)
        self.assertIsNone(identity_from_ask_session_row(None)[0])

    def test_invalid_and_wrong_binding_rejected(self):
        ident, st = identity_from_ask_session_row({"owner_id": "", "session_id_hash": "x" * 64, "expires_at": time.time() + 100})
        self.assertIsNone(ident)
        row, _, _ = _row_from_create("alice")
        ident_ok, _ = identity_from_ask_session_row(row)
        other = ExecutionIdentity(owner_id="bob", session_id=ident_ok.session_id)
        _v8_on()
        from orchestration.goal.kernel import process_goal
        from orchestration.goal.planner import plan_goal
        goal = process_goal("hello", context={"owner_id": "alice", "session_id": ident_ok.session_id}).goal
        plan = plan_goal(goal).plan
        out = execute_plan(plan, identity=other)
        self.assertEqual(out.status, ExecutionStatus.SESSION_UNAVAILABLE)

    def test_user_controlled_fields_cannot_become_identity(self):
        ident, st = identity_from_ask_session_row({
            "owner_id": "attacker",
            "session_id_hash": "",
            "expires_at": time.time() + 99,
            "csrf_token": "x",
        })
        self.assertIsNone(ident)
        _v8_on()
        out = self.core.process_request(
            "hello",
            context={"owner_id": "alice", "session_id": "sess"},
        )
        self.assertIn(ExecutionStatus.IDENTITY_REQUIRED.value, out)

    def test_context_goalspec_plan_task_ledger_memory_audit_not_identity(self):
        _v8_on()
        for ctx in (
            {"owner_id": "alice", "session_id": "s"},
            {"goal_hash": "h" * 64},
            {"plan_hash": "h" * 64},
            {"task_id": "t1"},
            {"ledger": {"state": "READY"}},
            {"memory": {"owner_id": "alice"}},
            {"audit": {"owner_id": "alice", "approved": True}},
        ):
            out = self.core.process_request("hello", context=ctx)
            self.assertIn(ExecutionStatus.IDENTITY_REQUIRED.value, out)

    def test_missing_identity_still_required(self):
        _v8_on()
        with patch.object(self.core.cognition, "process", return_value=_cog_state()) as proc:
            out = self.core.process_request("hello")
        proc.assert_not_called()
        self.assertIn(ExecutionStatus.IDENTITY_REQUIRED.value, out)

    def test_valid_session_reaches_process_request(self):
        row, raw, _ = _row_from_create("alice")
        ident, st = identity_from_ask_session_row(row)
        self.assertEqual(st, "OK")
        _v8_on()
        with patch("orchestration.production.execute_plan", wraps=execute_plan) as spy:
            out = self.core.process_request("hello", identity=ident)
        spy.assert_called_once()
        self.assertIs(spy.call_args.kwargs["identity"], ident)
        self.assertEqual(spy.call_args.kwargs["identity"].owner_id, "alice")
        self.assertEqual(spy.call_args.kwargs["identity"].session_id, row["session_id_hash"])
        self.assertIn(ExecutionStatus.SUCCESS.value, out)

    def test_computer_session_not_fabricated(self):
        row, _, _ = _row_from_create("alice")
        ident, _ = identity_from_ask_session_row(row)
        self.assertEqual(ident.computer_session_id, "")
        _v8_on()
        out = self.core.process_request("open notepad", identity=ident)
        self.assertTrue("CAPABILITY_UNAVAILABLE" in out or "PLANNING_UNAVAILABLE" in out)

    def test_valid_computer_session_binds(self):
        row, _, _ = _row_from_create("alice")
        fake = {"session_id": "cs-real-1", "owner_id": "alice", "status": "OBSERVING"}
        with patch("proactive.computer.session.get_session", return_value=fake):
            ident, st = identity_from_ask_session_row(row, computer_session_id="cs-real-1")
        self.assertEqual(st, "OK")
        self.assertEqual(ident.computer_session_id, "cs-real-1")
        with patch("proactive.computer.session.get_session", return_value=None):
            ident2, st2 = identity_from_ask_session_row(row, computer_session_id="forged-cs")
        self.assertIsNone(ident2)
        self.assertEqual(st2, ExecutionStatus.SESSION_UNAVAILABLE.value)

    def test_v8_off_preserves_cognition(self):
        _v8_off()
        with patch.object(self.core.cognition, "process", return_value=_cog_state("legacy-ok")) as proc:
            out = self.core.process_request("hello")
        proc.assert_called_once()
        self.assertIn("legacy-ok", out)

    def test_v8_on_no_legacy_fallback(self):
        _v8_on()
        with patch.object(self.core.cognition, "process") as proc:
            self.core.process_request("open notepad")
            row, _, _ = _row_from_create("alice")
            ident, _ = identity_from_ask_session_row(row)
            self.core.process_request("open notepad", identity=ident)
        proc.assert_not_called()

    def test_identity_does_not_authorize_plan(self):
        row, _, _ = _row_from_create("alice")
        fake = {"session_id": "cs1", "owner_id": "alice", "status": "OBSERVING"}
        with patch("proactive.computer.session.get_session", return_value=fake):
            ident, st = identity_from_ask_session_row(row, computer_session_id="cs1")
        self.assertEqual(st, "OK")
        _v8_on()
        from dataclasses import replace
        from orchestration.goal.kernel import process_goal
        from orchestration.goal.plan_validator import build_goal_plan
        goal = process_goal("hello", context={
            "owner_id": ident.owner_id,
            "session_id": ident.session_id,
            "computer_session_id": "cs1",
        }).goal
        plan = build_goal_plan(goal, [{
            "step_id": "s1", "capability_id": "computer", "action": "CLICK",
            "parameters": {"automation_id": "btn", "session_id": "cs1"},
            "dependencies": (), "verification_required": False,
            "verification_type": "", "retry_count": 0, "timeout_ms": 8000,
        }])
        result = execute_plan(plan, identity=ident, authorized_plan_hash="")
        self.assertEqual(result.status, ExecutionStatus.APPROVAL_REQUIRED)
        result2 = execute_plan(replace(plan, approved=True), identity=ident, authorized_plan_hash="deadbeef")
        self.assertEqual(result2.status, ExecutionStatus.APPROVAL_REQUIRED)

    def test_expired_revoked_cannot_execute(self):
        row, _, _ = _row_from_create("alice")
        _v8_on()
        expired, _ = identity_from_ask_session_row({**row, "expires_at": 1})
        self.assertIsNone(expired)
        revoked, _ = identity_from_ask_session_row({**row, "revoked_at": 1})
        self.assertIsNone(revoked)

    def test_other_owner_session_cannot_execute(self):
        alice, _, _ = _row_from_create("alice")
        bob, _, _ = _row_from_create("bob")
        ia, _ = identity_from_ask_session_row(alice)
        ib, _ = identity_from_ask_session_row(bob)
        _v8_on()
        from orchestration.goal.kernel import process_goal
        from orchestration.goal.planner import plan_goal
        goal = process_goal("hello", context={"owner_id": ia.owner_id, "session_id": ia.session_id}).goal
        plan = plan_goal(goal).plan
        out = execute_plan(plan, identity=ib)
        self.assertEqual(out.status, ExecutionStatus.SESSION_UNAVAILABLE)

    def test_no_secret_in_public_identity_or_exceptions(self):
        row, raw, csrf = _row_from_create("alice")
        ident, _ = identity_from_ask_session_row(row)
        pub = str(ident.as_public())
        self.assertNotIn(raw, pub)
        self.assertNotIn(csrf, pub)
        self.assertNotIn("cookie", pub.lower())

    def test_production_and_dashboard_do_not_trust_body_owner(self):
        src = DASH.read_text(encoding="utf-8")
        self.assertIn("/api/proactive/v8/command", src)
        self.assertIn("identity_from_ask_session_row", src)
        fn = src.split("async def v8_authenticated_command")[1].split("async def ")[0]
        self.assertNotIn('body.get("owner_id")', fn)
        self.assertNotIn('body.get("session_id")', fn)
        self.assertNotIn('headers.get("X-Owner-ID")', fn)
        self.assertNotIn('headers.get("X-Session-ID")', fn)
        self.assertNotIn("request.query_params", fn)
        ident_src = IDENT.read_text(encoding="utf-8")
        self.assertNotIn("from proactive.config import", ident_src)
        self.assertNotIn("DOOM_OWNER_ID", ident_src)
        self.assertNotIn("os.environ", ident_src)
        self.assertNotIn("use_test_execution_hooks", ident_src)

    def test_static_identity_module(self):
        tree = ast.parse(IDENT.read_text(encoding="utf-8"))
        forbidden = {"subprocess", "importlib", "ctypes", "eval", "exec"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], forbidden)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("eval", "exec"))

    def test_session_data_bounded(self):
        row = {
            "owner_id": "a" * 80,
            "session_id_hash": "b" * 80,
            "expires_at": time.time() + 99,
        }
        ident, st = identity_from_ask_session_row(row)
        self.assertEqual(st, "OK")
        self.assertEqual(len(ident.owner_id), 64)
        self.assertEqual(len(ident.session_id), 64)

    def test_hooks_not_in_identity(self):
        import orchestration
        self.assertNotIn("use_test_execution_hooks", orchestration.__all__)


if __name__ == "__main__":
    unittest.main()
