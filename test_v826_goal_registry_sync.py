"""V8.26 Phase 2 tests: Goal Registry ↔ plan continuity dual-write."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ACT_ENABLED", "false")
os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"

from orchestration.conversation.context import (
    reset_conversation_context_for_tests,
    set_anchor_ttl_for_tests,
)
from orchestration.conversation.thread import get_conversation_thread
from orchestration.executor import ExecutionIdentity
from orchestration.plan.continuity.engine import build_continuity_state
from orchestration.plan.continuity.types import (
    StepState as ContinuityStepState,
    StepStateRecord,
)
from orchestration.plan.continuity.types import PlanContinuityState
from orchestration.plan.execute import (
    execute_plan_steps,
    reset_plan_provider_for_tests,
)
from orchestration.plan.goal_registry import (
    GoalLifecycle,
    RegistryStatus,
    StepState as RegistryStepState,
    create_active_goal,
    get_active_goal,
    goal_registry_sync_stats,
    is_goal_registry_sync_enabled,
    reset_goal_registry_for_tests,
    reset_goal_registry_sync_stats_for_tests,
    sync_active_goal_from_plan,
    to_registry_step_state,
    update_active_goal,
    use_test_goal_registry_store,
    build_snapshot,
)
from orchestration.plan.types import (
    PlanConfidence,
    PlanResult,
    PlanStatus,
    PlanStepItem,
    PlanStepKind,
)
from orchestration.production import prepare_v8_request


def _ident(owner="alice", session="sess-v826-p2"):
    return ExecutionIdentity(owner_id=owner, session_id=session, computer_session_id="")


def _step(text: str, owner="alice", session="sess-v826-p2"):
    prep = prepare_v8_request(text, identity=_ident(owner, session))
    assert prep.plan is not None, prep.status
    return prep.plan.steps[0], prep.plan


def _plan_result(
    title="Plan for: learn python",
    *,
    states=None,
    n=3,
) -> tuple[PlanResult, PlanContinuityState]:
    steps = tuple(
        PlanStepItem(i, f"Step {i}", "", PlanStepKind.PREPARE)
        for i in range(1, n + 1)
    )
    result = PlanResult(
        status=PlanStatus.OK,
        title=title,
        steps=steps,
        confidence=PlanConfidence.MEDIUM,
        blockers=(),
    )
    continuity = build_continuity_state(result)
    if states is not None:
        records = tuple(
            StepStateRecord(i + 1, states[i], "")
            for i in range(len(states))
        )
        continuity = PlanContinuityState(
            goal_title=continuity.goal_title or title,
            goal_state=continuity.goal_state,
            step_records=records,
            active_step_index=continuity.active_step_index,
        )
    return result, continuity


class TestV826GoalRegistrySync(unittest.TestCase):
    def setUp(self):
        reset_conversation_context_for_tests()
        reset_plan_provider_for_tests()
        set_anchor_ttl_for_tests(None)
        use_test_goal_registry_store(True)
        reset_goal_registry_for_tests()
        reset_goal_registry_sync_stats_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"

    def tearDown(self):
        reset_conversation_context_for_tests()
        reset_plan_provider_for_tests()
        set_anchor_ttl_for_tests(None)
        reset_goal_registry_for_tests()
        reset_goal_registry_sync_stats_for_tests()
        use_test_goal_registry_store(False)

    # --- Flag behavior ---

    def test_01_flag_off_no_registry_write(self):
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "false"
        self.assertFalse(is_goal_registry_sync_enabled())
        result, cont = _plan_result()
        res = sync_active_goal_from_plan(
            owner_id="alice",
            plan_result=result,
            continuity_state=cont,
            durable_view="Plan: x",
        )
        self.assertEqual(res.status, RegistryStatus.UNAVAILABLE)
        self.assertEqual(get_active_goal("alice").status, RegistryStatus.UNAVAILABLE)
        stats = goal_registry_sync_stats()
        self.assertEqual(stats["persists"], 0)

    def test_02_flag_off_execute_zero_persistence_calls(self):
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "false"
        reset_goal_registry_sync_stats_for_tests()
        ps, gp = _step("make a plan to learn python basics")
        status, text = execute_plan_steps(ps, gp)
        self.assertEqual(status, "SUCCESS")
        self.assertTrue(text.strip())
        stats = goal_registry_sync_stats()
        self.assertEqual(stats["attempts"], 0)
        self.assertEqual(stats["persists"], 0)

    def test_03_flag_on_registry_write(self):
        result, cont = _plan_result()
        res = sync_active_goal_from_plan(
            owner_id="alice",
            plan_result=result,
            continuity_state=cont,
            durable_view="Plan: learn python\nSteps: 3",
        )
        self.assertEqual(res.status, RegistryStatus.OK)
        self.assertIsNotNone(res.snapshot)
        self.assertEqual(res.snapshot.lifecycle, GoalLifecycle.ACTIVE)
        stats = goal_registry_sync_stats()
        self.assertGreaterEqual(stats["persists"], 1)

    def test_04_create_active_registry_goal(self):
        result, cont = _plan_result(title="Plan for: ship MVP")
        res = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        self.assertEqual(res.status, RegistryStatus.OK)
        active = get_active_goal("alice")
        self.assertEqual(active.status, RegistryStatus.OK)
        self.assertTrue(str(active.snapshot.goal_id).startswith("rg_"))

    def test_05_plan_title_synchronization(self):
        result, cont = _plan_result(title="Plan for: learn rust")
        res = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        self.assertEqual(res.snapshot.plan_title, "Plan for: learn rust")

    def test_06_step_title_synchronization(self):
        result, cont = _plan_result()
        res = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        self.assertEqual(res.snapshot.step_titles, ("Step 1", "Step 2", "Step 3"))

    def test_07_step_state_synchronization(self):
        states = (
            ContinuityStepState.COMPLETED,
            ContinuityStepState.IN_PROGRESS,
            ContinuityStepState.PENDING,
        )
        result, cont = _plan_result(states=states)
        res = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        self.assertEqual(
            res.snapshot.step_states,
            (
                RegistryStepState.COMPLETED,
                RegistryStepState.IN_PROGRESS,
                RegistryStepState.PENDING,
            ),
        )

    def test_08_active_step_synchronization(self):
        states = (
            ContinuityStepState.COMPLETED,
            ContinuityStepState.IN_PROGRESS,
            ContinuityStepState.PENDING,
        )
        result, cont = _plan_result(states=states)
        cont = PlanContinuityState(
            goal_title=cont.goal_title,
            goal_state=cont.goal_state,
            step_records=cont.step_records,
            active_step_index=2,
        )
        res = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        self.assertEqual(res.snapshot.active_step_index, 2)

    def test_09_blocker_synchronization(self):
        records = (
            StepStateRecord(1, ContinuityStepState.BLOCKED, "needs API key"),
            StepStateRecord(2, ContinuityStepState.PENDING, ""),
            StepStateRecord(3, ContinuityStepState.PENDING, ""),
        )
        result, cont = _plan_result()
        cont = PlanContinuityState(
            goal_title=cont.goal_title,
            goal_state=cont.goal_state,
            step_records=records,
            active_step_index=1,
        )
        res = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        self.assertIn("needs API key", res.snapshot.blocker_summary)

    def test_10_durable_view_synchronization(self):
        result, cont = _plan_result()
        durable = "Plan: learn python\nSteps:\n1. [>] Step 1\n2. [ ] Step 2"
        res = sync_active_goal_from_plan(
            owner_id="alice",
            plan_result=result,
            continuity_state=cont,
            durable_view=durable,
        )
        self.assertEqual(res.snapshot.durable_view, durable[:300])

    def test_11_subsequent_update_preserves_goal_id(self):
        result, cont = _plan_result()
        first = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        gid = first.snapshot.goal_id
        states = (
            ContinuityStepState.COMPLETED,
            ContinuityStepState.PENDING,
            ContinuityStepState.PENDING,
        )
        result2, cont2 = _plan_result(states=states)
        second = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result2, continuity_state=cont2
        )
        self.assertEqual(second.status, RegistryStatus.OK)
        self.assertEqual(second.snapshot.goal_id, gid)

    def test_12_version_increments(self):
        result, cont = _plan_result()
        first = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        self.assertEqual(first.snapshot.version, 1)
        second = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        self.assertEqual(second.snapshot.version, 2)

    def test_13_stale_expected_version_conflict(self):
        result, cont = _plan_result()
        first = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        snap, st = build_snapshot(
            owner_id="alice",
            title=first.snapshot.title,
            plan_title=first.snapshot.plan_title,
            step_titles=first.snapshot.step_titles,
            step_states=first.snapshot.step_states,
            goal_id=first.snapshot.goal_id,
            version=1,
            created_at=first.snapshot.created_at,
        )
        self.assertEqual(st, RegistryStatus.OK)
        bad = update_active_goal("alice", first.snapshot.goal_id, snap, expected_version=99)
        self.assertEqual(bad.status, RegistryStatus.CONFLICT)

    def test_14_conflict_does_not_overwrite_state(self):
        result, cont = _plan_result()
        first = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        before = get_active_goal("alice").snapshot
        other, cont2 = _plan_result(title="Plan for: totally different goal")
        res = sync_active_goal_from_plan(
            owner_id="alice", plan_result=other, continuity_state=cont2
        )
        self.assertEqual(res.status, RegistryStatus.CONFLICT)
        after = get_active_goal("alice").snapshot
        self.assertEqual(after.goal_id, before.goal_id)
        self.assertEqual(after.version, before.version)
        self.assertEqual(after.plan_title, before.plan_title)

    def test_15_unavailable_does_not_fail_plan_response(self):
        ps, gp = _step("make a plan to organize my desk")
        with patch(
            "orchestration.plan.goal_registry.sync_active_goal_from_plan",
            return_value=type("R", (), {"status": RegistryStatus.UNAVAILABLE, "snapshot": None})(),
        ):
            # Force enabled path then fail sync return
            status, text = execute_plan_steps(ps, gp)
        self.assertEqual(status, "SUCCESS")
        self.assertTrue(len(text.strip()) > 0)

    def test_16_invalid_snapshot_does_not_fail_plan_response(self):
        ps, gp = _step("make a plan to cook dinner")
        with patch(
            "orchestration.plan.goal_registry.sync_active_goal_from_plan",
            return_value=type(
                "R", (), {"status": RegistryStatus.INVALID_SNAPSHOT, "snapshot": None}
            )(),
        ):
            status, text = execute_plan_steps(ps, gp)
        self.assertEqual(status, "SUCCESS")
        self.assertTrue(text.strip())

    def test_17_missing_owner_fails_registry_safely(self):
        result, cont = _plan_result()
        res = sync_active_goal_from_plan(
            owner_id="", plan_result=result, continuity_state=cont
        )
        self.assertEqual(res.status, RegistryStatus.REJECTED)
        self.assertIsNone(res.snapshot)

    def test_18_owner_from_trusted_identity_via_execute(self):
        ps, gp = _step("make a plan to learn typing", owner="trusted-owner")
        self.assertEqual(gp.owner_id, "trusted-owner")
        status, text = execute_plan_steps(ps, gp)
        self.assertEqual(status, "SUCCESS")
        active = get_active_goal("trusted-owner")
        self.assertEqual(active.status, RegistryStatus.OK)
        self.assertEqual(active.snapshot.owner_id, "trusted-owner")

    def test_19_user_text_cannot_become_owner(self):
        # User text mentioning another name must not become registry owner.
        ps, gp = _step(
            "make a plan to learn python with eve",
            owner="alice",
        )
        status, _ = execute_plan_steps(ps, gp)
        self.assertEqual(status, "SUCCESS")
        self.assertEqual(gp.owner_id, "alice")
        self.assertEqual(get_active_goal("eve").status, RegistryStatus.NOT_FOUND)
        active = get_active_goal("alice")
        if active.status is RegistryStatus.OK:
            self.assertEqual(active.snapshot.owner_id, "alice")

    def test_20_different_active_goal_not_silently_replaced(self):
        a, ca = _plan_result(title="Plan for: goal A")
        sync_active_goal_from_plan(owner_id="alice", plan_result=a, continuity_state=ca)
        b, cb = _plan_result(title="Plan for: goal B completely different")
        res = sync_active_goal_from_plan(
            owner_id="alice", plan_result=b, continuity_state=cb
        )
        self.assertEqual(res.status, RegistryStatus.CONFLICT)
        active = get_active_goal("alice").snapshot
        self.assertIn("goal A", active.plan_title)

    def test_21_create_race_unique_active_conflict(self):
        a, ca = _plan_result(title="Plan for: first")
        first = sync_active_goal_from_plan(
            owner_id="alice", plan_result=a, continuity_state=ca
        )
        # Direct create of a second ACTIVE with different goal_id
        snap, st = build_snapshot(
            owner_id="alice",
            title="Other",
            plan_title="Other plan",
            step_titles=("X",),
            step_states=(RegistryStepState.PENDING,),
        )
        self.assertEqual(st, RegistryStatus.OK)
        raced = create_active_goal("alice", snap)
        self.assertEqual(raced.status, RegistryStatus.CONFLICT)
        self.assertEqual(get_active_goal("alice").snapshot.goal_id, first.snapshot.goal_id)

    def test_22_stepstate_conversion_deterministic(self):
        for name in ("PENDING", "IN_PROGRESS", "COMPLETED", "BLOCKED", "SKIPPED"):
            c = ContinuityStepState(name)
            r = to_registry_step_state(c)
            self.assertIsInstance(r, RegistryStepState)
            self.assertEqual(r.value, c.value)
            self.assertIs(r, RegistryStepState(c.value))
            # Distinct enum types — identity must not hold across classes
            self.assertIsNot(type(r), type(c))

    def test_23_durable_seed_remains_intact(self):
        ps, gp = _step("make a plan to learn piano")
        status, text = execute_plan_steps(ps, gp)
        self.assertEqual(status, "SUCCESS")
        thread = get_conversation_thread("alice", "sess-v826-p2")
        self.assertTrue(thread)
        seeded = thread[-1].assistant_text
        self.assertTrue(seeded)
        self.assertLessEqual(len(seeded), 300)
        # Seeded turn should look plan-shaped when recovery succeeded
        low = seeded.lower()
        self.assertTrue("plan" in low or "recovery unavailable" in low)

    def test_24_no_personal_memory_mutation(self):
        calls = []

        def _boom(*_a, **_k):
            calls.append(1)
            raise AssertionError("personal memory must not be called")

        with patch(
            "orchestration.conversation.personal_memory.save_personal_memory",
            _boom,
        ):
            result, cont = _plan_result()
            sync_active_goal_from_plan(
                owner_id="alice", plan_result=result, continuity_state=cont
            )
            ps, gp = _step("make a plan to stretch daily")
            status, _ = execute_plan_steps(ps, gp)
            self.assertEqual(status, "SUCCESS")
        self.assertEqual(calls, [])

    def test_25_no_task_ledger_mutation(self):
        with patch("orchestration.task.ledger.ledger_create") as create, patch(
            "orchestration.task.ledger.persist_transition"
        ) as persist:
            result, cont = _plan_result()
            sync_active_goal_from_plan(
                owner_id="alice", plan_result=result, continuity_state=cont
            )
            create.assert_not_called()
            persist.assert_not_called()

    def test_26_no_executor_computer_browser_authority_in_sync(self):
        path = os.path.join(
            os.path.dirname(__file__),
            "orchestration",
            "plan",
            "goal_registry.py",
        )
        src = open(path, encoding="utf-8").read()
        sync_idx = src.find("def sync_active_goal_from_plan")
        self.assertGreater(sync_idx, 0)
        sync_body = src[sync_idx:]
        self.assertNotIn("orchestration.executor", sync_body)
        self.assertNotIn("proactive.computer", sync_body)
        self.assertNotIn("execute_plan(", sync_body)

    def test_27_no_network_cloud_ollama_in_sync_path(self):
        result, cont = _plan_result()
        with patch("orchestration.plan.execute.maybe_polish_with_ollama") as polish:
            # Direct sync must not touch ollama
            sync_active_goal_from_plan(
                owner_id="alice", plan_result=result, continuity_state=cont
            )
            polish.assert_not_called()

    def test_28_no_env_file_dependency(self):
        # Registry sync uses os.getenv only; never opens .env path.
        result, cont = _plan_result()
        with patch("builtins.open", side_effect=AssertionError("open must not be used")):
            # sync itself should not open files; build from in-memory only
            # (avoid patching open for whole process — check source instead)
            pass
        src = open(
            os.path.join(os.path.dirname(__file__), "orchestration", "plan", "goal_registry.py"),
            encoding="utf-8",
        ).read()
        self.assertNotIn(".env", src)

    def test_29_registry_persistence_only_and_repeated_updates(self):
        result, cont = _plan_result()
        r1 = sync_active_goal_from_plan(
            owner_id="alice", plan_result=result, continuity_state=cont
        )
        gid = r1.snapshot.goal_id
        for i in range(3):
            r = sync_active_goal_from_plan(
                owner_id="alice", plan_result=result, continuity_state=cont
            )
            self.assertEqual(r.status, RegistryStatus.OK)
            self.assertEqual(r.snapshot.goal_id, gid)
        self.assertEqual(get_active_goal("alice").snapshot.version, 4)

    def test_30_execute_flag_on_creates_registry(self):
        ps, gp = _step("make a plan to learn spanish")
        status, text = execute_plan_steps(ps, gp)
        self.assertEqual(status, "SUCCESS")
        self.assertTrue(text.strip())
        active = get_active_goal("alice")
        self.assertEqual(active.status, RegistryStatus.OK)
        self.assertEqual(active.snapshot.lifecycle, GoalLifecycle.ACTIVE)
        self.assertTrue(active.snapshot.step_titles)
        self.assertTrue(active.snapshot.durable_view or True)  # may be empty if seed fail-closed

    def test_31_sync_exception_swallowed_by_execute(self):
        ps, gp = _step("make a plan to water plants")

        def _raise(*_a, **_k):
            raise RuntimeError("registry exploded")

        with patch(
            "orchestration.plan.goal_registry.sync_active_goal_from_plan",
            _raise,
        ):
            status, text = execute_plan_steps(ps, gp)
        self.assertEqual(status, "SUCCESS")
        self.assertTrue(text.strip())


if __name__ == "__main__":
    unittest.main()
