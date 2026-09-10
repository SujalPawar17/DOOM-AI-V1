#!/usr/bin/env python3
"""HARD $0 Global Cost Guard tests. Never contact real paid APIs."""

from __future__ import annotations

import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.cost_guard import (
    CostClass,
    CostDecisionAction,
    CostGuard,
    CostGuardBlockedError,
    CostReason,
    ResourceRequest,
    ResourceType,
    cost_guard,
)
from core.cost_guard.hosts import is_loopback_host
from core.cost_guard.registry import ResourceAttestation, build_default_registry
from core.model_router import ModelRouter
from core.reliability.circuit_breaker import provider_circuit_breaker
from models.base_provider import BaseLLMProvider, LLMResponse
from models.fallback_provider import FallbackProvider
from models.openai_provider import OpenAIProvider
from models.gemini_provider import GeminiProvider
from models.bedrock_provider import BedrockProvider
from models.groq_provider import GroqProvider
from models.nim_provider import NIMProvider


class PaidProbe(BaseLLMProvider):
    name = "openai"
    cost_tier = "PAID"

    def __init__(self):
        self._enabled = True
        self.invoked = 0

    def is_available(self) -> bool:
        return True

    def _generate(self, prompt: str, system_prompt: str = "", tools=None, temperature: float = 0.7, **kwargs):
        self.invoked += 1
        raise AssertionError("PAID endpoint invoked under HARD $0")


class LocalFake(BaseLLMProvider):
    name = "ollama"
    cost_tier = "LOCAL"

    def __init__(self, text: str = "local-ok"):
        self._enabled = True
        self.text = text
        self.invoked = 0
        self.base_url = "http://localhost:11434"

    def is_available(self) -> bool:
        return True

    def _generate(self, prompt: str, system_prompt: str = "", tools=None, temperature: float = 0.7, **kwargs):
        self.invoked += 1
        return LLMResponse(text=self.text, tool_calls=[], model_name="ollama/fake")


class TestCostGuardUnit(unittest.TestCase):
    def setUp(self):
        cost_guard.reset_records_for_tests()

    def test_openai_blocked_with_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-not-real"}):
            d = cost_guard.authorize(ResourceRequest(ResourceType.LLM, "openai", model="gpt-4o"))
        self.assertEqual(d.action, CostDecisionAction.BLOCK)
        self.assertEqual(d.reason, CostReason.PAID_PROVIDER_BLOCKED)
        p = OpenAIProvider()
        with self.assertRaises(CostGuardBlockedError):
            p.generate("hello")

    def test_gemini_blocked_with_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            d = cost_guard.authorize(ResourceRequest(ResourceType.LLM, "gemini"))
        self.assertEqual(d.reason, CostReason.PAID_PROVIDER_BLOCKED)
        with self.assertRaises(CostGuardBlockedError):
            GeminiProvider().generate("hello")

    def test_bedrock_blocked(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.LLM, "bedrock"))
        self.assertEqual(d.reason, CostReason.PAID_PROVIDER_BLOCKED)
        b = BedrockProvider()
        b._enabled = True
        with patch.object(b, "is_available", return_value=True):
            with self.assertRaises(CostGuardBlockedError):
                b.generate("hello")

    def test_elevenlabs_blocked_with_credentials(self):
        d = cost_guard.authorize(ResourceRequest(
            ResourceType.TTS, "elevenlabs", endpoint="https://api.elevenlabs.io", host="api.elevenlabs.io"
        ))
        self.assertEqual(d.reason, CostReason.PAID_PROVIDER_BLOCKED)

    def test_unknown_provider_blocked(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.LLM, "omniroute"))
        self.assertEqual(d.action, CostDecisionAction.BLOCK)
        self.assertEqual(d.reason, CostReason.UNKNOWN_PROVIDER_BLOCKED)

    def test_unknown_model_blocked_when_required(self):
        reg = build_default_registry()
        key = ("LLM", "attested")
        reg[key] = ResourceAttestation(
            resource_type=ResourceType.LLM,
            provider="attested",
            cost_class=CostClass.FREE_TIER,
            verified_free_tier=True,
            models_required=True,
            allowed_models=("ok-model",),
        )
        g = CostGuard(registry=reg)
        d = g.authorize(ResourceRequest(ResourceType.LLM, "attested", model="secret-paid-alias"))
        self.assertEqual(d.reason, CostReason.UNKNOWN_MODEL_BLOCKED)
        self.assertFalse(d.is_allow)

    def test_groq_unknown_blocked(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.LLM, "groq"))
        self.assertEqual(d.cost_class, CostClass.UNKNOWN)
        self.assertEqual(d.action, CostDecisionAction.BLOCK)
        with self.assertRaises(CostGuardBlockedError):
            GroqProvider().generate("hello")

    def test_nim_unknown_blocked(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.LLM, "nim"))
        self.assertEqual(d.cost_class, CostClass.UNKNOWN)
        with self.assertRaises(CostGuardBlockedError):
            NIMProvider().generate("hello")

    def test_ollama_local_allowed(self):
        d = cost_guard.authorize(ResourceRequest(
            ResourceType.LLM, "ollama", endpoint="http://localhost:11434", host="localhost"
        ))
        self.assertTrue(d.is_allow)
        self.assertEqual(d.reason, CostReason.LOCAL_RESOURCE_ALLOWED)

    def test_fallback_allowed(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.LLM, "fallback"))
        self.assertTrue(d.is_allow)
        text = FallbackProvider().generate("who are you").text
        self.assertTrue(text)

    def test_pyttsx3_allowed(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.TTS, "pyttsx3"))
        self.assertTrue(d.is_allow)

    def test_edge_tts_blocked(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.TTS, "edge_tts"))
        self.assertEqual(d.cost_class, CostClass.UNKNOWN)
        self.assertFalse(d.is_allow)

    def test_google_stt_blocked(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.STT, "google_web_speech"))
        self.assertFalse(d.is_allow)

    def test_local_whisper_stt_allowed(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.STT, "local_whisper"))
        self.assertTrue(d.is_allow)
        self.assertEqual(d.cost_class, CostClass.LOCAL_FREE)

    def test_google_translate_blocked(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.TRANSLATION, "google_translate"))
        self.assertFalse(d.is_allow)

    def test_external_search_blocked(self):
        ddg = cost_guard.authorize(ResourceRequest(ResourceType.SEARCH, "duckduckgo", host="api.duckduckgo.com"))
        ipa = cost_guard.authorize(ResourceRequest(ResourceType.SEARCH, "ip_api", host="ip-api.com"))
        yt = cost_guard.authorize(ResourceRequest(ResourceType.SEARCH, "youtube", host="www.youtube.com"))
        self.assertFalse(ddg.is_allow)
        self.assertFalse(ipa.is_allow)
        self.assertFalse(yt.is_allow)

    def test_override_openai_blocked(self):
        router = ModelRouter()
        probe = PaidProbe()
        router.providers["openai"] = probe
        groq = PaidProbe()
        groq.name = "groq"
        router.providers["groq"] = groq
        resp = router.generate("hi", task_type="general", provider_override="openai")
        self.assertEqual(probe.invoked, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_override_cannot_bypass(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.LLM, "openai"))
        self.assertEqual(d.action, CostDecisionAction.BLOCK)

    def test_failed_groq_no_openai(self):
        router = ModelRouter()
        groq = PaidProbe()
        groq.name = "groq"
        openai = PaidProbe()
        router.providers["groq"] = groq
        router.providers["openai"] = openai
        router.generate("hi", task_type="general")
        self.assertEqual(groq.invoked, 0)
        self.assertEqual(openai.invoked, 0)

    def test_failed_nim_no_gemini(self):
        router = ModelRouter()
        nim = PaidProbe()
        nim.name = "nim"
        gem = PaidProbe()
        gem.name = "gemini"
        router.providers["nim"] = nim
        router.providers["gemini"] = gem
        router.generate("hi", task_type="general")
        self.assertEqual(nim.invoked, 0)
        self.assertEqual(gem.invoked, 0)

    def test_circuit_open_local_no_paid(self):
        provider_circuit_breaker.reset()
        router = ModelRouter()
        local = LocalFake()
        paid = PaidProbe()
        router.providers["ollama"] = local
        router.providers["openai"] = paid
        provider_circuit_breaker.record_failure("ollama")
        provider_circuit_breaker.record_failure("ollama")
        provider_circuit_breaker.record_failure("ollama")
        router.generate("who are you", task_type="bounded_draft")
        self.assertEqual(paid.invoked, 0)
        provider_circuit_breaker.reset()

    def test_unknown_http_blocked(self):
        d = cost_guard.authorize(ResourceRequest(
            ResourceType.HTTP, "arbitrary", host="api.example-paid.test", endpoint="https://api.example-paid.test/v1"
        ))
        self.assertEqual(d.reason, CostReason.UNKNOWN_ENDPOINT_BLOCKED)
        self.assertFalse(d.is_allow)

    def test_remote_postgres_not_local_free(self):
        d = cost_guard.authorize(ResourceRequest(
            ResourceType.DATABASE, "postgres", host="db.rds.amazonaws.com"
        ))
        self.assertEqual(d.cost_class, CostClass.UNKNOWN)
        self.assertFalse(d.is_allow)
        self.assertTrue(is_loopback_host("localhost"))
        local = cost_guard.authorize(ResourceRequest(ResourceType.DATABASE, "postgres", host="127.0.0.1"))
        self.assertTrue(local.is_allow)

    def test_fastembed_download_blocked(self):
        d = cost_guard.authorize(ResourceRequest(
            ResourceType.EMBEDDING_DOWNLOAD, "huggingface", host="huggingface.co"
        ))
        self.assertFalse(d.is_allow)

    def test_no_secrets_in_telemetry_attrs(self):
        cost_guard.authorize(ResourceRequest(
            ResourceType.LLM, "openai", model="gpt-4o", correlation_id="req-1"
        ))
        rec = cost_guard.recent_decisions()[-1]
        blob = str(rec.request) + rec.reason.value + rec.cost_class.value
        self.assertNotIn("sk-", blob)
        self.assertNotIn("gsk_", blob)

    def test_concurrent_authorize(self):
        errors = []

        def worker():
            try:
                d = cost_guard.authorize(ResourceRequest(ResourceType.LLM, "openai"))
                if d.is_allow:
                    errors.append("allowed")
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_retries_cannot_bypass(self):
        p = OpenAIProvider()
        for _ in range(3):
            with self.assertRaises(CostGuardBlockedError):
                p.generate("retry")

    def test_fallback_only_allow_candidates(self):
        router = ModelRouter()
        paid = PaidProbe()
        router.providers["openai"] = paid
        router.providers["groq"] = PaidProbe()
        router.providers["groq"].name = "groq"
        resp = router.generate("who are you", task_type="general")
        self.assertEqual(paid.invoked, 0)
        self.assertTrue(resp.text or resp.tool_calls)

    def test_fake_paid_never_invoked(self):
        probe = PaidProbe()
        with self.assertRaises(CostGuardBlockedError):
            probe.generate("charge me")
        self.assertEqual(probe.invoked, 0)

    def test_free_tier_without_attestation_blocked(self):
        d = cost_guard.authorize(ResourceRequest(ResourceType.LLM, "groq"))
        self.assertNotEqual(d.reason, CostReason.FREE_TIER_ALLOWED)

    def test_free_tier_attested_allowed(self):
        reg = build_default_registry()
        reg[("LLM", "sandbox")] = ResourceAttestation(
            resource_type=ResourceType.LLM,
            provider="sandbox",
            cost_class=CostClass.FREE_TIER,
            verified_free_tier=True,
        )
        g = CostGuard(registry=reg)
        d = g.authorize(ResourceRequest(ResourceType.LLM, "sandbox"))
        self.assertTrue(d.is_allow)
        self.assertEqual(d.reason, CostReason.FREE_TIER_ALLOWED)

    def test_remote_ollama_unknown(self):
        d = cost_guard.authorize(ResourceRequest(
            ResourceType.LLM, "ollama", endpoint="https://ollama.cloud.example/v1", host="ollama.cloud.example"
        ))
        self.assertEqual(d.cost_class, CostClass.UNKNOWN)
        self.assertFalse(d.is_allow)

    def test_sensitive_draft_still_empty(self):
        from proactive.draft_policy import allowed_providers_for
        self.assertEqual(allowed_providers_for("SENSITIVE"), [])

    def test_normal_draft_no_openai_gemini(self):
        from proactive.draft_policy import allowed_providers_for
        os.environ["PROACTIVE_LLM_DRAFT_NORMAL_ENABLED"] = "true"
        names = allowed_providers_for("NORMAL")
        self.assertNotIn("openai", names)
        self.assertNotIn("gemini", names)
        os.environ["PROACTIVE_LLM_DRAFT_NORMAL_ENABLED"] = "false"

    def test_private_local_compatible(self):
        from proactive.draft_policy import allowed_providers_for
        os.environ["PROACTIVE_LLM_DRAFT_PRIVATE_ENABLED"] = "true"
        ollama = MagicMock()
        ollama.is_enabled.return_value = True
        ollama.is_available.return_value = True
        ollama.deployment_mode = "LOCAL"
        ollama.name = "ollama"
        ollama.base_url = "http://127.0.0.1:11434"
        ollama.model = "llama3"
        with patch("core.model_router.model_router") as mr:
            mr.providers = {"ollama": ollama, "openai": MagicMock()}
            names = allowed_providers_for("PRIVATE")
        self.assertEqual(names, ["ollama"])
        os.environ["PROACTIVE_LLM_DRAFT_PRIVATE_ENABLED"] = "false"

    def test_elevenlabs_no_network(self):
        from core.cinematic_voice import DOOMCinematicVoice
        v = DOOMCinematicVoice.__new__(DOOMCinematicVoice)
        v.elevenlabs_key = "sk_test_eleven"
        v.elevenlabs_voice_id = "voice"
        v.stop_event = threading.Event()
        with patch("requests.post") as post:
            ok = DOOMCinematicVoice._speak_with_elevenlabs(v, "hello", "en")
        self.assertFalse(ok)
        post.assert_not_called()

    def test_google_stt_listen_no_network(self):
        from core import listen as listen_mod
        self.assertFalse(listen_mod._google_stt_allowed())
        with patch.object(listen_mod, "get_recognizer"):
            with patch("speech_recognition.Microphone"):
                pass

    def test_translate_blocked(self):
        from core.translate import translate
        with patch("core.translate.speak"):
            with patch("core.translate.stop_speaking"):
                with patch("requests.get") as get:
                    out = translate("hello", "hi")
        get.assert_not_called()
        self.assertEqual(out, "hello")

    def test_web_search_blocked(self):
        from core.web_search import DOOMWebSearch
        s = DOOMWebSearch()
        with patch("requests.get") as get:
            self.assertEqual(s.search_duckduckgo("news"), [])
            self.assertEqual(s.get_location_from_ip(), "Unknown location")
        get.assert_not_called()

    def test_developer_http_blocked(self):
        from tools.developer_tools import APITesterTool
        tool = APITesterTool()
        with patch("requests.get") as get:
            res = tool._execute_impl(url="https://api.example-paid.test/v1", method="GET")
        get.assert_not_called()
        self.assertFalse(res.success)

    def test_safe_http_github_blocked(self):
        from proactive.connectors.http_safe import SafeHttp, SafeHttpError
        http = SafeHttp(transport=lambda *a, **k: (200, {}, b"{}"))
        with self.assertRaises(SafeHttpError) as ctx:
            http.get("https://api.github.com/notifications")
        self.assertIn("cost_policy", str(ctx.exception))

    def test_dashboard_types_not_direct_groq(self):
        import inspect
        from dashboard import server as dash
        src = inspect.getsource(dash.dev_generate_types)
        self.assertNotIn("GroqProvider()", src)
        self.assertIn("model_router.generate", src)

    def test_ide_uses_router(self):
        import inspect
        from ide import server as ide
        src = inspect.getsource(ide)
        self.assertIn("model_router.generate", src)
        self.assertNotIn("provider.generate(full_prompt)", src)

    def test_provider_metadata_not_authority(self):
        self.assertFalse(GroqProvider.billing_possible)
        d = cost_guard.authorize(ResourceRequest(ResourceType.LLM, "groq"))
        self.assertEqual(d.cost_class, CostClass.UNKNOWN)

    def test_fastembed_missing_weights_no_download(self):
        from memory.embedding.fastembed_provider import FastEmbedProvider
        from memory.embedding.base import ProviderUnavailableError
        p = FastEmbedProvider(lazy_load=True)
        with patch.object(p, "_local_weights_present", return_value=False):
            with patch("fastembed.TextEmbedding") as te:
                with self.assertRaises(ProviderUnavailableError):
                    p._ensure_model_loaded()
                te.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
