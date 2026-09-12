"""V8 computer verification flag and timeout clamp. No click/type."""
from __future__ import annotations

import os
import unittest
import uuid
from types import SimpleNamespace

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_VERIFICATION_ENABLED", "false")

from orchestration.executor import _verification_spec_timeout_ms
from proactive.computer.policy import verification_allowed, verify_timeout_ms
from proactive.computer.verify.kernel import execute_verification
from proactive.computer.verify.types import (
    VerificationRequest,
    VerificationSpec,
    VerificationStatus,
)


def _computer_spec(timeout_ms: int) -> VerificationSpec:
    return VerificationSpec(
        verification_id=str(uuid.uuid4()),
        capability="computer",
        verification_type="TARGET_STATE_MATCH",
        expected=(
            ("name", "DOOM_TEST_BUTTON"),
            ("control_type", "Button"),
            ("runtime_id", "1.1"),
            ("expected_outcome", "OK"),
        ),
        timeout_ms=int(timeout_ms),
        max_attempts=1,
        owner_id="alice",
    )


def _obs_ok():
    return SimpleNamespace(
        observation_hash="a" * 64,
        uia_automation_id="",
        uia_runtime_id="root",
        uia_control_type="50032",
        outcome_code="OK",
        hwnd=1,
        title_advisory="DOOM Safe Test Window",
    )


class TestV8VerificationTimeoutAndFlag(unittest.TestCase):
    def tearDown(self):
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "false"
        os.environ["PROACTIVE_COMPUTER_VERIFICATION_ENABLED"] = "false"
        os.environ.pop("PROACTIVE_COMPUTER_VERIFY_TIMEOUT_MS", None)

    def test_verification_blocked_when_flag_false(self):
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_VERIFICATION_ENABLED"] = "false"
        self.assertFalse(verification_allowed())
        out = execute_verification(
            VerificationRequest(spec=_computer_spec(2000), action_id="s1"),
            computer_fn=lambda owner, **kw: (_obs_ok(), "OK", ()),
            cost_ok_fn=lambda _c: True,
            emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(out.status, VerificationStatus.VERIFICATION_BLOCKED)
        self.assertEqual(out.failure_code, "VERIFICATION_DISABLED")

    def test_verification_allowed_when_flag_true(self):
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_VERIFICATION_ENABLED"] = "true"
        self.assertTrue(verification_allowed())
        target = {
            "automation_id": "",
            "runtime_id": "1.1",
            "control_type": "Button",
            "name": "DOOM_TEST_BUTTON",
        }
        out = execute_verification(
            VerificationRequest(spec=_computer_spec(2000), action_id="s1"),
            computer_fn=lambda owner, **kw: (_obs_ok(), "OK", (target,)),
            cost_ok_fn=lambda _c: True,
            emergency_stop_fn=lambda o, s: False,
        )
        self.assertNotEqual(out.status, VerificationStatus.VERIFICATION_BLOCKED)
        self.assertNotEqual(out.status, VerificationStatus.INVALID_VERIFICATION_SPEC)
        self.assertEqual(out.status, VerificationStatus.VERIFIED)

    def test_planner_8000_ms_is_clamped_to_verify_cap(self):
        os.environ["PROACTIVE_COMPUTER_VERIFY_TIMEOUT_MS"] = "2000"
        cap = verify_timeout_ms()
        self.assertEqual(cap, 2000)
        clamped = _verification_spec_timeout_ms(8000)
        self.assertEqual(clamped, 2000)
        self.assertLessEqual(clamped, cap)
        unclamped = execute_verification(
            VerificationRequest(spec=_computer_spec(8000), action_id="s1"),
            computer_fn=lambda owner, **kw: (_obs_ok(), "OK", ()),
            cost_ok_fn=lambda _c: True,
            emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(unclamped.status, VerificationStatus.INVALID_VERIFICATION_SPEC)
        self.assertEqual(unclamped.failure_code, "INVALID_TIMEOUT")
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_VERIFICATION_ENABLED"] = "true"
        target = {
            "runtime_id": "1.1",
            "control_type": "Button",
            "name": "DOOM_TEST_BUTTON",
        }
        clamped_run = execute_verification(
            VerificationRequest(spec=_computer_spec(clamped), action_id="s1"),
            computer_fn=lambda owner, **kw: (_obs_ok(), "OK", (target,)),
            cost_ok_fn=lambda _c: True,
            emergency_stop_fn=lambda o, s: False,
        )
        self.assertNotEqual(clamped_run.status, VerificationStatus.INVALID_VERIFICATION_SPEC)
        self.assertEqual(clamped_run.status, VerificationStatus.VERIFIED)

    def test_clamp_never_exceeds_cap_or_drops_below_50(self):
        os.environ["PROACTIVE_COMPUTER_VERIFY_TIMEOUT_MS"] = "2000"
        self.assertEqual(_verification_spec_timeout_ms(1), 50)
        self.assertEqual(_verification_spec_timeout_ms(2000), 2000)
        self.assertEqual(_verification_spec_timeout_ms(30000), 2000)

    def test_fail_closed_without_matching_target(self):
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_VERIFICATION_ENABLED"] = "true"
        out = execute_verification(
            VerificationRequest(spec=_computer_spec(2000), action_id="s1"),
            computer_fn=lambda owner, **kw: (_obs_ok(), "OK", ()),
            cost_ok_fn=lambda _c: True,
            emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(out.status, VerificationStatus.NOT_VERIFIED)
        self.assertEqual(out.failure_code, "STATE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
