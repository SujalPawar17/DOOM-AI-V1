"""V8.26 Phase 3 tests: registry-backed re-entry routing."""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ACT_ENABLED", "false")
os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"

from orchestration.conversation.context import reset_conversation_context_for_tests
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.types import IntentClass
from orchestration.plan.goal_registry import (
    RegistryStatus,
    StepState as RegistryStepState,
    build_snapshot,
    create_active_goal,
    get_active_goal,
    reset_goal_registry_for_tests,
    use_test_goal_registry_store,
)
from orchestration.production import format_v8_execution, prepare_v8_request


def _ident(owner="alice", session="sess-p3r"):
    return ExecutionIdentity(owner_id=owner, session_id=session, computer_session_id="")


def _seed_registry(
    owner="alice",
    *,
    title="Learn spanish",
    plan_title="Plan for: learn spanish",
    states=None,
    active=1,
    n=3,
):
    if states is None:
        states = tuple(RegistryStepState.PENDING for _ in range(n))
    titles = tuple(f"Step {i}" for i in range(1, n + 1))
    durable = (
        f"Plan: {plan_title}\n\nSteps:\n"
        + "\n".join(f"{i}. [ ] {t}" for i, t in enumerate(titles, 1))
        + "\n\nConfidence: Medium\n"
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


class TestV826GoalRegistryRouting(unittest.TestCase):
    def setUp(self):
        reset_conversation_context_for_tests()
        use_test_goal_registry_store(True)
        reset_goal_registry_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"

    def tearDown(self):
        reset_conversation_context_for_tests()
        reset_goal_registry_for_tests()
        use_test_goal_registry_store(False)

    def _run(self, phrase: str, owner="alice", session="sess-p3r"):
        ident = _ident(owner, session)
        prep = prepare_v8_request(phrase, identity=ident)
        return prep, ident

    def _exec(self, phrase: str, owner="alice", session="sess-p3r"):
        prep, ident = self._run(phrase, owner, session)
        self.assertEqual(prep.intent, IntentClass.PLAN.value, (prep.status, prep.reason))
        self.assertIsNotNone(prep.plan)
        res = execute_plan(prep.plan, identity=ident, authorized_plan_hash="")
        return format_v8_execution(res, intent=prep.intent)

    def test_01_continue_without_anchor_with_registry(self):
        _seed_registry()
        out = self._exec("Continue")
        self.assertTrue("Next step" in out or "step" in out.lower())

    def test_02_lets_continue_without_anchor(self):
        _seed_registry()
        out = self._exec("Let's continue.")
        self.assertTrue("Next step" in out or "step" in out.lower())

    def test_03_what_should_i_do_next(self):
        _seed_registry()
        out = self._exec("What should I do next?")
        self.assertTrue("Next step" in out or "step" in out.lower())

    def test_04_where_was_i(self):
        _seed_registry()
        prep, _ = self._run("Where was I?")
        self.assertEqual(prep.intent, IntentClass.PLAN.value)
        out = self._exec("Where was I?")
        self.assertTrue(len(out.strip()) > 0)

    def test_05_finished_step_1_progress(self):
        _seed_registry()
        out = self._exec("Finished step 1")
        low = out.lower()
        self.assertTrue(
            "completed" in low or "next step" in low or "noted" in low or "step" in low
        )
        # Dual-write should keep ACTIVE registry
        active = get_active_goal("alice")
        self.assertEqual(active.status, RegistryStatus.OK)

    def test_06_blockers_validate(self):
        states = (
            RegistryStepState.BLOCKED,
            RegistryStepState.PENDING,
            RegistryStepState.PENDING,
        )
        _seed_registry(states=states, active=2)
        prep, _ = self._run("Are there any blockers?")
        self.assertEqual(prep.intent, IntentClass.PLAN.value)
        out = self._exec("Are there any blockers?")
        self.assertTrue(len(out.strip()) > 0)

    def test_07_dependencies(self):
        _seed_registry()
        prep, _ = self._run("What are the dependencies?")
        self.assertEqual(prep.intent, IntentClass.PLAN.value)
        out = self._exec("What are the dependencies?")
        self.assertTrue(len(out.strip()) > 0)

    def test_08_click_save_computer_boundary(self):
        _seed_registry()
        # Without computer enabled, still must not become PLAN via registry
        intent = normalize_intent("Click Save")
        self.assertNotEqual(intent, IntentClass.PLAN)
        prep, _ = self._run("Click Save")
        self.assertNotEqual(prep.intent, IntentClass.PLAN.value)

    def test_09_decision_boundary(self):
        _seed_registry()
        prep, _ = self._run("Python or Node.js?")
        self.assertEqual(prep.intent, IntentClass.DECISION.value)

    def test_10_unrelated_conversation_not_plan(self):
        _seed_registry()
        prep, _ = self._run("Tell me a joke")
        self.assertNotEqual(prep.intent, IntentClass.PLAN.value)

    def test_11_no_registry_continue_no_invented_plan(self):
        prep, _ = self._run("Continue")
        self.assertNotEqual(prep.intent, IntentClass.PLAN.value)

    def test_12_ambiguous_pivot_no_silent_replacement(self):
        first = _seed_registry(title="Goal A", plan_title="Plan for: goal A")
        # Explicit new plan create should not silently replace via recovery path
        prep, ident = self._run("Make a plan to learn guitar from scratch today")
        self.assertEqual(prep.intent, IntentClass.PLAN.value)
        res = execute_plan(prep.plan, identity=ident, authorized_plan_hash="")
        self.assertEqual(res.status.value if hasattr(res.status, "value") else res.status, "SUCCESS")
        active = get_active_goal("alice")
        # Either conflict kept Goal A, or dual-write updated same-title — never two ACTIVES
        self.assertEqual(active.status, RegistryStatus.OK)
        self.assertEqual(active.snapshot.goal_id, first.goal_id)

    def test_13_registry_never_overrides_decision(self):
        _seed_registry()
        prep, _ = self._run("Should I choose React or Vue?")
        self.assertEqual(prep.intent, IntentClass.DECISION.value)

    def test_14_registry_never_overrides_computer(self):
        _seed_registry()
        prep, _ = self._run("Open Notepad")
        self.assertNotEqual(prep.intent, IntentClass.PLAN.value)

    def test_15_registry_never_creates_new_active_silently_on_hi(self):
        prep, _ = self._run("hi")
        self.assertNotEqual(prep.intent, IntentClass.PLAN.value)
        self.assertEqual(get_active_goal("alice").status, RegistryStatus.NOT_FOUND)

    def test_16_flag_off_continue_no_plan(self):
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"
        _seed_registry(owner="carol")
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "false"
        prep, _ = self._run("Continue", owner="carol", session="sess-carol")
        self.assertNotEqual(prep.intent, IntentClass.PLAN.value)

    def test_17_owner_isolation_routing(self):
        _seed_registry(owner="alice")
        prep, _ = self._run("Continue", owner="eve", session="sess-eve")
        self.assertNotEqual(prep.intent, IntentClass.PLAN.value)


if __name__ == "__main__":
    unittest.main()
