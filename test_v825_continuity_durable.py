"""V8.25 Phase 2 tests: Durable state serialization and parser recovery.

Tests:
- Structural state markers round trip ([x], [>], [!], [-], [ ])
- Legacy unmarked plans round trip (backward compatibility)
- Title cleaning and marker non-contamination
- Prevention of marker nesting ('[x] [x]')
- 300-character ceiling and word-boundary truncation (no mid-token 'Defe')
- Fail-closed behavior on state overflow (no false completion)
- Arbitrary bracketed text handling ([delete], [EXEC])
- Sensitive credential scrubbing
- Deterministic repeated serialization
- V8.24.1 durable recovery compatibility
"""

from __future__ import annotations

import unittest

from orchestration.plan.continuity.types import StepState
from orchestration.plan.durable import (
    DURABLE_PLAN_LIMIT,
    extract_state_and_clean_title,
    looks_like_plan_text,
    preserve_plan_durable_text,
    serialize_plan_durable,
    truncate_at_word,
)
from orchestration.plan.parse import (
    parse_plan_from_assistant_text,
    parse_step_states_from_assistant_text,
)
from orchestration.plan.types import (
    PlanConfidence,
    PlanResult,
    PlanStatus,
    PlanStepItem,
    PlanStepKind,
)


def _make_plan(num_steps: int = 4, title: str = "Improve Python skills") -> PlanResult:
    items = tuple(
        PlanStepItem(
            index=i,
            title=f"Step {i} title",
            detail=f"Detail for step {i}",
            kind=PlanStepKind.PREPARE,
        )
        for i in range(1, num_steps + 1)
    )
    return PlanResult(
        status=PlanStatus.OK,
        title=title,
        steps=items,
        confidence=PlanConfidence.MEDIUM,
    )


class TestV825ContinuityDurable(unittest.TestCase):
    # -------------------------------------------------------------
    # 1. Completed marker round trip
    # -------------------------------------------------------------
    def test_01_completed_marker_round_trip(self):
        plan = _make_plan(2)
        states = (StepState.COMPLETED, StepState.COMPLETED)
        raw = serialize_plan_durable(plan, step_states=states, limit=300)
        self.assertIsNotNone(raw)
        self.assertIn("1. [x] Step 1 title", raw)
        self.assertIn("2. [x] Step 2 title", raw)

        parsed = parse_plan_from_assistant_text(raw)
        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.step_states, states)
        self.assertEqual(parsed.result.steps[0].title, "Step 1 title")
        self.assertEqual(parsed.result.steps[1].title, "Step 2 title")

    # -------------------------------------------------------------
    # 2. In-progress marker round trip
    # -------------------------------------------------------------
    def test_02_in_progress_marker_round_trip(self):
        plan = _make_plan(2)
        states = (StepState.IN_PROGRESS, StepState.IN_PROGRESS)
        raw = serialize_plan_durable(plan, step_states=states)
        self.assertIn("[>]", raw)

        parsed = parse_plan_from_assistant_text(raw)
        self.assertEqual(parsed.step_states, states)
        self.assertEqual(parsed.result.steps[0].title, "Step 1 title")

    # -------------------------------------------------------------
    # 3. Blocked marker round trip
    # -------------------------------------------------------------
    def test_03_blocked_marker_round_trip(self):
        plan = _make_plan(2)
        states = (StepState.BLOCKED, StepState.BLOCKED)
        raw = serialize_plan_durable(plan, step_states=states)
        self.assertIn("[!]", raw)

        parsed = parse_plan_from_assistant_text(raw)
        self.assertEqual(parsed.step_states, states)
        self.assertEqual(parsed.result.steps[0].title, "Step 1 title")

    # -------------------------------------------------------------
    # 4. Skipped marker round trip
    # -------------------------------------------------------------
    def test_04_skipped_marker_round_trip(self):
        plan = _make_plan(2)
        states = (StepState.SKIPPED, StepState.SKIPPED)
        raw = serialize_plan_durable(plan, step_states=states)
        self.assertIn("[-]", raw)

        parsed = parse_plan_from_assistant_text(raw)
        self.assertEqual(parsed.step_states, states)
        self.assertEqual(parsed.result.steps[0].title, "Step 1 title")

    # -------------------------------------------------------------
    # 5. Pending marker round trip
    # -------------------------------------------------------------
    def test_05_pending_marker_round_trip(self):
        plan = _make_plan(2)
        states = (StepState.PENDING, StepState.PENDING)
        raw = serialize_plan_durable(plan, step_states=states)
        self.assertIn("[ ]", raw)

        parsed = parse_plan_from_assistant_text(raw)
        self.assertEqual(parsed.step_states, states)
        self.assertEqual(parsed.result.steps[0].title, "Step 1 title")

    # -------------------------------------------------------------
    # 6. Mixed-state round trip
    # -------------------------------------------------------------
    def test_06_mixed_state_round_trip(self):
        plan = _make_plan(5)
        states = (
            StepState.COMPLETED,
            StepState.IN_PROGRESS,
            StepState.BLOCKED,
            StepState.SKIPPED,
            StepState.PENDING,
        )
        raw = serialize_plan_durable(plan, step_states=states)
        self.assertIsNotNone(raw)
        self.assertIn("1. [x]", raw)
        self.assertIn("2. [>]", raw)
        self.assertIn("3. [!]", raw)
        self.assertIn("4. [-]", raw)
        self.assertIn("5. [ ]", raw)

        parsed = parse_plan_from_assistant_text(raw)
        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.step_states, states)

    # -------------------------------------------------------------
    # 7. Legacy unmarked plan round trip
    # -------------------------------------------------------------
    def test_07_legacy_unmarked_plan_round_trip(self):
        legacy = (
            "Plan: Python skills Steps: "
            "1. Clarify scope 2. Identify smallest change 3. Implement it"
        )
        parsed = parse_plan_from_assistant_text(legacy)
        self.assertTrue(parsed.ok)
        self.assertEqual(len(parsed.result.steps), 3)
        self.assertEqual(parsed.result.steps[0].title, "Clarify scope")
        # Legacy unmarked steps default to PENDING
        self.assertEqual(
            parsed.step_states,
            (StepState.PENDING, StepState.PENDING, StepState.PENDING),
        )

    # -------------------------------------------------------------
    # 8. Marker does not contaminate title
    # -------------------------------------------------------------
    def test_08_marker_does_not_contaminate_title(self):
        raw = "Plan: Test Steps: 1. [x] Clarify scope 2. [>] Write code"
        parsed = parse_plan_from_assistant_text(raw)
        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.result.steps[0].title, "Clarify scope")
        self.assertEqual(parsed.result.steps[1].title, "Write code")
        self.assertNotIn("[x]", parsed.result.steps[0].title)
        self.assertNotIn("[>]", parsed.result.steps[1].title)

    # -------------------------------------------------------------
    # 9. Repeated parse/serialize does not create marker nesting
    # -------------------------------------------------------------
    def test_09_no_marker_nesting_on_repeated_cycle(self):
        state, clean = extract_state_and_clean_title("[x] [x] [x] Clarify scope")
        self.assertEqual(state, StepState.COMPLETED)
        self.assertEqual(clean, "Clarify scope")

        plan = PlanResult(
            status=PlanStatus.OK,
            title="Python Plan",
            steps=(PlanStepItem(index=1, title="[x] Clarify scope"),),
            confidence=PlanConfidence.HIGH,
        )
        s1 = serialize_plan_durable(plan, step_states=(StepState.COMPLETED,))
        p1 = parse_plan_from_assistant_text(s1)
        self.assertEqual(p1.result.steps[0].title, "Clarify scope")

        s2 = serialize_plan_durable(p1.result, step_states=p1.step_states)
        p2 = parse_plan_from_assistant_text(s2)
        self.assertEqual(p2.result.steps[0].title, "Clarify scope")
        self.assertNotIn("[x] [x]", s2)
        # Exactly one marker per step
        self.assertEqual(s2.count("[x]"), 1)

    # -------------------------------------------------------------
    # 10. 6-step maximum boundary
    # -------------------------------------------------------------
    def test_10_6_step_maximum_boundary(self):
        plan_6 = _make_plan(6)
        raw = serialize_plan_durable(plan_6)
        self.assertIsNotNone(raw)
        self.assertLessEqual(len(raw), DURABLE_PLAN_LIMIT)

        # 7 steps fails closed
        plan_7 = _make_plan(7)
        self.assertIsNone(serialize_plan_durable(plan_7))

    # -------------------------------------------------------------
    # 11. Exact 300-character boundary
    # -------------------------------------------------------------
    def test_11_300_character_boundary(self):
        plan = _make_plan(6, title="A" * 80)
        raw = serialize_plan_durable(plan, limit=300)
        self.assertIsNotNone(raw)
        self.assertLessEqual(len(raw), 300)

    # -------------------------------------------------------------
    # 12. Over-limit behavior fails closed
    # -------------------------------------------------------------
    def test_12_over_limit_behavior_fails_closed(self):
        plan = _make_plan(4)
        self.assertIsNone(serialize_plan_durable(plan, limit=30))

    # -------------------------------------------------------------
    # 13. Long title truncation
    # -------------------------------------------------------------
    def test_13_long_title_truncation(self):
        long_title = (
            "This is an extremely long plan title that definitely exceeds "
            "the standard limit"
        )
        plan = _make_plan(2, title=long_title)
        raw = serialize_plan_durable(plan, limit=300)
        self.assertIsNotNone(raw)
        self.assertLessEqual(len(raw), 300)
        self.assertNotIn("Defe", raw)

    # -------------------------------------------------------------
    # 14. Long step title truncation
    # -------------------------------------------------------------
    def test_14_long_step_title_truncation(self):
        long_step_title = (
            "Identify the smallest and most practical initial change to "
            "the codebase without breaking anything"
        )
        items = (PlanStepItem(index=1, title=long_step_title),)
        plan = PlanResult(status=PlanStatus.OK, title="Plan", steps=items)
        raw = serialize_plan_durable(plan, limit=300)
        self.assertIsNotNone(raw)
        self.assertLessEqual(len(raw), 300)
        # Marker added AFTER truncation — marker itself intact
        self.assertIn("[ ]", raw)
        self.assertNotIn("Defe", raw)

    # -------------------------------------------------------------
    # 15. Completed + pending final-step preservation (no false completion)
    # -------------------------------------------------------------
    def test_15_no_false_completion_on_overflow(self):
        raw = (
            "Plan: Test Steps: "
            "1. [x] Step 1 Complete 2. [x] Step 2 Complete "
            "3. [x] Step 3 Complete 4. [ ] Step 4 Pending"
        )
        # Fitting all 4 requires ~110 chars. Give only 70 chars.
        preserved = preserve_plan_durable_text(raw, limit=70)
        # Must fail closed (return "") rather than return 3 completed steps
        self.assertEqual(preserved, "")

    # -------------------------------------------------------------
    # 16. Malformed marker handling
    # -------------------------------------------------------------
    def test_16_malformed_marker_handling(self):
        raw = (
            "Plan: Test Steps: "
            "1. [?] Weird marker title 2. [123] Number title "
            "3. [x something] Clarify scope 4. [[x]] Nested braces"
        )
        parsed = parse_plan_from_assistant_text(raw)
        self.assertTrue(parsed.ok)
        # Unrecognized markers stay in title; state defaults to PENDING
        self.assertEqual(parsed.step_states[0], StepState.PENDING)
        self.assertIn("[?]", parsed.result.steps[0].title)
        self.assertEqual(parsed.step_states[2], StepState.PENDING)
        self.assertIn("[x something]", parsed.result.steps[2].title)
        self.assertEqual(parsed.step_states[3], StepState.PENDING)
        self.assertIn("[[x]]", parsed.result.steps[3].title)

    # -------------------------------------------------------------
    # 17. Arbitrary bracket text handling
    # -------------------------------------------------------------
    def test_17_arbitrary_bracket_text_handling(self):
        raw = (
            "Plan: Test Steps: "
            "1. [delete] Old file 2. [EXEC] Do not run "
            "3. [run command] something"
        )
        parsed = parse_plan_from_assistant_text(raw)
        self.assertTrue(parsed.ok)
        self.assertEqual(
            parsed.step_states,
            (StepState.PENDING, StepState.PENDING, StepState.PENDING),
        )
        self.assertEqual(parsed.result.steps[0].title, "[delete] Old file")
        self.assertEqual(parsed.result.steps[1].title, "[EXEC] Do not run")
        self.assertEqual(parsed.result.steps[2].title, "[run command] something")

    # -------------------------------------------------------------
    # 18. Sensitive text sanitization
    # -------------------------------------------------------------
    def test_18_sensitive_text_sanitization(self):
        # Existing contract: sensitive blobs sanitize to empty → drop that step.
        # Keep one clean step so the plan remains parseable without secrets.
        raw = (
            "Plan: Test Steps: "
            "1. Clarify scope "
            "2. [x] Step with api_key=fake_token_placeholder"
        )
        parsed = parse_plan_from_assistant_text(raw)
        self.assertTrue(parsed.ok)
        titles = " ".join(s.title for s in parsed.result.steps)
        self.assertNotIn("fake_token_placeholder", titles)
        self.assertNotIn("api_key", titles.lower())
        self.assertEqual(parsed.result.steps[0].title, "Clarify scope")

    # -------------------------------------------------------------
    # 19. Deterministic repeated serialization
    # -------------------------------------------------------------
    def test_19_deterministic_repeated_serialization(self):
        plan = _make_plan(4)
        states = (
            StepState.COMPLETED,
            StepState.IN_PROGRESS,
            StepState.PENDING,
            StepState.PENDING,
        )
        first = serialize_plan_durable(plan, step_states=states)
        for _ in range(5):
            repeated = serialize_plan_durable(plan, step_states=states)
            self.assertEqual(first, repeated)

    # -------------------------------------------------------------
    # 20. V8.24.1 durable recovery compatibility
    # -------------------------------------------------------------
    def test_20_v8241_durable_recovery_compatibility(self):
        flat = (
            "Plan: Plan for: improve my Python skills Steps: "
            "1. Clarify scope 2. Identify the smallest change "
            "3. Implement it 4. Test it"
        )
        out = parse_plan_from_assistant_text(flat)
        self.assertTrue(out.ok)
        self.assertEqual(len(out.result.steps), 4)
        self.assertEqual(out.result.steps[0].title, "Clarify scope")
        self.assertEqual(out.result.steps[1].title, "Identify the smallest change")
        for t in (s.title for s in out.result.steps):
            self.assertFalse(t.endswith("Defe"))
            self.assertNotIn("[x]", t)

        # Unmarked serialization still parseable when markers disabled
        plan = PlanResult(
            status=PlanStatus.OK,
            title="Plan for: improve my Python skills",
            steps=out.result.steps,
            confidence=PlanConfidence.MEDIUM,
        )
        legacy = serialize_plan_durable(plan, include_markers=False, limit=300)
        self.assertIsNotNone(legacy)
        self.assertNotIn("[x]", legacy)
        self.assertNotIn("[ ]", legacy)
        reparsed = parse_plan_from_assistant_text(legacy)
        self.assertTrue(reparsed.ok)
        self.assertEqual(reparsed.result.steps[0].title, "Clarify scope")

    # -------------------------------------------------------------
    # Historical V8.24.1 mid-token regression
    # -------------------------------------------------------------
    def test_21_v8241_word_boundary_no_mid_token_truncation(self):
        s = truncate_at_word("Defer heavy work if needed", 4)
        self.assertNotEqual(s, "Defe")
        self.assertFalse(s.endswith("Defe"))
        self.assertTrue(len(s) <= 4 or s == "")

    # -------------------------------------------------------------
    # 6-step full marker vector + 300 boundary
    # -------------------------------------------------------------
    def test_22_six_step_full_marker_vector(self):
        titles = (
            "Clarify the overall project scope carefully",
            "Identify the smallest useful change first",
            "Resolve the blocking dependency early",
            "Skip optional polish until later",
            "Implement the core change safely",
            "Verify the result with a quick check",
        )
        states = (
            StepState.COMPLETED,
            StepState.IN_PROGRESS,
            StepState.BLOCKED,
            StepState.SKIPPED,
            StepState.PENDING,
            StepState.PENDING,
        )
        plan = PlanResult(
            status=PlanStatus.OK,
            title="Improve Python skills with a long descriptive goal title",
            steps=tuple(
                PlanStepItem(index=i, title=t, kind=PlanStepKind.PREPARE)
                for i, t in enumerate(titles, start=1)
            ),
            confidence=PlanConfidence.MEDIUM,
        )
        raw = serialize_plan_durable(plan, step_states=states, limit=300)
        # Either complete vector fits, or fail closed — never partial.
        if raw is None:
            return
        self.assertLessEqual(len(raw), 300)
        self.assertIn("[x]", raw)
        self.assertIn("[>]", raw)
        self.assertIn("[!]", raw)
        self.assertIn("[-]", raw)
        self.assertIn("[ ]", raw)
        parsed = parse_plan_from_assistant_text(raw)
        self.assertTrue(parsed.ok)
        self.assertEqual(len(parsed.result.steps), 6)
        self.assertEqual(parsed.step_states, states)
        for step in parsed.result.steps:
            self.assertFalse(step.title.startswith("["))
            self.assertNotIn("Defe", step.title)

    # -------------------------------------------------------------
    # Nested adjacent markers stripped
    # -------------------------------------------------------------
    def test_23_adjacent_nested_markers(self):
        state, clean = extract_state_and_clean_title("[x][x] Clarify scope")
        self.assertEqual(state, StepState.COMPLETED)
        self.assertEqual(clean, "Clarify scope")

        state2, clean2 = extract_state_and_clean_title("[x] [>] Clarify scope")
        self.assertEqual(state2, StepState.COMPLETED)
        self.assertEqual(clean2, "Clarify scope")

        raw = "Plan: Test Steps: 1. [x] [>] Clarify scope"
        parsed = parse_plan_from_assistant_text(raw)
        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.step_states[0], StepState.COMPLETED)
        self.assertEqual(parsed.result.steps[0].title, "Clarify scope")

    # -------------------------------------------------------------
    # Helper parse_step_states_from_assistant_text
    # -------------------------------------------------------------
    def test_24_parse_step_states_helper(self):
        raw = "Plan: Test Steps: 1. [x] Clarify 2. [!] Blocked"
        states = parse_step_states_from_assistant_text(raw)
        self.assertEqual(states, (StepState.COMPLETED, StepState.BLOCKED))

    # -------------------------------------------------------------
    # Marker after truncation (not before)
    # -------------------------------------------------------------
    def test_25_marker_added_after_truncation(self):
        # Very long first word would fail mid-token if marker were prepended first.
        plan = PlanResult(
            status=PlanStatus.OK,
            title="Plan",
            steps=(
                PlanStepItem(
                    index=1,
                    title="Defer heavy work if needed later tonight",
                ),
            ),
            confidence=PlanConfidence.LOW,
        )
        raw = serialize_plan_durable(
            plan, step_states=(StepState.COMPLETED,), limit=300
        )
        self.assertIsNotNone(raw)
        self.assertTrue(raw.startswith("Plan:"))
        self.assertIn("1. [x] ", raw)
        # Historical mid-token failure was literally "Defe" (not "Defer").
        self.assertNotRegex(raw, r"\bDefe\b")
        self.assertFalse(raw.rstrip().endswith("Defe"))
        self.assertIn("Defer", raw)
        self.assertTrue(looks_like_plan_text(raw))


if __name__ == "__main__":
    unittest.main()
