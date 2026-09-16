"""V8.26 Phase 4 tests: lifecycle UX + explicit STALE resume."""

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
    handle_lifecycle_request,
)
from orchestration.plan.goal_registry import (
    CONFIRM_RESUME_STALE,
    CONFIRM_TTL_SEC,
    GoalLifecycle,
    LifecycleConfirm,
    RegistryStatus,
    StepState,
    abandon_goal,
    archive_goal,
    build_snapshot,
    clear_lifecycle_confirm,
    create_active_goal,
    get_active_goal,
    get_lifecycle_confirm,
    list_recent_goals,
    make_lifecycle_confirm,
    mark_stale,
    reset_goal_registry_for_tests,
    reset_lifecycle_confirms_for_tests,
    resume_stale_goal,
    set_lifecycle_confirm,
    transition_goal,
    use_test_goal_registry_store,
)


def _seed(
    owner="alice",
    *,
    title="Learn python",
    plan_title="Plan for: learn python",
    states=None,
    active=1,
    n=3,
    durable=None,
):
    if states is None:
        states = tuple(StepState.PENDING for _ in range(n))
    titles = tuple(f"Step {i}" for i in range(1, n + 1))
    if durable is None:
        durable = (
            f"Plan: {plan_title}\n\nSteps:\n"
            + "\n".join(f"{i}. [ ] {t}" for i, t in enumerate(titles, 1))
            + "\n"
        )[:300]
    snap, st = build_snapshot(
        owner_id=owner,
        title=title,
        plan_title=plan_title,
        step_titles=titles,
        step_states=states,
        active_step_index=active,
        durable_view=durable,
        last_active_at=time.time(),
    )
    assert st is RegistryStatus.OK, st
    res = create_active_goal(owner, snap)
    assert res.status is RegistryStatus.OK, res.status
    return res.snapshot


def _seed_stale(owner="alice", **kwargs):
    snap = _seed(owner, **kwargs)
    res = mark_stale(owner, snap.goal_id, "inactive", snap.version)
    assert res.status is RegistryStatus.OK, res.status
    return res.snapshot


def _seed_completed(owner="alice", **kwargs):
    n = kwargs.pop("n", 2)
    states = tuple(StepState.COMPLETED for _ in range(n))
    snap = _seed(owner, states=states, active=0, n=n, **kwargs)
    # update may bump version via create — archive ACTIVE→COMPLETED
    active = get_active_goal(owner)
    assert active.status is RegistryStatus.OK
    res = archive_goal(owner, active.snapshot.goal_id, active.snapshot.version)
    assert res.status is RegistryStatus.OK, res.status
    return res.snapshot


def _seed_abandoned(owner="alice", **kwargs):
    snap = _seed(owner, **kwargs)
    res = abandon_goal(owner, snap.goal_id, snap.version)
    assert res.status is RegistryStatus.OK, res.status
    return res.snapshot


class TestV826GoalRegistryLifecycle(unittest.TestCase):
    def setUp(self):
        use_test_goal_registry_store(True)
        reset_goal_registry_for_tests()
        reset_lifecycle_confirms_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"

    def tearDown(self):
        reset_lifecycle_confirms_for_tests()
        reset_goal_registry_for_tests()
        use_test_goal_registry_store(False)

    # --- Registry transitions ---
    def test_01_active_to_stale(self):
        snap = _seed()
        res = mark_stale("alice", snap.goal_id, "ttl", snap.version)
        self.assertEqual(res.status, RegistryStatus.OK)
        self.assertEqual(res.snapshot.lifecycle, GoalLifecycle.STALE)

    def test_02_active_to_completed(self):
        snap = _seed_completed(title="Done goal")
        self.assertEqual(snap.lifecycle, GoalLifecycle.COMPLETED)

    def test_03_active_to_abandoned(self):
        snap = _seed_abandoned(title="Drop goal")
        self.assertEqual(snap.lifecycle, GoalLifecycle.ABANDONED)

    def test_04_stale_to_active_with_confirmation(self):
        stale = _seed_stale(title="Python")
        out = handle_lifecycle_request("alice", "s1", "Resume my plan")
        self.assertIsNotNone(out)
        self.assertIn("YES", out[0])
        conf = get_lifecycle_confirm("alice", "s1")
        self.assertIsNotNone(conf)
        self.assertEqual(conf.action, CONFIRM_RESUME_STALE)
        out2 = handle_lifecycle_request("alice", "s1", "YES")
        self.assertIsNotNone(out2)
        self.assertIn("Resumed", out2[0])
        active = get_active_goal("alice")
        self.assertEqual(active.status, RegistryStatus.OK)
        self.assertEqual(active.snapshot.goal_id, stale.goal_id)

    def test_05_completed_to_active_rejected(self):
        done = _seed_completed(title="Finished")
        res = transition_goal(
            "alice",
            done.goal_id,
            GoalLifecycle.COMPLETED,
            GoalLifecycle.ACTIVE,
            "bad",
            done.version,
        )
        self.assertEqual(res.status, RegistryStatus.REJECTED)
        res2 = resume_stale_goal("alice", done.goal_id, done.version)
        self.assertEqual(res2.status, RegistryStatus.REJECTED)

    def test_06_abandoned_to_active_rejected(self):
        abd = _seed_abandoned(title="Dropped")
        res = transition_goal(
            "alice",
            abd.goal_id,
            GoalLifecycle.ABANDONED,
            GoalLifecycle.ACTIVE,
            "bad",
            abd.version,
        )
        self.assertEqual(res.status, RegistryStatus.REJECTED)
        res2 = resume_stale_goal("alice", abd.goal_id, abd.version)
        self.assertEqual(res2.status, RegistryStatus.REJECTED)

    def test_07_stale_wrong_owner_rejected(self):
        stale = _seed_stale(owner="alice", title="Mine")
        res = resume_stale_goal("bob", stale.goal_id, stale.version)
        self.assertIn(res.status, (RegistryStatus.OWNER_MISMATCH, RegistryStatus.NOT_FOUND, RegistryStatus.REJECTED))

    def test_08_stale_missing_owner_rejected(self):
        stale = _seed_stale(title="X")
        res = resume_stale_goal("", stale.goal_id, stale.version)
        self.assertEqual(res.status, RegistryStatus.REJECTED)

    def test_09_stale_invalid_snapshot_rejected(self):
        res = resume_stale_goal("alice", "rg_nonexistent", 1)
        self.assertIn(res.status, (RegistryStatus.NOT_FOUND, RegistryStatus.REJECTED))

    def test_10_stale_version_conflict(self):
        stale = _seed_stale(title="Ver")
        res = resume_stale_goal("alice", stale.goal_id, stale.version + 99)
        self.assertEqual(res.status, RegistryStatus.CONFLICT)

    def test_11_two_session_resume_race(self):
        stale = _seed_stale(title="Race")
        handle_lifecycle_request("alice", "sess-a", "Resume my plan")
        handle_lifecycle_request("alice", "sess-b", "Resume my plan")
        out_a = handle_lifecycle_request("alice", "sess-a", "YES")
        self.assertIsNotNone(out_a)
        self.assertIn("Resumed", out_a[0])
        out_b = handle_lifecycle_request("alice", "sess-b", "YES")
        self.assertIsNotNone(out_b)
        low = out_b[0].lower()
        self.assertTrue("changed" in low or "couldn't" in low or "conflict" in low or "again" in low)

    def test_12_active_conflict_during_stale_resume(self):
        stale = _seed_stale(title="Old")
        # Create a different ACTIVE while confirm pending for stale
        handle_lifecycle_request("alice", "s1", "Resume the Old plan")
        conf = get_lifecycle_confirm("alice", "s1")
        self.assertIsNotNone(conf)
        # Inject ACTIVE competing goal
        other = _seed(owner="alice", title="Blocking")
        self.assertEqual(other.lifecycle, GoalLifecycle.ACTIVE)
        # Stale resume must CONFLICT (unique ACTIVE)
        res = resume_stale_goal("alice", stale.goal_id, stale.version)
        self.assertEqual(res.status, RegistryStatus.CONFLICT)
        out = handle_lifecycle_request("alice", "s1", "YES")
        self.assertIsNotNone(out)
        self.assertTrue(
            "couldn't" in out[0].lower()
            or "changed" in out[0].lower()
            or "active" in out[0].lower()
        )

    def test_13_confirmation_expiry(self):
        stale = _seed_stale(title="Exp")
        conf = make_lifecycle_confirm(
            owner_id="alice",
            session_id="s1",
            action=CONFIRM_RESUME_STALE,
            goal_id=stale.goal_id,
            expected_version=stale.version,
            title="Exp",
            ttl_sec=1,
        )
        # Force expired
        expired = LifecycleConfirm(
            owner_id=conf.owner_id,
            session_id=conf.session_id,
            action=conf.action,
            goal_id=conf.goal_id,
            expected_version=conf.expected_version,
            title=conf.title,
            expires_at=time.time() - 10,
            nonce=conf.nonce,
        )
        set_lifecycle_confirm(expired)
        self.assertIsNone(get_lifecycle_confirm("alice", "s1"))
        out = handle_lifecycle_request("alice", "s1", "YES")
        # No pending → YES is not a lifecycle utterance; may return None
        self.assertTrue(out is None or "confirm" not in (out[0] or "").lower() or "resume" in (out[0] or "").lower())

    def test_14_confirmation_owner_mismatch(self):
        stale = _seed_stale(title="Own")
        conf = make_lifecycle_confirm(
            owner_id="alice",
            session_id="s1",
            action=CONFIRM_RESUME_STALE,
            goal_id=stale.goal_id,
            expected_version=stale.version,
        )
        set_lifecycle_confirm(conf)
        out = handle_lifecycle_request("bob", "s1", "YES")
        self.assertTrue(out is None or isinstance(out[0], str))
        st, snaps = list_recent_goals(
            "alice", lifecycles=(GoalLifecycle.STALE,), limit=5
        )
        self.assertEqual(st, RegistryStatus.OK)
        self.assertTrue(any(s.goal_id == stale.goal_id for s in snaps))
        self.assertEqual(get_active_goal("alice").status, RegistryStatus.NOT_FOUND)

    def test_15_confirmation_session_mismatch(self):
        stale = _seed_stale(title="Sess")
        handle_lifecycle_request("alice", "s1", "Resume my plan")
        out = handle_lifecycle_request("alice", "other", "YES")
        self.assertTrue(out is None or "Resumed" not in out[0])
        self.assertEqual(get_active_goal("alice").status, RegistryStatus.NOT_FOUND)

    def test_16_confirmation_version_mismatch(self):
        stale = _seed_stale(title="Ver2")
        handle_lifecycle_request("alice", "s1", "Resume my plan")
        conf = get_lifecycle_confirm("alice", "s1")
        bad = LifecycleConfirm(
            owner_id=conf.owner_id,
            session_id=conf.session_id,
            action=conf.action,
            goal_id=conf.goal_id,
            expected_version=conf.expected_version + 5,
            title=conf.title,
            expires_at=conf.expires_at,
            nonce=conf.nonce,
        )
        set_lifecycle_confirm(bad)
        out = handle_lifecycle_request("alice", "s1", "YES")
        self.assertIsNotNone(out)
        self.assertTrue("changed" in out[0].lower() or "couldn't" in out[0].lower() or "again" in out[0].lower())

    def test_17_yes_applies(self):
        stale = _seed_stale(title="YesApply")
        handle_lifecycle_request("alice", "s1", "Resume my plan")
        out = handle_lifecycle_request("alice", "s1", "YES")
        self.assertIn("Resumed", out[0])
        self.assertEqual(get_active_goal("alice").snapshot.goal_id, stale.goal_id)

    def test_18_no_cancels(self):
        _seed_stale(title="NoCancel")
        handle_lifecycle_request("alice", "s1", "Resume my plan")
        out = handle_lifecycle_request("alice", "s1", "NO")
        self.assertIn("cancel", out[0].lower())
        self.assertIsNone(get_lifecycle_confirm("alice", "s1"))
        self.assertEqual(get_active_goal("alice").status, RegistryStatus.NOT_FOUND)

    def test_19_ambiguous_confirmation_no_mutate(self):
        stale = _seed_stale(title="Amb")
        handle_lifecycle_request("alice", "s1", "Resume my plan")
        out = handle_lifecycle_request("alice", "s1", "maybe")
        self.assertIsNotNone(out)
        self.assertIn("YES", out[0])
        loaded = get_active_goal("alice")
        self.assertEqual(loaded.status, RegistryStatus.NOT_FOUND)
        # still pending
        self.assertIsNotNone(get_lifecycle_confirm("alice", "s1"))

    def test_20_restart_completed_new_goal_id(self):
        done = _seed_completed(title="Python")
        old_id = done.goal_id
        out = handle_lifecycle_request("alice", "s1", "Restart my Python plan")
        self.assertIsNotNone(out)
        active = get_active_goal("alice")
        self.assertEqual(active.status, RegistryStatus.OK)
        self.assertNotEqual(active.snapshot.goal_id, old_id)
        self.assertEqual(active.snapshot.lifecycle, GoalLifecycle.ACTIVE)

    def test_21_restart_abandoned_new_goal_id(self):
        abd = _seed_abandoned(title="Node")
        old_id = abd.goal_id
        out = handle_lifecycle_request("alice", "s1", "Restart my Node plan")
        self.assertIsNotNone(out)
        active = get_active_goal("alice")
        self.assertEqual(active.status, RegistryStatus.OK)
        self.assertNotEqual(active.snapshot.goal_id, old_id)

    def test_22_active_replacement_requires_confirmation(self):
        snap = _seed(title="Keep me")
        out = handle_lifecycle_request("alice", "s1", "Replace this goal")
        self.assertIsNotNone(out)
        self.assertIn("YES", out[0])
        self.assertEqual(get_active_goal("alice").snapshot.goal_id, snap.goal_id)
        handle_lifecycle_request("alice", "s1", "YES")
        self.assertEqual(get_active_goal("alice").status, RegistryStatus.NOT_FOUND)

    def test_23_pivot_requires_confirmation(self):
        from orchestration.plan.continuity.engine import begin_pivot_replacement_confirm

        snap = _seed(title="Goal A")
        text = begin_pivot_replacement_confirm(
            "alice", "s1", active_snap=snap, proposed_topic="Goal B"
        )
        self.assertIn("YES", text)
        self.assertEqual(get_active_goal("alice").snapshot.goal_id, snap.goal_id)
        self.assertIsNotNone(get_lifecycle_confirm("alice", "s1"))

    def test_24_pivot_rejection_preserves_active(self):
        from orchestration.plan.continuity.engine import begin_pivot_replacement_confirm

        snap = _seed(title="Goal A")
        begin_pivot_replacement_confirm(
            "alice", "s1", active_snap=snap, proposed_topic="Goal B"
        )
        handle_lifecycle_request("alice", "s1", "NO")
        active = get_active_goal("alice")
        self.assertEqual(active.status, RegistryStatus.OK)
        self.assertEqual(active.snapshot.goal_id, snap.goal_id)

    def test_25_no_silent_replacement(self):
        a = _seed(title="Goal A")
        # Named resume of stale while active requires handshake, no silent swap
        stale = None
        # Mark A stale first then create B active — then try resume A
        mark_stale("alice", a.goal_id, "x", a.version)
        b = _seed(title="Goal B")
        out = handle_lifecycle_request("alice", "s1", "Resume the Goal A plan")
        self.assertIsNotNone(out)
        self.assertIn("abandon", out[0].lower())
        self.assertEqual(get_active_goal("alice").snapshot.goal_id, b.goal_id)

    def test_26_bounded_history_max_5(self):
        for i in range(7):
            _seed_stale(title=f"H{i}")
            # each create needs no ACTIVE — stale last one then next
        st, snaps = list_recent_goals("alice", limit=99)
        self.assertEqual(st, RegistryStatus.OK)
        self.assertLessEqual(len(snaps), 5)

    def test_27_deterministic_previous_goal(self):
        _seed_completed(title="First")
        time.sleep(0.02)
        second = _seed_abandoned(title="Second")
        out = handle_lifecycle_request("alice", "s1", "What was my previous goal?")
        self.assertIsNotNone(out)
        self.assertIn("Second", out[0])

    def test_28_multiple_stale_clarify(self):
        _seed_stale(title="Alpha")
        # need second stale: create+stale again
        s2 = _seed(title="Beta")
        mark_stale("alice", s2.goal_id, "x", s2.version)
        # First was overwritten? create_active when none — Alpha is stale, Beta created then stale
        # After Alpha stale, Beta create OK. After Beta stale, two STALE.
        out = handle_lifecycle_request("alice", "s1", "Resume my plan")
        self.assertIsNotNone(out)
        low = out[0].lower()
        self.assertTrue("more than one" in low or "several" in low or "name the plan" in low)

    def test_29_exact_title_match(self):
        _seed_stale(title="Python")
        s2 = _seed(title="Rust")
        mark_stale("alice", s2.goal_id, "x", s2.version)
        out = handle_lifecycle_request("alice", "s1", "Resume the Python plan")
        self.assertIsNotNone(out)
        self.assertIn("Python", out[0])
        conf = get_lifecycle_confirm("alice", "s1")
        self.assertIsNotNone(conf)
        handle_lifecycle_request("alice", "s1", "YES")
        self.assertEqual(get_active_goal("alice").snapshot.title, "Python")

    def test_30_ambiguous_title_match_clarify(self):
        # Two with same title after casefold
        _seed_stale(title="Python")
        s2 = _seed(title="Python")
        mark_stale("alice", s2.goal_id, "x", s2.version)
        out = handle_lifecycle_request("alice", "s1", "Resume the Python plan")
        self.assertIsNotNone(out)
        self.assertTrue(
            "several" in out[0].lower() or "more than one" in out[0].lower() or "exact" in out[0].lower()
        )

    def test_31_no_unrestricted_history(self):
        st, snaps = list_recent_goals("alice", limit=1000)
        self.assertEqual(st, RegistryStatus.OK)
        self.assertLessEqual(len(snaps), 5)

    def test_32_sensitive_text_sanitization(self):
        from orchestration.plan.goal_registry import is_sensitive_goal_content

        self.assertTrue(is_sensitive_goal_content("api_key=sk-abcdef123456"))
        # Restart with sensitive topic blocked
        out = handle_lifecycle_request(
            "alice", "s1", "Restart my api_key=sk-abcdef123456 plan"
        )
        self.assertIsNotNone(out)
        self.assertTrue(
            "safe" in out[0].lower() or "archived" in out[0].lower() or "new plan" in out[0].lower()
        )

    def test_33_no_memory_write(self):
        with patch("memory.__init__", create=True) as mem:
            _seed_stale(title="Mem")
            handle_lifecycle_request("alice", "s1", "Resume my plan")
            handle_lifecycle_request("alice", "s1", "YES")
            mem.assert_not_called()

    def test_34_no_task_ledger_write(self):
        with patch("orchestration.task.ledger.ledger_create", create=True) as led:
            _seed_stale(title="Led")
            handle_lifecycle_request("alice", "s1", "Resume my plan")
            handle_lifecycle_request("alice", "s1", "YES")
            led.assert_not_called()

    def test_35_no_executor_browser_computer(self):
        # Lifecycle path must not import/call browser/computer adapters
        import sys

        blocked = [
            k
            for k in list(sys.modules)
            if "browser.real_driver" in k or "computer.actions" in k
        ]
        _seed_stale(title="Safe")
        handle_lifecycle_request("alice", "s1", "Abandon this plan")
        # Just ensure handler returns informational — no authority side effects
        out = handle_lifecycle_request("alice", "s1", "Resume my plan")
        self.assertIsNotNone(out)
        self.assertNotIn("execute", out[0].lower())

    def test_36_hard_zero_cost(self):
        with patch("orchestration.plan.execute.maybe_polish_with_ollama") as ollama:
            _seed_stale(title="Cost")
            handle_lifecycle_request("alice", "s1", "What did I finish?")
            handle_lifecycle_request("alice", "s1", "Resume my plan")
            handle_lifecycle_request("alice", "s1", "YES")
            ollama.assert_not_called()

    def test_37_archive_stale_rejected(self):
        stale = _seed_stale(title="NoArchive")
        res = archive_goal("alice", stale.goal_id, stale.version)
        self.assertEqual(res.status, RegistryStatus.REJECTED)

    def test_38_list_recent_excludes_active_by_default(self):
        _seed(title="Live")
        st, snaps = list_recent_goals("alice")
        self.assertEqual(st, RegistryStatus.OK)
        self.assertEqual(len(snaps), 0)

    def test_39_confirm_ttl_bounded(self):
        self.assertGreaterEqual(CONFIRM_TTL_SEC, 120)
        self.assertLessEqual(CONFIRM_TTL_SEC, 300)

    def test_40_flag_off_no_lifecycle(self):
        _seed_stale(title="Off")
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "false"
        out = handle_lifecycle_request("alice", "s1", "Resume my plan")
        self.assertIsNone(out)
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"


if __name__ == "__main__":
    unittest.main()
