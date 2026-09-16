"""V8.26 Phase 3 tests: Goal Registry recovery + continuity rehydration."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ACT_ENABLED", "false")
os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"

from orchestration.plan.continuity.engine import (
    has_continuity_anchor,
    has_continuity_context,
    has_registry_goal_anchor,
    plan_result_from_registry_snapshot,
    rehydrate_continuity_from_registry_snapshot,
)
from orchestration.plan.continuity.state import recompute_active_step
from orchestration.plan.continuity.types import GoalState, StepState as ContinuityStepState
from orchestration.plan.goal_registry import (
    GoalLifecycle,
    REGISTRY_INACTIVITY_SEC,
    RegistryStatus,
    SCHEMA_VERSION,
    StepState as RegistryStepState,
    archive_goal,
    build_snapshot,
    create_active_goal,
    get_active_goal,
    is_goal_registry_sync_enabled,
    mark_stale,
    recover_active_goal_for_owner,
    reset_goal_registry_for_tests,
    transition_goal,
    use_test_goal_registry_store,
)


def _seed_active(
    owner="alice",
    *,
    title="Learn python",
    plan_title="Plan for: learn python",
    states=None,
    active=1,
    blocker="",
    durable="Plan: learn python\nSteps:\n1. [ ] Step 1",
    last_active_at=None,
    n=3,
):
    if states is None:
        states = tuple(RegistryStepState.PENDING for _ in range(n))
    titles = tuple(f"Step {i}" for i in range(1, n + 1))
    snap, st = build_snapshot(
        owner_id=owner,
        title=title,
        plan_title=plan_title,
        step_titles=titles,
        step_states=states,
        active_step_index=active,
        blocker_summary=blocker,
        durable_view=durable,
        last_active_at=last_active_at if last_active_at is not None else time.time(),
    )
    assert st is RegistryStatus.OK, st
    res = create_active_goal(owner, snap)
    assert res.status is RegistryStatus.OK, res.status
    return res.snapshot


class TestV826GoalRegistryRecovery(unittest.TestCase):
    def setUp(self):
        use_test_goal_registry_store(True)
        reset_goal_registry_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"

    def tearDown(self):
        reset_goal_registry_for_tests()
        use_test_goal_registry_store(False)

    def test_01_flag_off_no_registry_recovery(self):
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"
        _seed_active(owner="bob")
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "false"
        self.assertFalse(is_goal_registry_sync_enabled())
        res = recover_active_goal_for_owner("bob")
        self.assertEqual(res.status, RegistryStatus.UNAVAILABLE)
        self.assertFalse(has_registry_goal_anchor("bob"))

    def test_02_flag_on_no_registry_fallback(self):
        res = recover_active_goal_for_owner("alice")
        self.assertEqual(res.status, RegistryStatus.NOT_FOUND)
        self.assertFalse(has_continuity_context("alice", "sess-x"))

    def test_03_flag_on_active_registry_recovery(self):
        snap = _seed_active()
        res = recover_active_goal_for_owner("alice")
        self.assertEqual(res.status, RegistryStatus.OK)
        self.assertEqual(res.snapshot.goal_id, snap.goal_id)
        self.assertTrue(has_registry_goal_anchor("alice"))
        self.assertTrue(has_continuity_context("alice", "no-session-anchor"))

    def test_04_valid_snapshot_continuity_rehydration(self):
        states = (
            RegistryStepState.COMPLETED,
            RegistryStepState.IN_PROGRESS,
            RegistryStepState.PENDING,
        )
        snap = _seed_active(states=states, active=2)
        cont = rehydrate_continuity_from_registry_snapshot(snap)
        self.assertIsNotNone(cont)
        self.assertEqual(cont.goal_title, "Learn python")
        self.assertEqual(cont.step_records[0].state, ContinuityStepState.COMPLETED)
        self.assertEqual(cont.step_records[1].state, ContinuityStepState.IN_PROGRESS)
        self.assertEqual(cont.active_step_index, 2)
        prior = plan_result_from_registry_snapshot(snap)
        self.assertEqual(len(prior.steps), 3)

    def test_05_invalid_snapshot_fail_closed(self):
        snap = _seed_active()
        # Corrupt via empty titles bypass — recover validates
        with patch(
            "orchestration.plan.goal_registry.validate_snapshot",
            return_value=RegistryStatus.INVALID_SNAPSHOT,
        ):
            res = recover_active_goal_for_owner("alice")
        self.assertEqual(res.status, RegistryStatus.INVALID_SNAPSHOT)

    def test_06_unsupported_schema_fail_closed(self):
        snap = _seed_active()
        bad = snap.__class__(
            **{**snap.__dict__, "schema_version": SCHEMA_VERSION + 9}
        )
        with patch(
            "orchestration.plan.goal_registry.get_active_goal",
            return_value=type("R", (), {"status": RegistryStatus.OK, "snapshot": bad})(),
        ):
            res = recover_active_goal_for_owner("alice")
        self.assertEqual(res.status, RegistryStatus.VERSION_UNSUPPORTED)

    def test_07_owner_mismatch_fail_closed(self):
        _seed_active(owner="alice")
        res = recover_active_goal_for_owner("eve")
        self.assertEqual(res.status, RegistryStatus.NOT_FOUND)
        self.assertFalse(has_registry_goal_anchor("eve"))

    def test_08_missing_owner_fail_closed(self):
        _seed_active()
        res = recover_active_goal_for_owner("")
        self.assertEqual(res.status, RegistryStatus.REJECTED)

    def test_09_completed_no_silent_resume(self):
        states = (
            RegistryStepState.COMPLETED,
            RegistryStepState.COMPLETED,
            RegistryStepState.SKIPPED,
        )
        snap = _seed_active(states=states, active=0)
        done = archive_goal("alice", snap.goal_id, snap.version)
        self.assertEqual(done.status, RegistryStatus.OK)
        self.assertEqual(done.snapshot.lifecycle, GoalLifecycle.COMPLETED)
        res = recover_active_goal_for_owner("alice")
        self.assertEqual(res.status, RegistryStatus.NOT_FOUND)

    def test_10_abandoned_no_silent_resume(self):
        snap = _seed_active()
        ab = transition_goal(
            "alice",
            snap.goal_id,
            GoalLifecycle.ACTIVE,
            GoalLifecycle.ABANDONED,
            "user abandoned",
            snap.version,
        )
        self.assertEqual(ab.status, RegistryStatus.OK)
        res = recover_active_goal_for_owner("alice")
        self.assertEqual(res.status, RegistryStatus.NOT_FOUND)

    def test_11_stale_no_silent_automatic_resume(self):
        snap = _seed_active()
        stale = mark_stale("alice", snap.goal_id, "manual", snap.version)
        self.assertEqual(stale.status, RegistryStatus.OK)
        self.assertEqual(stale.snapshot.lifecycle, GoalLifecycle.STALE)
        res = recover_active_goal_for_owner("alice")
        self.assertEqual(res.status, RegistryStatus.NOT_FOUND)

    def test_12_bounded_six_step_recovery(self):
        states = tuple(RegistryStepState.PENDING for _ in range(6))
        snap = _seed_active(states=states, n=6, active=1)
        cont = rehydrate_continuity_from_registry_snapshot(snap)
        self.assertEqual(len(cont.step_records), 6)

    def test_13_active_step_preserved(self):
        states = (
            RegistryStepState.COMPLETED,
            RegistryStepState.PENDING,
            RegistryStepState.PENDING,
        )
        snap = _seed_active(states=states, active=2)
        cont = rehydrate_continuity_from_registry_snapshot(snap)
        self.assertEqual(cont.active_step_index, 2)

    def test_14_completed_steps_remain(self):
        states = (
            RegistryStepState.COMPLETED,
            RegistryStepState.PENDING,
            RegistryStepState.PENDING,
        )
        snap = _seed_active(states=states, active=2)
        cont = rehydrate_continuity_from_registry_snapshot(snap)
        self.assertEqual(cont.step_records[0].state, ContinuityStepState.COMPLETED)

    def test_15_blocked_steps_remain(self):
        states = (
            RegistryStepState.BLOCKED,
            RegistryStepState.PENDING,
            RegistryStepState.PENDING,
        )
        snap = _seed_active(states=states, active=2, blocker="needs key")
        cont = rehydrate_continuity_from_registry_snapshot(snap)
        self.assertEqual(cont.step_records[0].state, ContinuityStepState.BLOCKED)
        self.assertIn("needs key", cont.step_records[0].detail)

    def test_16_skipped_steps_remain(self):
        states = (
            RegistryStepState.SKIPPED,
            RegistryStepState.PENDING,
            RegistryStepState.PENDING,
        )
        snap = _seed_active(states=states, active=2)
        cont = rehydrate_continuity_from_registry_snapshot(snap)
        self.assertEqual(cont.step_records[0].state, ContinuityStepState.SKIPPED)

    def test_17_dependencies_respected_via_recompute(self):
        states = (
            RegistryStepState.PENDING,
            RegistryStepState.PENDING,
            RegistryStepState.PENDING,
        )
        snap = _seed_active(states=states, active=1)
        cont = rehydrate_continuity_from_registry_snapshot(snap)
        # With no deps, recompute keeps eligible first step
        active = recompute_active_step(cont, dependencies=())
        self.assertEqual(active, 1)

    def test_18_blocker_summary_preserved(self):
        states = (
            RegistryStepState.BLOCKED,
            RegistryStepState.PENDING,
            RegistryStepState.PENDING,
        )
        snap = _seed_active(states=states, active=2, blocker="waiting on review")
        prior = plan_result_from_registry_snapshot(snap)
        self.assertIn("waiting on review", prior.blockers[0])

    def test_19_registry_read_failure_fallback(self):
        _seed_active()
        with patch(
            "orchestration.plan.goal_registry.get_active_goal",
            side_effect=RuntimeError("db down"),
        ):
            res = recover_active_goal_for_owner("alice")
        self.assertEqual(res.status, RegistryStatus.UNAVAILABLE)

    def test_20_malformed_payload_no_exception_leak(self):
        cont = rehydrate_continuity_from_registry_snapshot(object())
        self.assertIsNone(cont)
        prior = plan_result_from_registry_snapshot(None)
        self.assertIsNone(prior)

    def test_21_sensitive_text_rejected(self):
        snap, st = build_snapshot(
            owner_id="alice",
            title="Learn api_key=secret123",
            plan_title="Plan",
            step_titles=("A",),
            step_states=(RegistryStepState.PENDING,),
        )
        self.assertEqual(st, RegistryStatus.REJECTED)
        self.assertIsNone(snap)

    def test_22_recovery_does_not_import_executor(self):
        import sys

        before = "orchestration.executor" in sys.modules
        _seed_active()
        recover_active_goal_for_owner("alice")
        # If executor was already loaded by tests, skip; recovery itself must not require it
        src = open(
            os.path.join(
                os.path.dirname(__file__),
                "orchestration",
                "plan",
                "goal_registry.py",
            ),
            encoding="utf-8",
        ).read()
        self.assertNotIn("orchestration.executor", src)
        _ = before

    def test_23_no_browser_computer_in_recovery(self):
        src = open(
            os.path.join(
                os.path.dirname(__file__),
                "orchestration",
                "plan",
                "goal_registry.py",
            ),
            encoding="utf-8",
        ).read()
        idx = src.find("def recover_active_goal_for_owner")
        body = src[idx : idx + 2500]
        self.assertNotIn("proactive.computer", body)
        self.assertNotIn("browser", body)

    def test_24_no_memory_in_recovery(self):
        src = open(
            os.path.join(
                os.path.dirname(__file__),
                "orchestration",
                "plan",
                "continuity",
                "engine.py",
            ),
            encoding="utf-8",
        ).read()
        idx = src.find("def rehydrate_continuity_from_registry_snapshot")
        body = src[idx : idx + 3500]
        self.assertNotIn("personal_memory", body)

    def test_25_inactivity_14d_marks_stale_and_refuses(self):
        seed_time = 1_700_000_000.0
        with patch("orchestration.plan.goal_registry.time.time", return_value=seed_time):
            _seed_active(last_active_at=seed_time)
        with patch(
            "orchestration.plan.goal_registry.time.time",
            return_value=seed_time + REGISTRY_INACTIVITY_SEC + 60,
        ):
            res = recover_active_goal_for_owner("alice")
        self.assertEqual(res.status, RegistryStatus.REJECTED)
        active = get_active_goal("alice")
        self.assertEqual(active.status, RegistryStatus.NOT_FOUND)

    def test_26_conversation_anchor_still_priority(self):
        # Registry alone true; without session conversation still uses registry.
        # has_continuity_anchor requires session thread — ensure false without thread.
        _seed_active()
        self.assertFalse(has_continuity_anchor("alice", "empty-sess"))
        self.assertTrue(has_registry_goal_anchor("alice"))
        self.assertTrue(has_continuity_context("alice", "empty-sess"))

    def test_27_completed_goal_state_on_full_completion(self):
        states = (
            RegistryStepState.COMPLETED,
            RegistryStepState.COMPLETED,
            RegistryStepState.COMPLETED,
        )
        snap = _seed_active(states=states, active=0)
        cont = rehydrate_continuity_from_registry_snapshot(snap)
        self.assertEqual(cont.goal_state, GoalState.COMPLETED)
        self.assertEqual(cont.active_step_index, 0)


if __name__ == "__main__":
    unittest.main()
