"""V8.25 Phase 3 tests: Adaptive goal continuity engine integration."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ACT_ENABLED", "false")

from orchestration.conversation.context import (
    record_conversation_turn,
    reset_conversation_context_for_tests,
    set_anchor_ttl_for_tests,
)
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.types import IntentClass
from orchestration.plan.continuity.engine import (
    build_continuity_state,
    format_progress_acknowledgement,
    needs_continuity_anchor,
    normalize_plan_mode_for_continuity,
    process_progress_update,
    recover_continuity_from_parse,
    rebuild_continuity_preserving_states,
    should_handle_as_progress,
    step_states_from_continuity,
)
from orchestration.plan.continuity.state import apply_progress_event, recompute_active_step
from orchestration.plan.continuity.types import (
    GoalState,
    PlanContinuityState,
    ProgressEvent,
    ProgressEventKind,
    StepState,
    StepStateRecord,
    SUGGEST_CLARIFY,
)
from orchestration.plan.durable import serialize_plan_durable
from orchestration.plan.modes import PlanMode, detect_plan_mode
from orchestration.plan.parse import parse_plan_from_assistant_text
from orchestration.plan.prioritize import prioritize_plan
from orchestration.plan.types import (
    PlanConfidence,
    PlanResult,
    PlanStatus,
    PlanStepItem,
    PlanStepKind,
)
from orchestration.production import format_v8_execution, prepare_v8_request


def _ident(owner="alice", session="sess-p3"):
    return ExecutionIdentity(owner_id=owner, session_id=session, computer_session_id="")


def _plan(steps=None, title="Improve Python skills") -> PlanResult:
    if steps is None:
        steps = (
            PlanStepItem(1, "Clarify scope", "", PlanStepKind.PREPARE),
            PlanStepItem(2, "Identify smallest change", "", PlanStepKind.PREPARE),
            PlanStepItem(3, "Implement it", "", PlanStepKind.PREPARE),
        )
    return PlanResult(
        status=PlanStatus.OK,
        title=title,
        steps=steps,
        confidence=PlanConfidence.MEDIUM,
    )


def _state(n=3) -> PlanContinuityState:
    return build_continuity_state(_plan(steps=tuple(
        PlanStepItem(i, f"Step {i}", "", PlanStepKind.PREPARE)
        for i in range(1, n + 1)
    )))


class TestV825ContinuityPhase3(unittest.TestCase):
    def setUp(self):
        reset_conversation_context_for_tests()
        set_anchor_ttl_for_tests(1200)

    def tearDown(self):
        reset_conversation_context_for_tests()
        set_anchor_ttl_for_tests(None)

    def test_01_active_goal_initialization(self):
        state = build_continuity_state(_plan())
        self.assertEqual(state.goal_state, GoalState.ACTIVE)
        self.assertEqual(len(state.step_records), 3)
        self.assertEqual(state.active_step_index, 1)
        self.assertTrue(all(r.state is StepState.PENDING for r in state.step_records))

    def test_02_completed_step_transition(self):
        analysis = process_progress_update("Finished step 1", _state())
        self.assertEqual(
            analysis.continuity_state.step_records[0].state,
            StepState.COMPLETED,
        )
        self.assertEqual(analysis.continuity_state.active_step_index, 2)

    def test_03_in_progress_transition(self):
        analysis = process_progress_update("Working on step 2", _state())
        self.assertEqual(
            analysis.continuity_state.step_records[1].state,
            StepState.IN_PROGRESS,
        )

    def test_04_blocked_step_transition(self):
        analysis = process_progress_update("I'm stuck on step 2", _state())
        self.assertEqual(
            analysis.continuity_state.step_records[1].state,
            StepState.BLOCKED,
        )

    def test_05_skipped_step_transition(self):
        analysis = process_progress_update("Skip step 3", _state(3))
        self.assertEqual(
            analysis.continuity_state.step_records[2].state,
            StepState.SKIPPED,
        )

    def test_06_idempotent_completion(self):
        state = _state()
        ev = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=1)
        a1 = apply_progress_event(state, ev)
        a2 = apply_progress_event(a1.continuity_state, ev)
        self.assertEqual(a2.continuity_state.completed_count, 1)

    def test_07_dependency_aware_eligibility(self):
        state = _state(3)
        deps = ((1, 2), (2, 3))
        ev = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=3)
        analysis = apply_progress_event(state, ev, dependencies=deps)
        self.assertEqual(analysis.continuity_state.active_step_index, 1)

    def test_08_blocked_dependency_propagation(self):
        state = _state(3)
        deps = ((1, 2), (2, 3))
        ev1 = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=1)
        s1 = apply_progress_event(state, ev1, dependencies=deps).continuity_state
        ev2 = ProgressEvent(kind=ProgressEventKind.STEP_BLOCKED, target_step_index=2)
        analysis = apply_progress_event(s1, ev2, dependencies=deps)
        self.assertEqual(analysis.continuity_state.active_step_index, 0)
        self.assertEqual(analysis.continuity_state.goal_state, GoalState.BLOCKED)

    def test_09_deterministic_next_step_selection(self):
        state = _state(4)
        ev = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=1)
        updated = apply_progress_event(state, ev).continuity_state
        a = prioritize_plan(_plan(steps=updated.step_records and _plan().steps), mode=PlanMode.NEXT, continuity_state=updated)
        b = prioritize_plan(_plan(), mode=PlanMode.NEXT, continuity_state=updated)
        self.assertEqual(a.next_step_index, b.next_step_index)
        self.assertEqual(a.next_step_index, 2)

    def test_10_plan_completion(self):
        state = _state(2)
        ev1 = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=1)
        s1 = apply_progress_event(state, ev1).continuity_state
        ev2 = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=2)
        final = apply_progress_event(s1, ev2).continuity_state
        self.assertEqual(final.goal_state, GoalState.COMPLETED)
        self.assertEqual(final.active_step_index, 0)

    def test_11_what_next_with_active_plan_integration(self):
        ident = _ident("p3a", "s1")
        seeded = serialize_plan_durable(_plan(), limit=300)
        record_conversation_turn(
            ident.owner_id,
            ident.session_id,
            user_text="Plan how to improve Python",
            assistant_text=seeded,
        )
        prep = prepare_v8_request("What should I do first?", identity=ident)
        res = execute_plan(prep.plan, identity=ident, authorized_plan_hash="")
        out = format_v8_execution(res, intent=prep.intent)
        self.assertIn("Next step", out)
        self.assertNotIn("recent plan", out.lower())

    def test_12_what_next_without_active_plan(self):
        ident = _ident("p3b", "s2")
        prep = prepare_v8_request("What should I do next?", identity=ident)
        self.assertEqual(prep.intent, IntentClass.PLAN.value)
        res = execute_plan(prep.plan, identity=ident, authorized_plan_hash="")
        out = format_v8_execution(res, intent=prep.intent)
        self.assertIn("goal", out.lower())

    def test_13_continue_with_active_plan(self):
        ident = _ident("p3c", "s3")
        seeded = serialize_plan_durable(_plan(), limit=300)
        record_conversation_turn(
            ident.owner_id,
            ident.session_id,
            user_text="Make a plan",
            assistant_text=seeded,
        )
        mode = normalize_plan_mode_for_continuity("continue", PlanMode.CREATE, had_prior=True)
        self.assertEqual(mode, PlanMode.NEXT)

    def test_14_continue_without_active_plan(self):
        ident = _ident("p3e", "s5")
        prep = prepare_v8_request("continue", identity=ident)
        res = execute_plan(prep.plan, identity=ident, authorized_plan_hash="")
        out = format_v8_execution(res, intent=prep.intent)
        self.assertNotIn("Next step", out)
        self.assertNotIn("Marked step", out)
        # Fail closed: no plan invented from a bare continuation.
        self.assertNotIn("Plan:", out[:40])

    def test_15_blocker_query_not_pivot(self):
        state = _state()
        from orchestration.plan.continuity.detect import detect_progress_event

        ev = detect_progress_event("Are there any blockers?", state)
        self.assertEqual(ev.kind, ProgressEventKind.NOOP)
        self.assertEqual(detect_plan_mode("Are there any blockers?"), PlanMode.VALIDATE)

    def test_16_dependency_query_not_pivot(self):
        from orchestration.plan.continuity.detect import detect_progress_event

        ev = detect_progress_event("What are the dependencies?", _state())
        self.assertEqual(ev.kind, ProgressEventKind.NOOP)
        self.assertEqual(detect_plan_mode("What are the dependencies?"), PlanMode.DEPEND)

    def test_17_refine_not_pivot(self):
        from orchestration.plan.continuity.detect import detect_progress_event

        ev = detect_progress_event("Improve that plan", _state())
        self.assertNotEqual(ev.kind, ProgressEventKind.GOAL_PIVOT)

    def test_18_decision_remains_decision(self):
        self.assertEqual(
            normalize_intent("Python or Node.js?"),
            IntentClass.DECISION,
        )

    def test_19_genuine_goal_pivot_detection(self):
        analysis = process_progress_update(
            "Actually, let's focus on Docker instead.",
            _state(),
        )
        self.assertEqual(analysis.detected_event.kind, ProgressEventKind.GOAL_PIVOT)
        self.assertEqual(analysis.continuity_state.goal_state, GoalState.STALE)

    def test_20_pivot_ambiguity_fail_closed(self):
        ambiguous_state = PlanContinuityState(
            goal_title="Test",
            step_records=_state().step_records,
            active_step_index=0,
        )
        analysis = process_progress_update("Done", ambiguous_state)
        self.assertEqual(analysis.suggested_action, SUGGEST_CLARIFY)

    def test_21_single_active_goal_invariant(self):
        state = build_continuity_state(_plan(title="Only one goal"))
        self.assertEqual(state.goal_title, "Only one goal")

    def test_22_stale_plan_detection(self):
        analysis = process_progress_update(
            "Switch to learning Docker instead",
            _state(),
        )
        self.assertEqual(analysis.continuity_state.goal_state, GoalState.STALE)

    def test_23_replan_required_behavior(self):
        analysis = process_progress_update(
            "Instead, let's focus on Kubernetes",
            _state(),
        )
        from orchestration.plan.continuity.types import SUGGEST_REPLAN

        self.assertEqual(analysis.suggested_action, SUGGEST_REPLAN)

    def test_24_durable_state_recovery_integration(self):
        raw = serialize_plan_durable(
            _plan(),
            step_states=(StepState.COMPLETED, StepState.PENDING, StepState.PENDING),
            limit=300,
        )
        parsed = parse_plan_from_assistant_text(raw)
        self.assertTrue(parsed.ok)
        state = recover_continuity_from_parse(parsed)
        self.assertIsNotNone(state)
        self.assertEqual(state.step_records[0].state, StepState.COMPLETED)

    def test_25_marker_states_survive_parse_continuity(self):
        raw = (
            "Plan: Python Steps: "
            "1. [x] Clarify scope 2. [>] Identify change 3. [ ] Implement"
        )
        parsed = parse_plan_from_assistant_text(raw)
        state = recover_continuity_from_parse(parsed)
        self.assertIsNotNone(state)
        self.assertEqual(state.step_records[0].state, StepState.COMPLETED)
        self.assertEqual(state.step_records[1].state, StepState.IN_PROGRESS)

    def test_26_legacy_unmarked_plans_pending(self):
        legacy = "Plan: Python Steps: 1. Clarify scope 2. Implement it"
        parsed = parse_plan_from_assistant_text(legacy)
        state = recover_continuity_from_parse(parsed)
        self.assertTrue(all(r.state is StepState.PENDING for r in state.step_records))

    def test_27_malformed_marker_safe(self):
        raw = "Plan: Test Steps: 1. [?] Weird title 2. Normal title"
        parsed = parse_plan_from_assistant_text(raw)
        self.assertTrue(parsed.ok)
        state = recover_continuity_from_parse(parsed)
        self.assertEqual(state.step_records[0].state, StepState.PENDING)

    def test_28_sensitive_text_sanitized_in_progress(self):
        from orchestration.plan.continuity.detect import sanitize_progress_evidence

        cleaned = sanitize_progress_evidence("Done api_key=secret123")
        self.assertNotIn("secret123", cleaned)

    def test_29_durable_overflow_fail_closed(self):
        steps = tuple(
            PlanStepItem(i, "VeryLongTitle" + ("X" * 60), "", PlanStepKind.PREPARE)
            for i in range(1, 7)
        )
        big = PlanResult(status=PlanStatus.OK, title="T" * 100, steps=steps)
        self.assertIsNone(serialize_plan_durable(big, limit=40))

    def test_30_no_execution_authority_in_engine(self):
        import ast
        from pathlib import Path

        path = Path(__file__).resolve().parent / "orchestration" / "plan" / "continuity" / "engine.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(node.module.startswith("proactive.computer"))
                self.assertNotIn("executor", node.module or "")

    def test_31_progress_does_not_create_plan(self):
        ident = _ident("p3d", "s4")
        seeded = serialize_plan_durable(_plan(), limit=300)
        record_conversation_turn(
            ident.owner_id,
            ident.session_id,
            user_text="Plan Python",
            assistant_text=seeded,
        )
        prep = prepare_v8_request("Finished step 1", identity=ident)
        self.assertIsNotNone(prep.plan)
        res = execute_plan(prep.plan, identity=ident, authorized_plan_hash="")
        out = format_v8_execution(res, intent=prep.intent)
        self.assertNotIn("INVALID", out)
        self.assertTrue(
            "Marked step 1 complete" in out or "complete" in out.lower(),
            out,
        )
        self.assertNotIn("Plan:", out[:30])

    def test_32_should_not_handle_progress_for_validate_mode(self):
        state = _state()
        self.assertFalse(
            should_handle_as_progress(
                "Are there any blockers?",
                mode=PlanMode.VALIDATE,
                had_prior=True,
                continuity=state,
            )
        )

    def test_33_rebuild_preserves_states(self):
        state = _state()
        ev = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=1)
        updated = apply_progress_event(state, ev).continuity_state
        rebuilt = rebuild_continuity_preserving_states(updated, _plan())
        self.assertEqual(rebuilt.step_records[0].state, StepState.COMPLETED)

    def test_34_step_states_for_durable_serialization(self):
        state = _state()
        ev = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=1)
        updated = apply_progress_event(state, ev).continuity_state
        states = step_states_from_continuity(updated)
        raw = serialize_plan_durable(_plan(), step_states=states, limit=300)
        self.assertIn("[x]", raw)
        self.assertIn("[ ]", raw)

    def test_35_format_progress_acknowledgement(self):
        state = _state()
        analysis = process_progress_update("Finished step 1", state)
        text = format_progress_acknowledgement(analysis, _plan())
        self.assertIn("Next eligible step", text)


if __name__ == "__main__":
    unittest.main()
