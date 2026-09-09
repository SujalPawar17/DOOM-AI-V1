# DOOM V6.1 — Proactive Foundation Implementation Report

**Status:** implementation complete — **not committed, not tagged, not pushed**  
**Date:** 2026-09-09  
**Baseline HEAD:** `e933c212278f5bd7e05d9375c9f73a773960dcff`

**Verdict:** **A. IMPLEMENTATION COMPLETE — READY FOR FORENSIC AUDIT**

---

## 1. Executive summary

V6.1 adds an **observational INFORM-only** layer behind `PROACTIVE_ENABLED` (**default false**). Flag-off: no ingest, no worker, no HUD insights. Flag-on: leased PostgreSQL outbox, derived WorldSnapshot, deterministic significance, attention budget, template INFORM cards on HUD/WS (`type: proactive_event`). **Zero tools, zero LLM, zero ACT/PREPARE/ASK, zero TTS, zero `command_logs` ingestion, zero `DOOMAutomation`.** Cognition is isolated (`process_request` does not import `proactive`).

---

## 2. Baseline

| Item | Value |
|------|--------|
| Branch | `DOOM-V5.2` |
| HEAD at start | `e933c21` (unchanged until future release) |
| Tag | `v5.3.7.4-observability` |
| Architecture contract | `DOOM_V6_PROACTIVE_INTELLIGENCE_ARCHITECTURE_AUDIT.md` |

Untracked provider-routing markdown **not cleaned**.

---

## 3. Architecture implemented

```
internal poll / ingest API
  → normalize + DATA_ONLY fence
  → idempotent PG outbox (proactive_signals)
  → ProactiveWorker (lease / retry / DLQ)
  → WorldSnapshot (TTL, derived)
  → Significance (IGNORE default)
  → Attention (budget, cooldown, quiet hours, busy gate)
  → INFORM only → HUD + WS
  → OTP category `proactive` (metadata)
```

---

## 4. Files changed

**Modified (minimal V5 hooks):**

- `doom.py` — startup starts worker **only if flag on** (not inside `process_request`)
- `dashboard/server.py` — same; `GET /api/proactive/status`, `GET /api/proactive/insights`
- `database/postgres_db.py` — V6.1 tables
- `observability/schemas.py` — category `proactive` + a few allowed attr keys (same bus, not a second telemetry system)

**New:**

- `proactive/config.py`, `schemas.py`, `fence.py`, `otp.py`, `ingest.py`, `store.py`, `snapshot.py`, `significance.py`, `attention.py`, `templates.py`, `delivery.py`, `poller.py`, `worker.py`, `__init__.py`
- `test_v61_proactive_foundation.py`
- this report

---

## 5. Database changes

Tables (bounded, not memory):

| Table | Role |
|-------|------|
| `proactive_signals` | outbox + lease + unique `idempotency_key` |
| `proactive_insights` | TTL insights; UNIQUE `dedupe_key`; intervention CHECK IGNORE/INFORM |
| `proactive_deliveries` | UNIQUE (insight_id, channel) |
| `proactive_attention` | daily count + cooldown map |

Retention: processed signals deleted after `PROACTIVE_SIGNAL_RETENTION_HOURS` (48); deliveries 72h; insights expire and `dedupe_key` mangled.

**Consistency boundary:** V6.1 **polls** V5 tables / circuit / host telemetry; it does **not** co-commit with lifecycle/task mutations. Documented: at-least-once signals via idempotency keys, not exactly-once with V5 writes.

---

## 6. Signal architecture

Types: TASK_STATUS, MEMORY_LIFECYCLE, PROJECT_CHANGE, EXPERIENCE_CREATED, STRATEGY_FAILURE, REQUEST_COMPLETED, HOST_TELEMETRY, PROVIDER_CIRCUIT, INACTIVITY.

SENSITIVE dropped at normalize. Forbidden payload keys stripped; injection/secret strings **drop entire signal**. Max payload `PROACTIVE_PAYLOAD_MAX_BYTES` (2048).

---

## 7. Worker architecture

`process_once` / daemon `start_proactive_worker`. FOR UPDATE SKIP LOCKED, lease, backoff retry, DEAD after `PROACTIVE_MAX_ATTEMPTS`. Crash: exceptions caught; `process_request` still works. Flag off: `process_once` returns 0.

---

## 8. WorldSnapshot

Read-only queries: projects (NORMAL only), task_checkpoints, memory_lifecycle_events, experiences, latest `system_telemetry` metrics, circuit OPEN count. **No command_logs.** TTL `PROACTIVE_SNAPSHOT_TTL_SECONDS`. Stale snapshot → IGNORE.

---

## 9. InsightStore

Template id + `safe_params` (numbers/ids). No memory bodies. Default intervention IGNORE.

---

## 10. Significance

Hard gates: SENSITIVE/PRIVATE → IGNORE; stale snapshot → IGNORE. INFORM candidates: e.g. disk ≥90, task FAILED, circuit OPEN, inactivity ≥7d. MEMORY_LIFECYCLE / REQUEST_COMPLETED stay low (new ≠ important).

---

## 11. Attention

Daily INFORM cap default **8**; cooldown **3600s**; optional `PROACTIVE_QUIET_HOURS`; busy when state PROCESSING/PLANNING/THINKING/EXECUTING/VERIFYING.

---

## 12. INFORM delivery

HUD in-memory ring + PG delivery row + WS `proactive_event` (not TTS). Duplicate (insight, hud) → skip. `TTS_PROACTIVE_ALLOWED = False`.

---

## 13. Privacy

SENSITIVE never queued. PRIVATE never INFORM. Owner id default `sujal` (`DOOM_OWNER_ID`). Single-user personal OS assumption.

---

## 14. Security

Payload fence; no shell/tools/LLM; no ACT; injection strings dropped. OTP forbids prompt attributes.

---

## 15. Observability

Events: `proactive.signal.ingested`, `.deduplicated`, `.insight.created`, `.decision`, `.delivery`, `.outcome`. IDs/enums/score_bucket/privacy_class only. Fail-open.

---

## 16. Failure isolation

Worker/poller/OTP/PG wrapped. Flag-off no worker. Snapshot/query failures → partial/empty, not crash.

---

## 17. Configuration (env)

| Variable | Default |
|----------|---------|
| `PROACTIVE_ENABLED` | **false** |
| `PROACTIVE_POLL_INTERVAL_SEC` | 2 |
| `PROACTIVE_LEASE_SECONDS` | 45 |
| `PROACTIVE_MAX_ATTEMPTS` | 5 |
| `PROACTIVE_SIGNAL_RETENTION_HOURS` | 48 |
| `PROACTIVE_INSIGHT_TTL_SECONDS` | 86400 |
| `PROACTIVE_DELIVERY_RETENTION_HOURS` | 72 |
| `PROACTIVE_DAILY_INFORM_BUDGET` | 8 |
| `PROACTIVE_COOLDOWN_SECONDS` | 3600 |
| `PROACTIVE_PAYLOAD_MAX_BYTES` | 2048 |
| `PROACTIVE_TIME_BUCKET_SECONDS` | 300 |
| `PROACTIVE_SNAPSHOT_TTL_SECONDS` | 60 |
| `PROACTIVE_SIGNIFICANCE_FLOOR` | 0.65 |
| `PROACTIVE_CONFIDENCE_FLOOR` | 0.50 |
| `PROACTIVE_QUIET_HOURS` | empty (off) |
| `DOOM_OWNER_ID` | sujal |

No LLM key required.

---

## 18. Test inventory

`test_v61_proactive_foundation.py`: **95** tests (flag, signal, normalize, outbox, worker, snapshot, significance, attention, INFORM, memory safety, privacy, OTP, integration, perf, recovery, adversarial). Minimum requested 82.

---

## 19. Test results

```
pytest test_v61_proactive_foundation.py -q
95 passed
```

PG was available (enqueue/claim/DLQ tests ran). None skipped in this run.

---

## 20. Performance (from tests, n as coded)

| Path | n | Bound (assert) | Result |
|------|---|----------------|--------|
| normalize | 50 | < 0.5s | pass |
| idempotency hash | 200 | < 0.2s | pass |
| process_once flag-off | 1 | < 0.5s | pass |
| snapshot force | 1 | < 3s | pass |

No p50/p95 histograms beyond these bounds (lightweight, zero LLM).

---

## 21. V5 regression

```
test_provider_routing_scenarios + test_dashboard_canonical_router: 26 passed
test_v5374_observability: 52 passed
test_v51_memory + test_v5373_governance_transfer: included in 158 passed combined run
```

OTP category extension is additive. Provider routing / TaskEngine / Governance modules not redesigned.

Known env failures (FastEmbed/pgvector/v32 live) **not re-fixed**.

---

## 22. Known limitations

- Signals from **poll**, not same transaction as V5 mutations.
- INACTIVITY uses `state_machine._last_changed` (coarse).
- Quiet hours require env; default off.
- HUD list prefers PG then in-process cards.
- Single-user owner `sujal`.
- Some concurrent tests are presence/smoke, not full linearizability proofs.

---

## 23. Scope exclusions (intentionally not built)

V6.2+ connectors, deadlines, prediction, LLM, PREPARE/ASK/ACT, TTS interrupt, TaskEngine execution, CognitiveEngine loop, legacy schedulers, command_logs mining.

---

## 24. Acceptance checklist

| Criterion | Met |
|-----------|-----|
| Flag default false | Yes |
| Flag-off no worker/delivery | Yes |
| Ingest/normalize/idempotent/bounded queue | Yes |
| Leases, retry, DLQ, recovery | Yes |
| Derived TTL snapshot | Yes |
| Deterministic significance; default IGNORE; max INFORM | Yes |
| No ACT/PREPARE/ASK/tools/LLM/TTS | Yes |
| Attention budget/cooldown/busy/quiet | Yes |
| SENSITIVE/PRIVATE; no command_logs | Yes |
| No ACTIVE memory writes | Yes |
| DATA_ONLY fence | Yes |
| OTP metadata fail-open | Yes |
| Isolation from cognition | Yes |
| Routing/task/gov unchanged | Yes |
| No DOOMAutomation | Yes |
| 95 tests passed | Yes |

---

## 25. Release recommendation

Ready for **V6.1 forensic audit**. Do **not** commit until that audit passes and you explicitly approve release.

Default remains **off** in production until operators set `PROACTIVE_ENABLED=true`.
