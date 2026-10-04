#!/usr/bin/env python
"""DOOM V12.6 — Real-Time Cognitive Loop tests (deterministic clock; HARD $0)."""

import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.v12.runtime import (  # noqa: E402
    BREAKER_PAUSE_S, MAX_PER_OWNER, MAX_QUEUE, CognitiveEventHandler, EventPriority, RealtimeRuntime,
    RuntimeEvent, make_runtime_monitor,
)

A, B = "rt_owner_a", "rt_owner_b"


class Clock:
    def __init__(self, t=10_000.0):
        self.t = t

    def __call__(self):
        return self.t


def ev(owner=A, etype="disk_low", desc="disk low", prio=EventPriority.NORMAL, source="system", session="s1", ts=None, key=""):
    return RuntimeEvent.make(owner, session, source, etype, desc, prio, timestamp=ts, dedup_key=key)


class Recorder:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    def __call__(self, event):
        if self.fail:
            raise RuntimeError("handler down")
        self.events.append(event)
        return {"ok": True}


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rec = Recorder()
        self.rt = RealtimeRuntime(self.rec, clock=self.clock, cooldown_s=30)

    def tearDown(self):
        self.rt.stop()


class TestIntakeAndOrdering(Base):

    def test_priority_order_and_owner_carried(self):
        self.rt.submit(ev(etype="a", prio=EventPriority.LOW, key="1"))
        self.rt.submit(ev(etype="b", prio=EventPriority.CRITICAL, key="2"))
        self.rt.submit(ev(owner=B, etype="c", prio=EventPriority.HIGH, key="3"))
        self.rt.run_once()
        self.assertEqual([e.event_type for e in self.rec.events], ["b", "c", "a"])
        self.assertEqual(self.rec.events[1].owner_id, B)

    def test_deduplication(self):
        self.assertEqual(self.rt.submit(ev()), "ACCEPTED")
        self.assertEqual(self.rt.submit(ev()), "DEDUPLICATED")
        self.clock.t += 301
        self.rt.run_once()
        self.assertEqual(self.rt.submit(ev(ts=self.clock.t)), "ACCEPTED")

    def test_cooldown_except_critical(self):
        self.assertEqual(self.rt.submit(ev(desc="x1", key="k1")), "ACCEPTED")
        self.assertEqual(self.rt.submit(ev(desc="x2", key="k2")), "COOLDOWN")
        self.assertEqual(self.rt.submit(ev(desc="x3", key="k3", prio=EventPriority.CRITICAL)), "ACCEPTED")
        self.clock.t += 31
        self.assertEqual(self.rt.submit(ev(desc="x4", key="k4")), "ACCEPTED")

    def test_backpressure_and_per_owner_quota(self):
        for i in range(MAX_PER_OWNER + 10):
            self.rt.submit(ev(etype=f"t{i}", key=f"q{i}", prio=EventPriority.LOW))
        self.assertEqual(self.rt.queue_size(A), MAX_PER_OWNER)
        self.assertEqual(self.rt.stats.rejected_backpressure, 10)
        # Another owner is not starved by A's flood.
        self.assertEqual(self.rt.submit(ev(owner=B, key="b1")), "ACCEPTED")
        # A higher-priority event for A displaces A's lowest-priority one.
        self.assertEqual(self.rt.submit(ev(etype="urgent", key="u1", prio=EventPriority.HIGH)), "ACCEPTED")
        self.assertEqual(self.rt.queue_size(A), MAX_PER_OWNER)
        self.assertEqual(self.rt.stats.evicted_backpressure, 1)

    def test_global_queue_bound(self):
        owners = [f"o{i}" for i in range(10)]
        for i in range(400):
            self.rt.submit(ev(owner=owners[i % 10], etype=f"t{i}", key=f"g{i}"))
        self.assertLessEqual(self.rt.queue_size(), MAX_QUEUE)


class TestProcessingControls(Base):

    def test_rate_limit_per_minute(self):
        rt = RealtimeRuntime(self.rec, clock=self.clock, cooldown_s=0, max_cycles_per_minute=5)
        for i in range(20):
            rt.submit(ev(etype=f"t{i}", key=f"r{i}"))
        rt.run_once(budget=100)
        self.assertEqual(len(self.rec.events), 5)
        self.clock.t += 61
        rt.run_once(budget=100)
        self.assertEqual(len(self.rec.events), 10)

    def test_circuit_breaker_and_recovery(self):
        failing = Recorder(fail=True)
        rt = RealtimeRuntime(failing, clock=self.clock, cooldown_s=0)
        for i in range(6):
            rt.submit(ev(etype=f"t{i}", key=f"f{i}"))
        rt.run_once(budget=100)
        self.assertEqual(rt.stats.failures, 3)
        self.assertTrue(rt.breaker_open)
        self.assertEqual(rt.queue_size(), 3, "processing pauses instead of burning through events")
        failing.fail = False
        self.clock.t += BREAKER_PAUSE_S + 1
        rt.run_once(budget=100)
        self.assertEqual(rt.queue_size(), 0)
        self.assertEqual(rt.stats.processed, 3)
        self.assertEqual([m.get("error") for m in rt.outbox(A)][:3], ["RuntimeError"] * 3)

    def test_timers_fire_without_bursts(self):
        self.rt.add_timer("heartbeat", A, "s1", 60, "heartbeat")
        self.clock.t += 600  # 10 intervals elapsed
        self.rt.run_once()
        self.assertEqual(self.rt.stats.timer_events, 1, "no catch-up burst")
        with self.assertRaises(ValueError):
            self.rt.add_timer("spin", A, "s1", 0.01, "x")

    def test_outbox_is_owner_and_session_scoped_and_bounded(self):
        for i in range(100):
            self.rt.submit(ev(etype=f"t{i}", key=f"o{i}", session="s1" if i % 2 else "s2"))
            self.clock.t += 31
            self.rt.run_once()
        self.assertLessEqual(len(self.rt.outbox(A)), 64)
        self.assertTrue(all(m["session_id"] == "s1" for m in self.rt.outbox(A, "s1")))
        self.assertEqual(self.rt.outbox(B), [])


class TestLifecycle(unittest.TestCase):

    def test_start_stop_restart_and_no_thread_leak(self):
        rec = Recorder()
        rt = RealtimeRuntime(rec, cooldown_s=0)
        baseline = threading.active_count()
        for cycle in range(5):
            rt.start(tick_s=0.02)
            rt.start(tick_s=0.02)  # idempotent
            rt.submit(ev(etype=f"life{cycle}", key=f"l{cycle}", ts=time.time()))
            deadline = time.time() + 5
            while len(rec.events) <= cycle and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(rt.stop())
            self.assertFalse(rt.running)
        self.assertEqual(len(rec.events), 5)
        rt.restart(tick_s=0.02)
        self.assertTrue(rt.running)
        self.assertTrue(rt.stop())
        time.sleep(0.1)
        self.assertLessEqual(threading.active_count(), baseline)

    def test_stop_while_handler_running_is_bounded(self):
        release = threading.Event()

        def slow(event):
            release.wait(5)
            return {}

        rt = RealtimeRuntime(slow, cooldown_s=0)
        rt.submit(ev(ts=time.time()))
        rt.start(tick_s=0.02)
        time.sleep(0.1)
        self.assertFalse(rt.stop(timeout_s=0.2), "reports that the in-flight handler is still running")
        release.set()
        self.assertTrue(rt.stop(timeout_s=5))


class TestV115MonitorIntegration(unittest.TestCase):

    def test_monitor_events_route_into_runtime_not_threads(self):
        rec = Recorder()
        rt = RealtimeRuntime(rec, cooldown_s=0)
        monitor = make_runtime_monitor(rt, A, "s1")
        states = {"goal_state": {"no_active_goal": True}, "user_model_state": {"entry_count": 1},
                  "experience_state": {"experience_count": 0}, "memory_state": {"memory_count": 0}}
        threads_before = threading.active_count()
        with patch.object(monitor, "_collect_all_states", return_value=states):
            monitor._continuous_monitor_cycle()
        self.assertEqual(threading.active_count(), threads_before, "no per-event thread")
        self.assertEqual(rt.queue_size(A), 1)
        rt.run_once()
        self.assertTrue(rec.events[0].source.startswith("v11_monitor:"))
        self.assertEqual(monitor.state.monitoring_errors, 0)
        # V11.5's own cooldown still applies to the attached monitor.
        self.assertFalse(monitor._is_cooldown_complete())


# --- full pipeline -------------------------------------------------------------------

from test_v11_8_end_to_end_integration import V118TestBase, make_plan, make_step, COMPUTER_SESSION, OWNER, SESSION  # noqa: E402
from test_v12_1_response_intelligence import FakeLocalModel  # noqa: E402
from core.v12.cognitive_orchestrator import V12CognitiveOrchestrator  # noqa: E402
from core.v12.adaptive_learning import AdaptiveLearning, LearningStore  # noqa: E402
from orchestration.conversation.respond import use_respond_provider_for_tests, reset_respond_provider_for_tests  # noqa: E402


class TestCognitivePipeline(V118TestBase):

    def setUp(self):
        super().setUp()
        self.orch = V12CognitiveOrchestrator(learning=AdaptiveLearning(store=LearningStore()))
        use_respond_provider_for_tests(FakeLocalModel(["Disk space is low; consider cleaning temp files."] * 5))
        self.rt = RealtimeRuntime(CognitiveEventHandler(self.orch), cooldown_s=0)

    def tearDown(self):
        reset_respond_provider_for_tests()
        self.rt.stop()
        super().tearDown()

    def test_event_becomes_monitoring_cycle_with_percept(self):
        self.rt.submit(RuntimeEvent.make(OWNER, SESSION, "system", "disk_low", "disk space low",
                                         EventPriority.HIGH, {"free_gb": 3}))
        with self.spy.hooks():
            self.rt.run_once()
        out = self.rt.outbox(OWNER)[0]["outcome"]
        self.assertTrue(out["success"])
        self.assertFalse(out["executed"])
        self.assertIn("Disk space is low", out["response_text"])
        items = self.orch.perception.items(OWNER, SESSION)
        self.assertEqual(items[0].summary, "event disk_low (free_gb=3)")
        self.assertEqual(self.orch.learning.explain(OWNER), [], "events are never learned as statements")

    def test_proactive_action_still_requires_authorization(self):
        plan = make_plan([make_step("s1", "computer", "CLICK", [("name", "Cleanup")], risk="MEDIUM", approval=True)],
                         goal_id="ag_v126", plan_risk="MEDIUM", approval=True, computer_session_id=COMPUTER_SESSION)
        from test_v11_8_end_to_end_integration import planning_for
        self.rt.submit(RuntimeEvent.make(OWNER, SESSION, "system", "cleanup_needed", "cleanup", EventPriority.HIGH))
        with self.spy.hooks(), \
                patch.object(self.orch, "planning_integration", return_value=planning_for(plan)), \
                patch("orchestration.authorization.verified_computer_session_id", side_effect=lambda o, cs: (cs, "OK")):
            self.rt.run_once()
        out = self.rt.outbox(OWNER)[0]["outcome"]
        self.assertFalse(out["executed"] and out["success"])
        self.assertTrue(out["pending_id"])
        self.assertEqual(self.spy.calls, [], "no autonomous action")


if __name__ == "__main__":
    unittest.main(verbosity=2)
