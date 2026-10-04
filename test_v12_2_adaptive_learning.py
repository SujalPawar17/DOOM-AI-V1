#!/usr/bin/env python
"""DOOM V12.2 — Adaptive Learning tests (deterministic, local, HARD $0)."""

import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.v12.adaptive_learning import (  # noqa: E402
    AdaptiveLearning, LearningStore, KnowledgeKind, KnowledgeStatus, MAX_ITEMS_PER_OWNER,
    MAX_EVIDENCE_PER_ITEM, DAY, extract_statements,
)

OWNER = "learn_owner"
SESSION = "learn_sess"
T0 = time.mktime((2026, 10, 5, 9, 0, 0, 0, 0, -1))  # local 09:00 (morning)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def exp(i, outcome="COMPLETED", steps=("conversation RESPOND status:SUCCESS",), hour=9, owner=OWNER,
        tags=("v11_execution",), blockers=()):
    created = time.mktime((2026, 10, 1 + i, hour, 0, 0, 0, 0, -1))
    return SimpleNamespace(experience_id=f"ge_{i:04d}", owner_id=owner, outcome=outcome, step_summary=steps,
                           tags=tags, blockers=blockers, created_at=created, title="t")


class LearningBase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.learning = AdaptiveLearning(store=LearningStore(), clock=self.clock)

    def items(self, owner=OWNER):
        return {(i["kind"], i["key"]): i for i in self.learning.explain(owner)}


class TestExperienceLearning(LearningBase):

    def test_learns_reliable_workflow_from_successes(self):
        self.learning.observe_experiences(OWNER, [exp(i) for i in range(3)])
        wf = self.items()[("WORKFLOW", "workflow_conversation_respond")]
        self.assertEqual(wf["value"], "reliable")
        self.assertEqual(wf["status"], "PROMOTED")
        self.assertEqual(wf["evidence_count"], 3)
        self.assertIn("3/3 succeeded", wf["explanation"])
        self.assertIn("learned_workflow_workflow_conversation_respond", self.learning.retrieve(OWNER))

    def test_learns_failure_pattern_with_blocker(self):
        self.learning.observe_experiences(OWNER, [
            exp(i, outcome="ABANDONED", steps=("filesystem WRITE_FILE status:STEP_FAILED",),
                blockers=("failed_step:s1",)) for i in range(3)])
        wf = self.items()[("WORKFLOW", "workflow_filesystem_write_file")]
        self.assertEqual(wf["value"], "unreliable")
        self.assertIn("failed_step:s1", wf["explanation"])
        # Regression (found by V12.7): a consistent failure pattern must be promoted.
        self.assertEqual(wf["status"], "PROMOTED")
        self.assertEqual(self.learning.retrieve(OWNER)["learned_workflow_workflow_filesystem_write_file"],
                         "unreliable")

    def test_mixed_outcomes_learn_no_workflow_claim(self):
        outcomes = ["COMPLETED", "ABANDONED", "COMPLETED", "ABANDONED"]
        self.learning.observe_experiences(OWNER, [exp(i, outcome=o) for i, o in enumerate(outcomes)])
        self.assertNotIn(("WORKFLOW", "workflow_conversation_respond"), self.items())
        self.assertIn(("GOAL_PATTERN", "recurring_conversation_respond"), self.items())

    def test_confidence_threshold_keeps_weak_inferences_out(self):
        self.learning.observe_experiences(OWNER, [exp(0), exp(1)])
        wf = self.items()[("WORKFLOW", "workflow_conversation_respond")]
        self.assertEqual(wf["status"], "CANDIDATE")
        self.assertEqual(wf["classification"], KnowledgeKind.LOW_CONFIDENCE_INFERENCE.value)
        self.assertNotIn("learned_workflow_workflow_conversation_respond", self.learning.retrieve(OWNER))

    def test_habit_requires_consistent_time(self):
        self.learning.observe_experiences(OWNER, [exp(i, hour=8) for i in range(3)])
        habit = self.items()[("HABIT", "habit_conversation_respond")]
        self.assertEqual(habit["value"], "usually in the morning")
        self.assertEqual(habit["status"], "PROMOTED")
        other = AdaptiveLearning(store=LearningStore(), clock=self.clock)
        other.observe_experiences(OWNER, [exp(0, hour=8), exp(1, hour=14), exp(2, hour=20)])
        self.assertNotIn(("HABIT", "habit_conversation_respond"),
                         {(i["kind"], i["key"]) for i in other.explain(OWNER)})

    def test_repeated_observation_is_deduplicated(self):
        batch = [exp(i) for i in range(3)]
        self.learning.observe_experiences(OWNER, batch)
        before = self.learning.explain(OWNER)
        self.learning.observe_experiences(OWNER, batch)
        self.learning.observe_experiences(OWNER, batch + batch)
        self.assertEqual(self.learning.explain(OWNER), before)

    def test_foreign_owner_records_are_ignored(self):
        self.learning.observe_experiences(OWNER, [exp(i, owner="someone_else") for i in range(5)])
        self.assertEqual(self.learning.explain(OWNER), [])


class TestExplicitLearning(LearningBase):

    def test_preference_and_conflict_resolution(self):
        self.learning.observe_utterance(OWNER, SESSION, "I prefer tabs over spaces")
        self.assertIn("tabs", self.learning.retrieve(OWNER).values())
        self.clock.t += 60
        self.learning.observe_utterance(OWNER, SESSION, "I prefer spaces over tabs.")
        items = [i for i in self.learning.explain(OWNER) if i["key"] == "choice_spaces_vs_tabs"]
        statuses = {i["value"]: i["status"] for i in items}
        self.assertEqual(statuses, {"tabs": "SUPERSEDED", "spaces": "PROMOTED"})
        self.assertIn("spaces", self.learning.retrieve(OWNER).values())
        self.assertNotIn("tabs", self.learning.retrieve(OWNER).values())

    def test_like_then_dislike_conflict(self):
        self.learning.observe_utterance(OWNER, SESSION, "I like jazz")
        self.clock.t += 1
        self.learning.observe_utterance(OWNER, SESSION, "I don't like jazz")
        self.assertEqual(self.learning.retrieve(OWNER)["learned_preference_likes_jazz"], "no")

    def test_favorite_and_fact(self):
        self.learning.observe_utterance(OWNER, SESSION, "My favorite editor is VS Code")
        self.learning.observe_utterance(OWNER, SESSION, "My name is Sam")
        got = self.learning.retrieve(OWNER)
        self.assertEqual(got["learned_preference_favorite_editor"], "VS Code")
        self.assertEqual(got["learned_fact_name"], "Sam")

    def test_restatement_adds_evidence_not_duplicates(self):
        for _ in range(12):
            self.learning.observe_utterance(OWNER, SESSION, "I prefer Python for scripting")
        items = [i for i in self.learning.explain(OWNER) if i["key"] == "for_scripting"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["evidence_count"], 1)  # same statement -> same evidence reference

    def test_stale_preferences_are_not_used_until_reaffirmed(self):
        self.learning.observe_utterance(OWNER, SESSION, "I prefer Python for scripting")
        self.clock.t += 181 * DAY
        self.assertEqual(self.learning.retrieve(OWNER), {})
        self.assertEqual(self.items()[("PREFERENCE", "for_scripting")]["status"], "STALE")
        self.learning.observe_utterance(OWNER, SESSION, "I prefer Python for scripting")
        self.assertEqual(self.learning.retrieve(OWNER)["learned_preference_for_scripting"], "Python")

    def test_temporary_context_is_session_scoped_and_expires(self):
        self.learning.observe_utterance(OWNER, SESSION, "Today I'm working on the quarterly report")
        self.assertEqual(self.learning.retrieve(OWNER, SESSION)["learned_temporary_context_current_focus"],
                         "the quarterly report")
        self.assertEqual(self.learning.retrieve(OWNER, "other_session"), {})
        self.clock.t += 13 * 3600
        self.assertEqual(self.learning.retrieve(OWNER, SESSION), {})

    def test_secrets_and_sensitive_attributes_are_never_learned(self):
        for text in ("My password is hunter2", "My api key is sk_live_abcdef123456",
                     "My religion is something", "My home address is 1 Main Street",
                     "I like politics", "My diagnosis is flu", "My salary is 100", "My phone is 5551234",
                     "My birthday is in May"):
            self.assertEqual(self.learning.observe_utterance(OWNER, SESSION, text), [], text)
        self.assertEqual(self.learning.explain(OWNER), [])

    def test_arbitrary_conversation_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "learning.json")
            learning = AdaptiveLearning(store=LearningStore(path), clock=self.clock)
            learning.observe_utterance(OWNER, SESSION, "I went to the store and bought milk with Alex")
            learning.observe_utterance(OWNER, SESSION, "I prefer tea over coffee")
            raw = open(path, encoding="utf-8").read()
            self.assertNotIn("store and bought milk", raw)
            self.assertNotIn("I prefer tea over coffee", raw)  # only key/value + digest evidence
            self.assertIn('"tea"', raw)

    def test_extraction_is_bounded(self):
        self.assertEqual(extract_statements("I prefer " + "x" * 400), [])


class TestBoundsForgetPersistence(LearningBase):

    def test_owner_isolation(self):
        self.learning.observe_utterance(OWNER, SESSION, "I prefer tea over coffee")
        self.learning.observe_experiences(OWNER, [exp(i) for i in range(3)])
        self.assertEqual(self.learning.retrieve("other_owner"), {})
        self.assertEqual(self.learning.explain("other_owner"), [])

    def test_memory_bounds(self):
        for i in range(200):
            self.clock.t += 1
            self.learning.observe_utterance(OWNER, SESSION, f"My favorite thing{i:03d} is value{i}")
        self.assertLessEqual(len(self.learning.store.items(OWNER)), MAX_ITEMS_PER_OWNER)
        self.assertLessEqual(len(self.learning.retrieve(OWNER)), 6)
        self.learning.observe_experiences(OWNER, [exp(i) for i in range(30)])
        self.assertTrue(all(len(i.evidence) <= MAX_EVIDENCE_PER_ITEM for i in self.learning.store.items(OWNER)))

    def test_forget_is_respected_until_explicit_restatement(self):
        batch = [exp(i) for i in range(3)]
        self.learning.observe_experiences(OWNER, batch)
        self.learning.observe_utterance(OWNER, SESSION, "I prefer tea over coffee")
        self.assertGreater(self.learning.forget(OWNER), 0)
        self.assertEqual(self.learning.retrieve(OWNER), {})
        self.learning.observe_experiences(OWNER, batch)  # same evidence must not re-infer
        self.assertEqual(self.learning.retrieve(OWNER), {})
        self.learning.observe_utterance(OWNER, SESSION, "I prefer tea over coffee")  # explicit consent
        self.assertIn("tea", self.learning.retrieve(OWNER).values())

    def test_persistence_round_trip_and_atomic_write(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "learning.json")
            first = AdaptiveLearning(store=LearningStore(path), clock=self.clock)
            first.observe_utterance(OWNER, SESSION, "I prefer tea over coffee")
            first.observe_experiences(OWNER, [exp(i) for i in range(3)])
            second = AdaptiveLearning(store=LearningStore(path), clock=self.clock)
            self.assertEqual(first.explain(OWNER), second.explain(OWNER))
            self.assertEqual(sorted(os.listdir(d)), ["learning.json"], "no temp files left behind")
            json.loads(open(path, encoding="utf-8").read())

    def test_deterministic(self):
        def run():
            learning = AdaptiveLearning(store=LearningStore(), clock=Clock())
            learning.observe_utterance(OWNER, SESSION, "I prefer tea over coffee")
            learning.observe_experiences(OWNER, [exp(i) for i in range(4)])
            return learning.explain(OWNER), learning.retrieve(OWNER, SESSION, "tea")
        self.assertEqual(run(), run())


# --- integration through the V12 orchestrator --------------------------------

from test_v11_8_end_to_end_integration import V118TestBase, make_plan, make_step  # noqa: E402
from core.v12.cognitive_orchestrator import V12CognitiveOrchestrator  # noqa: E402
from test_v12_1_response_intelligence import FakeLocalModel  # noqa: E402
from orchestration.conversation.respond import (  # noqa: E402
    use_respond_provider_for_tests, reset_respond_provider_for_tests,
)


class TestOrchestratorIntegration(V118TestBase):

    def setUp(self):
        super().setUp()
        self.learning = AdaptiveLearning(store=LearningStore())
        self.orchestrator = V12CognitiveOrchestrator(learning=self.learning)
        use_respond_provider_for_tests(FakeLocalModel(["Noted."] * 10))

    def tearDown(self):
        reset_respond_provider_for_tests()
        super().tearDown()

    def test_statement_learned_and_retrieved_into_context(self):
        first = self.run_cycle("I prefer Python over Ruby")
        self.assertEqual(first["stages"]["adaptive_learning"]["learned_from_statement"],
                         ["PREFERENCE:choice_python_vs_ruby"])
        second = self.run_cycle("Which language should I use?")
        fused = second["stages"]["context_fusion"]["fused_context"]
        self.assertEqual(fused.context.get("learned_preference_choice_python_vs_ruby"), "Python")
        self.assertEqual(fused.provenance["learned_preference_choice_python_vs_ruby"].source, "caller_context")

    def test_monitoring_input_is_not_learned_as_user_statement(self):
        result = self.run_cycle("I prefer Ruby over Python", context={"monitoring_trigger": True})
        self.assertEqual(result["stages"]["adaptive_learning"]["learned_from_statement"], [])
        self.assertEqual(self.learning.explain("test_owner_v118"), [])

    def test_executed_plans_produce_workflow_knowledge(self):
        for i in range(3):
            plan = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "x")])],
                             goal_id=f"ag_v122_wf_{i}")
            result = self.run_cycle(f"run {i}", plan=plan)
            self.assertTrue(result["success"])
        retrieved = self.learning.retrieve("test_owner_v118")
        self.assertEqual(retrieved.get("learned_workflow_workflow_conversation_respond"), "reliable")
        nxt = self.run_cycle("status?")
        self.assertIn("learned_workflow_workflow_conversation_respond",
                      nxt["stages"]["adaptive_learning"]["retrieved_keys"])

    def test_learning_failure_never_breaks_cycle(self):
        def boom(*a, **k):
            raise RuntimeError("store down")
        self.learning.observe_utterance = boom
        result = self.run_cycle("I prefer tea over coffee")
        self.assertTrue(result["success"])
        self.assertEqual(result["stages"]["adaptive_learning"]["error"], "RuntimeError")


if __name__ == "__main__":
    unittest.main(verbosity=2)
