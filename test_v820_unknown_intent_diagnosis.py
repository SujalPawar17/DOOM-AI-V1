"""V8.20 PART A — UNKNOWN_INTENT diagnosis (no classifier fix).

Documents that normalize_intent is deterministic for exact strings, and that
UNKNOWN_INTENT is produced only when classification returns UNKNOWN.
"""
from __future__ import annotations

import collections
import os
import re
import unittest
from pathlib import Path

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")

from orchestration.goal.kernel import process_goal
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.types import IntentClass
from orchestration.production import prepare_v8_request
from orchestration.executor import ExecutionIdentity

ROOT = Path(__file__).resolve().parent
APP_JS = ROOT / "dashboard" / "static" / "js" / "app.js"
NORMALIZER = ROOT / "orchestration" / "goal" / "normalizer.py"
KERNEL = ROOT / "orchestration" / "goal" / "kernel.py"
PRODUCTION = ROOT / "orchestration" / "production.py"

EXACT_QUERIES = (
    "What programming language do I prefer?",
    "Considering my current computer, what should I focus on?",
    "Should I run a heavy AI task right now?",
    "Considering what you know about me and my current computer, give me brief advice.",
    "Explain Python lists.",
    "What is my current system health?",
)

EXPECTED = {
    "What programming language do I prefer?": IntentClass.CONVERSATION,
    "Considering my current computer, what should I focus on?": IntentClass.CONVERSATION,
    "Should I run a heavy AI task right now?": IntentClass.CONVERSATION,
    "Considering what you know about me and my current computer, give me brief advice.": IntentClass.CONVERSATION,
    "Explain Python lists.": IntentClass.CONVERSATION,
    "What is my current system health?": IntentClass.SYSTEM_STATUS,
}

STT_JUNK_UNKNOWN = ("um", "yeah", "okay", "doom", "what", "computer", "focus", "advice", "should")


def _ident():
    return ExecutionIdentity(owner_id="alice", session_id="diag-sess", computer_session_id="")


class TestV820UnknownIntentDiagnosis(unittest.TestCase):
    def test_01_classifier_is_pure_function_no_mutable_state(self):
        src = NORMALIZER.read_text(encoding="utf-8")
        self.assertNotIn("global ", src)
        self.assertNotIn("random", src.lower())
        self.assertNotIn("uuid", src.lower())
        self.assertNotIn("time.time", src)
        self.assertNotIn("session", src.lower().split("def normalize_intent")[0])
        # Function body must not read env / DB / ASK
        body = src.split("def normalize_intent", 1)[1]
        self.assertNotIn("os.environ", body)
        self.assertNotIn("postgres", body.lower())
        self.assertNotIn("ask", body.lower())
        self.assertNotIn("ollama", body.lower())

    def test_02_exact_queries_deterministic_50x(self):
        for q in EXACT_QUERIES:
            counts = collections.Counter(
                normalize_intent(q) for _ in range(50)
            )
            self.assertEqual(len(counts), 1, q)
            self.assertEqual(next(iter(counts)), EXPECTED[q], q)

    def test_03_unknown_intent_sources_documented(self):
        """Every production UNKNOWN_INTENT path is kernel UNKNOWN classification."""
        ksrc = KERNEL.read_text(encoding="utf-8")
        self.assertIn('reason_code="UNKNOWN_INTENT"', ksrc)
        self.assertIn("IntentClass.UNKNOWN", ksrc)
        psrc = PRODUCTION.read_text(encoding="utf-8")
        self.assertIn("IntentClass.UNKNOWN", psrc)
        self.assertIn("IntentClass.AMBIGUOUS", psrc)

    def test_04_empty_and_stt_junk_map_to_unknown(self):
        self.assertEqual(normalize_intent(""), IntentClass.UNKNOWN)
        self.assertEqual(normalize_intent("   "), IntentClass.UNKNOWN)
        for q in STT_JUNK_UNKNOWN:
            self.assertEqual(normalize_intent(q), IntentClass.UNKNOWN, q)
            result = process_goal(q, context={"owner_id": "alice", "session_id": "s"})
            self.assertEqual(result.reason_code, "UNKNOWN_INTENT", q)

    def test_05_exact_prepare_paths(self):
        for q, intent in EXPECTED.items():
            prep = prepare_v8_request(q, identity=_ident())
            self.assertEqual(prep.intent, intent.value, q)
            if intent in (IntentClass.UNKNOWN, IntentClass.AMBIGUOUS):
                self.assertIsNone(prep.plan)
            else:
                self.assertIsNotNone(prep.plan, q)
                self.assertEqual(prep.status, "OK", q)

    def test_06_handsfree_race_exists_in_dashboard_js(self):
        """Documented race: continuous recognition can call executeGoal while typing."""
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("Listening (hands-free)", js)
        self.assertIn("handsFree: true", js)
        self.assertIn("recognition.continuous = true", js)
        self.assertIn("commandInput.value = cleanPrompt", js)
        self.assertIn("executeGoal(cleanPrompt)", js)
        # No in-flight lock around executeGoal historically (diagnosis).
        # After PART B, mic gate must exist; race documentation still valid for ON mode.
        self.assertRegex(js, r"recognition\.onresult\s*=")

    def test_07_wake_strip_can_leave_short_unknown(self):
        """'doom what' after wake strip → 'what' → UNKNOWN (hands-free pattern)."""
        transcript = "doom what"
        lower = transcript.lower()
        clean = transcript
        for w in ("hey doom", "hello doom", "ok doom", "doom"):
            if lower.startswith(w):
                clean = transcript[len(w):].strip().lstrip(",: ").strip()
                break
        self.assertEqual(clean, "what")
        self.assertEqual(normalize_intent(clean), IntentClass.UNKNOWN)

    def test_08_classification_independent_of_prior_calls(self):
        normalize_intent("Click the button")
        normalize_intent("Restart Ollama.")
        self.assertEqual(
            normalize_intent("Should I run a heavy AI task right now?"),
            IntentClass.CONVERSATION,
        )
        self.assertEqual(
            normalize_intent("What programming language do I prefer?"),
            IntentClass.CONVERSATION,
        )


if __name__ == "__main__":
    unittest.main()
