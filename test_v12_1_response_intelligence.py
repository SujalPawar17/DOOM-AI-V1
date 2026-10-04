#!/usr/bin/env python
"""
DOOM V12.1 — Response Intelligence tests (deterministic, local, HARD $0).

The local model is replaced by a subclass of the real OllamaProvider (only
_generate is overridden), so the real V8 RESPOND path, the real Cost Guard
(LOCAL_FREE @ localhost) and the real V11 pipeline all run.
"""

import os
import sys
import unittest
from unittest.mock import patch

os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from test_v11_8_end_to_end_integration import (  # noqa: E402  (shared harness)
    V118TestBase, make_plan, make_step, OWNER, SESSION, COMPUTER_SESSION,
)
from core.v12.cognitive_orchestrator import V12CognitiveOrchestrator  # noqa: E402
from core.v12.response_intelligence import (  # noqa: E402
    ResponseIntelligence, ResponseIntent, ResponseSource, ComposedResponse,
)
from models.base_provider import LLMResponse, ProviderTimeoutError, ProviderUnavailableError  # noqa: E402
from models.ollama_provider import OllamaProvider  # noqa: E402
from orchestration.conversation.respond import (  # noqa: E402
    use_respond_provider_for_tests, reset_respond_provider_for_tests,
)
from orchestration.conversation.context import clear_conversation_thread  # noqa: E402
from orchestration.executor_errors import ExecutionStatus  # noqa: E402
from orchestration.experience.store import list_experiences  # noqa: E402
from orchestration.user_model.store import (  # noqa: E402
    use_test_user_model_store, reset_user_model_for_tests, upsert_profile_entry,
)
from orchestration.user_model.types import Category, Confidence, Provenance  # noqa: E402
from orchestration.conversation.personal_memory import (  # noqa: E402
    use_test_personal_memory_store, reset_personal_memory_for_tests,
)


class FakeLocalModel(OllamaProvider):
    """Real OllamaProvider (localhost) with deterministic generation."""

    def __init__(self, replies=None, exc=None):
        super().__init__()
        self.replies = list(replies or [])
        self.exc = exc
        self.calls = []

    def _generate(self, prompt, system_prompt="", tools=None, temperature=0.7, **kwargs):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt, "tools": tools})
        if self.exc is not None:
            raise self.exc
        text = self.replies.pop(0) if self.replies else "OK."
        return LLMResponse(text, [], "ollama/llama3")


class NonLocalProvider:
    name = "openai"
    model = "gpt-x"
    base_url = "https://api.openai.com/v1"

    def __init__(self):
        self.called = False

    def generate(self, *a, **k):
        self.called = True
        raise AssertionError("a non-local provider must never be called")


class V121TestBase(V118TestBase):

    def setUp(self):
        super().setUp()
        self.orchestrator = V12CognitiveOrchestrator()
        use_test_user_model_store(True)
        reset_user_model_for_tests()
        use_test_personal_memory_store(True)
        reset_personal_memory_for_tests()
        clear_conversation_thread(OWNER, SESSION)

    def tearDown(self):
        reset_respond_provider_for_tests()
        clear_conversation_thread(OWNER, SESSION)
        reset_user_model_for_tests()
        use_test_user_model_store(False)
        reset_personal_memory_for_tests()
        use_test_personal_memory_store(False)
        super().tearDown()

    def model(self, *replies, exc=None):
        fake = FakeLocalModel(replies, exc)
        use_respond_provider_for_tests(fake)
        return fake

    def response_stage(self, result):
        return result["stages"]["response_generation"]

    def assert_not_internal(self, result):
        reasoning = result["stages"]["reasoning_decision"]["reasoning_result"].reasoning_summary
        self.assertNotEqual(result["response_text"].strip(), str(reasoning).strip())
        self.assert_no_execution_claim(result["response_text"])


class TestInformationalAnswers(V121TestBase):

    def test_capital_of_france_is_answered_not_summarized(self):
        model = self.model("Paris.")
        result = self.run_cycle("What is the capital of France?")
        stage = self.response_stage(result)
        self.assertTrue(result["success"], result.get("failure_reason"))
        self.assertEqual(result["response_text"], "Paris.")
        self.assertEqual(stage["intent"], ResponseIntent.ANSWER.value)
        self.assertEqual(stage["source"], ResponseSource.LOCAL_RESPONDER.value)
        self.assertEqual(len(model.calls), 1)
        self.assertIn("capital of France", model.calls[0]["prompt"])
        self.assertIsNone(model.calls[0]["tools"], "RESPOND never offers tools to the model")
        self.assertEqual(self.spy.calls, [], "Answering never runs executor adapters")
        self.assertFalse(result["stages"]["execution"]["executed"])
        self.assertEqual(list_experiences(OWNER).experiences, ())
        self.assert_not_internal(result)
        self.assert_voice_metadata(result)

    def test_greeting_is_deterministic_without_model(self):
        model = self.model("should not be used")
        result = self.run_cycle("Hello DOOM")
        self.assertEqual(result["response_text"], "Hello. What can I do for you?")
        self.assertEqual(self.response_stage(result)["intent"], ResponseIntent.GREETING.value)
        self.assertEqual(model.calls, [])

    def test_farewell_and_thanks(self):
        self.model()
        self.assertEqual(self.response_stage(self.run_cycle("Goodbye"))["intent"], ResponseIntent.FAREWELL.value)
        self.assertEqual(self.run_cycle("thanks")["response_text"], "You're welcome.")

    def test_explanation_question(self):
        self.model("A list is mutable; a tuple is immutable.")
        result = self.run_cycle("What is the difference between a list and a tuple in Python?")
        self.assertEqual(result["response_text"], "A list is mutable; a tuple is immutable.")
        self.assertEqual(self.response_stage(result)["intent"], ResponseIntent.ANSWER.value)

    def test_unknown_information_is_passed_through_honestly(self):
        self.model("I don't know what you had for breakfast.")
        result = self.run_cycle("What did I eat for breakfast today?")
        self.assertEqual(result["response_text"], "I don't know what you had for breakfast.")

    def test_memory_based_answer_uses_user_model_without_model(self):
        model = self.model("should not be used")
        upsert_profile_entry(OWNER, {
            "category": Category.PREFERENCE, "key": "prefer_language", "value": "Python",
            "provenance": Provenance.USER_EXPLICIT, "confidence": Confidence.HIGH,
        })
        result = self.run_cycle("What programming language do I prefer?")
        self.assertIn("Python", result["response_text"])
        self.assertEqual(model.calls, [], "Direct profile facts are answered deterministically")
        self.assertEqual(self.response_stage(result)["intent"], ResponseIntent.ANSWER.value)

    def test_follow_up_receives_previous_turn(self):
        model = self.model("Python is a programming language created by Guido van Rossum.",
                           "It was first released in 1991.")
        self.run_cycle("Tell me about the Python programming language")
        result = self.run_cycle("What about its history?")
        self.assertEqual(result["response_text"], "It was first released in 1991.")
        self.assertEqual(len(model.calls), 2)
        second = model.calls[1]
        context_seen = (second["system_prompt"] + " " + second["prompt"]).lower()
        self.assertIn("python", context_seen, "The follow-up must carry the previous turn's topic")


class TestFallbacksAndCostGuard(V121TestBase):

    def test_model_unavailable_gives_truthful_fallback(self):
        self.model(exc=ProviderUnavailableError("down"))
        result = self.run_cycle("What is the capital of France?")
        stage = self.response_stage(result)
        self.assertEqual(stage["intent"], ResponseIntent.UNAVAILABLE.value)
        self.assertEqual(stage["source"], ResponseSource.FALLBACK.value)
        self.assertIn("local language model", result["response_text"])
        self.assert_not_internal(result)

    def test_model_timeout_gives_truthful_fallback(self):
        self.model(exc=ProviderTimeoutError("slow"))
        result = self.run_cycle("Explain quantum computing in detail")
        self.assertIn("didn't respond in time", result["response_text"])

    def test_non_local_provider_is_never_called(self):
        provider = NonLocalProvider()
        use_respond_provider_for_tests(provider)
        result = self.run_cycle("What is the capital of France?")
        self.assertFalse(provider.called)
        self.assertEqual(self.response_stage(result)["intent"], ResponseIntent.UNAVAILABLE.value)

    def test_oversized_input_is_not_sent_to_model(self):
        model = self.model("x")
        result = self.run_cycle("why " * 2000)
        self.assertEqual(model.calls, [])
        self.assertIn("too long", result["response_text"])


class TestActionOutcomes(V121TestBase):

    def test_successful_execution(self):
        self.model()
        result = self.run_cycle("Summarize", plan=make_plan(
            [make_step("s1", "conversation", "RESPOND", [("text", "Summarize")])], goal_id="ag_v121_ok"))
        self.assertTrue(result["success"])
        self.assertEqual(result["response_text"], "Deterministic local response.")
        self.assertEqual(self.response_stage(result)["intent"], ResponseIntent.SUCCESS.value)
        self.assertEqual(self.response_stage(result)["source"], ResponseSource.EXECUTOR.value)

    def test_failed_execution(self):
        self.model()
        self.spy.overrides["conversation"] = ExecutionStatus.STEP_FAILED.value
        result = self.run_cycle("Do it", plan=make_plan(
            [make_step("s1", "conversation", "RESPOND", [("text", "x")])], goal_id="ag_v121_fail"))
        self.assertFalse(result["success"])
        self.assertEqual(result["response_text"], "I couldn't complete that because a step failed.")
        self.assertEqual(self.response_stage(result)["intent"], ResponseIntent.FAILURE.value)

    def test_verification_failure_is_not_success(self):
        self.model()
        self.spy.overrides["verification"] = ExecutionStatus.VERIFICATION_FAILED.value
        result = self.run_cycle("Do and verify", plan=make_plan(
            [make_step("s1", "conversation", "RESPOND", [("text", "x")],
                       verification_required=True, verification_type="TARGET_EXISTS")], goal_id="ag_v121_vf"))
        self.assertEqual(self.response_stage(result)["intent"], ResponseIntent.VERIFICATION_FAILED.value)
        self.assertIn("couldn't verify", result["response_text"])

    def test_cost_blocked(self):
        self.model()
        result = self.run_cycle("Run job", plan=make_plan(
            [make_step("s1", "world_act", "RUN", [("title", "job")], risk="HIGH")],
            goal_id="ag_v121_cost", plan_risk="HIGH"))
        self.assertEqual(self.response_stage(result)["intent"], ResponseIntent.COST_BLOCKED.value)
        self.assertIn("zero-cost policy", result["response_text"])
        self.assertIn("Nothing was done", result["response_text"])

    def test_pending_authorization(self):
        self.model()
        plan = make_plan(
            [make_step("s1", "computer", "CLICK", [("name", "Save")], risk="MEDIUM", approval=True)],
            goal_id="ag_v121_auth", plan_risk="MEDIUM", approval=True, computer_session_id=COMPUTER_SESSION)
        with patch("orchestration.authorization.verified_computer_session_id",
                   side_effect=lambda owner, cs: (cs, "OK")):
            result = self.run_cycle("Click save", plan=plan)
        pending_id = result["stages"]["authorization"]["pending_id"]
        self.assertTrue(pending_id)
        self.assertEqual(self.response_stage(result)["intent"], ResponseIntent.PENDING_AUTHORIZATION.value)
        self.assertIn(pending_id, result["response_text"])
        self.assertIn("Nothing has been done", result["response_text"])
        self.assertEqual(self.spy.calls, [])

    def test_planning_failure(self):
        from core.v10.planning_integration import PlanningResult
        self.model()
        failed = PlanningResult(planning_required=True, planning_successful=False, plan=None, error="x")
        result = self.run_cycle("Organize", planning=failed)
        self.assertEqual(self.response_stage(result)["intent"], ResponseIntent.PLANNING_FAILED.value)
        self.assert_no_execution_claim(result["response_text"])


class TestValidation(V121TestBase):

    def test_model_claiming_an_action_is_corrected(self):
        self.model("I have deleted the temporary files for you.")
        result = self.run_cycle("Can you clear my temp files?")
        stage = self.response_stage(result)
        self.assertEqual(result["response_text"], "I can explain how to do that, but I haven't taken any action.")
        self.assertIn("unverified_action_claim_removed", stage["corrections"])
        self.assertEqual(self.spy.calls, [])

    def test_secrets_are_redacted(self):
        self.model("Sure. api_key=sk_live_1234567890abcdef is what you asked about.")
        result = self.run_cycle("What is in my config?")
        self.assertNotIn("sk_live_1234567890abcdef", result["response_text"])

    def test_internal_markers_are_scrubbed(self):
        self.model("According to the safe_context, the answer is 42.")
        result = self.run_cycle("What is six times seven?")
        self.assertNotIn("safe_context", result["response_text"].lower())
        self.assertIn("42", result["response_text"])

    def test_reasoning_summary_is_never_returned(self):
        ri = ResponseIntelligence()

        class R:
            reasoning_summary = "User requested conversational or identity lookup; resolve immediately."

        draft = ComposedResponse(R.reasoning_summary, ResponseIntent.ANSWER, ResponseSource.LOCAL_RESPONDER)
        out = ri.validate(draft, {"reasoning_result": R()})
        self.assertNotEqual(out.text, R.reasoning_summary)
        self.assertIn("internal_reasoning_blocked", out.corrections)

    def test_verified_success_may_describe_the_action(self):
        ri = ResponseIntelligence()

        class E:
            status = ExecutionStatus.SUCCESS
            response_text = "I opened the report."

        draft = ComposedResponse("I opened the report.", ResponseIntent.SUCCESS, ResponseSource.EXECUTOR)
        out = ri.validate(draft, {"executed": True, "execution_result": E()})
        self.assertEqual(out.text, "I opened the report.")

    def test_long_output_is_bounded(self):
        ri = ResponseIntelligence()
        out = ri.validate(ComposedResponse("x" * 5000, ResponseIntent.ANSWER, ResponseSource.LOCAL_RESPONDER), {})
        self.assertLessEqual(len(out.text), 2048)
        self.assertIn("truncated", out.corrections)


if __name__ == "__main__":
    unittest.main(verbosity=2)
