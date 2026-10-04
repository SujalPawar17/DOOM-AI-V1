#!/usr/bin/env python
"""
DOOM V11.9 — Performance / Reliability / Long-Session Hardening Tests

Deterministic, local, HARD $0. Reuses the V11.8 harness (executor test hooks,
in-memory experience/authorization stores, audio guards).

Covers: 100+ cognitive cycles, repeated context fusion, advanced-memory cache
lifecycle and bounds, stale-memory invalidation, OUTCOME/GOAL_STATE continuity
after failures, monitor lifecycle (busy-skip, stop/restart, thread leaks),
proactive cooldown/rate limit, repeated authorization, failure/recovery,
concurrency, reinitialization, and owner/session isolation over long sessions.
"""

import gc
import io
import os
import statistics
import sys
import threading
import time
import tracemalloc
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "1"

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from test_v11_8_end_to_end_integration import (  # noqa: E402  (shared V11.8 harness)
    V118TestBase, AdapterSpy, make_plan, make_step, planning_for, _SyncThread,
    OWNER, SESSION, COMPUTER_SESSION,
)
from core.v10.context_fusion import fuse_context  # noqa: E402
from core.v11 import advanced_memory as am_module  # noqa: E402
from core.v11.advanced_memory import AdvancedMemorySystem, get_advanced_memory_system  # noqa: E402
from core.v11.cognitive_orchestrator import V11CognitiveOrchestrator  # noqa: E402
from core.v11 import proactive_behavior  # noqa: E402
from core.v11.proactive_behavior import (  # noqa: E402
    ContinuousMonitoringEnhancement, ContinuousMonitoringConfig, MonitoringEvent,
    MonitoringEventType, MonitoringPriority,
)
from orchestration import authorization as v8_authorization  # noqa: E402
from orchestration.authorization import claim_authorization  # noqa: E402
from orchestration.executor import ExecutionIdentity  # noqa: E402
from orchestration.executor_errors import ExecutionStatus  # noqa: E402
from orchestration.experience import store as experience_store  # noqa: E402
from orchestration.experience.store import list_experiences  # noqa: E402
from orchestration.experience.policy import normalize_owner_id  # noqa: E402
from orchestration.experience.types import (  # noqa: E402
    Outcome, ExperienceStatus, MAX_EXPERIENCES_PER_OWNER, MAX_LIST_RESULTS,
)
from orchestration.plan import goal_registry  # noqa: E402

METRICS = {}


def _conversation_plan(goal_id, text="ok"):
    return make_plan([make_step("s1", "conversation", "RESPOND", [("text", text)])], goal_id=goal_id)


def _record(name, value):
    METRICS[name] = value


def _store_rows(owner):
    """All in-memory experience rows for an owner (list_experiences caps at MAX_LIST_RESULTS)."""
    with experience_store._LOCK:
        return [dict(r) for r in experience_store._TEST_ROWS.get(normalize_owner_id(owner), [])]


def _active_rows(owner):
    return [r for r in _store_rows(owner) if r.get("status") == ExperienceStatus.ACTIVE_RECORD.value]


def _experience_created(result):
    return result["provenance"]["experience_integration"].get("status") == "OK"


# =============================================================================
# Long-session cognitive cycles
# =============================================================================
class TestLongSessionCycles(V118TestBase):

    def test_120_mixed_cycles_stable(self):
        """120 cycles: 100 planned (success/failure mix) + 20 real non-plan turns."""
        latencies, exceptions, outcomes, created = [], 0, {}, 0
        threads_before = threading.active_count()
        gc.collect()
        tracemalloc.start()
        snapshot_after_warmup = None
        for i in range(120):
            if i % 6 == 5:
                result = self.run_cycle(f"Hello DOOM {i % 3}")
            else:
                fail = (i % 5 == 0)
                self.spy.overrides["conversation"] = ExecutionStatus.STEP_FAILED.value if fail else None
                result = self.run_cycle(f"Task {i}", plan=_conversation_plan(f"ag_v119_long_{i}"))
                expected = not fail
                self.assertEqual(result["success"], expected, (i, result.get("failure_reason")))
            if "error" in result:
                exceptions += 1
            created += 1 if _experience_created(result) else 0
            outcomes[result["stages"]["outcome_memory_update"]["cycle_outcome"]] = \
                outcomes.get(result["stages"]["outcome_memory_update"]["cycle_outcome"], 0) + 1
            latencies.append(result["timing"]["total_time_ms"])
            if i == 19:
                gc.collect()
                snapshot_after_warmup = tracemalloc.take_snapshot()
        gc.collect()
        final = tracemalloc.take_snapshot()
        tracemalloc.stop()
        growth = sum(s.size_diff for s in final.compare_to(snapshot_after_warmup, "filename"))

        self.assertEqual(exceptions, 0)
        self.assertEqual(outcomes.get("EXECUTION_SUCCEEDED"), 80)
        self.assertEqual(outcomes.get("EXECUTION_FAILED"), 20)
        self.assertEqual(outcomes.get("NON_PLAN_RESPONSE"), 20)
        self.assertLessEqual(threading.active_count(), threads_before, "No thread leak across cycles")
        # 100 recorded experiences are expected growth; anything beyond a few MB is a leak.
        self.assertLess(growth, 8 * 1024 * 1024, f"Memory growth after warm-up: {growth} bytes")
        # Every executed plan produced an experience; the store keeps a bounded active set.
        self.assertEqual(created, 100)
        self.assertEqual(len(_active_rows(OWNER)), MAX_EXPERIENCES_PER_OWNER)
        self.assertEqual(len(list_experiences(OWNER).experiences), MAX_LIST_RESULTS)
        _record("experience_rows_total_after_100_plans", len(_store_rows(OWNER)))
        _record("experience_rows_active_after_100_plans", len(_active_rows(OWNER)))

        ordered = sorted(latencies)
        _record("long_session_cycles", 120)
        _record("long_session_p50_ms", round(statistics.median(latencies), 1))
        _record("long_session_p95_ms", round(ordered[int(len(ordered) * 0.95) - 1], 1))
        _record("long_session_max_ms", round(max(latencies), 1))
        _record("long_session_mem_growth_kb_after_warmup", round(growth / 1024, 1))
        _record("long_session_exceptions", exceptions)

    def test_cycle_ids_unique_under_concurrency(self):
        """8 threads x 10 planned cycles on one orchestrator: no lost/duplicate cycle ids."""
        results, errors = [], []

        def worker(t):
            try:
                for j in range(10):
                    plan = _conversation_plan(f"ag_v119_cc_{t}_{j}")
                    with patch.object(self.orchestrator, "planning_integration", return_value=planning_for(plan)):
                        results.append(self.orchestrator.process_cognitive_cycle(
                            user_input=f"concurrent {t}-{j}", owner_id=OWNER, session_id=SESSION))
            except Exception as exc:  # pragma: no cover - reported via assertion
                errors.append(exc)

        with self.spy.hooks():
            threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=300)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 80)
        self.assertEqual(len({r["cycle_id"] for r in results}), 80)
        self.assertTrue(all("error" not in r for r in results), [r.get("error") for r in results if "error" in r][:3])
        self.assertEqual(self.orchestrator.cycle_count, 80)

    def test_reinitialized_orchestrator_continues(self):
        for i in range(10):
            self.assertTrue(self.run_cycle("x", plan=_conversation_plan(f"ag_v119_re_{i}"))["success"])
        self.orchestrator = V11CognitiveOrchestrator()
        result = self.run_cycle("after restart", plan=_conversation_plan("ag_v119_re_after"))
        self.assertTrue(result["success"], result.get("failure_reason"))
        self.assertEqual(self.orchestrator.cycle_count, 1)
        self.assertEqual(len(_active_rows(OWNER)), 11)


# =============================================================================
# Context fusion: repetition, determinism, diagnostics, continuity fixes
# =============================================================================
class TestContextFusionReliability(V118TestBase):

    def test_repeated_fusion_is_deterministic_and_quiet(self):
        buf = io.StringIO()
        hashes, times = set(), []
        with redirect_stdout(buf):
            for _ in range(100):
                t0 = time.perf_counter()
                hashes.add(fuse_context(request="status report", owner_id=OWNER, session_id=SESSION).context_hash)
                times.append((time.perf_counter() - t0) * 1000)
        self.assertEqual(len(hashes), 1, "Same state + request must fuse to the same hash")
        self.assertNotIn("DEBUG", buf.getvalue(), "Diagnostics must be opt-in (no private data on stdout)")
        _record("fusion_repeats", 100)
        _record("fusion_p50_ms", round(statistics.median(times), 2))

    def test_system_source_never_blocks(self):
        """Regression: SYSTEM source slept ~1 s/cycle in psutil.cpu_percent(interval=1)."""
        import psutil

        def no_blocking_sample(*args, **kwargs):
            raise AssertionError("context fusion must not take a blocking CPU sample")

        # Assert the property itself (no blocking sample), not wall-clock time, which
        # varies with machine load.
        with patch.object(psutil, "cpu_percent", side_effect=no_blocking_sample) as sample:
            fused = fuse_context(request="status", owner_id=OWNER, session_id=SESSION)
        self.assertEqual(sample.call_count, 0)
        self.assertIs(fused.context.get("system_available"), True)
        self.assertEqual(fused.context.get("system_cpu"), 0)

    def test_debug_opt_in_never_prints_values(self):
        buf = io.StringIO()
        with patch.dict(os.environ, {"DOOM_CONTEXT_FUSION_DEBUG": "1"}), redirect_stdout(buf):
            fuse_context(request="status", owner_id=OWNER, session_id=SESSION,
                         context={"monitor_note": "private-value-123"})
        out = buf.getvalue()
        self.assertIn("DEBUG", out)
        self.assertNotIn("private-value-123", out)

    def test_outcome_context_survives_failed_experiences(self):
        """Regression: an undefined name (victim_text) emptied OUTCOME after any V11 failure."""
        self.spy.overrides["conversation"] = ExecutionStatus.STEP_FAILED.value
        failed = self.run_cycle("fail", plan=_conversation_plan("ag_v119_outcome_fail"))
        self.assertFalse(failed["success"])
        exp = list_experiences(OWNER).experiences[0]
        self.assertEqual(exp.outcome, Outcome.ABANDONED)
        self.assertTrue(any("failed_step" in b for b in exp.blockers))

        fused = fuse_context(request="how are things", owner_id=OWNER, session_id=SESSION)
        self.assertIn("outcome_success_rate", fused.context)
        self.assertEqual(fused.context["outcome_success_rate"], 0.0)
        self.assertIn("outcome_frequent_error_signatures", fused.context)

    def test_goal_state_survives_active_registry_goal(self):
        """Regression: GoalSnapshot field mismatch emptied GOAL_STATE when a goal was ACTIVE."""
        self.assertTrue(self.run_cycle("done", plan=_conversation_plan("ag_v119_gs_exp"))["success"])
        with patch.dict(os.environ, {"PROACTIVE_V826_GOAL_REGISTRY_ENABLED": "true"}):
            goal_registry.use_test_goal_registry_store(True)
            try:
                snap, _ = goal_registry.build_snapshot(
                    owner_id=OWNER, title="Keep project tidy", plan_title="Tidy plan",
                    step_titles=("Sort",), step_states=(goal_registry.StepState.PENDING,))
                goal_registry.create_active_goal(OWNER, snap)
                fused = fuse_context(request="what is my goal", owner_id=OWNER, session_id=SESSION)
            finally:
                goal_registry.reset_goal_registry_for_tests()
                goal_registry.use_test_goal_registry_store(False)
        self.assertEqual(fused.context.get("active_goal_title"), "Keep project tidy")
        self.assertIn("ag_v119_gs_exp", [v for k, v in fused.context.items()
                                          if k.endswith("_source_goal_id")])


# =============================================================================
# Advanced memory cache lifecycle
# =============================================================================
class TestAdvancedMemoryCache(unittest.TestCase):

    def _system(self):
        system = AdvancedMemorySystem()
        # Deterministic sources: one record whose content names the owner + query.
        def fake_personal(query, owner_id):
            from memory.schemas import MemoryRecord
            return [MemoryRecord(content=f"{owner_id}|{query}")]
        empty = lambda *a, **k: []  # noqa: E731
        patches = [
            patch.object(system, "_normalize_personal_memory", side_effect=fake_personal),
            patch.object(system, "_normalize_user_model", side_effect=empty),
            patch.object(system, "_normalize_experience", side_effect=empty),
            patch.object(system, "_normalize_general_memory", side_effect=empty),
            patch.object(system, "_normalize_goal_memory", side_effect=empty),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return system

    def test_cache_is_bounded(self):
        system = self._system()
        for i in range(600):
            system.retrieve_advanced_memories(query=f"q{i}", owner_id="o1")
        self.assertLessEqual(system.cache_size(), system.state.cache_max_entries)
        self.assertGreater(system.state.cache_evictions, 0)
        _record("memory_cache_entries_after_600_queries", system.cache_size())

    def test_expired_entries_are_purged(self):
        system = self._system()
        system.state.cache_ttl = 0.05
        for i in range(20):
            system.retrieve_advanced_memories(query=f"q{i}", owner_id="o1")
        time.sleep(0.1)
        system.retrieve_advanced_memories(query="fresh", owner_id="o1")
        self.assertEqual(system.cache_size(), 1)

    def test_cache_hit_and_owner_invalidation(self):
        system = self._system()
        first = system.retrieve_advanced_memories(query="q", owner_id="o1")
        second = system.retrieve_advanced_memories(query="q", owner_id="o1")
        self.assertEqual([r.content for r in first], [r.content for r in second])
        self.assertEqual(system._normalize_personal_memory.call_count, 1)
        system.retrieve_advanced_memories(query="q", owner_id="o2")
        self.assertEqual(system.invalidate_owner_cache("o1"), 1)
        self.assertEqual(system.cache_size(), 1, "Other owners' entries untouched")
        system.retrieve_advanced_memories(query="q", owner_id="o1")
        self.assertEqual(system._normalize_personal_memory.call_count, 3)

    def test_crafted_keys_cannot_collide_across_owners(self):
        system = self._system()
        a = system.retrieve_advanced_memories(query="b:x", owner_id="a", session_id="s")
        b = system.retrieve_advanced_memories(query="x", owner_id="a:b", session_id="s")
        self.assertEqual(a[0].content, "a|b:x")
        self.assertEqual(b[0].content, "a:b|x", "Owner a:b must not receive owner a's cached memory")

    def test_cached_list_mutation_does_not_corrupt_cache(self):
        system = self._system()
        got = system.retrieve_advanced_memories(query="q", owner_id="o1")
        got.clear()
        self.assertEqual(len(system.retrieve_advanced_memories(query="q", owner_id="o1")), 1)

    def test_concurrent_access_is_safe(self):
        system = self._system()
        system.state.cache_max_entries = 32
        errors = []

        def worker(n):
            try:
                for i in range(200):
                    system.retrieve_advanced_memories(query=f"q{(n * 7 + i) % 90}", owner_id=f"o{n % 3}")
                    if i % 25 == 0:
                        system.invalidate_owner_cache(f"o{n % 3}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(errors, [])
        self.assertLessEqual(system.cache_size(), 32)


class TestStaleMemoryInvalidation(V118TestBase):

    def test_recorded_experience_invalidates_owner_memory_cache(self):
        system = get_advanced_memory_system()
        with patch.object(system, "invalidate_owner_cache", wraps=system.invalidate_owner_cache) as spy:
            self.assertTrue(self.run_cycle("do it", plan=_conversation_plan("ag_v119_inv"))["success"])
            spy.assert_called_with(OWNER)
            spy.reset_mock()
            self.run_cycle("Hello DOOM")
            spy.assert_not_called()  # no experience -> no invalidation


# =============================================================================
# Monitor lifecycle / proactive bounds
# =============================================================================
class TestMonitorLifecycle(V118TestBase):

    def _event(self, n):
        return MonitoringEvent(
            event_id=f"evt_v119_{n}", event_type=MonitoringEventType.SYSTEM_HEALTH,
            priority=MonitoringPriority.HIGH, owner_id=OWNER, session_id=SESSION,
            timestamp=0.0, source="continuous_monitor", description=f"Disk warning {n}")

    def test_busy_cycle_is_never_stacked(self):
        monitor = ContinuousMonitoringEnhancement(owner_id=OWNER, session_id=SESSION)
        release = threading.Event()
        started = threading.Event()

        def slow_cycle(**kwargs):
            started.set()
            release.wait(10)
            return {"success": True}

        with patch.object(proactive_behavior, "v11_cognitive_orchestrator") as orch:
            orch.process_cognitive_cycle.side_effect = slow_cycle
            monitor._handle_monitoring_event(self._event(1))
            self.assertTrue(started.wait(5))
            for n in range(2, 12):
                monitor._handle_monitoring_event(self._event(n))
            self.assertEqual(orch.process_cognitive_cycle.call_count, 1)
            self.assertEqual(monitor.state.cycles_skipped_busy, 10)
            self.assertTrue(monitor.get_status()["cycle_in_flight"])
            release.set()
            monitor.stop()
            self.assertFalse(monitor.get_status()["cycle_in_flight"])
            monitor._handle_monitoring_event(self._event(99))
            monitor.stop()
            self.assertEqual(orch.process_cognitive_cycle.call_count, 2)

    def test_repeated_start_stop_leaves_no_threads(self):
        config = ContinuousMonitoringConfig()
        config.polling_interval = 0.05
        baseline = threading.active_count()
        with patch.object(ContinuousMonitoringEnhancement, "_continuous_monitor_cycle", return_value=None):
            for _ in range(20):
                monitor = ContinuousMonitoringEnhancement(owner_id=OWNER, session_id=SESSION, config=config)
                monitor.start()
                monitor.start()  # duplicate start is a no-op
                monitor.stop()
                self.assertFalse(monitor.get_status()["is_running"])
        time.sleep(0.2)
        self.assertLessEqual(threading.active_count(), baseline)

    def test_cooldown_and_rate_limit_bound_proactive_cycles(self):
        config = ContinuousMonitoringConfig()
        config.cooldown_period = 60.0
        config.max_cycles_per_hour = 5
        monitor = ContinuousMonitoringEnhancement(owner_id=OWNER, session_id=SESSION, config=config)
        clock = [1_000_000.0]
        ran = []
        with patch.object(proactive_behavior.time, "time", side_effect=lambda: clock[0]), \
                patch.object(proactive_behavior.threading, "Thread", _SyncThread), \
                patch.object(proactive_behavior, "v11_cognitive_orchestrator") as orch:
            orch.process_cognitive_cycle.side_effect = lambda **k: ran.append(k["context"]["event_id"]) or {}
            for n in range(200):  # one candidate event every 10 simulated seconds
                if monitor._is_cooldown_complete() and monitor._is_rate_limit_ok():
                    monitor._handle_monitoring_event(self._event(n))
                clock[0] += 10.0
        # 2000 simulated seconds: cooldown allows one per 60 s, the hourly cap stops at 5.
        self.assertEqual(len(ran), 5)
        self.assertLessEqual(len(monitor.state.cycles_in_last_hour), 5)

    def test_duplicate_events_are_deduplicated(self):
        monitor = ContinuousMonitoringEnhancement(owner_id=OWNER, session_id=SESSION)
        first = monitor._should_process_event(self._event(1))
        dup = monitor._should_process_event(self._event(1))
        self.assertTrue(first)
        self.assertFalse(dup)
        for n in range(500):
            monitor._should_process_event(MonitoringEvent(
                event_id=f"e{n}", event_type=MonitoringEventType.STATE_CHANGE,
                priority=MonitoringPriority.NORMAL, owner_id=OWNER, session_id=SESSION,
                timestamp=0.0, source=f"src{n}", description=f"change {n}"))
        self.assertLessEqual(len(monitor.state.recent_event_fingerprints), 150)


# =============================================================================
# Repeated authorization
# =============================================================================
class TestRepeatedAuthorization(V118TestBase):

    def setUp(self):
        super().setUp()
        self.plan = make_plan(
            [make_step("s1", "computer", "CLICK", [("name", "SaveButton")], risk="MEDIUM", approval=True)],
            goal_id="ag_v119_auth", plan_risk="MEDIUM", approval=True, computer_session_id=COMPUTER_SESSION)
        self._live = patch(
            "orchestration.authorization.verified_computer_session_id",
            side_effect=lambda owner, cs: (cs, "OK") if (owner == OWNER and cs == COMPUTER_SESSION)
            else (None, ExecutionStatus.SESSION_UNAVAILABLE.value))
        self._live.start()

    def tearDown(self):
        self._live.stop()
        super().tearDown()

    def test_repeated_approval_requests_reuse_one_pending_entry(self):
        before = len(v8_authorization._PENDING)
        ids = set()
        for _ in range(50):
            r = self.run_cycle("Click save", plan=self.plan)
            self.assertEqual(r["stages"]["execution"]["execution_result"].status, ExecutionStatus.APPROVAL_REQUIRED)
            ids.add(r["stages"]["authorization"]["pending_id"])
        self.assertEqual(len(ids), 1, "Identical re-proposals must reuse the live pending entry")
        self.assertEqual(len(v8_authorization._PENDING) - before, 1)
        self.assertEqual(self.spy.calls, [])
        _record("pending_entries_after_50_identical_requests", len(v8_authorization._PENDING) - before)

        # After the pending entry is consumed a new request gets a fresh entry.
        identity = ExecutionIdentity(owner_id=OWNER, session_id=SESSION, computer_session_id=COMPUTER_SESSION)
        claim, code = claim_authorization(ids.pop(), identity)
        self.assertEqual(code, "OK")
        executed = self.run_cycle("Click save", plan=self.plan, authorized_plan_hash=claim.authorized_plan_hash,
                                  computer_session_id=COMPUTER_SESSION)
        self.assertTrue(executed["success"])
        again = self.run_cycle("Click save", plan=self.plan)
        self.assertNotIn(again["stages"]["authorization"]["pending_id"], (None, claim.authorization_id))
        self.assertEqual(len(v8_authorization._PENDING) - before, 2)

    def test_pending_reuse_is_identity_scoped(self):
        r1 = self.run_cycle("Click save", plan=self.plan)
        other_plan = make_plan(
            [make_step("s1", "computer", "CLICK", [("name", "SaveButton")], risk="MEDIUM", approval=True)],
            goal_id="ag_v119_auth", plan_risk="MEDIUM", approval=True, computer_session_id=COMPUTER_SESSION,
            session_id="other_session")
        r2 = self.run_cycle("Click save", plan=other_plan, session_id="other_session")
        p1 = r1["stages"]["authorization"]["pending_id"]
        p2 = r2["stages"]["authorization"]["pending_id"]
        self.assertTrue(p1 and p2)
        self.assertNotEqual(p1, p2)
        # The other session's pending entry cannot be claimed from this session.
        identity = ExecutionIdentity(owner_id=OWNER, session_id=SESSION, computer_session_id=COMPUTER_SESSION)
        _claim, code = claim_authorization(p2, identity)
        self.assertEqual(code, ExecutionStatus.AUTHORIZATION_INVALID.value)

    def test_reuse_map_is_bounded(self):
        orchestrator = self.orchestrator
        for i in range(orchestrator._MAX_PENDING_REFS + 40):
            with orchestrator._state_lock:
                orchestrator._pending_by_plan[("o", "s", "c", f"h{i}")] = f"p{i}"
                while len(orchestrator._pending_by_plan) > orchestrator._MAX_PENDING_REFS:
                    orchestrator._pending_by_plan.popitem(last=False)
        r = self.run_cycle("Click save", plan=self.plan)
        self.assertTrue(r["stages"]["authorization"]["pending_id"])
        self.assertLessEqual(len(orchestrator._pending_by_plan), orchestrator._MAX_PENDING_REFS)


# =============================================================================
# Failure / recovery and long-session isolation
# =============================================================================
class TestFailureRecoveryAndIsolation(V118TestBase):

    def test_alternating_failure_and_recovery(self):
        for i in range(40):
            fail = (i % 2 == 0)
            self.spy.overrides["conversation"] = ExecutionStatus.STEP_FAILED.value if fail else None
            r = self.run_cycle(f"attempt {i}", plan=_conversation_plan(f"ag_v119_rec_{i}"))
            self.assertEqual(r["success"], not fail)
            self.assertEqual(r["stages"]["outcome_memory_update"]["cycle_outcome"],
                             "EXECUTION_FAILED" if fail else "EXECUTION_SUCCEEDED")
            self.assertTrue(_experience_created(r))
        outcomes = [str(r.get("outcome")) for r in _active_rows(OWNER)]
        self.assertEqual(len(outcomes), 40)
        self.assertEqual(sum(o.endswith("COMPLETED") for o in outcomes), 20)
        self.assertEqual(sum(o.endswith("ABANDONED") for o in outcomes), 20)

    def test_adapter_exceptions_do_not_poison_later_cycles(self):
        def boom(step, plan):
            raise RuntimeError("transient")
        for i in range(10):
            self.spy.overrides["conversation"] = boom
            bad = self.run_cycle("crash", plan=_conversation_plan(f"ag_v119_boom_{i}"))
            self.assertEqual(bad["error_type"], "RuntimeError")
            self.spy.overrides["conversation"] = None
            good = self.run_cycle("recover", plan=_conversation_plan(f"ag_v119_ok_{i}"))
            self.assertTrue(good["success"], good.get("failure_reason"))

    def test_two_owner_long_session_isolation(self):
        other_owner, other_session = "test_owner_v119_b", "test_sess_v119_b"
        for i in range(30):
            self.run_cycle(f"a{i}", plan=_conversation_plan(f"ag_ownerA_{i}"))
            plan_b = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "b")])],
                               goal_id=f"ag_ownerB_{i}", owner_id=other_owner, session_id=other_session)
            self.run_cycle(f"b{i}", plan=plan_b, owner_id=other_owner, session_id=other_session)
        rows_a, rows_b = _active_rows(OWNER), _active_rows(other_owner)
        self.assertEqual(len(rows_a), 30)
        self.assertEqual(len(rows_b), 30)
        self.assertTrue(all(str(r.get("source_goal_id")).startswith("ag_ownerA_") for r in rows_a))
        self.assertTrue(all(str(r.get("source_goal_id")).startswith("ag_ownerB_") for r in rows_b))
        self.assertTrue(all(e.source_goal_id.startswith("ag_ownerB_")
                            for e in list_experiences(other_owner).experiences))
        fused_b = fuse_context(request="how did it go", owner_id=other_owner, session_id=other_session)
        self.assertFalse(any("ag_ownerA_" in str(v) for v in fused_b.context.values()))
        fused_a = fuse_context(request="how did it go", owner_id=OWNER, session_id=SESSION)
        self.assertFalse(any("ag_ownerB_" in str(v) for v in fused_a.context.values()))


def _print_metrics():
    if METRICS:
        print("\nV11.9 METRICS")
        for k in sorted(METRICS):
            print(f"  {k}: {METRICS[k]}")


if __name__ == "__main__":
    import atexit
    atexit.register(_print_metrics)
    unittest.main(verbosity=2)
