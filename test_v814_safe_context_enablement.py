"""V8.14 operator enablement: Safe Context on the real RESPOND path. No live computer/browser."""
from __future__ import annotations

import inspect
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_BROWSER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_SEQUENCES_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_EXPERIENCE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_ACT_ENABLED", "false")

from core.cost_guard.invoke import authorize_llm_provider
from core.cost_guard.types import CostClass, CostPolicyMode
from core.cost_guard.guard import cost_guard
from models.base_provider import LLMResponse
from models.ollama_provider import OllamaProvider
from orchestration.authorization import claim_authorization
from orchestration.context.memory_adapter import MemoryAdapter
from orchestration.context.retriever import retrieve_context
from orchestration.context.types import ContextRequest
from orchestration.conversation.respond import (
    execute_respond,
    reset_respond_provider_for_tests,
    use_respond_provider_for_tests,
)
from orchestration.conversation.safe_context import (
    RESPOND_MAX_ITEM_CHARS,
    RESPOND_MAX_ITEMS,
    RESPOND_MAX_TOTAL_CHARS,
    load_respond_context,
    reset_respond_memory_for_tests,
    use_respond_memory_for_tests,
)
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.kernel import process_goal
from orchestration.goal.planner import plan_goal
from orchestration.goal.types import IntentClass
from orchestration.production import handle_v8_enabled_request
from proactive.config import (
    is_act_enabled,
    is_computer_browser_enabled,
    is_computer_filesystem_enabled,
    is_proactive_enabled,
)

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
CONV = ROOT / "orchestration" / "conversation"
VOICE_FILES = (
    ROOT / "doom.py",
    ROOT / "core" / "listen.py",
    ROOT / "core" / "cinematic_voice.py",
    ROOT / "core" / "stt" / "local_whisper.py",
)


def _dotenv_flag(name: str) -> str:
    raw = ENV_PATH.read_text(encoding="utf-8")
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == name:
            return value.strip()
    return ""


def _flag_on(name: str) -> bool:
    return _dotenv_flag(name).lower() in ("1", "true", "yes", "on")


def _flag_present(name: str) -> bool:
    return _dotenv_flag(name) != ""


class SpyMem:
    def __init__(self, rows):
        self.rows = list(rows)
        self.writes = 0

    def write(self, *_a, **_k):
        self.writes += 1

    def read_fn(self, **kwargs):
        owner = kwargs["owner_id"]
        return [r for r in self.rows if r.get("owner_id") == owner]


class BoomMem:
    def read_fn(self, **_kwargs):
        raise RuntimeError("db unavailable")


class FakeOllama(OllamaProvider):
    def __init__(self, text="Hello from DOOM.", tool_calls=None):
        super().__init__()
        self.text = text
        self.tool_calls = tool_calls or []
        self.last = None
        self.calls = 0

    def _generate(self, prompt, system_prompt="", tools=None, temperature=0.7, **kwargs):
        self.calls += 1
        self.last = {"prompt": prompt, "system_prompt": system_prompt, "tools": tools}
        return LLMResponse(self.text, list(self.tool_calls), "ollama/llama3")


class CloudSpy:
    name = "groq"
    called = False

    def generate(self, *a, **k):
        self.called = True
        raise AssertionError("cloud provider must not be called")


def _mem(ref, content, owner="alice", relevance=0.9):
    return {
        "memory_id": ref,
        "content": content,
        "owner_id": owner,
        "relevance": relevance,
        "timestamp_unix_ms": 1,
        "provenance": "USER_EXPLICIT",
    }


def _ident():
    return ExecutionIdentity(owner_id="alice", session_id="sess-1", computer_session_id="")


def _plan(text="hello"):
    os.environ["PROACTIVE_V8_ENABLED"] = "true"
    goal = process_goal(text, context={"owner_id": "alice", "session_id": "sess-1"}).goal
    return plan_goal(goal).plan


class TestV814SafeContextEnablement(unittest.TestCase):
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

    def test_operator_env_enables_context_only(self):
        self.assertTrue(_flag_on("PROACTIVE_V8_CONTEXT_ENABLED"))
        self.assertTrue(_flag_on("PROACTIVE_V8_ENABLED"))
        self.assertTrue(_flag_on("PROACTIVE_COMPUTER_ENABLED"))
        self.assertTrue(_flag_on("PROACTIVE_COMPUTER_OBSERVE_ENABLED"))
        self.assertTrue(_flag_on("PROACTIVE_COMPUTER_CLICK_ENABLED"))
        self.assertTrue(_flag_on("PROACTIVE_COMPUTER_TYPE_ENABLED"))
        self.assertTrue(_flag_on("PROACTIVE_COMPUTER_VERIFICATION_ENABLED"))
        self.assertTrue(_flag_on("PROACTIVE_V8_LEDGER_ENABLED"))
        self.assertTrue(_flag_on("PROACTIVE_V8_AUDIT_ENABLED"))
        if _flag_present("PROACTIVE_COMPUTER_BROWSER_ENABLED"):
            self.assertFalse(_flag_on("PROACTIVE_COMPUTER_BROWSER_ENABLED"))
        self.assertFalse(_flag_on("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"))
        self.assertFalse(_flag_on("PROACTIVE_COMPUTER_SEQUENCES_ENABLED"))
        self.assertFalse(_flag_on("PROACTIVE_COMPUTER_EXPERIENCE_ENABLED"))
        self.assertFalse(_flag_on("PROACTIVE_ACT_ENABLED"))
        self.assertFalse(_flag_on("PROACTIVE_ENABLED"))

    def test_a_context_off_respond_still_works(self):
        os.environ["PROACTIVE_V8_CONTEXT_ENABLED"] = "false"
        fake = FakeOllama("Hi without context.")
        use_respond_provider_for_tests(fake)
        use_respond_memory_for_tests(MemoryAdapter(SpyMem([_mem("m1", "User prefers tea")]).read_fn))
        plan = _plan()
        status, body = execute_respond(plan.steps[0], plan)
        self.assertEqual(status, ExecutionStatus.SUCCESS.value)
        self.assertEqual(body, "Hi without context.")
        self.assertNotIn("</safe_context>", fake.last["system_prompt"])

    def test_b_c_context_on_includes_safe_memory(self):
        fake = FakeOllama("Hi with tea.")
        use_respond_provider_for_tests(fake)
        spy = SpyMem([_mem("m1", "User prefers tea")])
        use_respond_memory_for_tests(MemoryAdapter(spy.read_fn))
        ident = _ident()
        out = handle_v8_enabled_request("hello", identity=ident)
        self.assertEqual(out, "Hi with tea.")
        self.assertIn("</safe_context>", fake.last["system_prompt"])
        self.assertIn("User prefers tea", fake.last["system_prompt"])
        self.assertEqual(spy.writes, 0)
        self.assertIsNone(fake.last["tools"])

    def test_d_context_bounds(self):
        self.assertEqual(RESPOND_MAX_ITEMS, 4)
        self.assertEqual(RESPOND_MAX_ITEM_CHARS, 280)
        self.assertEqual(RESPOND_MAX_TOTAL_CHARS, 800)
        fake = FakeOllama("ok")
        use_respond_provider_for_tests(fake)
        rows = [_mem(f"m{i}", "A" * 400) for i in range(8)]
        use_respond_memory_for_tests(MemoryAdapter(SpyMem(rows).read_fn))
        plan = _plan()
        execute_respond(plan.steps[0], plan)
        inner = fake.last["system_prompt"].split("<safe_context>")[1].split("</safe_context>")[0]
        numbered = [ln for ln in inner.splitlines() if ln.strip()[:1].isdigit()]
        self.assertLessEqual(len(numbered), 4)
        for ln in numbered:
            self.assertLessEqual(len(ln), RESPOND_MAX_ITEM_CHARS + 8)
        self.assertLessEqual(len(inner), RESPOND_MAX_TOTAL_CHARS + 40)

    def test_e_sensitive_excluded(self):
        fake = FakeOllama("ok")
        use_respond_provider_for_tests(fake)
        use_respond_memory_for_tests(MemoryAdapter(SpyMem([
            _mem("m1", "api_key=sk-live-not-for-model"),
            _mem("m2", "User prefers tea"),
        ]).read_fn))
        plan = _plan()
        execute_respond(plan.steps[0], plan)
        sys = fake.last["system_prompt"]
        self.assertNotIn("sk-live-not-for-model", sys)
        self.assertIn("User prefers tea", sys)

    def test_f_owner_mismatch_excluded(self):
        fake = FakeOllama("ok")
        use_respond_provider_for_tests(fake)
        use_respond_memory_for_tests(MemoryAdapter(SpyMem([
            _mem("m1", "Attacker private note", owner="attacker"),
            _mem("m2", "User prefers tea", owner="alice"),
        ]).read_fn))
        plan = _plan()
        execute_respond(plan.steps[0], plan)
        sys = fake.last["system_prompt"]
        self.assertNotIn("Attacker private note", sys)
        self.assertIn("User prefers tea", sys)

    def test_g_retrieval_failure_degrades(self):
        fake = FakeOllama("still here")
        use_respond_provider_for_tests(fake)
        use_respond_memory_for_tests(MemoryAdapter(BoomMem().read_fn))
        ident = _ident()
        out = handle_v8_enabled_request("hello", identity=ident)
        self.assertEqual(out, "still here")
        self.assertNotIn("</safe_context>", fake.last["system_prompt"])

    def test_h_context_hash_diagnostic_only(self):
        rows = [_mem("m1", "User prefers tea")]
        adapter = MemoryAdapter(SpyMem(rows).read_fn)
        os.environ["PROACTIVE_V8_CONTEXT_ENABLED"] = "true"
        req = ContextRequest(
            goal_id="g", owner_id="alice", session_id="sess-1",
            normalized_intent="CONVERSATION", capability_class="conversation",
            query="hello", max_items=4, max_memory_items=4, max_experience_items=0,
        )
        result = retrieve_context(req, adapter, None)
        self.assertTrue(result.context_hash)
        self.assertFalse(result.as_public().get("authorizes_execution"))
        sig = inspect.signature(execute_plan)
        self.assertNotIn("context_hash", sig.parameters)
        self.assertNotIn("safe_context", sig.parameters)
        ident_fields = {f.name for f in ExecutionIdentity.__dataclass_fields__.values()}
        self.assertNotIn("context_hash", ident_fields)
        fake = FakeOllama("ok")
        use_respond_provider_for_tests(fake)
        use_respond_memory_for_tests(adapter)
        plan = _plan()
        execute_respond(plan.steps[0], plan)
        self.assertNotIn(result.context_hash, fake.last["system_prompt"])

    def test_i_context_cannot_reach_authorization(self):
        claim_sig = inspect.signature(claim_authorization)
        self.assertNotIn("context", claim_sig.parameters)
        self.assertNotIn("context_hash", claim_sig.parameters)
        self.assertNotIn("safe_context", claim_sig.parameters)
        exec_sig = inspect.signature(execute_plan)
        self.assertEqual(set(exec_sig.parameters), {"plan", "identity", "authorized_plan_hash"})
        ident_fields = ExecutionIdentity.__dataclass_fields__
        self.assertEqual(set(ident_fields), {"owner_id", "session_id", "computer_session_id"})
        src = (CONV / "safe_context.py").read_text(encoding="utf-8")
        self.assertNotIn("claim_authorization", src)
        self.assertNotIn("execute_plan", src)
        self.assertNotIn("def claim", src)

    def test_j_tools_none(self):
        fake = FakeOllama("ok")
        use_respond_provider_for_tests(fake)
        use_respond_memory_for_tests(MemoryAdapter(SpyMem([_mem("m1", "User prefers tea")]).read_fn))
        plan = _plan()
        execute_respond(plan.steps[0], plan)
        self.assertIsNone(fake.last["tools"])

    def test_k_fake_tool_json_not_executed(self):
        fake = FakeOllama('{"name":"CLICK","arguments":{}}', tool_calls=[{"name": "CLICK"}])
        use_respond_provider_for_tests(fake)
        use_respond_memory_for_tests(MemoryAdapter(SpyMem([_mem("m1", "User prefers tea")]).read_fn))
        plan = _plan()
        with patch("proactive.computer.actions.execute_computer_action") as comp:
            status, body = execute_respond(plan.steps[0], plan)
        self.assertEqual(status, ExecutionStatus.SUCCESS.value)
        self.assertIn("CLICK", body)
        comp.assert_not_called()

    def test_l_m_cost_guard_local_free_no_cloud(self):
        self.assertEqual(cost_guard.policy, CostPolicyMode.HARD_ZERO)
        provider = OllamaProvider()
        decision = authorize_llm_provider(provider, capability="v8_respond")
        self.assertTrue(decision.is_allow)
        self.assertEqual(decision.cost_class, CostClass.LOCAL_FREE)
        cloud = CloudSpy()
        use_respond_provider_for_tests(cloud)
        use_respond_memory_for_tests(MemoryAdapter(SpyMem([_mem("m1", "User prefers tea")]).read_fn))
        plan = _plan()
        status, _ = execute_respond(plan.steps[0], plan)
        self.assertEqual(status, ExecutionStatus.LOCAL_MODEL_UNAVAILABLE.value)
        self.assertFalse(cloud.called)

    def test_o_browser_off(self):
        self.assertFalse(is_computer_browser_enabled())
        out = handle_v8_enabled_request("open this website", identity=_ident())
        self.assertTrue("UNAVAILABLE" in out or "BLOCKED" in out, out)

    def test_p_fs_sequences_proactive_off(self):
        self.assertFalse(is_computer_filesystem_enabled())
        self.assertFalse(is_proactive_enabled())
        self.assertFalse(is_act_enabled())
        self.assertFalse(os.getenv("PROACTIVE_COMPUTER_SEQUENCES_ENABLED", "false").lower() in ("1", "true"))
        out = handle_v8_enabled_request("list this folder", identity=_ident())
        self.assertTrue("UNAVAILABLE" in out or "BLOCKED" in out, out)

    def test_voice_files_untouched_by_conversation(self):
        for path in CONV.glob("*.py"):
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("cinematic_voice", src)
            self.assertNotIn("local_whisper", src)
            self.assertNotIn("core.listen", src)
        self.assertTrue((ROOT / "doom.py").is_file())

    def test_conversation_classification(self):
        goal = process_goal("hello", context={"owner_id": "alice", "session_id": "sess-1"}).goal
        self.assertEqual(goal.normalized_intent, IntentClass.CONVERSATION)

    def test_live_ollama_respond_if_available(self):
        reset_respond_provider_for_tests()
        provider = OllamaProvider()
        try:
            available = bool(provider.is_available())
        except Exception:
            available = False
        if not available:
            self.skipTest("local Ollama unavailable")
        use_respond_memory_for_tests(MemoryAdapter(SpyMem([_mem("m1", "User prefers tea")]).read_fn))
        with patch("proactive.computer.actions.execute_computer_action") as comp:
            with patch("orchestration.executor._default_computer") as dcomp:
                out = handle_v8_enabled_request("Say the word tea if you can read notes.", identity=_ident())
        self.assertTrue(out.strip(), out)
        self.assertFalse(out.startswith("[V8] LOCAL_MODEL_UNAVAILABLE"), out)
        self.assertNotIn("[V8] LOCAL_MODEL_ERROR", out)
        comp.assert_not_called()
        dcomp.assert_not_called()
        self.assertEqual(process_goal("Say the word tea if you can read notes.").goal.normalized_intent, IntentClass.CONVERSATION)


if __name__ == "__main__":
    unittest.main()
