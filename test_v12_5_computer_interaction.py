#!/usr/bin/env python
"""DOOM V12.5 — Controlled Computer Interaction tests (simulated UI; no real desktop)."""

import ast
import os
import sys
import time
import unittest
from unittest.mock import patch

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.cost_guard.types import CostClass, CostDecision, CostDecisionAction, CostReason, ResourceRequest, ResourceType  # noqa: E402
from core.v12.computer_interaction import (  # noqa: E402
    CATALOG, ComputerActionKind, ComputerIntent, ComputerInteractionController, ComputerStatus,
    SimulatedUIBackend, UIElement, UIRole,
)
from core.v12.integrations.framework import IntegrationIdentity  # noqa: E402

OWNER, SESSION = "pc_owner", "pc_sess"
ME = IntegrationIdentity(OWNER, SESSION)


def click_intent(**kw):
    base = dict(owner_id=OWNER, session_id=SESSION, kind=ComputerActionKind.CLICK, target_name="DOOM_TEST_BUTTON",
                target_role=UIRole.BUTTON, expect_label="Status", expect_value="CLICKED")
    base.update(kw)
    return ComputerIntent(**base)


def type_intent(text="hello", **kw):
    base = dict(owner_id=OWNER, session_id=SESSION, kind=ComputerActionKind.TYPE, target_name="DOOM_TEST_INPUT",
                target_role=UIRole.INPUT, text=text)
    base.update(kw)
    return ComputerIntent(**base)


class Base(unittest.TestCase):
    def setUp(self):
        self.ui = SimulatedUIBackend()
        self.pc = ComputerInteractionController(self.ui)

    def tearDown(self):
        self.pc.shutdown()

    def approved(self, intent):
        plan, res = self.pc.plan(intent)
        self.assertEqual(res.status, ComputerStatus.SUCCESS, res)
        pending = self.pc.execute(plan, ME)
        self.assertEqual(pending.status, ComputerStatus.APPROVAL_REQUIRED)
        authorized, code = self.pc.approvals.claim(pending.pending_id, ME)
        self.assertEqual(code, "OK")
        return plan, authorized


class TestObserveAndRead(Base):

    def test_observe_needs_no_approval(self):
        plan, _ = self.pc.plan(ComputerIntent(OWNER, SESSION, ComputerActionKind.OBSERVE))
        r = self.pc.execute(plan, ME)
        self.assertTrue(r.ok)
        self.assertIn(("button", "DOOM_TEST_BUTTON"), r.output["elements"])

    def test_read_value_of_safe_field(self):
        self.ui.input_value = "draft"
        plan, _ = self.pc.plan(ComputerIntent(OWNER, SESSION, ComputerActionKind.READ_VALUE, "DOOM_TEST_INPUT"))
        self.assertEqual(self.pc.execute(plan, ME).output, {"value": "draft"})

    def test_password_fields_are_never_read_or_typed(self):
        for intent in (ComputerIntent(OWNER, SESSION, ComputerActionKind.READ_VALUE, "Password"),
                       type_intent(target_name="Password")):
            plan, res = self.pc.plan(intent)
            self.assertIsNone(plan)
            self.assertEqual(res.status, ComputerStatus.SENSITIVE_TARGET)
        self.ui.extra = [UIElement("x1", UIRole.INPUT, "API key")]
        self.assertEqual(self.pc.plan(type_intent(target_name="API key"))[1].status, ComputerStatus.SENSITIVE_TARGET)


class TestAuthorizedActions(Base):

    def test_click_full_flow(self):
        plan, authorized = self.approved(click_intent())
        self.assertEqual(self.ui.clicks, 0, "nothing happens before approval")
        done = self.pc.execute(plan, ME, authorized)
        self.assertTrue(done.ok, done)
        self.assertEqual(self.ui.status, "CLICKED")
        self.assertEqual(done.output, {"Status": "CLICKED"})
        replay = self.pc.execute(plan, ME, authorized)
        self.assertEqual(replay.status, ComputerStatus.AUTHORIZATION_INVALID)
        self.assertEqual(self.ui.clicks, 1)

    def test_cross_plan_approval_rejected(self):
        plan_a, authorized_a = self.approved(type_intent("alpha"))
        plan_b, _ = self.pc.plan(type_intent("bravo"))
        # An approval for plan A cannot authorize a different plan B.
        self.assertEqual(self.pc.execute(plan_b, ME, authorized_a).status, ComputerStatus.AUTHORIZATION_INVALID)
        self.assertEqual(self.ui.input_value, "")
        # Plan A still runs with its own approval exactly once.
        self.assertTrue(self.pc.execute(plan_a, ME, authorized_a).ok)
        self.assertEqual(self.ui.input_value, "alpha")

    def test_hash_knowledge_is_not_authorization(self):
        plan, _ = self.pc.plan(click_intent())
        r = self.pc.execute(plan, ME, plan.as_request().request_hash())
        self.assertEqual(r.status, ComputerStatus.AUTHORIZATION_INVALID)
        self.assertEqual(self.ui.clicks, 0)

    def test_type_full_flow(self):
        plan, authorized = self.approved(type_intent("quarterly report"))
        r = self.pc.execute(plan, ME, authorized)
        self.assertTrue(r.ok)
        self.assertEqual(self.ui.input_value, "quarterly report")

    def test_type_verification_failure_rolls_back(self):
        self.ui.input_value = "original"
        plan, authorized = self.approved(type_intent("replacement"))
        self.ui.type_has_effect = False
        r = self.pc.execute(plan, ME, authorized)
        self.assertEqual(r.status, ComputerStatus.VERIFICATION_FAILED)
        self.assertTrue(r.rolled_back)
        self.assertEqual(self.ui.input_value, "original")

    def test_click_verification_failure_is_reported_not_retried(self):
        plan, authorized = self.approved(click_intent())
        self.ui.click_has_effect = False
        r = self.pc.execute(plan, ME, authorized)
        self.assertEqual(r.status, ComputerStatus.VERIFICATION_FAILED)
        self.assertFalse(r.rolled_back)
        self.assertEqual(self.ui.clicks, 1)


class TestRefusals(Base):

    def test_browser_windows_are_refused(self):
        pc = ComputerInteractionController(SimulatedUIBackend(app_kind="browser"))
        try:
            self.assertEqual(pc.plan(click_intent())[1].status, ComputerStatus.BROWSER_BLOCKED)
        finally:
            pc.shutdown()

    def test_targets_must_be_unique_visible_enabled(self):
        self.assertEqual(self.pc.plan(click_intent(target_name="Nope"))[1].status, ComputerStatus.TARGET_NOT_FOUND)
        self.ui.extra = [UIElement("btn2", UIRole.BUTTON, "DOOM_TEST_BUTTON")]
        self.assertEqual(self.pc.plan(click_intent())[1].status, ComputerStatus.TARGET_AMBIGUOUS)
        self.ui.extra = [UIElement("h1", UIRole.BUTTON, "Hidden", visible=False),
                         UIElement("d1", UIRole.BUTTON, "Disabled", enabled=False)]
        for name in ("Hidden", "Disabled"):
            self.assertEqual(self.pc.plan(click_intent(target_name=name))[1].status,
                             ComputerStatus.TARGET_NOT_INTERACTABLE)

    def test_invalid_requests(self):
        cases = [
            type_intent("x" * 300),
            type_intent("line\nbreak"),
            type_intent(""),
            click_intent(expect_label=""),
            click_intent(target_name="DOOM_TEST_INPUT", target_role=UIRole.INPUT),
            type_intent(target_name="DOOM_TEST_BUTTON", target_role=UIRole.BUTTON),
            ComputerIntent("", SESSION, ComputerActionKind.CLICK),
        ]
        for intent in cases:
            self.assertEqual(self.pc.plan(intent)[1].status, ComputerStatus.INVALID_REQUEST, intent)

    def test_stale_observation_blocks_action(self):
        plan, authorized = self.approved(click_intent())
        self.ui.input_value = "someone typed meanwhile"
        r = self.pc.execute(plan, ME, authorized)
        self.assertEqual(r.status, ComputerStatus.STALE_OBSERVATION)
        self.assertEqual(self.ui.clicks, 0)

    def test_emergency_stop(self):
        plan, authorized = self.approved(click_intent())
        self.pc.emergency_stop()
        self.assertEqual(self.pc.execute(plan, ME, authorized).status, ComputerStatus.EMERGENCY_STOPPED)
        self.assertEqual(self.pc.plan(click_intent())[1].status, ComputerStatus.EMERGENCY_STOPPED)
        self.assertEqual(self.ui.clicks, 0)
        self.pc.clear_emergency_stop()
        self.assertTrue(self.pc.execute(plan, ME, authorized).ok)

    def test_owner_and_session_mismatch(self):
        plan, _ = self.pc.plan(click_intent())
        for ident in (IntegrationIdentity("intruder", SESSION), IntegrationIdentity(OWNER, "other"), None):
            self.assertEqual(self.pc.execute(plan, ident).status, ComputerStatus.OWNER_MISMATCH)

    def test_cost_guard_block(self):
        blocked = CostDecision(CostDecisionAction.BLOCK, CostClass.UNKNOWN, CostReason.UNKNOWN_PROVIDER_BLOCKED,
                               ResourceRequest(resource_type=ResourceType.VISION, provider="local_uia"))
        plan, _ = self.pc.plan(click_intent())
        with patch.object(self.pc.cost_guard, "decision", return_value=blocked):
            self.assertEqual(self.pc.execute(plan, ME).status, ComputerStatus.COST_BLOCKED)
        self.assertEqual(self.ui.clicks, 0)

    def test_timeout(self):
        plan, authorized = self.approved(click_intent())
        self.ui.delay_s = 3.5
        t0 = time.time()
        r = self.pc.execute(plan, ME, authorized)
        self.assertEqual(r.status, ComputerStatus.TIMEOUT)
        self.assertLess(time.time() - t0, 3.4)


class TestDeclarationsAndAudit(Base):

    def test_every_action_is_fully_declared(self):
        for kind in ComputerActionKind:
            spec = CATALOG[kind]
            for field_name in ("capability", "risk", "expected_result", "verification", "on_failure"):
                self.assertTrue(getattr(spec, field_name), (kind, field_name))
            self.assertGreater(spec.timeout_ms, 0)
            self.assertEqual(spec.requires_authorization, spec.risk != "LOW")

    def test_audit_is_owner_scoped_and_bounded(self):
        plan, _ = self.pc.plan(ComputerIntent(OWNER, SESSION, ComputerActionKind.OBSERVE))
        for _ in range(300):
            self.pc.execute(plan, ME)
        log = self.pc.audit_log(OWNER)
        self.assertLessEqual(len(log), 256)
        self.assertEqual(self.pc.audit_log("intruder"), [])

    def test_no_real_ui_automation_imports(self):
        tree = ast.parse(open(os.path.join(PROJECT_ROOT, "core", "v12", "computer_interaction.py"),
                              encoding="utf-8").read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        for banned in ("pyautogui", "pywinauto", "comtypes", "selenium", "playwright", "uiautomation", "win32api"):
            self.assertNotIn(banned, names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
