#!/usr/bin/env python
"""DOOM final performance / reliability audit (completion program, phase 12).

Drives N cognitive cycles (default 1000) through the integrated DoomOS with a mixed,
realistic workload and checks for uncontrolled growth. Deterministic and HARD $0:
the local model, V8 executor adapters and stores run in their test modes; no audio,
no network, no real desktop.

Usage:  python doom_performance_audit.py [--cycles 1000] [--out DOOM_PERFORMANCE_METRICS.json]
Exit code 0 only if every bound holds.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import sys
import threading
import time
import tracemalloc
from unittest.mock import patch

os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"
ROOT = os.path.abspath(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=1000)
    parser.add_argument("--out", default="DOOM_PERFORMANCE_METRICS.json")
    parser.add_argument("--attribute-growth", action="store_true",
                        help="report the top allocation sites that grew after warm-up")
    args = parser.parse_args()

    import psutil
    from test_v11_8_end_to_end_integration import AdapterSpy, make_plan, make_step, planning_for, COMPUTER_SESSION
    from test_v12_1_response_intelligence import FakeLocalModel
    from core.v10.planning_integration import PlanningResult
    from core.v11.advanced_memory import get_advanced_memory_system
    from core.v12.adaptive_learning import AdaptiveLearning, LearningStore
    from core.v12.cognitive_orchestrator import V12CognitiveOrchestrator
    from core.v12.doom_os import DoomOS
    from core.v12.integrations import ConnectorSandbox
    from core.v12.runtime import EventPriority, RuntimeEvent
    from orchestration import authorization as v8_auth
    from orchestration.conversation.respond import use_respond_provider_for_tests
    from orchestration.conversation.personal_memory import use_test_personal_memory_store, reset_personal_memory_for_tests
    from orchestration.experience import store as exp_store
    from orchestration.experience.store import use_test_experience_store, reset_experience_for_tests
    from orchestration.executor import reset_execution_ledger_for_tests
    from orchestration.user_model.store import use_test_user_model_store, reset_user_model_for_tests

    use_test_experience_store(True); reset_experience_for_tests()
    use_test_personal_memory_store(True); reset_personal_memory_for_tests()
    use_test_user_model_store(True); reset_user_model_for_tests()
    reset_execution_ledger_for_tests(); v8_auth.reset_authorization_store_for_tests()
    use_respond_provider_for_tests(FakeLocalModel(["Here is a concise answer."] * (args.cycles * 2)))

    owner, session = "perf_owner", "perf_sess"
    proc = psutil.Process()
    spy = AdapterSpy()
    box = ConnectorSandbox(owner).__enter__()

    def new_os():
        return DoomOS(owner_id=owner, session_id=session, registry=box.registry,
                      orchestrator=V12CognitiveOrchestrator(learning=AdaptiveLearning(store=LearningStore())))

    doom = new_os()
    approval_plan = make_plan([make_step("s1", "computer", "CLICK", [("name", "Save")], risk="MEDIUM", approval=True)],
                              goal_id="ag_perf_auth", plan_risk="MEDIUM", approval=True,
                              computer_session_id=COMPUTER_SESSION, owner_id=owner, session_id=session)
    cost_plan = make_plan([make_step("s1", "world_act", "RUN", [("title", "x")], risk="HIGH")],
                          goal_id="ag_perf_cost", plan_risk="HIGH",
                          owner_id=owner, session_id=session)
    no_plan = PlanningResult(False, False, None, None)
    current = {"planning": no_plan}

    def planner(**_kw):
        return current["planning"]

    lat = {}
    errors, expected_adapter_runs, approvals_done = 0, 0, 0
    windows, threads_seen = [], []
    baseline_threads = threading.active_count()
    gc.collect()
    tracemalloc.start(8 if args.attribute_growth else 1)  # deep frames only when attributing
    warm_snapshot = None
    cpu0, wall0 = proc.cpu_times(), time.perf_counter()
    rss_start = proc.memory_info().rss
    snapshot_prev = None

    with spy.hooks(), patch("orchestration.authorization.verified_computer_session_id",
                            side_effect=lambda o, cs: (cs, "OK")):
        for i in range(args.cycles):
            if i and i % 250 == 0:  # periodic re-initialization of the whole facade
                doom.shutdown()
                doom = new_os()
            patcher = patch.object(doom.orchestrator, "planning_integration", side_effect=planner)
            patcher.start()
            kind, r = "", None
            t0 = time.perf_counter()
            try:
                m = i % 20
                if m < 8:
                    kind, current["planning"] = "informational", no_plan
                    r = doom.handle_text(f"Explain topic number {i % 37}")
                elif m < 12:
                    kind = "plan_success"
                    current["planning"] = planning_for(make_plan(
                        [make_step("s1", "conversation", "RESPOND", [("text", f"task {i}")])], goal_id=f"ag_perf_{i}", owner_id=owner, session_id=session))
                    spy.overrides.pop("conversation", None)
                    r = doom.handle_text(f"Do task {i}")
                    expected_adapter_runs += 1
                    if not r.details.get("success"):
                        errors += 1
                elif m == 12:
                    kind = "plan_failure"
                    current["planning"] = planning_for(make_plan(
                        [make_step("s1", "conversation", "RESPOND", [("text", f"fail {i}")])], goal_id=f"ag_perf_f{i}", owner_id=owner, session_id=session))
                    spy.overrides["conversation"] = "STEP_FAILED"
                    r = doom.handle_text(f"Fail task {i}")
                    spy.overrides.pop("conversation", None)
                    expected_adapter_runs += 1
                elif m == 13:
                    # one distinct plan per 100-cycle batch: V8's execution ledger treats an
                    # identical, already-succeeded plan as done (duplicate-execution guard)
                    batch_plan = make_plan(
                        [make_step("s1", "computer", "CLICK", [("name", "Save")], risk="MEDIUM", approval=True)],
                        goal_id=f"ag_perf_auth_{i // 100}", plan_risk="MEDIUM", approval=True,
                        computer_session_id=COMPUTER_SESSION, owner_id=owner, session_id=session)
                    kind, current["planning"] = "approval", planning_for(batch_plan)
                    r = doom.handle_text("Click save")
                    if i % 100 == 13 and r.details.get("pending_id"):
                        done = doom.approve(r.details["pending_id"], computer_session_id=COMPUTER_SESSION)
                        approvals_done += 1 if done.details.get("success") else 0
                elif m == 14:
                    kind, current["planning"] = "cost_blocked", planning_for(cost_plan)
                    r = doom.handle_text("Run the remote job")
                elif m in (15, 16):
                    kind, current["planning"] = "connector", no_plan
                    r = doom.handle_text("git status" if m == 15 else "read file notes.txt")
                else:
                    kind, current["planning"] = "learning", no_plan
                    r = doom.handle_text(f"I prefer option{i % 7} over option{(i + 3) % 7}")
            except Exception as exc:  # contained per cycle; counted
                errors += 1
                kind = kind or "error"
            finally:
                patcher.stop()
            lat.setdefault(kind, []).append((time.perf_counter() - t0) * 1000)
            if r is not None and r.route == "cognitive" and not r.text:
                errors += 1
            if i % 3 == 0:
                doom.orchestrator.perceive(doom.orchestrator.normalizer.text(owner, session, f"note {i % 50}"))
            if i % 5 == 0:
                doom.runtime.submit(RuntimeEvent.make(owner, session, "system", f"evt{i % 9}", f"event {i}",
                                                      EventPriority.NORMAL, timestamp=time.time()))
                doom.tick()
            if (i + 1) % 100 == 0:
                gc.collect()
                snap = tracemalloc.take_snapshot()
                growth = 0 if snapshot_prev is None else sum(s.size_diff for s in snap.compare_to(snapshot_prev, "filename"))
                snapshot_prev = snap
                if i + 1 == 200:
                    warm_snapshot = snap
                windows.append({
                    "cycle": i + 1,
                    "traced_kb": round(tracemalloc.get_traced_memory()[0] / 1024, 1),
                    "growth_kb_vs_prev_window": round(growth / 1024, 1),
                    "rss_mb": round(proc.memory_info().rss / 1048576, 1),
                    "threads": threading.active_count(),
                    "memory_cache": get_advanced_memory_system().cache_size(),
                    "perception_items": len(doom.orchestrator.perception.items(owner, session)),
                    "learning_items": len(doom.orchestrator.learning.store.items(owner)),
                    "runtime_queue": doom.runtime.queue_size(),
                    "runtime_outbox": len(doom.runtime.outbox(owner)),
                    "assistant_inbox": len(doom.assistant.inbox(owner)),
                    "gateway_pending": doom.gateway.approvals.size(),
                    "facade_pending_routes": len(doom._pending),
                    "orchestrator_pending_refs": len(doom.orchestrator._pending_by_plan),
                    "v8_pending_store": len(v8_auth._PENDING),
                    "experience_rows_active": sum(1 for r_ in exp_store._TEST_ROWS.get(owner, [])
                                                  if r_.get("status") == "ACTIVE_RECORD"),
                    "experience_rows_total": len(exp_store._TEST_ROWS.get(owner, [])),
                })
                threads_seen.append(threading.active_count())
                print(f"[audit] {i + 1} cycles | rss {windows[-1]['rss_mb']} MB | "
                      f"traced {windows[-1]['traced_kb']} KB | threads {windows[-1]['threads']}", flush=True)

    growth_sites = []
    if args.attribute_growth and warm_snapshot is not None:
        exclude = [tracemalloc.Filter(False, tracemalloc.__file__), tracemalloc.Filter(False, __file__)]
        final_snap = tracemalloc.take_snapshot().filter_traces(exclude)
        for stat in final_snap.compare_to(warm_snapshot.filter_traces(exclude), "traceback")[:8]:
            frames = [f"{os.path.relpath(fr.filename, ROOT) if fr.filename.startswith(ROOT) else os.path.basename(fr.filename)}:{fr.lineno}"
                      for fr in list(stat.traceback)[-4:]]  # innermost frames
            growth_sites.append({"kb": round(stat.size_diff / 1024, 1), "count": stat.count_diff, "where": frames})
    wall = time.perf_counter() - wall0
    cpu1 = proc.cpu_times()
    doom.shutdown()
    box.__exit__(None, None, None)
    time.sleep(0.3)
    end_threads = threading.active_count()
    tracemalloc.stop()

    def pct(values, q):
        ordered = sorted(values)
        return round(ordered[min(len(ordered) - 1, int(len(ordered) * q))], 1)

    latency = {k: {"n": len(v), "p50_ms": round(statistics.median(v), 1), "p95_ms": pct(v, 0.95),
                   "max_ms": round(max(v), 1)} for k, v in sorted(lat.items())}
    steady = windows[2:] if len(windows) > 3 else windows
    steady_growth_kb = sum(w["growth_kb_vs_prev_window"] for w in steady[1:]) if len(steady) > 1 else 0.0
    duplicate_actions = len([c for c in spy.calls if c[0] == "conversation"]) - expected_adapter_runs
    metrics = {
        "cycles": args.cycles, "wall_s": round(wall, 1), "cycles_per_s": round(args.cycles / wall, 2),
        "cpu_user_s": round(cpu1.user - cpu0.user, 1), "cpu_system_s": round(cpu1.system - cpu0.system, 1),
        "cpu_utilization_pct": round(100 * ((cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)) / wall, 1),
        "rss_start_mb": round(rss_start / 1048576, 1), "rss_end_mb": windows[-1]["rss_mb"] if windows else None,
        "latency": latency, "errors": errors, "approvals_executed": approvals_done,
        "computer_actions_executed": len([c for c in spy.calls if c[0] == "computer"]),
        "duplicate_actions": duplicate_actions,
        "steady_state_traced_growth_kb": round(steady_growth_kb, 1),
        "threads": {"baseline": baseline_threads, "max_seen": max(threads_seen or [0]), "end": end_threads},
        "windows": windows,
        "growth_sites": growth_sites,
    }
    with open(os.path.join(ROOT, args.out), "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)

    last = windows[-1] if windows else {}
    checks = {
        "no_cycle_errors": errors == 0,
        "no_duplicate_actions": duplicate_actions == 0,
        "approved_actions_ran_exactly": metrics["computer_actions_executed"] == approvals_done,
        "memory_cache_bounded": last.get("memory_cache", 0) <= 256,
        "perception_bounded": last.get("perception_items", 0) <= 32,
        "learning_bounded": last.get("learning_items", 0) <= 64,
        "runtime_queue_bounded": last.get("runtime_queue", 0) <= 128,
        "outbox_bounded": last.get("runtime_outbox", 0) <= 64,
        "inbox_bounded": last.get("assistant_inbox", 0) <= 64,
        "approval_stores_bounded": last.get("gateway_pending", 0) <= 256 and last.get("orchestrator_pending_refs", 0) <= 256,
        "experience_active_bounded": last.get("experience_rows_active", 0) <= 64,
        "no_thread_growth": metrics["threads"]["max_seen"] <= baseline_threads + 3 and end_threads <= baseline_threads + 1,
        "steady_state_growth_under_8mb": steady_growth_kb < 8 * 1024,
    }
    metrics["checks"] = checks
    with open(os.path.join(ROOT, args.out), "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)
    print(json.dumps({k: v for k, v in metrics.items() if k != "windows"}, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
