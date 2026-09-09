#!/usr/bin/env python3
"""Dashboard /api/agent/chat must execute only through ModelRouter.generate."""

import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from models.base_provider import LLMResponse


CONTRACT_KEYS = {"response", "model", "mode", "latency_ms", "steps", "code_blocks", "timestamp"}


def _ok(text="router-ok"):
    return LLMResponse(text=text, tool_calls=[], model_name="fake/router")


class TestDashboardCanonicalRouter(unittest.TestCase):
    def setUp(self):
        import httpx
        from dashboard.server import app

        self.httpx = httpx
        self.app = app

    def _post(self, model: str, prompt: str = "hello"):
        async def _run():
            transport = self.httpx.ASGITransport(app=self.app)
            async with self.httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/agent/chat",
                    json={"prompt": prompt, "model": model, "mode": "pair_programmer"},
                )

        return asyncio.run(_run())

    def test_01_auto_uses_model_router_generate_without_override(self):
        with patch("dashboard.server.model_router.generate", return_value=_ok("auto-text")) as gen, \
             patch("dashboard.server.postgres_manager.is_connected", return_value=False):
            res = self._post("auto")
        self.assertEqual(res.status_code, 200)
        gen.assert_called_once()
        kwargs = gen.call_args.kwargs
        self.assertIsNone(kwargs.get("provider_override"))
        self.assertEqual(kwargs.get("task_type"), "general")
        self.assertEqual(res.json()["response"], "auto-text")
        self.assertEqual(res.json()["model"], "auto")

    def test_02_named_nim_passes_override_not_constructor(self):
        with patch("dashboard.server.model_router.generate", return_value=_ok("nim-text")) as gen, \
             patch("models.nim_provider.NIMProvider") as nim_cls, \
             patch("dashboard.server.postgres_manager.is_connected", return_value=False):
            res = self._post("nim")
        self.assertEqual(res.status_code, 200)
        gen.assert_called_once()
        self.assertEqual(gen.call_args.kwargs.get("provider_override"), "nim")
        nim_cls.assert_not_called()
        self.assertEqual(res.json()["response"], "nim-text")

    def test_03_named_gemini_passes_override_not_constructor(self):
        with patch("dashboard.server.model_router.generate", return_value=_ok("gem-text")) as gen, \
             patch("models.gemini_provider.GeminiProvider") as gem_cls, \
             patch("dashboard.server.postgres_manager.is_connected", return_value=False):
            res = self._post("gemini")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(gen.call_args.kwargs.get("provider_override"), "gemini")
        gem_cls.assert_not_called()

    def test_04_named_ollama_passes_override_not_constructor(self):
        with patch("dashboard.server.model_router.generate", return_value=_ok("ollama-text")) as gen, \
             patch("models.ollama_provider.OllamaProvider") as ollama_cls, \
             patch("models.groq_provider.GroqProvider") as groq_cls, \
             patch("dashboard.server.postgres_manager.is_connected", return_value=False):
            res = self._post("ollama")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(gen.call_args.kwargs.get("provider_override"), "ollama")
        ollama_cls.assert_not_called()
        groq_cls.assert_not_called()
        self.assertEqual(res.json()["response"], "ollama-text")

    def test_05_named_groq_passes_override_not_constructor(self):
        with patch("dashboard.server.model_router.generate", return_value=_ok("groq-text")) as gen, \
             patch("models.groq_provider.GroqProvider") as groq_cls, \
             patch("dashboard.server.postgres_manager.is_connected", return_value=False):
            res = self._post("groq")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(gen.call_args.kwargs.get("provider_override"), "groq")
        groq_cls.assert_not_called()

    def test_06_provider_override_reaches_canonical_router(self):
        with patch("dashboard.server.model_router.generate", return_value=_ok()) as gen, \
             patch("dashboard.server.postgres_manager.is_connected", return_value=False):
            self._post("NIM")
        self.assertEqual(gen.call_args.kwargs.get("provider_override"), "nim")
        self.assertIn("prompt", gen.call_args.kwargs)
        self.assertIn("system_prompt", gen.call_args.kwargs)

    def test_07_no_direct_provider_construction_on_named_requests(self):
        patches = {
            "nim": patch("models.nim_provider.NIMProvider"),
            "gemini": patch("models.gemini_provider.GeminiProvider"),
            "ollama": patch("models.ollama_provider.OllamaProvider"),
            "groq": patch("models.groq_provider.GroqProvider"),
        }
        mocks = {k: p.start() for k, p in patches.items()}
        try:
            with patch("dashboard.server.model_router.generate", return_value=_ok()) as gen, \
                 patch("dashboard.server.postgres_manager.is_connected", return_value=False):
                for name in ("nim", "gemini", "ollama", "groq"):
                    self._post(name)
                    self.assertEqual(gen.call_args.kwargs.get("provider_override"), name)
            for cls_mock in mocks.values():
                cls_mock.assert_not_called()
        finally:
            for p in patches.values():
                p.stop()

    def test_08_router_owns_fallback_dashboard_does_not_catch_and_retry(self):
        """Ollama named request must not construct Groq on failure; router generate is the only call."""
        with patch("dashboard.server.model_router.generate", return_value=_ok("cascaded")) as gen, \
             patch("models.ollama_provider.OllamaProvider") as ollama_cls, \
             patch("models.groq_provider.GroqProvider") as groq_cls, \
             patch("dashboard.server.postgres_manager.is_connected", return_value=False):
            res = self._post("ollama")
        self.assertEqual(gen.call_count, 1)
        self.assertEqual(gen.call_args.kwargs.get("provider_override"), "ollama")
        ollama_cls.assert_not_called()
        groq_cls.assert_not_called()
        self.assertEqual(res.json()["response"], "cascaded")

    def test_09_disabled_provider_not_invoked_by_dashboard_bypass(self):
        with patch("dashboard.server.model_router.generate", return_value=_ok("policy-ok")) as gen, \
             patch("models.bedrock_provider.BedrockProvider") as bedrock_cls, \
             patch("dashboard.server.postgres_manager.is_connected", return_value=False):
            res = self._post("bedrock")
        self.assertEqual(res.status_code, 200)
        gen.assert_called_once()
        self.assertEqual(gen.call_args.kwargs.get("provider_override"), "bedrock")
        bedrock_cls.assert_not_called()

    def test_10_existing_api_response_contract(self):
        with patch("dashboard.server.model_router.generate", return_value=_ok("hello ```python\nprint(1)\n```")) as gen, \
             patch("dashboard.server.postgres_manager.is_connected", return_value=False):
            res = self._post("auto")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(CONTRACT_KEYS.issubset(body.keys()))
        self.assertEqual(body["mode"], "pair_programmer")
        self.assertEqual(body["model"], "auto")
        self.assertIsInstance(body["latency_ms"], (int, float))
        self.assertIsInstance(body["steps"], list)
        self.assertIsInstance(body["code_blocks"], list)
        self.assertTrue(body["code_blocks"])
        self.assertEqual(body["code_blocks"][0]["language"], "python")
        gen.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
