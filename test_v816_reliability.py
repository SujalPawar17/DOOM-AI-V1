"""V8.16 reliability: TYPE value verification, bounded RESPOND timeout, conversation class."""
from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_BROWSER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_SEQUENCES_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ACT_ENABLED", "false")

from models.base_provider import LLMResponse
from models.ollama_provider import OllamaProvider
from orchestration.authorization import claim_authorization, reset_authorization_store_for_tests, stash_pending_plan
from orchestration.conversation.respond import (
    MAX_GENERATION_TOKENS,
    MAX_INPUT_CHARS,
    MAX_OUTPUT_CHARS,
    MODEL_TIMEOUT_SEC,
    execute_respond,
    reset_respond_provider_for_tests,
    use_respond_provider_for_tests,
)
from orchestration.executor import ExecutionIdentity, _default_verify, reset_execution_ledger_for_tests
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.kernel import process_goal
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.plan_registry import CONVERSATION_TIMEOUT_MS, MAX_TIMEOUT_MS
from orchestration.goal.plan_validator import build_goal_plan
from orchestration.goal.planner import plan_goal
from orchestration.goal.types import IntentClass
from orchestration.production import handle_v8_enabled_request
from proactive.computer.verify.kernel import execute_verification
from proactive.computer.verify.types import VerificationRequest, VerificationSpec, VerificationStatus
from test_v8_harness import exec_plan

ROOT = Path(__file__).resolve().parent
EXEC = ROOT / "orchestration" / "executor.py"
RESPOND = ROOT / "orchestration" / "conversation" / "respond.py"
NORMALIZER = ROOT / "orchestration" / "goal" / "normalizer.py"


def _v8_on():
    os.environ["PROACTIVE_V8_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_TYPE_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_VERIFICATION_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_BROWSER_ENABLED"] = "false"


def _off():
    os.environ["PROACTIVE_V8_ENABLED"] = "false"
    os.environ["PROACTIVE_V8_CONTEXT_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_VERIFICATION_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_BROWSER_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_SEQUENCES_ENABLED"] = "false"
    os.environ["PROACTIVE_ACT_ENABLED"] = "false"


def _ident(**kw):
    return ExecutionIdentity(
        owner_id=kw.get("owner_id", "alice"),
        session_id=kw.get("session_id", "sess-1"),
        computer_session_id=kw.get("computer_session_id", "cs-1"),
    )


def _goal(text, ident):
    return process_goal(text, context={
        "owner_id": ident.owner_id,
        "session_id": ident.session_id,
        "computer_session_id": ident.computer_session_id,
    }).goal


def _type_plan(ident, *, text="hello doom"):
    return build_goal_plan(_goal('Type "hello doom" into the Search field.', ident), [{
        "step_id": "s1",
        "capability_id": "computer",
        "action": "TYPE",
        "parameters": {
            "automation_id": "txtSearch",
            "runtime_id": "9.9.9",
            "control_type": "Edit",
            "name": "Search",
            "text": text,
            "session_id": ident.computer_session_id,
            "precondition_observation_hash": "a" * 64,
        },
        "dependencies": (),
        "verification_required": True,
        "verification_type": "TARGET_STATE_MATCH",
        "retry_count": 0,
        "timeout_ms": 8000,
    }])


class _Spy:
    def __init__(self, status="SUCCESS"):
        self.n = 0
        self.status = status

    def __call__(self, step, plan):
        self.n += 1
        return self.status


class FakeOllama(OllamaProvider):
    def __init__(self, text="ok"):
        super().__init__()
        self.text = text
        self.last = None

    def _generate(self, prompt, system_prompt="", tools=None, temperature=0.7, **kwargs):
        self.last = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "tools": tools,
            "timeout": kwargs.get("timeout"),
            "num_predict": kwargs.get("num_predict"),
        }
        return LLMResponse(self.text, [], "ollama/llama3")


def _pack(value, *, name="Search", aid="txtSearch", rid="9.9.9"):
    obs = SimpleNamespace(
        observation_hash="h1",
        hwnd=1,
        outcome_code="OK",
        uia_automation_id=aid,
        uia_runtime_id=rid,
        uia_control_type="Edit",
        title_advisory="Demo",
    )
    targets = ({
        "automation_id": aid,
        "runtime_id": rid,
        "control_type": "Edit",
        "name": name,
        "value": value,
    },)
    return (obs, "OK", targets)


class TestV816Reliability(unittest.TestCase):
    def setUp(self):
        _off()
        reset_execution_ledger_for_tests()
        reset_authorization_store_for_tests()
        reset_respond_provider_for_tests()
        self._live = patch(
            "orchestration.authorization.verified_computer_session_id",
            return_value=("cs-1", "OK"),
        )
        self._live.start()

    def tearDown(self):
        self._live.stop()
        reset_respond_provider_for_tests()
        reset_execution_ledger_for_tests()
        reset_authorization_store_for_tests()
        _off()

    def test_type_value_match_success(self):
        _v8_on()
        ident = _ident()
        plan = _type_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        computer = _Spy("SUCCESS")
        with patch(
            "proactive.computer.observe.capture_structured_observation",
            return_value=_pack("hello doom"),
        ):
            out = exec_plan(
                claim.plan,
                identity=claim.identity,
                authorized_plan_hash=claim.authorized_plan_hash,
                adapters={"computer": computer, "verification": _default_verify},
            )
        self.assertEqual(out.status, ExecutionStatus.SUCCESS)
        self.assertEqual(computer.n, 1)

    def test_type_unchanged_empty_fails(self):
        _v8_on()
        ident = _ident()
        plan = _type_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        computer = _Spy("SUCCESS")
        with patch(
            "proactive.computer.observe.capture_structured_observation",
            return_value=_pack(""),
        ):
            out = exec_plan(
                claim.plan,
                identity=claim.identity,
                authorized_plan_hash=claim.authorized_plan_hash,
                adapters={"computer": computer, "verification": _default_verify},
            )
        self.assertEqual(out.status, ExecutionStatus.NOT_VERIFIED)
        self.assertNotEqual(out.status, ExecutionStatus.SUCCESS)
        self.assertEqual(computer.n, 1)

    def test_type_wrong_text_fails(self):
        _v8_on()
        ident = _ident()
        plan = _type_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        with patch(
            "proactive.computer.observe.capture_structured_observation",
            return_value=_pack("other"),
        ):
            out = exec_plan(
                claim.plan,
                identity=claim.identity,
                authorized_plan_hash=claim.authorized_plan_hash,
                adapters={"computer": _Spy("SUCCESS"), "verification": _default_verify},
            )
        self.assertEqual(out.status, ExecutionStatus.NOT_VERIFIED)

    def test_type_verify_failure_consumes_and_no_retry(self):
        _v8_on()
        ident = _ident()
        plan = _type_plan(ident)
        pid, _ = stash_pending_plan(plan, ident)
        claim, _ = claim_authorization(pid, ident)
        computer = _Spy("SUCCESS")
        with patch(
            "proactive.computer.observe.capture_structured_observation",
            return_value=_pack(""),
        ):
            out = exec_plan(
                claim.plan,
                identity=claim.identity,
                authorized_plan_hash=claim.authorized_plan_hash,
                adapters={"computer": computer, "verification": _default_verify},
            )
        self.assertNotEqual(out.status, ExecutionStatus.SUCCESS)
        self.assertEqual(computer.n, 1)
        _, code = claim_authorization(pid, ident)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_CONSUMED.value)

    def test_type_kernel_value_mismatch_is_not_verified(self):
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_VERIFICATION_ENABLED"] = "true"
        spec = VerificationSpec(
            verification_id="v1",
            capability="computer",
            verification_type="TARGET_STATE_MATCH",
            expected=(
                ("automation_id", "txtSearch"),
                ("control_type", "Edit"),
                ("expected_outcome", "OK"),
                ("expected_value", "hello doom"),
                ("name", "Search"),
                ("runtime_id", "9.9.9"),
            ),
            timeout_ms=2000,
            max_attempts=1,
            owner_id="alice",
        )
        req = VerificationRequest(spec=spec, action_id="s1", computer_session_id="cs-1")
        out = execute_verification(
            req,
            computer_fn=lambda owner, computer_session_id="": _pack(""),
            cost_ok_fn=lambda _c: True,
            emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(out.status, VerificationStatus.NOT_VERIFIED)
        self.assertEqual(out.failure_code, "VALUE_MISMATCH")

    def test_respond_timeout_bounded_and_applied(self):
        self.assertEqual(CONVERSATION_TIMEOUT_MS, 55000)
        self.assertEqual(MAX_TIMEOUT_MS, 55000)
        self.assertEqual(MODEL_TIMEOUT_SEC, 50)
        self.assertLess(MODEL_TIMEOUT_SEC * 1000, CONVERSATION_TIMEOUT_MS)
        self.assertLessEqual(CONVERSATION_TIMEOUT_MS, MAX_TIMEOUT_MS)
        self.assertLessEqual(MAX_GENERATION_TOKENS, 256)
        self.assertLessEqual(MAX_INPUT_CHARS, 2048)
        self.assertLessEqual(MAX_OUTPUT_CHARS, 2048)
        _v8_on()
        fake = FakeOllama("bounded")
        use_respond_provider_for_tests(fake)
        ident = ExecutionIdentity(owner_id="alice", session_id="sess-1", computer_session_id="")
        out = handle_v8_enabled_request("hello", identity=ident)
        self.assertEqual(out, "bounded")
        self.assertEqual(fake.last["timeout"], MODEL_TIMEOUT_SEC)
        self.assertEqual(fake.last["num_predict"], MAX_GENERATION_TOKENS)
        self.assertIsNone(fake.last["tools"])
        src = RESPOND.read_text(encoding="utf-8")
        self.assertIn("timeout=MODEL_TIMEOUT_SEC", src)
        self.assertNotIn("MODEL_TIMEOUT_SEC = 0", src)
        tree = ast.parse(src)
        assigned = [
            n.value.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Name) and t.id == "MODEL_TIMEOUT_SEC" and isinstance(n.value, ast.Constant)
        ]
        self.assertTrue(assigned)
        self.assertGreaterEqual(assigned[0], 20)
        self.assertLessEqual(assigned[0], 60)

    def test_conversation_plan_uses_bounded_timeout(self):
        _v8_on()
        goal = process_goal("What is RAM?", context={"owner_id": "alice", "session_id": "sess-1"}).goal
        plan = plan_goal(goal).plan
        self.assertEqual(plan.steps[0].timeout_ms, CONVERSATION_TIMEOUT_MS)
        self.assertLessEqual(plan.steps[0].timeout_ms, MAX_TIMEOUT_MS)

    def test_harmless_conversation_classification(self):
        self.assertEqual(normalize_intent("Say the word tea"), IntentClass.CONVERSATION)
        self.assertEqual(normalize_intent("Say the word tea if you can read notes."), IntentClass.CONVERSATION)
        self.assertEqual(normalize_intent("Explain black holes"), IntentClass.CONVERSATION)
        self.assertEqual(normalize_intent("Write a Python function that reverses a string"), IntentClass.CONVERSATION)
        self.assertEqual(normalize_intent("Write a simple Python function that reverses a string."), IntentClass.CONVERSATION)
        self.assertEqual(normalize_intent("Show me a JavaScript function that adds two numbers."), IntentClass.CONVERSATION)
        self.assertEqual(normalize_intent("What is RAM?"), IntentClass.CONVERSATION)

    def test_action_intents_stay_non_conversation(self):
        self.assertEqual(normalize_intent("Click DOOM_TEST_BUTTON"), IntentClass.COMPUTER)
        self.assertEqual(normalize_intent("Type hello into DOOM_TEST_INPUT"), IntentClass.COMPUTER)
        self.assertEqual(normalize_intent("Open Notepad"), IntentClass.COMPUTER)
        self.assertNotEqual(normalize_intent("Delete this file"), IntentClass.CONVERSATION)
        self.assertEqual(normalize_intent("Delete this file"), IntentClass.FILESYSTEM)
        self.assertEqual(normalize_intent("Go to this website"), IntentClass.BROWSER)
        self.assertEqual(normalize_intent("write this file"), IntentClass.FILESYSTEM)
        self.assertEqual(normalize_intent("open this"), IntentClass.AMBIGUOUS)

    def test_type_verify_requires_expected_value(self):
        src = EXEC.read_text(encoding="utf-8")
        fn = src.split("def _default_verify")[1].split("def _default_world")[0]
        self.assertIn('step.action == "TYPE"', fn)
        self.assertIn("expected_value", fn)

    def test_no_voice_or_browser_in_v816_files(self):
        for path in (EXEC, RESPOND, NORMALIZER):
            blob = path.read_text(encoding="utf-8")
            self.assertNotIn("cinematic_voice", blob)
            self.assertNotIn("local_whisper", blob)
            self.assertNotIn("playwright", blob.lower())


if __name__ == "__main__":
    unittest.main()
