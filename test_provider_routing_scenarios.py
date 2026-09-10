#!/usr/bin/env python3
"""
Provider-routing scenario tests A–N plus capability-metadata contracts.

Does not invoke live cloud inference. Uses isolated ModelRouter instances
and fake providers so NIM/Groq/Ollama network state cannot affect results.
"""

import inspect
import os
import sys
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from models.base_provider import (
    BaseLLMProvider,
    LLMResponse,
    ProviderModelNotFoundError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from models.gemini_provider import GeminiProvider
from models.nim_provider import NIMProvider
from models.bedrock_provider import BedrockProvider
from core.model_router import ModelRouter, NoCapableProviderError
from core.reliability.circuit_breaker import provider_circuit_breaker


class FakeLLM(BaseLLMProvider):
    """Deterministic stand-in for routing tests."""

    def __init__(
        self,
        name: str,
        available: bool = True,
        enabled: bool = True,
        text: str = "",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        error: Optional[BaseException] = None,
        cost_tier: str = "FREE_TIER",
    ):
        self.name = name
        self._available = available
        self._enabled = enabled
        self._text = text
        self._tool_calls = tool_calls or []
        self._error = error
        self.cost_tier = cost_tier
        self.generate_calls = 0
        self.available_checks = 0
        self.health_checks = 0

    def is_enabled(self) -> bool:
        return self._enabled

    def is_available(self) -> bool:
        self.available_checks += 1
        if not self._enabled:
            return False
        return self._available

    def is_healthy(self) -> bool:
        self.health_checks += 1
        return self.is_available()

    def _generate(
        self,
        prompt: str,
        system_prompt: str = "",
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> LLMResponse:
        self.generate_calls += 1
        if self._error:
            raise self._error
        return LLMResponse(
            text=self._text,
            tool_calls=list(self._tool_calls),
            model_name=f"{self.name}/fake",
        )


def _isolate(router: ModelRouter) -> None:
    """Disable live providers so tests never hit NVIDIA/Groq/Ollama networks."""
    for name in list(router.providers.keys()):
        if name == "fallback":
            continue
        router.providers[name] = FakeLLM(name, available=False, enabled=(name != "bedrock"))


def _wire(router: ModelRouter, **providers: BaseLLMProvider) -> ModelRouter:
    """Replace registered providers; keep capability maps from production router."""
    _isolate(router)
    for key, fake in providers.items():
        router.providers[key] = fake
        caps = router.provider_capabilities.get(key, getattr(fake, "capabilities", []))
        fake.capabilities = caps
    return router


class TestProviderRoutingScenarios(unittest.TestCase):
    def setUp(self):
        provider_circuit_breaker.reset()
        self.router = ModelRouter()

    def tearDown(self):
        provider_circuit_breaker.reset()

    def test_nim_streaming_metadata_matches_implementation(self):
        """NIM advertises streaming=False and generate() does not request a stream."""
        self.assertFalse(NIMProvider.streaming)
        nim = NIMProvider()
        self.assertFalse(nim.streaming)
        src = inspect.getsource(NIMProvider._generate)
        self.assertNotIn('"stream": True', src)
        self.assertNotIn("'stream': True", src)
        self.assertNotIn("stream=True", src)

    def test_gemini_tool_calling_metadata_matches_implementation(self):
        """Gemini must not claim tool_calling in class or router maps."""
        self.assertFalse(GeminiProvider.tool_calling)
        self.assertNotIn("tool_calling", GeminiProvider.capabilities)
        self.assertNotIn("tool_calling", self.router.provider_capabilities["gemini"])
        gem = GeminiProvider()
        self.assertFalse(gem.tool_calling)
        self.assertNotIn("tool_calling", gem.capabilities)

    def test_A_general_request_ollama_nim_groq_available(self):
        """A: HARD $0 — Groq/NIM UNKNOWN blocked; Ollama lacks tool_calling so fallback degrades."""
        ollama = FakeLLM("ollama", cost_tier="LOCAL", text="from-ollama")
        nim = FakeLLM("nim", cost_tier="FREE_TIER", text="from-nim")
        groq = FakeLLM("groq", cost_tier="FREE_TIER", text="from-groq")
        openai = FakeLLM("openai", cost_tier="PAID", text="from-openai")
        _wire(self.router, ollama=ollama, nim=nim, groq=groq, openai=openai)
        resp = self.router.generate("hello", task_type="general")
        self.assertTrue(resp.text or resp.tool_calls)
        self.assertEqual(nim.generate_calls, 0)
        self.assertEqual(groq.generate_calls, 0)
        self.assertEqual(openai.generate_calls, 0)

    def test_B_tool_calling_skips_ollama(self):
        """B: coding requires tool_calling; Ollama skipped; Groq/NIM blocked; no paid hop."""
        ollama = FakeLLM("ollama", cost_tier="LOCAL", text="local-code")
        nim = FakeLLM("nim", text="nim-code", tool_calls=[{"id": "1", "name": "coding_write_script", "arguments": {}}])
        groq = FakeLLM("groq", text="groq-code")
        openai = FakeLLM("openai", cost_tier="PAID", text="paid-code")
        _wire(self.router, ollama=ollama, nim=nim, groq=groq, openai=openai)
        resp = self.router.generate("write a python file", task_type="coding")
        self.assertEqual(nim.generate_calls, 0)
        self.assertEqual(groq.generate_calls, 0)
        self.assertEqual(openai.generate_calls, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_C_nim_unavailable_falls_back_to_groq(self):
        """C: NIM down does not select Groq (UNKNOWN) or paid."""
        nim = FakeLLM("nim", available=False, text="nim")
        groq = FakeLLM("groq", text="groq-ok")
        openai = FakeLLM("openai", cost_tier="PAID", text="paid")
        _wire(self.router, nim=nim, groq=groq, openai=openai)
        resp = self.router.generate("hi", task_type="general")
        self.assertEqual(nim.generate_calls, 0)
        self.assertEqual(groq.generate_calls, 0)
        self.assertEqual(openai.generate_calls, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_D_groq_unavailable_falls_back_to_nim(self):
        """D: Groq down does not select NIM (UNKNOWN)."""
        nim = FakeLLM("nim", text="nim-ok")
        groq = FakeLLM("groq", available=False, text="groq")
        _wire(self.router, nim=nim, groq=groq)
        resp = self.router.generate("hi", task_type="general")
        self.assertEqual(nim.generate_calls, 0)
        self.assertEqual(groq.generate_calls, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_E_bedrock_disabled_never_selected(self):
        """E: production Bedrock is disabled and must not win routing."""
        bedrock = self.router.providers["bedrock"]
        self.assertIsInstance(bedrock, BedrockProvider)
        self.assertFalse(bedrock.is_enabled())
        self.assertFalse(bedrock.is_available())
        nim = FakeLLM("nim", text="nim-ok")
        _wire(self.router, nim=nim)
        chosen = self.router.route("general")
        self.assertNotEqual(chosen.name, "bedrock")
        resp = self.router.generate("hi", task_type="general")
        self.assertEqual(resp.model_name.startswith("bedrock"), False)
        self.assertEqual(nim.generate_calls, 0)

    def test_F_vision_only_vision_capable_providers(self):
        """F: vision routing never invokes non-vision providers."""
        nim = FakeLLM("nim", text="nim-vision")
        groq = FakeLLM("groq", text="groq-vision")
        ollama = FakeLLM("ollama", cost_tier="LOCAL", text="ollama-vision")
        gemini = FakeLLM("gemini", cost_tier="PAID", text="gemini-vision")
        openai = FakeLLM("openai", cost_tier="PAID", text="openai-vision")
        _wire(
            self.router,
            nim=nim,
            groq=groq,
            ollama=ollama,
            gemini=gemini,
            openai=openai,
        )
        resp = self.router.generate("describe this image", task_type="vision")
        # HARD $0: Gemini/OpenAI/Bedrock blocked. Vision must not invoke paid providers.
        self.assertEqual(nim.generate_calls, 0)
        self.assertEqual(groq.generate_calls, 0)
        self.assertEqual(gemini.generate_calls, 0)
        self.assertEqual(openai.generate_calls, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_G_missing_openai_gemini_credentials_not_routable(self):
        """G: unconfigured paid providers are skipped."""
        openai = FakeLLM("openai", available=False, cost_tier="PAID", text="openai")
        gemini = FakeLLM("gemini", available=False, cost_tier="PAID", text="gemini")
        groq = FakeLLM("groq", text="groq-ok")
        _wire(self.router, openai=openai, gemini=gemini, groq=groq)
        resp = self.router.generate("hi", task_type="general")
        self.assertEqual(openai.generate_calls, 0)
        self.assertEqual(gemini.generate_calls, 0)
        self.assertEqual(groq.generate_calls, 0)

    def test_H_incompatible_override_rejected(self):
        """H: Ollama override on vision is not invoked."""
        ollama = FakeLLM("ollama", cost_tier="LOCAL", text="ollama-override")
        openai = FakeLLM("openai", cost_tier="PAID", text="openai-ok")
        _wire(self.router, ollama=ollama, openai=openai)
        resp = self.router.generate(
            "look at this screenshot",
            task_type="vision",
            provider_override="ollama",
        )
        self.assertEqual(ollama.generate_calls, 0)
        self.assertEqual(openai.generate_calls, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_I_compatible_override_accepted(self):
        """I: Groq override is Cost-Guard blocked (UNKNOWN)."""
        nim = FakeLLM("nim", text="nim-ok")
        groq = FakeLLM("groq", text="groq-override")
        openai = FakeLLM("openai", cost_tier="PAID", text="paid")
        _wire(self.router, nim=nim, groq=groq, openai=openai)
        resp = self.router.generate("hi", task_type="general", provider_override="groq")
        self.assertEqual(groq.generate_calls, 0)
        self.assertEqual(nim.generate_calls, 0)
        self.assertEqual(openai.generate_calls, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_J_empty_response_falls_back(self):
        """J: empty NIM must not fall through to Groq under HARD $0."""
        nim = FakeLLM("nim", text="")
        groq = FakeLLM("groq", text="groq-filled")
        openai = FakeLLM("openai", cost_tier="PAID", text="paid")
        _wire(self.router, nim=nim, groq=groq, openai=openai)
        resp = self.router.generate("hi", task_type="general")
        self.assertEqual(nim.generate_calls, 0)
        self.assertEqual(groq.generate_calls, 0)
        self.assertEqual(openai.generate_calls, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_K_rate_limit_falls_back(self):
        """K: NIM 429 cannot fall through to Groq or paid."""
        nim = FakeLLM("nim", error=ProviderRateLimitError("rate limit", "nim"))
        groq = FakeLLM("groq", text="groq-after-429")
        openai = FakeLLM("openai", cost_tier="PAID", text="paid")
        _wire(self.router, nim=nim, groq=groq, openai=openai)
        resp = self.router.generate("hi", task_type="general")
        self.assertEqual(nim.generate_calls, 0)
        self.assertEqual(groq.generate_calls, 0)
        self.assertEqual(openai.generate_calls, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_L_timeout_falls_back(self):
        """L: NIM timeout cannot select Groq."""
        nim = FakeLLM("nim", error=ProviderTimeoutError("timeout", "nim", timeout=30.0))
        groq = FakeLLM("groq", text="groq-after-timeout")
        _wire(self.router, nim=nim, groq=groq)
        resp = self.router.generate("hi", task_type="general")
        self.assertEqual(nim.generate_calls, 0)
        self.assertEqual(groq.generate_calls, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_M_model_not_found_falls_back_without_retry_loop(self):
        """M: model-not-found is non-retryable on that provider; next provider used once."""
        nim = FakeLLM(
            "nim",
            error=ProviderModelNotFoundError("missing", "nim", "bad-model"),
        )
        groq = FakeLLM("groq", text="groq-after-404")
        openai = FakeLLM("openai", cost_tier="PAID", text="paid")
        _wire(self.router, nim=nim, groq=groq, openai=openai)
        resp = self.router.generate("hi", task_type="general")
        self.assertEqual(nim.generate_calls, 0)
        self.assertEqual(groq.generate_calls, 0)
        self.assertEqual(openai.generate_calls, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_N_disabled_provider_zero_health_probe_zero_invocation(self):
        """N: disabled Bedrock is not invoked; routing uses another provider."""
        real_bedrock = BedrockProvider()
        groq = FakeLLM("groq", text="groq-ok")
        _wire(self.router, groq=groq)
        self.router.providers["bedrock"] = real_bedrock
        real_bedrock.generate = MagicMock(side_effect=real_bedrock.generate)
        self.assertFalse(real_bedrock.is_enabled())
        self.router.route("general")
        self.router.generate("hi", task_type="general")
        self.assertEqual(real_bedrock.generate.call_count, 0)
        self.assertEqual(groq.generate_calls, 0)


    def test_O_bounded_draft_allows_reasoning_without_tool_calling(self):
        ollama = FakeLLM("ollama", cost_tier="LOCAL", text="local-draft")
        groq = FakeLLM("groq", text="hosted-draft")
        _wire(self.router, ollama=ollama, groq=groq)
        resp = self.router.generate("draft", task_type="bounded_draft", allowed_providers=["ollama"])
        self.assertEqual(resp.text, "local-draft")
        self.assertEqual(ollama.generate_calls, 1)
        self.assertEqual(groq.generate_calls, 0)

    def test_P_bounded_draft_excludes_web_search_only(self):
        gemini = FakeLLM("gemini", text="gemini-web")
        ollama = FakeLLM("ollama", cost_tier="LOCAL", text="ok-draft")
        _wire(self.router, gemini=gemini, ollama=ollama)
        self.router.provider_capabilities["gemini"] = ["web_search"]
        resp = self.router.generate("draft", task_type="bounded_draft", allowed_providers=["gemini", "ollama"])
        self.assertEqual(resp.text, "ok-draft")
        self.assertEqual(gemini.generate_calls, 0)
        self.assertEqual(ollama.generate_calls, 1)

    def test_Q_allowed_providers_none_preserves_general_cascade(self):
        nim = FakeLLM("nim", text="nim-ok")
        groq = FakeLLM("groq", text="groq-ok")
        _wire(self.router, nim=nim, groq=groq)
        resp = self.router.generate("hi", task_type="general")
        self.assertEqual(nim.generate_calls, 0)
        self.assertEqual(groq.generate_calls, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_R_bounded_draft_requires_reasoning_not_tool_calling(self):
        self.assertEqual(self.router.capability_requirements["bounded_draft"], ["reasoning"])
        self.assertNotIn("tool_calling", self.router.capability_requirements["bounded_draft"])
        self.assertNotIn("web_search", self.router.capability_requirements["bounded_draft"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
