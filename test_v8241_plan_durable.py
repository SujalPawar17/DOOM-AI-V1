"""V8.24.1 plan durable serialization / refine recovery tests."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ACT_ENABLED", "false")

from core.cost_guard.guard import cost_guard
from core.cost_guard.types import CostPolicyMode
from orchestration.conversation.context import (
    MAX_CONV_MSG_CHARS,
    get_conversation_turns,
    record_conversation_turn,
    reset_conversation_context_for_tests,
    set_anchor_ttl_for_tests,
)
from orchestration.conversation.resolve import durable_assistant_thread_text
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.types import IntentClass
from orchestration.plan.durable import (
    DURABLE_PLAN_LIMIT,
    looks_like_plan_text,
    preserve_plan_durable_text,
    serialize_plan_durable,
    truncate_at_word,
)
from orchestration.plan.modes import PlanMode, detect_plan_mode
from orchestration.plan.parse import parse_plan_from_assistant_text
from orchestration.plan.types import (
    PlanConfidence,
    PlanResult,
    PlanStatus,
    PlanStepItem,
    PlanStepKind,
)
from orchestration.production import format_v8_execution, prepare_v8_request


def _ident(owner="alice", session="sess-a"):
    return ExecutionIdentity(owner_id=owner, session_id=session, computer_session_id="")


def _resource_result() -> PlanResult:
    return PlanResult(
        status=PlanStatus.OK,
        title="Plan to reduce resource pressure",
        steps=(
            PlanStepItem(
                1,
                "Close unused applications",
                "Free memory and CPU by closing apps you do not need.",
                PlanStepKind.PREPARE,
            ),
            PlanStepItem(
                2,
                "Re-check system status",
                "Review current memory and CPU in Dashboard or system status.",
                PlanStepKind.CHECK,
            ),
            PlanStepItem(
                3,
                "Confirm available resources",
                "Ensure pressure has dropped before heavy work.",
                PlanStepKind.VERIFY,
            ),
            PlanStepItem(
                4,
                "Defer heavy work if needed",
                "Postpone heavy AI tasks until pressure is normal.",
                PlanStepKind.OPTIONAL,
            ),
        ),
        confidence=PlanConfidence.MEDIUM,
    )


class TestV8241PlanDurable(unittest.TestCase):
    def setUp(self):
        reset_conversation_context_for_tests()
        set_anchor_ttl_for_tests(None)

    def tearDown(self):
        reset_conversation_context_for_tests()
        set_anchor_ttl_for_tests(None)

    def test_01_truncate_at_word_no_mid_token(self):
        s = truncate_at_word("Defer heavy work if needed", 4)
        self.assertNotEqual(s, "Defe")
        self.assertFalse(s.endswith("Defe"))
        self.assertTrue(len(s) <= 4 or s == "")

    def test_02_serialize_fits_300(self):
        raw = serialize_plan_durable(_resource_result(), limit=DURABLE_PLAN_LIMIT)
        self.assertIsNotNone(raw)
        self.assertLessEqual(len(raw), DURABLE_PLAN_LIMIT)
        self.assertIn("Plan:", raw)
        self.assertIn("Steps:", raw)

    def test_03_serialize_titles_only_no_detail_merge(self):
        raw = serialize_plan_durable(_resource_result(), limit=300)
        self.assertNotIn("Free memory and CPU", raw)
        self.assertNotIn("Postpone heavy AI", raw)
        self.assertIn("Close unused applications", raw)

    def test_04_serialize_all_steps_complete(self):
        raw = serialize_plan_durable(_resource_result(), limit=300)
        parsed = parse_plan_from_assistant_text(raw)
        self.assertTrue(parsed.ok)
        titles = [s.title for s in parsed.result.steps]
        self.assertEqual(len(titles), 4)
        for t in titles:
            self.assertFalse(t.endswith("Defe"))
            self.assertNotIn("Free memory", t)

    def test_05_multiline_parse(self):
        text = (
            "Plan: Plan for: improve my Python skills\n\n"
            "Steps:\n"
            "1. Clarify scope\n"
            "   Define the smallest useful version.\n"
            "2. Identify the smallest change\n"
            "3. Implement it\n\n"
            "Confidence: Medium\n"
        )
        out = parse_plan_from_assistant_text(text)
        self.assertTrue(out.ok)
        self.assertGreaterEqual(len(out.result.steps), 3)

    def test_06_flattened_durable_parse(self):
        flat = (
            "Plan: Plan for: improve my Python skills Steps: "
            "1. Clarify scope 2. Identify the smallest change "
            "3. Implement it 4. Test it"
        )
        out = parse_plan_from_assistant_text(flat)
        self.assertTrue(out.ok)
        self.assertEqual(len(out.result.steps), 4)
        self.assertEqual(out.result.steps[0].title, "Clarify scope")

    def test_07_durable_assistant_preserves_plan_shape(self):
        seeded = serialize_plan_durable(_resource_result(), limit=300)
        out = durable_assistant_thread_text(seeded, limit=MAX_CONV_MSG_CHARS)
        self.assertTrue(looks_like_plan_text(out))
        self.assertIn("Plan:", out)
        self.assertIn("Steps:", out)
        self.assertNotIn("Free memory and CPU", out)

    def test_08_preserve_no_mid_cut_on_overflow(self):
        huge = (
            "Plan: " + ("LongTitle " * 20) + " Steps: "
            + " ".join(f"{i}. StepTitle{i} " + ("word " * 30) for i in range(1, 7))
        )
        out = preserve_plan_durable_text(huge, limit=300)
        if out:
            self.assertLessEqual(len(out), 300)
            self.assertFalse(out.rstrip().endswith("Defe"))
            # Must not end mid-token: last char alphanumeric ok if full word
            self.assertTrue(looks_like_plan_text(out) or out == "")

    def test_09_missing_plan_clarify(self):
        out = parse_plan_from_assistant_text("")
        self.assertFalse(out.ok)
        self.assertIn("recent plan", out.clarify.lower())

    def test_10_oversized_serialize_fail_closed(self):
        steps = tuple(
            PlanStepItem(
                i,
                "VeryLongStepTitle" + ("X" * 60),
                "detail",
                PlanStepKind.PREPARE,
            )
            for i in range(1, 7)
        )
        r = PlanResult(
            status=PlanStatus.OK,
            title="T" * 100,
            steps=steps,
            confidence=PlanConfidence.LOW,
        )
        # Extremely tight limit
        self.assertIsNone(serialize_plan_durable(r, limit=40))

    def test_11_no_identity_in_durable(self):
        raw = serialize_plan_durable(_resource_result(), limit=300) or ""
        low = raw.lower()
        for banned in (
            "owner_id",
            "session_id",
            "csrf",
            "plan_hash",
            "executionidentity",
            "cookie",
            "api_key",
        ):
            self.assertNotIn(banned, low)

    def test_12_live_regression_create_refine(self):
        """Reproduce Dashboard A→B without title/detail merge or partial steps."""
        set_anchor_ttl_for_tests(1200)
        ident = _ident()
        prep = prepare_v8_request(
            "Make me a plan to improve my Python skills", identity=ident
        )
        self.assertIsNotNone(prep.plan)
        res = execute_plan(prep.plan, identity=ident, authorized_plan_hash="")
        text = format_v8_execution(res, intent=prep.intent)
        self.assertIn("Plan:", text)
        self.assertNotIn("recent plan", text.lower())

        turns = get_conversation_turns(ident.owner_id, ident.session_id)
        asst = [t for t in turns if t.get("role") == "assistant"]
        self.assertTrue(asst)
        stored = asst[-1]["text"]
        self.assertLessEqual(len(stored), MAX_CONV_MSG_CHARS)
        self.assertIn("Plan:", stored)
        self.assertIn("Steps:", stored)
        # No merged resource details from live system template bleed
        for bad in ("Free memory and CPU", "Defe"):
            self.assertNotIn(bad, stored)

        parsed = parse_plan_from_assistant_text(stored)
        self.assertTrue(parsed.ok, stored)
        for step in parsed.result.steps:
            self.assertFalse(step.title.endswith("Defe"))
            self.assertNotIn("Free memory and CPU", step.title)
            self.assertEqual(step.detail, "")

        prep2 = prepare_v8_request("Improve that plan", identity=ident)
        self.assertEqual(detect_plan_mode("Improve that plan"), PlanMode.REFINE)
        res2 = execute_plan(prep2.plan, identity=ident, authorized_plan_hash="")
        out2 = format_v8_execution(res2, intent=prep2.intent)
        self.assertNotIn("recent plan", out2.lower())
        self.assertIn("Plan:", out2)
        self.assertIn("Steps:", out2)

    def test_13_refine_next_validate_depend_modes(self):
        set_anchor_ttl_for_tests(1200)
        ident = _ident("bob", "sess-b")
        seeded = serialize_plan_durable(_resource_result(), limit=300)
        record_conversation_turn(
            ident.owner_id,
            ident.session_id,
            user_text="Plan how to free memory before a heavy AI task",
            assistant_text=seeded,
        )
        for q, mode in (
            ("Improve that plan", PlanMode.REFINE),
            ("What should I do first?", PlanMode.NEXT),
            ("Are there any blockers?", PlanMode.VALIDATE),
            ("What are the dependencies?", PlanMode.DEPEND),
        ):
            self.assertEqual(detect_plan_mode(q), mode)
            prep = prepare_v8_request(q, identity=ident)
            self.assertEqual(prep.intent, IntentClass.PLAN.value)
            res = execute_plan(prep.plan, identity=ident, authorized_plan_hash="")
            out = format_v8_execution(res, intent=prep.intent)
            self.assertNotIn("recent plan", out.lower(), q)

    def test_14_routing_unchanged(self):
        self.assertEqual(
            normalize_intent("Which is better, Python or Node?"),
            IntentClass.DECISION,
        )
        self.assertEqual(normalize_intent("Click the Save button"), IntentClass.COMPUTER)

    def test_15_cost_guard(self):
        self.assertEqual(cost_guard.policy, CostPolicyMode.HARD_ZERO)

    def test_16_no_action_imports(self):
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parent / "orchestration" / "plan"
        for path in root.glob("*.py"):
            if path.name.startswith("_"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(node.module.startswith("proactive.computer"))
                    self.assertNotIn("authorization", node.module or "")


if __name__ == "__main__":
    unittest.main()
