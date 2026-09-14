"""V8.15 live local Ollama smoke. Real production path only. No computer/browser."""
from __future__ import annotations

import inspect
import json
import os
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "true")
os.environ.setdefault("PROACTIVE_COMPUTER_BROWSER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ACT_ENABLED", "false")

from core.cost_guard.guard import cost_guard
from core.cost_guard.invoke import authorize_llm_provider
from core.cost_guard.types import CostClass, CostPolicyMode
from core.orchestrator import DOOMCore
from models.ollama_provider import OllamaProvider
from orchestration.authorization import claim_authorization
from orchestration.conversation.respond import MAX_OUTPUT_CHARS, reset_respond_provider_for_tests
from orchestration.conversation.safe_context import reset_respond_memory_for_tests
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.goal.kernel import process_goal
from orchestration.goal.planner import plan_goal
from orchestration.goal.types import IntentClass
from proactive.config import is_computer_browser_enabled, is_v8_context_enabled

ROOT = Path(__file__).resolve().parent
SMOKE = (
    "Hello DOOM. Respond with one short sentence confirming that you are "
    "running locally."
)


def _ident():
    return ExecutionIdentity(owner_id="alice", session_id="sess-1", computer_session_id="")


def probe_ollama():
    """Return (http_up, model_names). Never prints credentials."""
    url = "http://127.0.0.1:11434/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        names = []
        for row in payload.get("models") or []:
            name = str(row.get("name") or "")
            if name:
                names.append(name)
        return True, tuple(names)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return False, ()


def configured_model_present(names):
    wanted = str(getattr(OllamaProvider(), "model", "") or "llama3")
    compact = [n.split(":")[0] for n in names]
    return wanted in names or wanted in compact or any(n.startswith(wanted) for n in names)


class TestV815LiveOllamaSmoke(unittest.TestCase):
    def setUp(self):
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V8_CONTEXT_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_BROWSER_ENABLED"] = "false"
        reset_respond_provider_for_tests()
        reset_respond_memory_for_tests()

    def tearDown(self):
        reset_respond_provider_for_tests()
        reset_respond_memory_for_tests()
        os.environ["PROACTIVE_V8_CONTEXT_ENABLED"] = "false"

    def test_01_cost_guard_and_isolation(self):
        self.assertEqual(cost_guard.policy, CostPolicyMode.HARD_ZERO)
        decision = authorize_llm_provider(OllamaProvider(), capability="v8_respond")
        self.assertTrue(decision.is_allow)
        self.assertEqual(decision.cost_class, CostClass.LOCAL_FREE)
        self.assertTrue(is_v8_context_enabled())
        self.assertFalse(is_computer_browser_enabled())
        self.assertNotIn("context_hash", inspect.signature(execute_plan).parameters)
        self.assertNotIn("safe_context", inspect.signature(claim_authorization).parameters)
        fields = set(ExecutionIdentity.__dataclass_fields__)
        self.assertEqual(fields, {"owner_id", "session_id", "computer_session_id"})
        for path in (ROOT / "doom.py", ROOT / "core" / "listen.py"):
            self.assertTrue(path.is_file())

    def test_02_conversation_plan_is_respond(self):
        goal = process_goal(SMOKE, context={"owner_id": "alice", "session_id": "sess-1"}).goal
        self.assertEqual(goal.normalized_intent, IntentClass.CONVERSATION)
        proposal = plan_goal(goal)
        self.assertIsNotNone(proposal.plan)
        self.assertEqual(proposal.plan.steps[0].action, "RESPOND")
        self.assertEqual(proposal.plan.steps[0].capability_id, "conversation")

    def test_03_live_production_path_to_ollama(self):
        http_up, names = probe_ollama()
        if not http_up:
            self.skipTest("LIVE OLLAMA: BLOCKED — LOCAL RUNTIME UNAVAILABLE")
        if not configured_model_present(names):
            self.skipTest("LIVE OLLAMA: BLOCKED — CONFIGURED LOCAL MODEL MISSING")

        reset_respond_provider_for_tests()
        calls = []
        original = OllamaProvider._generate

        def wrapped(self, prompt, system_prompt="", tools=None, temperature=0.7, **kwargs):
            calls.append({
                "tools": tools,
                "provider": self.name,
                "host": str(self.base_url or ""),
            })
            return original(self, prompt, system_prompt=system_prompt, tools=tools, temperature=temperature, **kwargs)

        core = DOOMCore()
        with patch.object(OllamaProvider, "_generate", wrapped):
            with patch.object(core.cognition, "process") as cognition:
                with patch("proactive.computer.actions.execute_computer_action") as computer:
                    out = core.process_request(SMOKE, identity=_ident())
        cognition.assert_not_called()
        computer.assert_not_called()
        self.assertTrue(calls, "real Ollama _generate was not invoked")
        self.assertEqual(calls[0]["provider"], "ollama")
        self.assertIsNone(calls[0]["tools"])
        host = calls[0]["host"]
        self.assertTrue("127.0.0.1" in host or "localhost" in host, host)
        self.assertTrue(str(out or "").strip())
        self.assertNotIn("LOCAL_MODEL_UNAVAILABLE", out)
        self.assertNotIn("LOCAL_MODEL_ERROR", out)
        self.assertLessEqual(len(out), MAX_OUTPUT_CHARS)
        self.assertFalse(out.startswith("[V8] IDENTITY"), out)


if __name__ == "__main__":
    unittest.main()
