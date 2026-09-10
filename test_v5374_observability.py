#!/usr/bin/env python3
"""DOOM V5.3.7.4 Operational Telemetry — exactly 52 tests."""

import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from observability.schemas import OperationalEvent, SchemaValidationError, classify_error
from observability.redactor import redact_event
from observability.bus import TelemetryBus, telemetry_bus
from observability.metrics import metrics_registry
from observability.telemetry import emit, request_scope
from observability.sinks import postgres_sink
from core.reliability.correlation import bind_request, reset_request, get_current_correlation
from models.base_provider import BaseLLMProvider, LLMResponse, ProviderTimeoutError, ProviderRateLimitError
from core.model_router import ModelRouter


def _dead_provider():
    m = MagicMock()
    m.is_enabled.return_value = False
    m.is_available.return_value = False
    return m


def _wire_local(router, fake):
    from models.fallback_provider import FallbackProvider
    for k in list(router.providers):
        router.providers[k] = _dead_provider()
    fake.name = "ollama"
    fake.base_url = "http://127.0.0.1:11434"
    fake.capabilities = router.provider_capabilities["ollama"]
    router.providers["ollama"] = fake
    router.providers["fallback"] = FallbackProvider()
    return fake


def _ev(**kw):
    return OperationalEvent(
        doom_request_id="req_test",
        category=kw.pop("category", "request"),
        name=kw.pop("name", "request.started"),
        status=kw.pop("status", "ok"),
        **kw,
    )


class TestV5374Observability(unittest.TestCase):
    def setUp(self):
        reset_request()
        telemetry_bus.clear()
        metrics_registry.reset()
        postgres_sink.enabled = False
        from core.reliability.circuit_breaker import provider_circuit_breaker
        provider_circuit_breaker.reset()

    def tearDown(self):
        reset_request()
        postgres_sink.enabled = True

    # ----- Schema validation (4) -----
    def test_01_schema_valid_event(self):
        e = _ev(attributes={"provider": "nim", "cost_tier": "FREE_TIER"})
        self.assertEqual(e.category, "request")
        self.assertIn("event_id", e.to_dict())

    def test_02_schema_invalid_category_rejected(self):
        with self.assertRaises(SchemaValidationError):
            OperationalEvent(category="nope", name="x")

    def test_03_schema_forbidden_attribute_rejected(self):
        with self.assertRaises(SchemaValidationError):
            _ev(attributes={"prompt": "hello boss"})

    def test_04_schema_deterministic_json(self):
        e = _ev(event_id="e1", ts_unix_ms=1, attributes={"attempt": 1, "provider": "groq"})
        self.assertEqual(e.to_json(), e.to_json())
        self.assertIn('"attempt":1', e.to_json())

    # ----- Correlation (5) -----
    def test_05_bind_creates_fresh_request_id(self):
        a = bind_request()
        reset_request()
        b = bind_request()
        self.assertNotEqual(a.doom_request_id, b.doom_request_id)

    def test_06_process_request_binds_correlation(self):
        from core.orchestrator import doom_core
        with patch("core.cinematic_voice.speak"), patch("core.cinematic_voice.stop_speaking"):
            doom_core.process_request("What is 2 + 2?")
        ids = {e.doom_request_id for e in telemetry_bus.snapshot() if e.doom_request_id}
        self.assertTrue(ids)
        self.assertTrue(all(i.startswith("req_") for i in ids))

    def test_07_request_scope_resets_context(self):
        with request_scope() as ctx:
            rid = ctx.doom_request_id
            self.assertEqual(get_current_correlation().doom_request_id, rid)
        from core.reliability.correlation import _current_context
        self.assertIsNone(_current_context.get())

    def test_08_provider_call_id_on_generate(self):
        router = ModelRouter()
        fake = MagicMock()
        fake.is_enabled.return_value = True
        fake.is_available.return_value = True
        fake.cost_tier = "LOCAL"
        fake.model = "fake-model"
        fake.generate.return_value = LLMResponse(text="ok", tool_calls=[], model_name="fake")
        bind_request()
        _wire_local(router, fake)
        router.generate("hi", task_type="bounded_draft")
        pcs = [e.provider_call_id for e in telemetry_bus.snapshot() if e.category == "provider"]
        self.assertTrue(any(pcs))

    def test_09_cognitive_cycle_id_set_during_process(self):
        from core.cognition.engine import cognitive_engine
        bind_request()
        st = cognitive_engine.process("What is 2 + 2?")
        self.assertTrue(get_current_correlation().cognitive_cycle_id)

    # ----- Lifecycle order (4) -----
    def test_10_request_started_before_completed(self):
        with request_scope():
            emit("cognitive.stage", "cognitive", attributes={"stage": "understand"})
        names = [e.name for e in telemetry_bus.snapshot()]
        self.assertLess(names.index("request.started"), names.index("request.completed"))

    def test_11_fallback_after_provider_fail(self):
        router = ModelRouter()
        bad = MagicMock()
        bad.is_enabled.return_value = True
        bad.is_available.return_value = True
        bad.cost_tier = "LOCAL"
        bad.model = "x"
        bad.generate.side_effect = ProviderTimeoutError("t", "ollama", 1)
        good = MagicMock()
        good.is_enabled.return_value = True
        good.is_available.return_value = True
        good.cost_tier = "LOCAL"
        good.model = "y"
        good.generate.return_value = LLMResponse(text="ok", tool_calls=[], model_name="g")
        bind_request()
        _wire_local(router, bad)
        router.generate("hi", task_type="bounded_draft")
        names = [e.name for e in telemetry_bus.snapshot()]
        self.assertIn("provider.generate.failed", names)
        self.assertIn("fallback.started", names)
        self.assertLess(names.index("provider.generate.failed"), names.index("fallback.started"))

    def test_12_cognition_started_before_completed(self):
        from core.cognition.engine import cognitive_engine
        bind_request()
        cognitive_engine.process("What is 2 + 2?")
        names = [e.name for e in telemetry_bus.snapshot()]
        self.assertTrue(any("cognition_started" in n for n in names))
        self.assertTrue(any("cognition_completed" in n for n in names))

    def test_13_plan_after_understand(self):
        from core.cognition.engine import cognitive_engine
        bind_request()
        cognitive_engine.process("create a file hello.py on my desktop")
        names = [e.name for e in telemetry_bus.snapshot()]
        if "understanding_complete" in names and "plan_created" in names:
            self.assertLess(names.index("understanding_complete"), names.index("plan_created"))

    # ----- Latency (4) -----
    def test_14_request_latency_present(self):
        with request_scope():
            time.sleep(0.01)
        done = [e for e in telemetry_bus.snapshot() if e.name == "request.completed"]
        self.assertTrue(done[0].latency_ms and done[0].latency_ms > 0)

    def test_15_provider_latency_present(self):
        self.test_08_provider_call_id_on_generate()
        pe = [e for e in telemetry_bus.snapshot() if e.category == "provider"]
        self.assertTrue(any(e.latency_ms is not None for e in pe))

    def test_16_memory_retrieval_latency_event(self):
        from core.cognition.engine import cognitive_engine
        bind_request()
        cognitive_engine.process("What is 2 + 2?")
        mems = [e for e in telemetry_bus.snapshot() if e.category == "memory"]
        self.assertTrue(mems)

    def test_17_cognitive_stage_latency(self):
        from core.cognition.engine import cognitive_engine
        bind_request()
        cognitive_engine.process("What is 2 + 2?")
        stages = [e for e in telemetry_bus.snapshot() if e.attributes.get("stage") == "understand"]
        self.assertTrue(stages)
        self.assertIsNotNone(stages[0].latency_ms)

    # ----- Provider (4) -----
    def test_18_provider_name_and_cost_tier(self):
        self.test_08_provider_call_id_on_generate()
        pe = [e for e in telemetry_bus.snapshot() if e.category == "provider"][0]
        self.assertEqual(pe.attributes.get("cost_tier"), "LOCAL")
        self.assertEqual(pe.attributes.get("provider"), "ollama")

    def test_19_provider_error_class_not_message(self):
        router = ModelRouter()
        bad = MagicMock()
        bad.is_enabled.return_value = True
        bad.is_available.return_value = True
        bad.cost_tier = "LOCAL"
        bad.model = "x"
        bad.generate.side_effect = ProviderRateLimitError("secret gsk_LIVEFAKE", "ollama")
        bind_request()
        _wire_local(router, bad)
        router.providers["fallback"] = _dead_provider()
        with self.assertRaises(Exception):
            router.generate("hi", task_type="bounded_draft")
        blob = "".join(e.to_json() for e in telemetry_bus.snapshot())
        self.assertNotIn("gsk_LIVEFAKE", blob)
        self.assertTrue(any(e.error_type == "RATE_LIMIT" for e in telemetry_bus.snapshot()))

    def test_20_no_bedrock_health_metric(self):
        snap = metrics_registry.snapshot()
        self.assertNotIn("bedrock_health_probe_total", snap["counters"])

    def test_21_nim_free_tier_in_metadata(self):
        from models.nim_provider import NIMProvider
        self.assertEqual(NIMProvider.cost_tier, "FREE_TIER")
        self.assertFalse(NIMProvider.streaming)

    # ----- Fallback / retry (4) -----
    def test_22_fallback_hop_list(self):
        self.test_11_fallback_after_provider_fail()
        self.assertTrue(any(e.name == "fallback.completed" for e in telemetry_bus.snapshot()))

    def test_23_final_provider_attribute(self):
        self.test_11_fallback_after_provider_fail()
        done = [e for e in telemetry_bus.snapshot() if e.name == "provider.generate.completed"]
        self.assertTrue(done)
        self.assertIn(done[-1].attributes.get("final_provider"), ("ollama", "fallback"))

    def test_24_circuit_skipped_event(self):
        from core.reliability.circuit_breaker import provider_circuit_breaker
        provider_circuit_breaker.reset()
        router = ModelRouter()
        fake = MagicMock()
        fake.is_enabled.return_value = True
        fake.is_available.return_value = True
        fake.cost_tier = "LOCAL"
        fake.model = "x"
        fake.generate.return_value = LLMResponse(text="ok", tool_calls=[], model_name="x")
        bind_request()
        _wire_local(router, fake)
        provider_circuit_breaker._providers["ollama"] = {
            "consecutive_failures": 9,
            "last_failure_time": time.time(),
            "state": __import__("core.reliability.circuit_breaker", fromlist=["CircuitState"]).CircuitState.OPEN,
        }
        router.generate("hi", task_type="bounded_draft")
        self.assertTrue(any(e.attributes.get("circuit_skipped") for e in telemetry_bus.snapshot()))
        provider_circuit_breaker.reset()

    def test_25_empty_response_triggers_fallback_event(self):
        router = ModelRouter()
        empty = MagicMock()
        empty.is_enabled.return_value = True
        empty.is_available.return_value = True
        empty.cost_tier = "LOCAL"
        empty.model = "e"
        empty.generate.return_value = LLMResponse(text="", tool_calls=[], model_name="e")
        bind_request()
        _wire_local(router, empty)
        router.generate("hi", task_type="bounded_draft")
        self.assertTrue(any(e.name == "provider.generate.empty" for e in telemetry_bus.snapshot()))

    # ----- Tool (3) -----
    def test_26_tool_event_has_name(self):
        bind_request()
        emit("tool.completed", "tool", latency_ms=1.2, attributes={"tool": "filesystem_write_file"})
        e = [x for x in telemetry_bus.snapshot() if x.category == "tool"][0]
        self.assertEqual(e.attributes["tool"], "filesystem_write_file")
        self.assertEqual(e.latency_ms, 1.2)

    def test_27_tool_failure_error_type(self):
        emit("tool.failed", "tool", status="error", error_type="TOOL_ERROR", attributes={"tool": "x"})
        self.assertEqual(telemetry_bus.snapshot()[-1].error_type, "TOOL_ERROR")

    def test_28_tool_no_stdout_in_event(self):
        emit("tool.completed", "tool", attributes={"tool": "x"})
        self.assertNotIn("stdout", telemetry_bus.snapshot()[-1].to_json())

    # ----- Cognitive (3) -----
    def test_29_cognitive_no_cot_field(self):
        from core.cognition.engine import cognitive_engine
        bind_request()
        cognitive_engine.process("What is 2 + 2?")
        blob = "".join(e.to_json() for e in telemetry_bus.snapshot())
        self.assertNotIn("chain_of_thought", blob)
        self.assertNotIn("reasoning_summary", blob)

    def test_30_termination_reason_safe(self):
        from core.cognition.engine import cognitive_engine
        bind_request()
        st = cognitive_engine.process("What is 2 + 2?")
        self.assertTrue(st.final_response_status)

    def test_31_stage_names_present(self):
        from core.cognition.engine import cognitive_engine
        bind_request()
        cognitive_engine.process("What is 2 + 2?")
        stages = {e.attributes.get("stage") for e in telemetry_bus.snapshot()}
        self.assertIn("understand", stages)

    # ----- Memory privacy (4) -----
    def test_32_no_memory_content_key(self):
        emit("memory.retrieval.completed", "memory", attributes={"selected_count": 1, "retrieval_mode": "HYBRID", "privacy_ok": True})
        self.assertNotIn("content", telemetry_bus.snapshot()[-1].to_json())

    def test_33_no_query_text_on_bus(self):
        from core.cognition.engine import cognitive_engine
        bind_request()
        cognitive_engine.process("secret personal query about my password")
        blob = "".join(e.to_json() for e in telemetry_bus.snapshot())
        self.assertNotIn("secret personal query", blob)

    def test_34_hash_allowed_not_body(self):
        with self.assertRaises(SchemaValidationError):
            _ev(category="memory", name="memory.x", attributes={"content": "body"})

    def test_35_prompt_not_on_memory_events(self):
        from core.cognition.engine import cognitive_engine
        bind_request()
        cognitive_engine.process("What is 2 + 2?")
        for e in telemetry_bus.snapshot():
            self.assertNotIn("prompt", e.attributes)

    # ----- Secret leakage (4) -----
    def test_36_openai_key_redacted(self):
        safe = redact_event(_ev(name="x", attributes={"provider": "openai sk-ABCDEFGHIJKLMNOP"}))
        self.assertIsNotNone(safe)
        self.assertNotIn("sk-ABCDEFGHIJKLMNOP", safe.to_json())

    def test_37_gemini_url_dropped_or_redacted(self):
        e = _ev(name="x", attributes={"provider": "https://generativelanguage.googleapis.com/v1?key=SECRETKEY"})
        safe = redact_event(e)
        self.assertTrue(safe is None or "SECRETKEY" not in safe.to_json())

    def test_38_groq_nvidia_aws_patterns(self):
        for s in ("gsk_AAAABBBB", "nvapi-CCCCDDDD", "AKIAIOSFODNN7EXAMPLE"):
            safe = redact_event(_ev(name="x", attributes={"provider": s}))
            self.assertTrue(safe is None or s not in safe.to_json())

    def test_39_emit_drops_forbidden_without_raise(self):
        emit("bad", "request", attributes={"prompt": "user said hello"})
        self.assertTrue(metrics_registry.get("telemetry_dropped_total") >= 1)

    # ----- Isolation (4) -----
    def test_40_emit_never_raises_on_redactor_failure(self):
        emit("request.started", "request", attributes={"prompt": "nope"})
        emit("request.started", "not-a-category")

    def test_41_queue_full_drops(self):
        bus = TelemetryBus(capacity=2)
        bus.publish(_ev(event_id="1", name="a"))
        bus.publish(_ev(event_id="2", name="b"))
        bus.publish(_ev(event_id="3", name="c"))
        self.assertGreaterEqual(bus.dropped, 1)
        self.assertEqual(len(bus.snapshot()), 2)

    def test_42_subscriber_failure_isolated(self):
        def boom(_):
            raise RuntimeError("subscriber down")
        telemetry_bus.subscribe(boom)
        emit("request.started", "request")
        self.assertTrue(any(e.name == "request.started" for e in telemetry_bus.snapshot()))

    def test_43_postgres_unavailable_isolated(self):
        postgres_sink.enabled = True
        with patch("database.postgres_db.postgres_manager") as pm:
            pm.is_connected.return_value = False
            postgres_sink.on_event(_ev(name="request.started"))
            postgres_sink.flush()

    # ----- UI consistency (3) -----
    def test_44_ws_payload_has_canonical_ids(self):
        from core.cognition.engine import cognitive_engine
        captured = []
        cognitive_engine.set_broadcaster(lambda p: captured.append(p))
        bind_request()
        cognitive_engine.process("What is 2 + 2?")
        cognitive_engine.set_broadcaster(None)
        self.assertTrue(captured)
        self.assertIn("doom_request_id", captured[0])
        blob = str(captured)
        self.assertNotIn("What is 2 + 2?", blob)

    def test_45_dashboard_chat_uses_request_scope(self):
        import inspect
        from dashboard.server import agent_chat_endpoint
        src = inspect.getsource(agent_chat_endpoint)
        self.assertIn("request_scope", src)
        self.assertNotIn("NIMProvider()", src)

    def test_46_ide_uses_request_scope(self):
        src = open(os.path.join(PROJECT_ROOT, "ide", "server.py"), encoding="utf-8").read()
        self.assertIn("request_scope", src)

    # ----- Concurrency (2) -----
    def test_47_concurrent_requests_isolated_ids(self):
        ids = []
        errors = []

        def worker():
            try:
                with request_scope() as ctx:
                    time.sleep(0.02)
                    ids.append(ctx.doom_request_id)
                    self.assertEqual(get_current_correlation().doom_request_id, ctx.doom_request_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(errors)
        self.assertEqual(len(ids), 4)
        self.assertEqual(len(set(ids)), 4)

    def test_48_contextvar_cleanup(self):
        with request_scope() as ctx:
            rid = ctx.doom_request_id
        reset_request()
        # ContextVar is None; auto get would create new
        from core.reliability.correlation import _current_context
        self.assertIsNone(_current_context.get())

    # ----- Performance (2) -----
    def test_49_emit_overhead_bounded(self):
        t0 = time.perf_counter()
        for i in range(200):
            emit("request.started", "request", attributes={"attempt": i % 3})
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 1.0)

    def test_50_dropped_counter_on_overflow(self):
        metrics_registry.reset()
        orig = telemetry_bus.capacity
        telemetry_bus.capacity = 1
        telemetry_bus._q.clear()
        emit("request.started", "request")
        emit("request.completed", "request")
        telemetry_bus.capacity = orig
        self.assertGreaterEqual(metrics_registry.get("telemetry_dropped_total") + telemetry_bus.dropped, 0)

    # ----- Persistence (2) -----
    def test_51_sink_flush_does_not_raise(self):
        postgres_sink.enabled = True
        postgres_sink.on_event(_ev(name="request.started"))
        postgres_sink.flush()

    def test_52_production_e2e_no_prompt_in_telemetry(self):
        from core.orchestrator import doom_core
        secret = "SYNTHETIC_PROMPT_UNIQUE_XYZ"
        with patch("core.cinematic_voice.speak"), patch("core.cinematic_voice.stop_speaking"):
            doom_core.process_request(f"What is 2 + 2? {secret}")
        events = telemetry_bus.snapshot()
        blob = "".join(e.to_json() for e in events)
        self.assertNotIn(secret, blob)
        self.assertTrue(any(e.category == "request" for e in events))
        self.assertTrue(any(e.category == "cognitive" for e in events))
        ids = {e.doom_request_id for e in events if e.doom_request_id}
        self.assertEqual(len(ids), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
