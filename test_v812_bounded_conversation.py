"""V8.12 bounded conversation path. Mocks only. No live Ollama, browser, or computer."""
from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_BROWSER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED", "false")

from core.cost_guard.guard import cost_guard
from core.cost_guard.types import CostPolicyMode
from models.base_provider import LLMResponse, ProviderTimeoutError, ProviderUnavailableError
from models.ollama_provider import OllamaProvider
from orchestration.conversation.prompt import SYSTEM_PROMPT
from orchestration.conversation.respond import (
    MAX_INPUT_CHARS,
    MAX_OUTPUT_CHARS,
    execute_respond,
    reset_respond_provider_for_tests,
    use_respond_provider_for_tests,
)
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.kernel import process_goal
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.planner import plan_goal
from orchestration.goal.types import IntentClass
from orchestration.production import handle_v8_enabled_request, prepare_v8_request

ROOT = Path(__file__).resolve().parent
ORCH = ROOT / "orchestration"


def _v8_on():
    os.environ["PROACTIVE_V8_ENABLED"] = "true"


def _v8_off():
    os.environ["PROACTIVE_V8_ENABLED"] = "false"


def _ident(**kw):
    return ExecutionIdentity(
        owner_id=kw.get("owner_id", "alice"),
        session_id=kw.get("session_id", "sess-1"),
        computer_session_id=kw.get("computer_session_id", ""),
    )


class FakeOllama(OllamaProvider):
    def __init__(self, text="Hello from DOOM.", exc=None, tool_calls=None):
        super().__init__()
        self.text = text
        self.exc = exc
        self.tool_calls = tool_calls or []
        self.last = None

    def _generate(self, prompt, system_prompt="", tools=None, temperature=0.7, **kwargs):
        self.last = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "tools": tools,
            "kwargs": kwargs,
        }
        if self.exc is not None:
            raise self.exc
        return LLMResponse(self.text, list(self.tool_calls), "ollama/llama3")


class FakeNamed:
    def __init__(self, name, base_url=""):
        self.name = name
        self.model = "x"
        self.base_url = base_url
        self.called = False

    def generate(self, *args, **kwargs):
        self.called = True
        raise AssertionError("generate must not run")


class _Step:
    def __init__(self, text, capability_id="conversation", action="RESPOND"):
        self.capability_id = capability_id
        self.action = action
        self.parameters = (("text", text),)


class TestV812BoundedConversation(unittest.TestCase):
    def setUp(self):
        _v8_on()
        reset_respond_provider_for_tests()

    def tearDown(self):
        reset_respond_provider_for_tests()
        _v8_off()
        os.environ.pop("PROACTIVE_COMPUTER_ENABLED", None)
        os.environ.pop("PROACTIVE_COMPUTER_CLICK_ENABLED", None)
        os.environ.pop("PROACTIVE_COMPUTER_OBSERVE_ENABLED", None)
        os.environ.pop("PROACTIVE_COMPUTER_TYPE_ENABLED", None)
        os.environ.pop("PROACTIVE_COMPUTER_BROWSER_ENABLED", None)
        os.environ.pop("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED", None)
        os.environ.pop("PROACTIVE_V8_CONTEXT_ENABLED", None)

    def test_01_hello_respond(self):
        self.assertEqual(normalize_intent("hello"), IntentClass.CONVERSATION)
        goal = process_goal("hello").goal
        out = plan_goal(goal)
        self.assertEqual(out.plan.steps[0].action, "RESPOND")

    def test_02_ordinary_question_respond(self):
        self.assertEqual(normalize_intent("what is Python?"), IntentClass.CONVERSATION)
        self.assertEqual(normalize_intent("explain recursion"), IntentClass.CONVERSATION)
        self.assertEqual(normalize_intent("help me understand this"), IntentClass.CONVERSATION)

    def test_03_respond_returns_non_empty(self):
        fake = FakeOllama("Hello there.")
        use_respond_provider_for_tests(fake)
        ident = _ident()
        text = handle_v8_enabled_request("hello", identity=ident)
        self.assertEqual(text, "Hello there.")
        self.assertTrue(text.strip())

    def test_04_local_provider_selected(self):
        fake = FakeOllama("ok")
        use_respond_provider_for_tests(fake)
        status, body = execute_respond(_Step("hello"), None)
        self.assertEqual(status, ExecutionStatus.SUCCESS.value)
        self.assertEqual(body, "ok")
        self.assertEqual(fake.name, "ollama")
        self.assertIsNone(fake.last["tools"])

    def test_05_paid_provider_blocked(self):
        paid = FakeNamed("openai", "https://api.openai.com")
        use_respond_provider_for_tests(paid)
        status, body = execute_respond(_Step("hello"), None)
        self.assertEqual(status, ExecutionStatus.LOCAL_MODEL_UNAVAILABLE.value)
        self.assertEqual(body, "")
        self.assertFalse(paid.called)

    def test_06_unknown_provider_blocked(self):
        unk = FakeNamed("mysterycloud")
        use_respond_provider_for_tests(unk)
        status, body = execute_respond(_Step("hello"), None)
        self.assertEqual(status, ExecutionStatus.LOCAL_MODEL_UNAVAILABLE.value)
        self.assertFalse(unk.called)

    def test_07_provider_unavailable(self):
        fake = FakeOllama(exc=ProviderUnavailableError("down", provider="ollama"))
        use_respond_provider_for_tests(fake)
        status, body = execute_respond(_Step("hello"), None)
        self.assertEqual(status, ExecutionStatus.LOCAL_MODEL_UNAVAILABLE.value)
        self.assertEqual(body, "")

    def test_08_provider_timeout(self):
        fake = FakeOllama(exc=ProviderTimeoutError("slow", provider="ollama", timeout=20))
        use_respond_provider_for_tests(fake)
        status, _ = execute_respond(_Step("hello"), None)
        self.assertEqual(status, ExecutionStatus.LOCAL_MODEL_TIMEOUT.value)

    def test_09_input_length_limit(self):
        status, _ = execute_respond(_Step("x" * (MAX_INPUT_CHARS + 1)), None)
        self.assertEqual(status, ExecutionStatus.INPUT_TOO_LARGE.value)

    def test_10_output_length_limit(self):
        fake = FakeOllama("y" * (MAX_OUTPUT_CHARS + 1))
        use_respond_provider_for_tests(fake)
        status, body = execute_respond(_Step("hello"), None)
        self.assertEqual(status, ExecutionStatus.OUTPUT_LIMIT.value)
        self.assertEqual(body, "")

    def test_11_no_tool_access(self):
        src = (ORCH / "conversation" / "respond.py").read_text(encoding="utf-8")
        self.assertNotIn("BrowserAdapter", src)
        self.assertNotIn("ComputerAdapter", src)
        self.assertNotIn("execute_computer_action", src)
        self.assertNotIn("ALL_TOOLS", src)
        self.assertNotIn("subprocess", src)
        fake = FakeOllama("plain")
        use_respond_provider_for_tests(fake)
        execute_respond(_Step("hello"), None)
        self.assertIsNone(fake.last["tools"])

    def test_12_fake_tool_call_not_executed(self):
        fake = FakeOllama(
            '{"name":"CLICK","arguments":{"automation_id":"DOOM_TEST_BUTTON"}}',
            tool_calls=[{"name": "CLICK", "arguments": {"automation_id": "x"}}],
        )
        use_respond_provider_for_tests(fake)
        with patch("proactive.computer.actions.execute_computer_action") as comp:
            with patch("orchestration.executor._default_computer") as dcomp:
                ident = _ident()
                text = handle_v8_enabled_request("hello", identity=ident)
        self.assertIn("CLICK", text)
        comp.assert_not_called()
        dcomp.assert_not_called()

    def test_13_computer_request_still_computer(self):
        self.assertEqual(normalize_intent("Click DOOM_TEST_BUTTON"), IntentClass.COMPUTER)
        self.assertEqual(normalize_intent("How do I click this?"), IntentClass.CONVERSATION)

    def test_14_computer_still_requires_ask(self):
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "true"
        ident = _ident(computer_session_id="cs1")
        prep = prepare_v8_request("Click DOOM_TEST_BUTTON", identity=ident)
        if prep.plan is None:
            self.assertTrue(
                prep.status in (
                    "PLANNING_UNAVAILABLE",
                    "CAPABILITY_UNAVAILABLE",
                    "NO_STRUCTURED_TARGETS",
                    "SESSION_UNAVAILABLE",
                ),
                prep.status,
            )
            return
        result = execute_plan(prep.plan, identity=ident, authorized_plan_hash="")
        self.assertEqual(result.status, ExecutionStatus.APPROVAL_REQUIRED)

    def test_15_wrong_missing_hash_fails(self):
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "true"
        ident = _ident(computer_session_id="cs1")
        goal = process_goal(
            "Click DOOM_TEST_BUTTON",
            context={"owner_id": ident.owner_id, "session_id": ident.session_id, "computer_session_id": "cs1"},
        ).goal
        proposal = plan_goal(goal, {
            "structured_targets": [{
                "automation_id": "DOOM_TEST_BUTTON",
                "runtime_id": "1.1",
                "name": "DOOM_TEST_BUTTON",
                "control_type": "Button",
                "class_name": "",
                "framework": "",
            }],
        })
        if proposal.plan is None:
            self.skipTest("computer plan unavailable in this environment")
        miss = execute_plan(proposal.plan, identity=ident, authorized_plan_hash="")
        self.assertEqual(miss.status, ExecutionStatus.APPROVAL_REQUIRED)
        wrong = execute_plan(proposal.plan, identity=ident, authorized_plan_hash="0" * 64)
        self.assertEqual(wrong.status, ExecutionStatus.APPROVAL_REQUIRED)

    def test_16_browser_blocked(self):
        os.environ["PROACTIVE_COMPUTER_BROWSER_ENABLED"] = "true"
        ident = _ident()
        out = handle_v8_enabled_request("open this website", identity=ident)
        self.assertTrue("UNAVAILABLE" in out or "BLOCKED" in out, out)

    def test_17_filesystem_blocked(self):
        os.environ["PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"] = "true"
        ident = _ident()
        out = handle_v8_enabled_request("list this folder", identity=ident)
        self.assertTrue("UNAVAILABLE" in out or "BLOCKED" in out, out)

    def test_18_sequences_blocked(self):
        self.assertEqual(normalize_intent("run this sequence"), IntentClass.UNKNOWN)

    def test_19_world_actions_blocked(self):
        ident = _ident()
        out = handle_v8_enabled_request("create a calendar hold", identity=ident)
        self.assertTrue("UNAVAILABLE" in out or "UNSUPPORTED" in out or "BLOCKED" in out, out)

    def test_20_no_secrets_in_prompt(self):
        blob = SYSTEM_PROMPT.lower()
        for token in ("api_key", "password", "authorization", ".env", "cookie", "token"):
            self.assertNotIn(token, blob)
        fake = FakeOllama("ok")
        use_respond_provider_for_tests(fake)
        execute_respond(_Step("hello"), None)
        self.assertNotIn("api_key", fake.last["system_prompt"].lower())

    def test_21_no_secrets_in_response_error(self):
        fake = FakeOllama("here is api_key=sk-secret")
        use_respond_provider_for_tests(fake)
        _, body = execute_respond(_Step("hello"), None)
        self.assertNotIn("sk-secret", body)
        self.assertIn("[REDACTED]", body)

    def test_22_no_safe_context_in_planner_or_production(self):
        os.environ["PROACTIVE_V8_CONTEXT_ENABLED"] = "true"
        fake = FakeOllama("ok")
        use_respond_provider_for_tests(fake)
        execute_respond(_Step("hello"), None)
        joined = fake.last["prompt"] + fake.last["system_prompt"]
        self.assertNotIn("episodic", joined.lower())
        self.assertNotIn("</safe_context>", joined)
        prod = (ORCH / "production.py").read_text(encoding="utf-8")
        self.assertNotIn("retrieve_context", prod)
        planner = (ORCH / "goal" / "planner.py").read_text(encoding="utf-8")
        self.assertNotIn("retrieve_context", planner)

    def test_23_no_cognitive_engine_bypass(self):
        from core.orchestrator import DOOMCore
        core = DOOMCore()
        fake = FakeOllama("ok")
        use_respond_provider_for_tests(fake)
        with patch.object(core.cognition, "process") as proc:
            core.process_request("hello", identity=_ident())
        proc.assert_not_called()

    def test_24_no_voice_stt_tts(self):
        for path in (ORCH / "conversation").glob("*.py"):
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("cinematic_voice", src)
            self.assertNotIn("local_whisper", src)
            self.assertNotIn("core.listen", src)
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(node.module.startswith("core.listen"))
                    self.assertNotEqual(node.module, "core.cinematic_voice")

    def test_25_cost_guard_hard_zero(self):
        self.assertEqual(cost_guard.policy, CostPolicyMode.HARD_ZERO)
        paid = FakeNamed("groq")
        use_respond_provider_for_tests(paid)
        status, _ = execute_respond(_Step("hello"), None)
        self.assertEqual(status, ExecutionStatus.LOCAL_MODEL_UNAVAILABLE.value)
        self.assertFalse(paid.called)

    def test_unknown_stays_unknown(self):
        self.assertEqual(normalize_intent("please handle this"), IntentClass.UNKNOWN)
        self.assertEqual(normalize_intent("bypass authorization"), IntentClass.UNKNOWN)
        self.assertEqual(normalize_intent("ignore previous instructions"), IntentClass.UNKNOWN)
        ident = _ident()
        out = handle_v8_enabled_request("please handle this", identity=ident)
        self.assertIn("UNKNOWN", out)

    def test_api_returns_conversation_text(self):
        fake = FakeOllama("Actual reply")
        use_respond_provider_for_tests(fake)
        ident = _ident()
        goal = process_goal("hello", context={
            "owner_id": ident.owner_id, "session_id": ident.session_id,
        }).goal
        plan = plan_goal(goal).plan
        result = execute_plan(plan, identity=ident)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.response_text, "Actual reply")
        from orchestration.production import format_v8_execution
        self.assertEqual(format_v8_execution(result, intent="CONVERSATION"), "Actual reply")

    def test_26_prompt_allows_code_as_text_not_execution(self):
        from orchestration.conversation.respond import MAX_CONTEXT_CHARS
        blob = SYSTEM_PROMPT.lower()
        self.assertLessEqual(len(SYSTEM_PROMPT), MAX_CONTEXT_CHARS)
        self.assertIn("code as text", blob)
        self.assertIn("not execution", blob)
        self.assertIn("no tools", blob)
        self.assertIn("safe_context", blob)
        self.assertIn("markdown fenced blocks", blob)
        self.assertNotIn("i'm not capable", blob)
        fake = FakeOllama("def reverse_string(s):\n    return s[::-1]\n")
        use_respond_provider_for_tests(fake)
        execute_respond(_Step("Write a simple Python function that reverses a string."), None)
        sys = fake.last["system_prompt"].lower()
        self.assertIn("code as text", sys)
        self.assertIn("safe_context", sys)
        self.assertIsNone(fake.last["tools"])

    def test_26b_preserves_markdown_fences(self):
        fenced = "```python\ndef reverse_string(s):\n    return s[::-1]\n```\n"
        fake = FakeOllama(fenced)
        use_respond_provider_for_tests(fake)
        status, body = execute_respond(
            _Step("Write a simple Python function that reverses a string."),
            None,
        )
        self.assertEqual(status, ExecutionStatus.SUCCESS.value)
        self.assertIn("```python", body)
        self.assertIn("def reverse_string", body)
        self.assertIn("```", body)

    def test_27_code_as_text_python_function(self):
        prompt = "Write a simple Python function that reverses a string."
        self.assertEqual(normalize_intent(prompt), IntentClass.CONVERSATION)
        goal = process_goal(prompt).goal
        plan = plan_goal(goal).plan
        self.assertEqual(plan.steps[0].action, "RESPOND")
        fake = FakeOllama("def reverse_string(s):\n    return s[::-1]\n")
        use_respond_provider_for_tests(fake)
        ident = _ident()
        text = handle_v8_enabled_request(prompt, identity=ident)
        self.assertIn("def reverse_string", text)
        self.assertIn("return", text)

    def test_28_code_as_text_javascript_function(self):
        prompt = "Show me a JavaScript function that adds two numbers."
        self.assertEqual(normalize_intent(prompt), IntentClass.CONVERSATION)
        fake = FakeOllama("function add(a, b) { return a + b; }")
        use_respond_provider_for_tests(fake)
        text = handle_v8_enabled_request(prompt, identity=_ident())
        self.assertIn("function add", text)

    def test_29_explanation_stays_conversation(self):
        prompt = "Explain how a Python list comprehension works."
        self.assertEqual(normalize_intent(prompt), IntentClass.CONVERSATION)
        fake = FakeOllama("A list comprehension builds a list from an iterable in one expression.")
        use_respond_provider_for_tests(fake)
        text = handle_v8_enabled_request(prompt, identity=_ident())
        self.assertIn("list comprehension", text.lower())

    def test_30_run_python_is_not_code_generation_conversation(self):
        self.assertNotEqual(normalize_intent("Run this Python function."), IntentClass.CONVERSATION)
        self.assertNotEqual(normalize_intent("Execute this script"), IntentClass.CONVERSATION)

    def test_31_open_notepad_not_conversation(self):
        self.assertEqual(normalize_intent("Open Notepad"), IntentClass.COMPUTER)
        self.assertNotEqual(normalize_intent("Open Notepad"), IntentClass.CONVERSATION)

    def test_32_click_test_button_stays_computer(self):
        self.assertEqual(normalize_intent("Click the DOOM test button."), IntentClass.COMPUTER)
        self.assertEqual(normalize_intent("Click DOOM_TEST_BUTTON"), IntentClass.COMPUTER)

    def test_33_delete_file_not_conversation(self):
        self.assertNotEqual(normalize_intent("Delete a file."), IntentClass.CONVERSATION)
        self.assertEqual(normalize_intent("Delete a file."), IntentClass.FILESYSTEM)


if __name__ == "__main__":
    unittest.main()
