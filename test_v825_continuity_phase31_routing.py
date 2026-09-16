"""V8.25 Phase 3.1 tests: natural continuation routing to PLAN continuity."""

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
)
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.types import IntentClass
from orchestration.plan.durable import serialize_plan_durable
from orchestration.plan.modes import PlanMode, detect_plan_mode
from orchestration.plan.types import (
    PlanConfidence,
    PlanResult,
    PlanStatus,
    PlanStepItem,
    PlanStepKind,
)
from orchestration.production import format_v8_execution, prepare_v8_request


def _ident(owner: str, session: str) -> ExecutionIdentity:
    return ExecutionIdentity(owner_id=owner, session_id=session, computer_session_id="")


def _plan() -> PlanResult:
    return PlanResult(
        status=PlanStatus.OK,
        title="Improve Python skills",
        steps=(
            PlanStepItem(1, "Clarify scope", "", PlanStepKind.PREPARE),
            PlanStepItem(2, "Identify smallest change", "", PlanStepKind.PREPARE),
            PlanStepItem(3, "Implement it", "", PlanStepKind.PREPARE),
        ),
        confidence=PlanConfidence.MEDIUM,
    )


def _seed(owner: str, session: str) -> None:
    record_conversation_turn(
        owner,
        session,
        user_text="Make a plan to improve Python",
        assistant_text=serialize_plan_durable(_plan(), limit=300),
    )


class TestV825ContinuityPhase31Routing(unittest.TestCase):
    def setUp(self):
        reset_conversation_context_for_tests()

    def _next_step_with_anchor(self, phrase: str, owner: str, session: str) -> str:
        _seed(owner, session)
        ident = _ident(owner, session)
        prep = prepare_v8_request(phrase, identity=ident)
        self.assertEqual(prep.intent, IntentClass.PLAN.value, prep.status)
        self.assertIsNotNone(prep.plan)
        res = execute_plan(prep.plan, identity=ident, authorized_plan_hash="")
        return format_v8_execution(res, intent=prep.intent)

    def test_01_continue_with_anchor(self):
        out = self._next_step_with_anchor("continue", "p31-a", "s-a")
        self.assertIn("Next step", out)

    def test_02_lets_continue_with_anchor(self):
        out = self._next_step_with_anchor("Let's continue.", "p31-b", "s-b")
        self.assertIn("Next step", out)

    def test_03_go_on_with_anchor(self):
        out = self._next_step_with_anchor("go on", "p31-c", "s-c")
        self.assertIn("Next step", out)

    def test_04_keep_going_with_anchor(self):
        out = self._next_step_with_anchor("keep going", "p31-d", "s-d")
        self.assertIn("Next step", out)

    def test_05_continue_without_anchor(self):
        prep = prepare_v8_request("continue", identity=_ident("p31-e", "s-e"))
        self.assertNotEqual(prep.intent, IntentClass.PLAN.value)

    def test_06_lets_continue_without_anchor(self):
        prep = prepare_v8_request("Let's continue.", identity=_ident("p31-f", "s-f"))
        self.assertNotEqual(prep.intent, IntentClass.PLAN.value)

    def test_07_what_next_remains_plan_next(self):
        out = self._next_step_with_anchor(
            "What should I do next?",
            "p31-g",
            "s-g",
        )
        self.assertIn("Next step", out)

    def test_08_blockers_remains_validate(self):
        self.assertEqual(detect_plan_mode("Are there any blockers?"), PlanMode.VALIDATE)
        self.assertEqual(normalize_intent("Are there any blockers?"), IntentClass.PLAN)

    def test_09_dependencies_remains_depend(self):
        self.assertEqual(detect_plan_mode("What are the dependencies?"), PlanMode.DEPEND)
        self.assertEqual(normalize_intent("What are the dependencies?"), IntentClass.PLAN)

    def test_10_refine_remains_refine(self):
        self.assertEqual(detect_plan_mode("Improve that plan"), PlanMode.REFINE)
        self.assertEqual(normalize_intent("Improve that plan"), IntentClass.PLAN)

    def test_11_decision_precedence(self):
        self.assertEqual(normalize_intent("Python or Node.js?"), IntentClass.DECISION)

    def test_12_computer_precedence(self):
        self.assertEqual(normalize_intent("Click Save"), IntentClass.COMPUTER)

    def test_13_browser_precedence(self):
        self.assertEqual(
            normalize_intent("open google.com in browser"),
            IntentClass.BROWSER,
        )


if __name__ == "__main__":
    unittest.main()
