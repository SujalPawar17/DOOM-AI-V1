# DOOM Final Performance / Reliability Report (completion program, phase 12)

**Date:** 2026-10-05 · **Branch:** DOOM-V10 · **Base:** `007c67f` (`DOOM-SECURITY-AUDIT`) · **Push:** none

**Reproduce:** `python doom_performance_audit.py --cycles 1000` (exit code 0 only if every bound holds). Add `--attribute-growth` for allocation-site attribution; it is about 8× slower because it records 8-frame traces, so its latencies are not representative.

**Raw data:** `DOOM_PERFORMANCE_METRICS.json` (plain run) and `DOOM_PERFORMANCE_GROWTH_ATTRIBUTION.json` (traced run).

## 1. Workload (1,000 cycles through the integrated `DoomOS`)

| Share | Cycle type |
|---|---|
| 40% | informational answers (V12.1 → V8 responder, deterministic local model) |
| 20% | successful plans (V10/V11 → Cost Guard → V8 executor → verification → experience → learning) |
| 5% | failing plans (truthful failure + experience) |
| 5% | approval-required computer plans (V8 stash). Every 100 cycles a **real approval** → claim → execute |
| 5% | cost-blocked plans (`world_act`) |
| 10% | connector calls (git status, read file) via the V12.3 gateway |
| 15% | learning statements (V12.2) |

Also exercised:
- a perceptual item every 3 cycles;
- a runtime event + proactive `tick()` every 5 cycles;
- **full DoomOS re-initialization every 250 cycles** and final shutdown;
- repeated context fusion and memory retrieval inside every cycle.

Plus the dedicated V11.9 long-session suite (120 cycles, 8-thread concurrency, 600-query cache churn, monitor start/stop loops) and the V12.6 runtime lifecycle tests.

## 2. Results

| Metric | Value |
|---|---|
| Cycles / wall time / throughput | 1,000 / 45.4 s / **22.0 cycles/s** |
| CPU | 38.9 s user + 3.9 s system (≈94% of one core while busy; single-threaded workload) |
| Errors / duplicate actions | **0 / 0** |
| Approvals → executed clicks | 10 → **10** (exactly once each) |
| Threads (baseline / max / end) | 2 / 3 / **2** |
| RSS start → end | 156.8 MB → 174.4 MB (warm-up +7.6 MB by cycle 100, then ≈ +1.1 MB per 100 cycles) |
| Steady-state traced Python heap growth (cycles 300→1000) | 652.6 KB (≈ 0.9 KB/cycle), attributed in §3 |

**Latency by cycle type (ms):**

| Type | n | p50 | p95 | max |
|---|---|---|---|---|
| connector | 100 | 26.9 | 58.0 | 68.5 |
| approval | 50 | 34.1 | 75.4 | 76.7 |
| informational | 400 | 36.3 | 44.7 | 646.4 (first cycle: imports) |
| cost_blocked | 50 | 36.6 | 43.2 | 57.8 |
| learning | 150 | 39.3 | 45.9 | 57.6 |
| plan_success | 200 | 46.8 | 55.3 | 71.1 |
| plan_failure | 50 | 47.3 | 55.2 | 56.8 |

These exclude real model inference: a live local llama3 answer adds ≈3–4 s (V12.1 live test). Historical context: V11.9 removed a 1 s blocking CPU sample per cycle (1,165 → 156 ms profiled).

**Bounded structures at cycle 1,000 (all within caps):**

| Structure | Size | Cap |
|---|---|---|
| Advanced-memory cache | 5 | 256 |
| Perception (owner/session) | 32 | 32 |
| Learned items | 16 | 64 |
| Runtime queue | 6 | 128 |
| Runtime outbox | 21 | 64 |
| Assistant inbox | 9 | 64 |
| Gateway pending approvals | 0 | 256 |
| Orchestrator pending refs | 3 | 256 |
| Active experiences | 64 | 64 (V8.28) |

## 3. Growth attribution (traced run, cycles 200→1000, innermost frames)

| Allocation site | Growth | Classification |
|---|---|---|
| `orchestration/goal/plan_validator.py:278` (`hash_goal_plan` creates a new class per call) | 144 KB | **bounded**: one-time interpreter allocation. Measured flat: 144.8 KB after 5k calls, 146.8 KB after 40k. Classes are freed (0 alive). A V8 inefficiency, not a leak. |
| `orchestration/experience/store.py:301/627`, `policy.py` | ≈180 KB | **V8.28 retention:** superseded experience rows are kept (360 rows total vs 64 active after 1,000 cycles). In production these rows are in PostgreSQL, i.e. disk growth of one small row per executed plan, not RAM. Pre-existing. |
| `orchestration/audit/recorder.py:106/135` | 35 KB | V8 audit log, bounded by `MAX_AUDIT_EVENTS`. |
| V8 executor ledger, V12.6 outbox/dedup | small | bounded (256 / 64 / 1024). |

No V11/V12 structure grows without bound. Every unbounded-looking slope traces to frozen V8 retention behaviour, documented above and in V11.9.

## 4. Reliability checks (script assertions, all PASS)

- `no_cycle_errors`
- `no_duplicate_actions`
- `approved_actions_ran_exactly`
- `memory_cache_bounded`, `perception_bounded`, `learning_bounded`
- `runtime_queue_bounded`, `outbox_bounded`, `inbox_bounded`
- `approval_stores_bounded`, `experience_active_bounded`
- `no_thread_growth`
- `steady_state_growth_under_8mb`

## 5. Observations

- **V8 executor ledger** treats an identical, already-succeeded plan as done. Approving the *same* plan twice executes it once (duplicate-execution protection). The audit uses distinct plans per approval batch.
- **First-cycle cost:** ≈0.6 s (imports and Context Fusion warm-up).
- **PostgreSQL connection setup and schema checks** in the V5/V8 DB layer remain the largest per-cycle cost when DB-backed stores are used (≈0.1 s; V11.9). The audit uses in-memory test stores for determinism, so DB-bound latency in production is higher than the table above.

## 6. Decision

**PERFORMANCE AUDIT PASSED.** No uncontrolled resource growth in the new architecture, no thread, queue or cache leaks, no duplicate actions, and recovery across failures and re-initialization was verified.
