"""V8.25 Phase 1 tests for Adaptive Goal Continuity.

Isolated deterministic logic tests:
- Data models (bounds, immutability, frozen properties)
- Progress and pivot detection (deterministic regexes, sanitization)
- State machine (apply_progress_event, recompute_active_step)
- Dependencies and blocker propagation
- Ambiguity and fail-closed out-of-bounds guards
"""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from orchestration.plan.continuity.detect import (
    detect_progress_event,
    sanitize_progress_evidence,
)
from orchestration.plan.continuity.state import (
    apply_progress_event,
    recompute_active_step,
)
from orchestration.plan.continuity.types import (
    MAX_CONTINUITY_STEPS,
    SUGGEST_CLARIFY,
    SUGGEST_PROCEED,
    SUGGEST_REPLAN,
    SUGGEST_RESOLVE_BLOCKER,
    GoalState,
    PlanContinuityState,
    ProgressEvent,
    ProgressEventKind,
    StepState,
    StepStateRecord,
)
from orchestration.plan.types import PlanConfidence


def _create_sample_state(num_steps: int = 4) -> PlanContinuityState:
    records = tuple(
        StepStateRecord(index=i, state=StepState.PENDING, detail=f"Step detail {i}")
        for i in range(1, num_steps + 1)
    )
    return PlanContinuityState(
        goal_title="Improve Python skills",
        goal_state=GoalState.ACTIVE,
        step_records=records,
        active_step_index=1,
    )


class TestV825ContinuityPhase1(unittest.TestCase):
    # -------------------------------------------------------------
    # 1. Step Completion
    # -------------------------------------------------------------
    def test_step_completion(self):
        state = _create_sample_state(4)
        event = detect_progress_event("Finished step 1", state)
        self.assertEqual(event.kind, ProgressEventKind.STEP_COMPLETED)
        self.assertEqual(event.target_step_index, 1)
        self.assertEqual(event.confidence, PlanConfidence.HIGH)

        analysis = apply_progress_event(state, event)
        self.assertEqual(analysis.continuity_state.step_records[0].state, StepState.COMPLETED)
        self.assertEqual(analysis.continuity_state.completed_count, 1)
        self.assertEqual(analysis.continuity_state.active_step_index, 2)
        self.assertEqual(analysis.suggested_action, SUGGEST_PROCEED)

    # -------------------------------------------------------------
    # 2. Step Blocker
    # -------------------------------------------------------------
    def test_step_blocker(self):
        state = _create_sample_state(4)
        event = detect_progress_event("Step 2 is blocked", state)
        self.assertEqual(event.kind, ProgressEventKind.STEP_BLOCKED)
        self.assertEqual(event.target_step_index, 2)

        analysis = apply_progress_event(state, event)
        self.assertEqual(analysis.continuity_state.step_records[1].state, StepState.BLOCKED)
        self.assertEqual(analysis.continuity_state.active_step_index, 1)
        self.assertEqual(analysis.suggested_action, SUGGEST_PROCEED)

    # -------------------------------------------------------------
    # 3. Step Skip
    # -------------------------------------------------------------
    def test_step_skip(self):
        state = _create_sample_state(4)
        event = detect_progress_event("Skip step 4", state)
        self.assertEqual(event.kind, ProgressEventKind.STEP_SKIPPED)
        self.assertEqual(event.target_step_index, 4)

        analysis = apply_progress_event(state, event)
        self.assertEqual(analysis.continuity_state.step_records[3].state, StepState.SKIPPED)
        self.assertEqual(analysis.continuity_state.active_step_index, 1)

    # -------------------------------------------------------------
    # 4. Generic Done Ambiguity
    # -------------------------------------------------------------
    def test_generic_done_ambiguity(self):
        # When active_step_index is 0 (or no active plan cursor)
        event = detect_progress_event("Done", active_step_index=0)
        self.assertEqual(event.kind, ProgressEventKind.STEP_COMPLETED)
        self.assertTrue(event.is_ambiguous)
        self.assertEqual(event.target_step_index, 0)

        state = _create_sample_state(4)
        analysis = apply_progress_event(state, event)
        self.assertEqual(analysis.suggested_action, SUGGEST_CLARIFY)
        self.assertTrue("Which step did you mean" in analysis.clarification_needed)
        # Verify state was not mutated
        self.assertEqual(analysis.continuity_state.completed_count, 0)

    # -------------------------------------------------------------
    # 5. Generic Stuck Ambiguity
    # -------------------------------------------------------------
    def test_generic_stuck_ambiguity(self):
        event = detect_progress_event("I'm stuck", active_step_index=0)
        self.assertEqual(event.kind, ProgressEventKind.STEP_BLOCKED)
        self.assertTrue(event.is_ambiguous)
        self.assertEqual(event.target_step_index, 0)

        state = _create_sample_state(4)
        analysis = apply_progress_event(state, event)
        self.assertEqual(analysis.suggested_action, SUGGEST_CLARIFY)
        self.assertTrue("Which step did you mean" in analysis.clarification_needed)

    # -------------------------------------------------------------
    # 6. Out of Range Step
    # -------------------------------------------------------------
    def test_out_of_range_step(self):
        state = _create_sample_state(4)
        event = detect_progress_event("Finished step 9", state)
        self.assertEqual(event.kind, ProgressEventKind.STEP_COMPLETED)
        self.assertEqual(event.target_step_index, 9)

        analysis = apply_progress_event(state, event)
        self.assertEqual(analysis.suggested_action, SUGGEST_CLARIFY)
        self.assertIn("That plan only has 4 steps", analysis.clarification_needed)
        # Unmutated state
        self.assertEqual(analysis.continuity_state.completed_count, 0)

    # -------------------------------------------------------------
    # 7. Out of Sequence Completion
    # -------------------------------------------------------------
    def test_out_of_sequence_completion(self):
        # Step 1, 2, 3 all pending. User finishes Step 3.
        state = _create_sample_state(3)
        deps = ((1, 2), (2, 3))
        event = detect_progress_event("Finished step 3", state)

        analysis = apply_progress_event(state, event, dependencies=deps)
        self.assertEqual(analysis.continuity_state.step_records[2].state, StepState.COMPLETED)
        self.assertEqual(analysis.continuity_state.step_records[0].state, StepState.PENDING)
        self.assertEqual(analysis.continuity_state.step_records[1].state, StepState.PENDING)
        # Step 1 is still the next eligible step
        self.assertEqual(analysis.continuity_state.active_step_index, 1)

    # -------------------------------------------------------------
    # 8. Idempotent Completion
    # -------------------------------------------------------------
    def test_idempotent_completion(self):
        state = _create_sample_state(4)
        event = detect_progress_event("Finished step 1", state)
        a1 = apply_progress_event(state, event)
        self.assertEqual(a1.continuity_state.completed_count, 1)

        # Apply same completion again
        a2 = apply_progress_event(a1.continuity_state, event)
        self.assertEqual(a2.continuity_state.completed_count, 1)
        self.assertEqual(a2.continuity_state.step_records[0].state, StepState.COMPLETED)
        self.assertEqual(a2.continuity_state.active_step_index, 2)

    # -------------------------------------------------------------
    # 9. Idempotent Skip
    # -------------------------------------------------------------
    def test_idempotent_skip(self):
        state = _create_sample_state(4)
        event = detect_progress_event("Skip step 2", state)
        a1 = apply_progress_event(state, event)
        self.assertEqual(a1.continuity_state.step_records[1].state, StepState.SKIPPED)

        a2 = apply_progress_event(a1.continuity_state, event)
        self.assertEqual(a2.continuity_state.step_records[1].state, StepState.SKIPPED)

    # -------------------------------------------------------------
    # 10. Blocked Dependency
    # -------------------------------------------------------------
    def test_blocked_dependency(self):
        # Step 2 blocked, Step 3 depends on Step 2. Neither can be active.
        # Step 1 is completed.
        state = _create_sample_state(3)
        # Complete step 1
        ev1 = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=1)
        state1 = apply_progress_event(state, ev1).continuity_state

        # Block step 2, with 2->3 dependency
        deps = ((1, 2), (2, 3))
        ev2 = ProgressEvent(kind=ProgressEventKind.STEP_BLOCKED, target_step_index=2)
        analysis = apply_progress_event(state1, ev2, dependencies=deps)

        self.assertEqual(analysis.continuity_state.step_records[1].state, StepState.BLOCKED)
        # Step 3 depends on Step 2 which is blocked, so no step is eligible
        self.assertEqual(analysis.continuity_state.active_step_index, 0)
        self.assertEqual(analysis.continuity_state.goal_state, GoalState.BLOCKED)
        self.assertEqual(analysis.suggested_action, SUGGEST_RESOLVE_BLOCKER)

    # -------------------------------------------------------------
    # 11. Independent Parallel Step
    # -------------------------------------------------------------
    def test_independent_parallel_step(self):
        # 4 steps: Step 1 completed, Step 2 blocked.
        # Step 3 depends on Step 2. Step 4 is independent.
        state = _create_sample_state(4)
        ev1 = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=1)
        state1 = apply_progress_event(state, ev1).continuity_state

        deps = ((1, 2), (2, 3))  # 4 has no prerequisite
        ev2 = ProgressEvent(kind=ProgressEventKind.STEP_BLOCKED, target_step_index=2)
        analysis = apply_progress_event(state1, ev2, dependencies=deps)

        self.assertEqual(analysis.continuity_state.step_records[1].state, StepState.BLOCKED)
        # Step 4 is independent and eligible!
        self.assertEqual(analysis.continuity_state.active_step_index, 4)
        self.assertEqual(analysis.continuity_state.goal_state, GoalState.ACTIVE)
        self.assertEqual(analysis.suggested_action, SUGGEST_PROCEED)

    # -------------------------------------------------------------
    # 12. Valid Goal Pivot
    # -------------------------------------------------------------
    def test_valid_goal_pivot(self):
        state = _create_sample_state(4)
        event = detect_progress_event("Actually, let's focus on Django instead.", state)
        self.assertEqual(event.kind, ProgressEventKind.GOAL_PIVOT)
        self.assertIn("Django", event.raw_evidence)

        analysis = apply_progress_event(state, event)
        self.assertEqual(analysis.continuity_state.goal_state, GoalState.STALE)
        self.assertIn("Django", analysis.continuity_state.staleness_reason)
        self.assertEqual(analysis.suggested_action, SUGGEST_REPLAN)

    # -------------------------------------------------------------
    # 13. False Pivot: Existing Step Question
    # -------------------------------------------------------------
    def test_false_pivot_existing_step(self):
        state = _create_sample_state(4)
        event = detect_progress_event("Actually, what is step 2?", state)
        self.assertNotEqual(event.kind, ProgressEventKind.GOAL_PIVOT)
        self.assertEqual(event.kind, ProgressEventKind.NOOP)

    # -------------------------------------------------------------
    # 14. False Pivot: Decision Choice
    # -------------------------------------------------------------
    def test_false_pivot_decision(self):
        state = _create_sample_state(4)
        event1 = detect_progress_event("Actually, should I use Python 3.11 or 3.12?", state)
        self.assertNotEqual(event1.kind, ProgressEventKind.GOAL_PIVOT)
        self.assertEqual(event1.kind, ProgressEventKind.NOOP)

        event2 = detect_progress_event("Instead, should I choose Python or Node?", state)
        self.assertNotEqual(event2.kind, ProgressEventKind.GOAL_PIVOT)
        self.assertEqual(event2.kind, ProgressEventKind.NOOP)

    # -------------------------------------------------------------
    # 15. False Pivot: Completion Statement
    # -------------------------------------------------------------
    def test_false_pivot_completion(self):
        state = _create_sample_state(4)
        event = detect_progress_event("Actually, step 2 is already done.", state)
        self.assertNotEqual(event.kind, ProgressEventKind.GOAL_PIVOT)
        # Should be classified as step completion
        self.assertEqual(event.kind, ProgressEventKind.STEP_COMPLETED)
        self.assertEqual(event.target_step_index, 2)

    # -------------------------------------------------------------
    # 16. Sensitive Progress Evidence Sanitization
    # -------------------------------------------------------------
    def test_sensitive_progress_evidence(self):
        raw = "Finished step 1 and api_key=secret12345"
        sanitized = sanitize_progress_evidence(raw)
        self.assertNotIn("secret12345", sanitized)
        self.assertNotIn("api_key", sanitized)

        event = detect_progress_event("Finished step 1 and bearer eyJhbGciOi...")
        self.assertNotIn("eyJhbGciOi", event.raw_evidence)

    # -------------------------------------------------------------
    # 17. Maximum 6 Steps Boundary
    # -------------------------------------------------------------
    def test_max_6_steps_boundary(self):
        records = tuple(
            StepStateRecord(index=i, state=StepState.PENDING)
            for i in range(1, 10)
        )
        state = PlanContinuityState(goal_title="Long Plan", step_records=records)
        self.assertEqual(len(state.step_records), MAX_CONTINUITY_STEPS)
        self.assertEqual(state.total_count, MAX_CONTINUITY_STEPS)

    # -------------------------------------------------------------
    # 18. Frozen Dataclasses Immutability
    # -------------------------------------------------------------
    def test_frozen_dataclasses_immutability(self):
        rec = StepStateRecord(index=1, state=StepState.PENDING)
        with self.assertRaises(FrozenInstanceError):
            rec.state = StepState.COMPLETED

        state = _create_sample_state(3)
        with self.assertRaises(FrozenInstanceError):
            state.active_step_index = 2

        event = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=1)
        with self.assertRaises(FrozenInstanceError):
            event.target_step_index = 2

    # -------------------------------------------------------------
    # 19. No Mutation of Input State
    # -------------------------------------------------------------
    def test_no_mutation_of_input_state(self):
        state = _create_sample_state(3)
        event = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=1)
        analysis = apply_progress_event(state, event)

        # Original state is untouched
        self.assertEqual(state.completed_count, 0)
        self.assertEqual(state.step_records[0].state, StepState.PENDING)
        # Analysis continuity_state reflects the update
        self.assertEqual(analysis.continuity_state.completed_count, 1)
        self.assertEqual(analysis.continuity_state.step_records[0].state, StepState.COMPLETED)

    # -------------------------------------------------------------
    # 20. Empty Plan Handling
    # -------------------------------------------------------------
    def test_empty_plan_handling(self):
        empty_state = PlanContinuityState(goal_title="Empty", step_records=())
        self.assertEqual(recompute_active_step(empty_state), 0)

        ev = ProgressEvent(kind=ProgressEventKind.STEP_COMPLETED, target_step_index=1)
        analysis = apply_progress_event(empty_state, ev)
        self.assertEqual(analysis.suggested_action, SUGGEST_CLARIFY)
        self.assertIn("No active plan steps found", analysis.clarification_needed)

    # -------------------------------------------------------------
    # 21. Blocked Step with No Alternative
    # -------------------------------------------------------------
    def test_blocked_step_no_alternative(self):
        # 1-step plan that gets blocked
        state = _create_sample_state(1)
        ev = ProgressEvent(kind=ProgressEventKind.STEP_BLOCKED, target_step_index=1)
        analysis = apply_progress_event(state, ev)

        self.assertEqual(analysis.continuity_state.active_step_index, 0)
        self.assertEqual(analysis.continuity_state.goal_state, GoalState.BLOCKED)
        self.assertEqual(analysis.suggested_action, SUGGEST_RESOLVE_BLOCKER)

    # -------------------------------------------------------------
    # 22. Title-Oriented Progress Match
    # -------------------------------------------------------------
    def test_title_oriented_progress_match(self):
        records = (
            StepStateRecord(index=1, state=StepState.PENDING, detail="Clarify scope and constraints"),
            StepStateRecord(index=2, state=StepState.PENDING, detail="Identify smallest practical change"),
        )
        state = PlanContinuityState(goal_title="Test", step_records=records)
        event = detect_progress_event("I finished clarifying scope", state)
        self.assertEqual(event.kind, ProgressEventKind.STEP_COMPLETED)
        self.assertEqual(event.target_step_index, 1)

    # -------------------------------------------------------------
    # 23. Deterministic Repeated Calls
    # -------------------------------------------------------------
    def test_deterministic_repeated_calls(self):
        state = _create_sample_state(4)
        query = "Finished step 2"
        for _ in range(5):
            ev = detect_progress_event(query, state)
            self.assertEqual(ev.kind, ProgressEventKind.STEP_COMPLETED)
            self.assertEqual(ev.target_step_index, 2)
            analysis = apply_progress_event(state, ev)
            self.assertEqual(analysis.continuity_state.step_records[1].state, StepState.COMPLETED)
            self.assertEqual(analysis.continuity_state.completed_count, 1)


if __name__ == "__main__":
    unittest.main()
