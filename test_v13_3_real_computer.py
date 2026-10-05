#!/usr/bin/env python
"""DOOM V13.3 — Safe real computer interaction tests.

Gating tests always run. Live tests drive ONLY the repository's harmless Safe Test Window
through Windows UI Automation semantic patterns, and run only when explicitly requested:

    set DOOM_V13_LIVE_UI=1   (the live tests also set DOOM_V13_REAL_COMPUTER=1 themselves)
"""

import os
import sys
import unittest
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.v12.computer_interaction import (  # noqa: E402
    ComputerActionKind, ComputerIntent, ComputerInteractionController, ComputerStatus, UIRole,
)
from core.v12.integrations.framework import IntegrationIdentity  # noqa: E402
from core.v13.real_computer import (  # noqa: E402
    AllowedWindow, RealComputerGated, RealUIABackend, SafeTestWindowProcess, real_computer_enabled,
)

OWNER, SESSION = "rc_owner", "rc_sess"
ME = IntegrationIdentity(OWNER, SESSION)
LIVE = os.getenv("DOOM_V13_LIVE_UI", "") == "1" and sys.platform == "win32"


class TestGating(unittest.TestCase):

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {"DOOM_V13_REAL_COMPUTER": ""}):
            self.assertFalse(real_computer_enabled())
            with self.assertRaises(RealComputerGated):
                RealUIABackend()

    def test_non_allowlisted_or_closed_window_is_unreachable(self):
        with patch.dict(os.environ, {"DOOM_V13_REAL_COMPUTER": "1"}):
            backend = RealUIABackend(AllowedWindow("No Such Window Title 9c1f", "NoSuchClass9c1f"))
            with self.assertRaises(RealComputerGated):
                backend.observe()
            controller = ComputerInteractionController(backend)
            try:
                plan, res = controller.plan(ComputerIntent(OWNER, SESSION, ComputerActionKind.OBSERVE))
                self.assertIsNone(plan)
                self.assertEqual(res.status, ComputerStatus.ACTION_FAILED)
            finally:
                controller.shutdown()

    def test_no_mouse_keyboard_or_shell_in_backend(self):
        import ast
        with open(os.path.join(PROJECT_ROOT, "core", "v13", "real_computer.py"), encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        for banned in ("pyautogui", "pywinauto", "selenium", "playwright", "keyboard", "mouse"):
            self.assertNotIn(banned, names)
        for call in ("SendInput", "mouse_event", "keybd_event", "SetCursorPos", "SetForegroundWindow", "shell=True"):
            self.assertNotIn(call, src)


@unittest.skipUnless(LIVE, "live UI test: set DOOM_V13_LIVE_UI=1 to drive the Safe Test Window")
class TestLiveSafeTestWindow(unittest.TestCase):

    def setUp(self):
        self._env = patch.dict(os.environ, {"DOOM_V13_REAL_COMPUTER": "1"})
        self._env.start()
        self.window = SafeTestWindowProcess(PROJECT_ROOT).__enter__()
        self.pc = ComputerInteractionController(RealUIABackend())

    def tearDown(self):
        self.pc.shutdown()
        self.window.__exit__(None, None, None)
        self._env.stop()

    def approved(self, intent):
        plan, res = self.pc.plan(intent)
        self.assertEqual(res.status, ComputerStatus.SUCCESS, res)
        pending = self.pc.execute(plan, ME)
        self.assertEqual(pending.status, ComputerStatus.APPROVAL_REQUIRED)
        authorized, code = self.pc.approvals.claim(pending.pending_id, ME)
        self.assertEqual(code, "OK")
        return plan, authorized

    def test_observe_shows_only_application_controls(self):
        plan, _ = self.pc.plan(ComputerIntent(OWNER, SESSION, ComputerActionKind.OBSERVE))
        out = self.pc.execute(plan, ME).output["elements"]
        self.assertIn(("button", "DOOM_TEST_BUTTON"), out)
        self.assertIn(("input", "DOOM_TEST_INPUT"), out)
        self.assertNotIn(("button", "Close"), out, "window chrome is excluded")

    def test_click_ready_to_clicked_exactly_once_with_verification(self):
        snap = self.pc.backend.observe()
        self.assertEqual(next(e for e in snap.elements if e.name == "Status").value, "READY")
        plan, authorized = self.approved(ComputerIntent(
            OWNER, SESSION, ComputerActionKind.CLICK, "DOOM_TEST_BUTTON", UIRole.BUTTON,
            expect_label="Status", expect_value="CLICKED"))
        done = self.pc.execute(plan, ME, authorized)
        self.assertTrue(done.ok, done)
        self.assertEqual(done.output, {"Status": "CLICKED"})
        self.assertEqual(self.pc.execute(plan, ME, authorized).status, ComputerStatus.AUTHORIZATION_INVALID)

    def test_type_into_input_verified(self):
        plan, authorized = self.approved(ComputerIntent(
            OWNER, SESSION, ComputerActionKind.TYPE, "DOOM_TEST_INPUT", UIRole.INPUT, text="hello from DOOM V13"))
        done = self.pc.execute(plan, ME, authorized)
        self.assertTrue(done.ok, done)
        value = next(e for e in self.pc.backend.observe().elements if e.name == "DOOM_TEST_INPUT"
                     and e.role is UIRole.INPUT).value
        self.assertEqual(value, "hello from DOOM V13")

    def test_unapproved_and_stale_plans_never_act(self):
        click = ComputerIntent(OWNER, SESSION, ComputerActionKind.CLICK, "DOOM_TEST_BUTTON", UIRole.BUTTON,
                               expect_label="Status", expect_value="CLICKED")
        plan, _ = self.pc.plan(click)
        self.assertEqual(self.pc.execute(plan, ME, plan.as_request().request_hash()).status,
                         ComputerStatus.AUTHORIZATION_INVALID, "a computed hash is not approval")
        stale_plan, authorized = self.approved(click)
        typed_plan, typed_auth = self.approved(ComputerIntent(
            OWNER, SESSION, ComputerActionKind.TYPE, "DOOM_TEST_INPUT", UIRole.INPUT, text="changed"))
        self.assertTrue(self.pc.execute(typed_plan, ME, typed_auth).ok)
        self.assertEqual(self.pc.execute(stale_plan, ME, authorized).status, ComputerStatus.STALE_OBSERVATION)
        status = next(e for e in self.pc.backend.observe().elements if e.name == "Status").value
        self.assertEqual(status, "READY", "no click happened")

    def test_emergency_stop_blocks_real_actions(self):
        plan, authorized = self.approved(ComputerIntent(
            OWNER, SESSION, ComputerActionKind.CLICK, "DOOM_TEST_BUTTON", UIRole.BUTTON,
            expect_label="Status", expect_value="CLICKED"))
        self.pc.emergency_stop()
        self.assertEqual(self.pc.execute(plan, ME, authorized).status, ComputerStatus.EMERGENCY_STOPPED)
        self.assertEqual(next(e for e in self.pc.backend.observe().elements if e.name == "Status").value, "READY")

    def test_window_chrome_cannot_be_targeted(self):
        plan, res = self.pc.plan(ComputerIntent(OWNER, SESSION, ComputerActionKind.CLICK, "Close", UIRole.BUTTON,
                                                expect_label="Status", expect_value="CLICKED"))
        self.assertIsNone(plan)
        self.assertEqual(res.status, ComputerStatus.TARGET_NOT_FOUND)


if __name__ == "__main__":
    unittest.main(verbosity=2)
