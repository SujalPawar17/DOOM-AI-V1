# DOOM V6.1 — Forensic Audit

**Mode:** hostile production audit. **No implementation, test, schema, or git mutations.**  
**Date:** 2026-09-09  
**Auditor artifact:** this file only (`DOOM_V6.1_FORENSIC_AUDIT.md`, untracked).

**Verdict:** **C. BLOCKED — REMEDIATION REQUIRED**

This is **not** an architecture-scope rejection. The V6.1 design remains INFORM-only, flag-default-off, and isolated from `DOOMCore.process_request`. Independent runtime checks found **no LLM, no tools, no TTS, no ACTIVE memory writes, and no `command_logs` world-state reads**.

Release is blocked because (1) **serious safety claims in the 95-test suite are backed by vacuous or duplicated assertions**, which this audit treats as an automatic blocker, and (2) **independent inspection found real defects** (HUD serialization dead code, crash-after-insight delivery loss, `retry_or_dead` fail-dead race, no queue-depth bound, incomplete injection fencing).

**Rule used:** “95 tests passed” ≠ “V6.1 is proven safe.”

---

## 1. Executive verdict

| Question | Answer |
|----------|--------|
| Is V6.1 INFORM-only in source and runtime? | **Yes** (no PREPARE/ASK/ACT/tools/LLM/TTS invocation found) |
| Does flag-off ingest/worker/process_once stay inert? | **Yes** (runtime: signal count unchanged) |
| Is the 95/95 suite sufficient evidence? | **No** |
| Can V6.1 be tagged/released as-is? | **No** |

---

## 2. Baseline verification

| Check | Result |
|-------|--------|
| Branch | `DOOM-V5.2` |
| `HEAD` | `e933c212278f5bd7e05d9375c9f73a773960dcff` |
| `git tag --points-at HEAD` | `v5.3.7.4-observability` |
| `v5.3.7.4-observability` commit | `e933c21` |
| `v5.3.7.3` | `73be7ff` |
| `v5.3.7.3-provider-routing` | `086582e` |
| V6.1 committed / tagged / pushed | **No** |

Working tree (expected, uncommitted V6.1):

- Modified: `doom.py`, `dashboard/server.py`, `database/postgres_db.py`, `observability/schemas.py`
- Untracked: `proactive/`, `test_v61_proactive_foundation.py`, `DOOM_V6.1_IMPLEMENTATION_REPORT.md`, prior provider-routing markdown (untouched)

`git diff e933c21..HEAD` is empty (V6.1 is working-tree only). No unexpected **tracked** modification of frozen history.

---

## 3. Scope verification

Implemented path: ingest → normalize/fence → idempotent PG outbox → leased worker → derived snapshot → significance → attention → INFORM HUD/WS → OTP metadata.

**Not implemented (correctly out of V6.1):** calendar/email/GitHub/Slack, PREPARE/ASK/ACT, LLM prediction, TTS interrupt, TaskEngine execution, CognitiveEngine loop, `DOOMAutomation`.

**Partial vs architecture list:** poller only emits `HOST_TELEMETRY`, `PROVIDER_CIRCUIT`, `INACTIVITY`. Types `TASK_STATUS`, `MEMORY_LIFECYCLE`, `PROJECT_CHANGE`, `EXPERIENCE_CREATED`, `STRATEGY_FAILURE`, `REQUEST_COMPLETED` are ingestable via `ingest_signal` but have **no production adapters**. That is incomplete foundation coverage, not V6.2 expansion.

---

## 4. Architecture verification

- Separate `proactive/` package; not a second memory/router/governance/task/telemetry bus.
- OTP reuses V5.3.7.4 `emit` with category `proactive`.
- WorldSnapshot is a derived cache, not a truth store.
- Dual process entry: `doom.py` startup **and** dashboard lifespan both may start a worker if the flag is on (two processes ⇒ two workers). SKIP LOCKED is the intended coordination.

---

## 5. Feature-flag verification

Authoritative flag: `proactive.config.is_proactive_enabled()` reads `PROACTIVE_ENABLED` **at call time**. Default when unset: **false**. `TTS_PROACTIVE_ALLOWED = False` (constant).

### FLAG OFF (runtime, PostgreSQL connected)

| Probe | Result |
|-------|--------|
| `ingest_signal(...)` | `""` |
| `start_proactive_worker()` | `False` |
| `worker_alive()` | `False` |
| `process_once()` | `0` |
| `proactive_signals` count | **32 → 32** (no write) |
| `GET /api/proactive/status` | `enabled: false`, `worker_alive: false`, `max_intervention: NONE` |
| `GET /api/proactive/insights` | `count: 0` |

### FLAG ON → OFF without `stop_proactive_worker`

| Probe | Result |
|-------|--------|
| Start with env true | thread **alive** |
| Flip env to false | `worker_alive()` still **True** |
| `process_once()` | `0` |
| Signal count after 2.2s wait | **unchanged** |
| After `stop_proactive_worker()` | alive **False** |

**Finding F-FLAG-01:** disabling the env var does **not** stop the daemon. The loop no-ops (`is_proactive_enabled()`), so this is **not** flag-off *work*, but it **violates** “no background thread remains alive after disabling” unless something calls `stop_proactive_worker()`. There is no env watcher.

**Finding F-FLAG-02 (INFO):** PG `CREATE TABLE IF NOT EXISTS proactive_*` runs on every schema init regardless of flag. Endpoints exist flag-off (read-only empty). `doom.py` / dashboard **import** `proactive.config` on startup even when off.

---

## 6. Production-path verification

`core/orchestrator.py` has **zero** `proactive` imports. `process_request` → `CognitiveEngine.process` only.

Runtime:

- Worker `_process_item` patched to raise → `process_once` did not crash the process.
- `doom_core.process_request("What is 2 + 2?")` returned `2 + 2 = 4` (**31ms** cognition) **after** that isolation probe.
- Instrumented `model_router.generate` / `tool_registry.execute`: **LLM_CALL_COUNT = 0**, **TOOL_CALL_COUNT = 0** during that request **and** during a flag-on `process_once` (n=1, **38.7ms**).
- `memory_records` count **4280 → 4280**; `SUM(importance)` **2957.9033 → 2957.9033**.

Voice clap path: `doom.py` `on_clap_awakening` → `handle_command` → `doom_core.process_request`. Dashboard clap → same core. **`POST /api/command/clap-wake` does not exist** (405). Production HTTP path is `POST /api/command` (`goal` required). V6.1 does not sit on that path.

---

## 7. Signal security

Canonical model: typed (`SIGNAL_TYPES`), sourced (`SOURCES`), privacy CHECK-aligned, idempotency hash, fence + size.

**Runtime adversarial normalize:**

| Payload | Result |
|---------|--------|
| keys `prompt`/`content`/`api_key`/`password`/`token`/`command_logs`/`user_command` | **stripped**; remaining `status` kept |
| `gsk_live_...` / `sk-...` in string | **drop entire signal** |
| `ignore previous` / `execute tool` / `bypass governance` | **drop** |
| oversized dict | **drop** |
| `occurred_at=-1` / huge future | **drop** |
| `privacy_class=SENSITIVE` | **drop** (never queued) |
| `note: password=hunter2` | **stored** (no marker match) |
| `note: send message to slack` | **stored** |
| `approve action and delete memory` | **not dropped** by fence |

PRIVATE signals **are stored** in `proactive_signals` (payload was `{"status":"FAILED"}` only). They are gated to IGNORE at significance and excluded from HUD SQL (`privacy_class = 'NORMAL'`).

`SOURCES` includes `"test"` — a production ingest loophole if anything other than tests calls it with the flag on.

---

## 8. Dedupe / idempotency

**DB:** `proactive_signals.idempotency_key VARCHAR(64) NOT NULL UNIQUE` + `ON CONFLICT DO NOTHING`.

**Runtime:** 20 concurrent `ingest_signal` same entity/type/bucket → **1 unique returned id**, **1 PG row**.

Time-bucket boundaries: different buckets ⇒ different keys (by design; **not tested** for off-by-one at the 300s edge).

Insight: `dedupe_key UNIQUE`. Delivery: `UNIQUE (insight_id, channel)`.

**Runtime concurrent `deliver_inform`:** 8 threads, **1** success, **1** `proactive_deliveries` row.

**Crash-after-insight / before-delivery:** `insert_insight` conflict returns `""`; worker treats that as “already exists” and **acks PROCESSED without delivering**. Independent source conclusion: **INFORM can be lost**. Not covered by tests.

---

## 9. Outbox / worker

Patterns present: `FOR UPDATE SKIP LOCKED`, `worker_id`, `lease_until`, `attempt_count`, backoff retry, `DEAD`, `recover_expired_leases`.

**Absent:** heartbeat, `lease_acquired_at`, max queue depth, claim of `CLAIMED` rows without recover (recover is required for expired CLAIMED).

**Runtime concurrent claim:** two workers, **overlap empty** (`len 20` and `5` from mixed pending backlog). **I13 holds in this run.**

**Lease expiry (lease_seconds=0):** row `CLAIMED` with `dt ≈ -0.001s`; `recover_expired_leases()` returned **1**; second worker reclaimed. **Implementation works**; **test_18 does not prove it.**

**DEAD:** not reclaimed. Good.

**Failure injection (source + partial runtime):**

| Case | Result class |
|------|----------------|
| Flag off | SAFE (no-op) |
| Crash before process | RECOVERABLE (lease + recover) |
| Crash during process | RECOVERABLE until attempts exhaust |
| Crash after insight, before delivery | **LOST INFORM** (conflict skip) |
| Crash after delivery persist, before ack | SAFE (no duplicate delivery); signal re-ack |
| Expired lease | RECOVERABLE (runtime verified) |
| Concurrent workers | SAFE claim (runtime); insight unique |
| DB unavailable | SAFE empty enqueue/claim (`""` / `[]`) |
| Malformed signal | DROP at normalize |
| `retry_or_dead` if SELECT misses owner | **UNSAFE**: `attempts = MAX_ATTEMPTS` then `UPDATE ... DEAD WHERE signal_id` **without** worker_id — can DEAD another worker’s row |

`process_once` outer `except: pass` swallows cycle errors (no OTP in that branch).

Attempt count increments **on every claim**, so five crash/reclaim cycles can DEAD a never-successfully-processed signal (bounded, not infinite).

---

## 10. Database integrity

Tables: `proactive_signals`, `proactive_insights`, `proactive_deliveries`, `proactive_attention`.

Enforced in DDL: PKs, unique idempotency, unique insight `dedupe_key`, unique delivery `(insight_id, channel)`, privacy CHECKs, intervention CHECK `IGNORE|INFORM`, signal/insight status CHECKs, delivery FK to insights.

**Not enforced:** payload size (Python only), queue depth, insight TTL delete (OPEN rows expired in place; DELIVERED insights not deleted), PENDING/CLAIMED signal retention (cleanup only `PROCESSED/DEAD/DROPPED/DEDUPED`).

Direct SQL can bypass the store (same as rest of DOOM). No second write path in `proactive/` except `store.py`.

`expire_stale` mangles `dedupe_key` with `#insight_id` so the unique key can be reused after expiry — reasonable, not memory deletion of V5.

---

## 11. WorldSnapshot

Derived SELECTs: `projects` (NORMAL only), `task_checkpoints`, `memory_lifecycle_events`, `experiences`, `system_telemetry`, circuit breaker in-memory, `state_machine._last_changed`. **No `command_logs`.** No INSERT/UPDATE in `snapshot.py`. TTL cache; `is_fresh()`; stale significance → IGNORE. Query failures → empty lists / `partial` (partial is **not** always set when `_q` returns `[]`). Snapshot construction: no LLM/tools.

---

## 12. Significance

Deterministic, no LLM. Default low scores for lifecycle/request/project. INFORM candidates: disk ≥80/90, circuit OPEN, task FAILED/blocked, strategy_failure, inactivity ≥7d, experience FAILURE. SENSITIVE/PRIVATE/stale snapshot → not candidate. Novelty-only MEMORY_LIFECYCLE stays IGNORE.

`STRATEGY_FAILURE` is always ≥ floor if type is used — any such ingest is “important” without extra evidence (acceptable for V6.1 if adapters stay absent).

---

## 13. Attention

Daily cap 8, cooldown 3600s, quiet hours env, busy gate on PROCESSING/PLANNING/THINKING/EXECUTING/VERIFYING. Persisted `proactive_attention`. HUD ring `MAX_HUD=50`.

**TOCTOU:** two workers may both pass `may_inform` before `record_inform`; insight UNIQUE collapses duplicate INFORM for the **same** `dedupe_key`. Different keys can still emit up to the daily cap — bounded, not zero-race.

**No runtime storm-to-HUD measurement of 1000 unique high-sig entities** (test_84 only hashes keys). Gap.

---

## 14. INFORM authority

`INTERVENTIONS = {IGNORE, INFORM}`. DDL CHECK matches. Worker only those two. No TaskEngine/CognitiveEngine/tool/subprocess in `proactive/`.

Adversarial notes asking to send Slack / approve / delete memory: **not executed**; some **not even dropped**.

---

## 15. TTS isolation

`TTS_PROACTIVE_ALLOWED` is false. `deliver_inform` **never imports or calls** `cinematic_voice.speak`. Cards set `"tts": False`.

Quirk: `if TTS_PROACTIVE_ALLOWED: return False` — if someone set the constant true, delivery would **abort**, not speak. Config boolean is not the only evidence; **call-graph evidence is the speak-free delivery module**.

Clap/startup TTS in `doom.py` / dashboard is **V5 user-path**, not V6 INFORM.

---

## 16. Memory isolation

No `memory_manager.store`, no importance/confidence SQL in `proactive/`. Runtime fingerprint unchanged across one V6 `process_once`. Retrieval not invoked in worker. **dI/dN_retrieval = 0** for this probe. Tests 54–57 are **not** an adequate substitute (hasattr / grep).

---

## 17. Project isolation

Snapshot reads project ids/status/privacy. No project UPDATE. Significance for `PROJECT_CHANGE` is below INFORM floor. No reassignment.

---

## 18. Privacy / security

SENSITIVE: dropped at normalize. PRIVATE: stored, not INFORM, not in HUD SQL. Secrets-as-forbidden-**keys** stripped; secrets-as-**values** only if marker prefixes match. `password=hunter2` in `note` can persist in JSONB.

OTP ALLOWED_ATTR_KEYS do not include `message` / payload.

---

## 19. DATA_ONLY

Fence drops a **fixed marker list**. Unlisted imperative English is kept as data (correct DATA_ONLY if never fed to an LLM). V6.1 has no LLM, so residual notes are **storage/HUD-template risk**, not instruction execution. Templates do not interpolate `note`.

---

## 20. Observability

Events: ingested / skipped, decision, insight.created, delivery, outcome, deduplicated. Fail-open `try/except`. Category `proactive` added to V5 schema. `prompt` attribute rejected (`test_66` is real). `reason` is an allowed attr — keep values enum-like (worker uses `budget`, `stale_snapshot`, etc.).

---

## 21. Dashboard / HUD / WS

GET `/api/proactive/status` — metadata. GET `/api/proactive/insights` — no mutate.

**F-HUD-01 (HIGH):** in `proactive_insights()`, after `if privacy_class != "NORMAL": continue`, the `params` / `out.append(...)` block is **indented under that `if`**, hence **unreachable**. PG rows are never serialized. Flag-on runtime: **`count: 0`** even with insights in PG. Fallback `hud_cards()` only if `out` empty — **process-local memory**, not durable HUD for a second process.

WS: `deliver_inform` sends full card JSON including **message prose** to `connected_clients` (`type: proactive_event`). Duplicate WS: unique delivery row prevents second persist; in-memory HUD append only on first insert success.

No POST execute/approve/prepare endpoints.

---

## 22. Legacy automation

No `DOOMAutomation`, `advanced_automation`, or `run_scheduler` in `proactive/` or V6.1 diffs. Acceptable coexistence of legacy files.

---

## 23. LLM isolation

No `model_router` / provider imports in `proactive/`. Runtime V6 cycle: **LLM_CALL_COUNT = 0**. Tests 91 is grep-only (still aligned).

---

## 24. Tool isolation

No tool registry usage in `proactive/`. Runtime **TOOL_CALL_COUNT = 0**. Malicious “execute tool” dropped at fence.

---

## 25. Failure matrix

| COMPONENT | FAILURE | EXPECTED | ACTUAL | SAFE? | BLOCKER? |
|-----------|---------|----------|--------|-------|----------|
| Flag OFF | ingest/worker | no-op | no-op, no row increment | Y | N |
| Flag true→false | thread stop | stop or no-op | thread **alive**, no-op work | Partial | N (non-blocking if documented) |
| PostgreSQL down | degrade | empty ops | enqueue/claim `""`/`[]` | Y | N |
| Telemetry | fail-open | continue | `except: pass` | Y | N |
| Snapshot dep | partial/IGNORE | IGNORE if stale | yes | Y | N |
| Invalid signal | drop | drop | drop | Y | N |
| Secret prefix | drop | drop | drop | Y | N |
| `password=` note | drop or redact | stored | stored | Partial | N |
| Duplicate signal | one row | one row | one row | Y | N |
| Duplicate delivery | one row | one row | one row | Y | N |
| Concurrent claim | disjoint | disjoint | disjoint (runtime) | Y | N* |
| Lease expiry | recover | recover | recover (runtime) | Y | N* |
| Crash post-insight pre-delivery | deliver once | **lost INFORM** | source | N | **Y (HIGH)** |
| `retry_or_dead` stale owner | no cross-worker DEAD | can DEAD by signal_id | source | N | **Y (HIGH)** |
| LLM unavailable | N/A | V6 needs none | none | Y | N |
| Memory unavailable | snapshot empty | empty lists | empty | Y | N |
| Normal DOOM request | unchanged | works | works | Y | N |
| HUD GET flag on | list INFORM | **0 cards from PG** | dead code | N | **Y (HIGH)** |
| Vacuous tests | prove concurrency | pass anyway | pass | evidence fail | **Y (policy)** |

\*Implementation OK; **suite does not prove it** — policy blocker.

---

## 26. Performance

| Path | n | p50 / bound | Evidence |
|------|---|-------------|----------|
| normalize | 50 | suite `< 0.5s` total | suite |
| hash dedupe | 200 | suite `< 0.2s` | suite |
| `process_once` flag-off | 1 | **< 0.5s** (suite); expected tiny | suite |
| snapshot force | 1 | suite `< 3s` | suite |
| `process_once` flag-on (1 item) | 1 | **38.7 ms** | auditor |
| `process_request` arithmetic | 1 | **31 ms** | auditor |

No multi-worker throughput histogram. Duplicate-hash storm is CPU-cheap. **PENDING table growth under unique-key flood is unbounded** (no depth cap).

---

## 27. Test-quality audit

**TOTAL TESTS: 95** (all named `test_01`–`test_95`). Pytest/unittest **PASS is not treated as proof.**

### Classification (primary label)

| IDs | Class |
|-----|--------|
| 01–03, 05–17, 19–21, 24, 36–42, 44–46, 59–68, 83–88, 94–95 | **REAL** (unit or integration with a real assertion) |
| 04, 10, 15–17, 19–21, 25, 27, 45, 68 | also **INTEGRATION** (PG / core) |
| 25, 67 | **FAULT-INJECTION** (limited) |
| 59–63, 85–88 | **SECURITY** |
| 68 | **REGRESSION** |
| 73–77 | **PERFORMANCE** (coarse) |
| 81 | **CONCURRENCY** (normalize only) |
| 18, 26, 54, 70–72, 82 | **VACUOUS** |
| 22, 47, 53, 69, 77, 79, 80 | **DUPLICATIVE** |
| 23, 28–33, 35, 39, 43, 48–51, 55–58, 78, 90–93 | **WEAK_ASSERTION** (tautology, isinstance-only, grep-only, config-only, or n≥0) |

**Counts (auditor, not disjoint-perfect):**

| Bucket | Approx |
|--------|--------|
| TOTAL | 95 |
| MEANINGFUL (non-vacuous, non-duplicate, non-weak) | **~52** |
| WEAK | **~24** |
| VACUOUS | **7** (18, 26, 54, 70, 71, 72, 82) |
| DUPLICATE | **7** |
| BLOCKING TEST GAPS | lease recovery, concurrent claim, concurrent delivery vs PG, crash-after-insight, retry_or_dead ownership, flag-on HUD, memory fingerprint, LLM/tool counters, unique-key flood, time-bucket edge, true→false stop |

### Named smoking guns

**test_18** (`test_v61_proactive_foundation.py` ~149–160): `assertGreaterEqual(n, 0)` is always true for a count; `any(...) or n >= 0` **cannot fail**. Does not prove lease recovery.

**test_26** (~230–243): `a.isdisjoint(b) or True` is **always true**. Concurrent claim is **untested** by the suite.

**test_53:** calls `test_52` (mocked `insert_delivery` → `""`). Does **not** prove duplicate delivery prevention.

**test_22 / 79 / 80:** re-call test_18.

**test_54:** `hasattr(MemoryRetriever, "retrieve")` — not read-only proof.

**test_82:** concurrent `deliver_inform` with **mocked** store and **no assertion**.

**test_50:** asserts a boolean constant, not a speak() call graph.

**test_95:** flag-off API only; flag-on HUD untested (would have caught F-HUD-01).

---

## 28. Weak / vacuous tests (findings)

See F-TEST-01 … F-TEST-05 in §31. Independent runtime **did** prove unique ingest, disjoint claim, unique delivery, and lease recover — the **implementation is stronger than the suite**, but **release evidence is not**.

---

## 29. Architectural invariant matrix

| ID | Invariant | Status |
|----|-----------|--------|
| I1 | Flag false ⇒ inert | **HOLD** for ingest/worker/process_once; caveats: schema, GET routes, import, leftover thread if previously started |
| I2 | No tools | **HOLD** |
| I3 | No LLM | **HOLD** |
| I4 | No proactive TTS | **HOLD** |
| I5 | No external mutation | **HOLD** (V6.1) |
| I6 | No ACTIVE memory create | **HOLD** (fingerprint + grep) |
| I7 | No V5 quality field mutation | **HOLD** (importance sum unchanged) |
| I8 | No command_logs world state | **HOLD** |
| I9 | Snapshot derived/TTL | **HOLD** |
| I10 | IGNORE/INFORM only | **HOLD** |
| I11 | Attention bounded | **HOLD** for daily cap; queue of PENDING **not** bounded |
| I12 | Retries bounded | **HOLD** (`MAX_ATTEMPTS`) |
| I13 | No double-claim | **HOLD in runtime**; **FAIL as tested** |
| I14 | Telemetry fail-open | **HOLD** |
| I15 | V6 cannot break DOOM | **HOLD** for probed path |
| I16 | No private/sensitive INFORM | **HOLD** for HUD SQL + significance; PRIVATE **stored** |
| I17 | No legacy automation | **HOLD** |
| I18 | No second cognition/router/gov | **HOLD** |

---

## 30. Git / scope audit

`git diff e933c21` working tree: **+155** lines on four V5 files; new `proactive/` + tests + reports.

| File | Class |
|------|--------|
| `proactive/*` | EXPECTED V6.1 |
| `test_v61_proactive_foundation.py` | EXPECTED V6.1 (quality insufficient) |
| `database/postgres_db.py` | EXPECTED V6.1 tables |
| `observability/schemas.py` | EXPECTED (category + attr keys) |
| `doom.py` | EXPECTED flag-gated start (minimal) |
| `dashboard/server.py` | EXPECTED start + GET APIs; **HUD handler buggy** |
| Provider-routing markdown | UNRELATED untracked (leave) |
| `DOOM_V6.1_IMPLEMENTATION_REPORT.md` | EXPECTED docs; **over-claims 95/95** |

No provider-routing redesign. No cognition/governance/TaskEngine rewrite.

---

## 31. Blocking findings

### F-TEST-01

- **SEVERITY:** CRITICAL (policy automatic blocker)
- **LOCATION:** `test_v61_proactive_foundation.py` test_18, test_22, test_26, test_53, test_79, test_80, test_82
- **EVIDENCE:** `or True`; `or n >= 0`; test_53→test_52; test_82 no assert + mock
- **IMPACT:** Release package claims lease recovery, concurrent workers, and duplicate-delivery prevention without proving them.
- **BLOCKER?** **YES**
- **RECOMMENDATION:** Replace with assertions on PG rows, disjoint claim sets, and unique `(insight_id, channel)` under threads. Do not call other tests as “coverage.”

### F-HUD-01

- **SEVERITY:** HIGH
- **LOCATION:** `dashboard/server.py` `proactive_insights` (~358–374)
- **EVIDENCE:** `continue` then unreachable `out.append`. Flag-on GET `count: 0` with PG insights present.
- **IMPACT:** Durable INFORM is not exposed on the HUD API; cross-process HUD fails.
- **BLOCKER?** **YES** (INFORM delivery surface broken)
- **RECOMMENDATION:** Unindent serialization; add flag-on API test against PG rows; do not rely only on in-process `_hud`.

### F-WRK-01

- **SEVERITY:** HIGH
- **LOCATION:** `proactive/worker.py` `_process_item` + `store.insert_insight` ON CONFLICT
- **EVIDENCE:** conflict → empty id → skip `deliver_inform` → still `ack PROCESSED`
- **IMPACT:** Crash between insight insert and delivery ⇒ **lost INFORM**, not retried.
- **BLOCKER?** **YES**
- **RECOMMENDATION:** On conflict, load existing OPEN/DELIVERED insight and deliver if no delivery row.

### F-WRK-02

- **SEVERITY:** HIGH
- **LOCATION:** `proactive/store.py` `retry_or_dead`
- **EVIDENCE:** missing row ⇒ `attempts = MAX_ATTEMPTS`; DEAD update **by signal_id only**
- **IMPACT:** Wrong worker can mark another’s live claim DEAD.
- **BLOCKER?** **YES**
- **RECOMMENDATION:** If SELECT misses, return DROP; DEAD/RETRY UPDATE must include `worker_id` and `status='CLAIMED'`.

### F-TEST-02 (supporting automatic blocker)

- **SEVERITY:** HIGH
- **LOCATION:** tests 54, 70–72, 50, 55–58, 90–92
- **EVIDENCE:** hasattr / constant / source grep
- **IMPACT:** Isolation claims are not runtime-proven in-suite (auditor proved some separately).
- **BLOCKER?** **YES** as part of “serious claims only vacuous”
- **RECOMMENDATION:** Fingerprint memory tables; assert `speak` not called; wrap router/tools during `process_once`.

---

## 32. Non-blocking findings

| ID | SEV | LOCATION | EVIDENCE | IMPACT | BLOCKER? | RECOMMENDATION |
|----|-----|----------|----------|--------|----------|----------------|
| F-FLAG-01 | MEDIUM | `worker.py` `_loop` | env false, thread still alive | leftover daemon | N | Stop thread when flag drops; or document “stop required” |
| F-Q-01 | MEDIUM | `expire_stale` | PENDING never deleted | unbounded outbox under unique-key flood | N* | Cap depth / drop oldest PENDING |
| F-FENCE-01 | MEDIUM | `fence.py` | `password=hunter2`, “approve action…” kept | JSONB residue | N | Broader secret/imperative heuristics **or** drop free-text `note` |
| F-SRC-01 | LOW | `schemas.py` SOURCES | `"test"` allowed | flag-on ingest abuse | N | Remove `test` from production set |
| F-SRC-02 | LOW | `poller.py` | only 3 sources | declared types unused | N | Document adapters as V6.1.1 |
| F-ATT-01 | LOW | `attention.py` | TOCTOU then unique insight | extra IGNORE | N | Optional transactional budget |
| F-OTP-01 | INFO | `process_once` | bare `except` | silent cycle fail | N | emit `proactive.outcome` error |
| F-SNAP-01 | LOW | `snapshot.py` | `_q []` not always `partial` | over-complete cache | N | set partial on empty error |
| F-DDL-01 | INFO | postgres init | tables always created | flag-off schema present | N | acceptable |
| F-PATH-01 | INFO | HTTP | no `/api/command/clap-wake` | audit path 405 | N | use `POST /api/command` / clap handler |
| F-PERF-01 | INFO | suite | no p50/p95 histograms | report overclaim | N | optional benches |
| F-CONS-01 | INFO | poll vs V5 TX | documented | at-least-once | N | keep for later phase |

\*F-Q-01 is **not** in the automatic blocker list; treat as HIGH if product requires hard bounded queues before launch.

---

## 33. Required remediation (do not implement in this audit)

1. Rewrite vacuous/duplicate tests; add concurrent claim/delivery/lease tests that **fail** if overlap or extra rows appear.
2. Fix HUD `proactive_insights` indentation; test flag-on against PostgreSQL.
3. Repair insight-conflict path so missing delivery is retried.
4. Repair `retry_or_dead` ownership.
5. Decide queue-depth / PENDING retention policy.
6. Optionally stop worker on flag transition; tighten fence for residual secrets.
7. Re-run this forensic checklist; only then consider commit/tag.

**Do not** “fix” unrelated V5 findings to get a green audit.

---

## 34. Final verdict

**C. BLOCKED — REMEDIATION REQUIRED**

V6.1 is **directionally correct** (observational INFORM, default off, isolated from cognition, zero LLM/tools/TTS/memory mutation in auditor probes). It is **not release-ready** because the claimed test proof is materially false in several safety-critical cases, the HUD INFORM API does not serialize PG insights, and worker recovery can lose INFORM or DEAD a row owned by another worker.

**No commit. No tag. No push.** Human decides remediation next.
