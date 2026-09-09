# DOOM V6.1 — Independent Forensic Re-Audit

**Mode:** remediation verification only. No implementation, test, schema, or git mutations except this file.  
**Date:** 2026-09-09  
**Remediation claim:** A. REMEDIATION COMPLETE — READY FOR FORENSIC RE-AUDIT  
**This audit does not accept that claim at face value.**

**Verdict:** **C. BLOCKED — REMEDIATION REQUIRED**

F-TEST-01, F-HUD-01, and F-WRK-02 **hold** under independent SQL + runtime checks. **F-WRK-01 does not.** The worker can still **ACK a claimed signal as IGNORE** (attention/budget/cooldown/busy) **after an INFORM insight already exists and while `proactive_deliveries` is empty**, so the signal is **not retried** and **no delivery row is created**. That is the original lost-INFORM class of failure, now on a different branch than unique-key conflict.

HUD listing can still show the orphan insight (API is insight-based). Durable **delivery** (WS / unique delivery row / retry) can still be skipped forever.

---

## 1. Executive verdict

Remediation improved test honesty, HUD serialization, and lease SQL fencing. It did **not** close the INFORM durability contract on the worker’s **attention-IGNORE short-circuit**. Independent crash-window probe:

- Insight inserted, **deliveries = 0**
- Matching FAILED task signal processed (`process_once` n=1)
- Signal **PROCESSED**
- **deliveries still 0**

That is sufficient to refuse release.

---

## 2. Baseline

| Check | Result |
|-------|--------|
| Branch | `DOOM-V5.2` |
| HEAD | `e933c212278f5bd7e05d9375c9f73a773960dcff` |
| `v5.3.7.4-observability` | `e933c21` |
| `v5.3.7.3` | `73be7ff` |
| `v5.3.7.3-provider-routing` | `086582e` |
| V6.1 committed/tagged/pushed | **No** |

`git status --short`: modified `dashboard/server.py`, `database/postgres_db.py`, `doom.py`, `observability/schemas.py`; untracked `proactive/`, `test_v61_proactive_foundation.py`, V6 reports, prior provider-routing markdown.

`git diff --stat` vs freeze: **+155** on those four tracked files (schema + flag start + OTP keys + HUD). History not rewritten.

---

## 3. Remediation claim verification

| CLAIM | IMPLEMENTATION | RUNTIME | TEST | ACTUALLY PROVEN? | FINDING |
|-------|----------------|---------|------|------------------|---------|
| F-TEST-01 vacuous tests replaced | `test_18/26/53/82` inspect PG | Concurrent claim overlap empty | 64 passed | **Mostly** | No `or True`. Crash-through-worker not in suite |
| F-HUD-01 PG listing | Endpoint uses `list_hud_insights` only | Flag off count 0; flag on included known `insight_id` | 95–98 | **Yes** | — |
| F-WRK-01 insight ≠ delivery; ACK after delivery | `insert_insight` returns existing id; **then** `may_inform` can force IGNORE **before** deliver | Insight+0 delivery → process_once → **PROCESSED, still 0 deliveries** | test_100 never runs worker ACK | **No** | **F-RA-01** |
| F-WRK-02 owner SQL | `_OWNER_PRED` on ACK/RETRY/DEAD | B fenced; expired A fenced; B reclaim; A fenced | 18, 99 | **Yes** | — |
| Flag false stops thread | Loop `break`; `worker_alive` requires flag | Thread name gone; signal count +0 over 2.5s | test_04 enumerates threads | **Yes** | — |
| Queue cap 256, may overshoot | `COUNT < MAX` without lock | Sequential reject at MAX=0 | test_102 | **Partial** | Overshoot bounded by concurrency, not unbounded |
| Secret markers | substring list | Many colon/hyphen variants **KEEP** | only `password=` / gsk_ | **Partial** | **F-RA-02** |
| Zero LLM/tools/TTS | no imports in `proactive/` | generate/speak/execute counts **0** on `process_once` + request | 50, 91, 104 | **Yes** for probed path | 104 wraps router only |
| Memory isolation | no memory writes in package | count 4280→4280, importance sum unchanged | none fingerprint | **Yes** this run | — |

---

## 4. F-TEST-01

**64 tests passed** (re-run). That is not the criterion.

| Tests | Class |
|-------|--------|
| 01–03, 94 | REAL flag |
| 04 | INTEGRATION + thread enum |
| 05–09, 11–14 | UNIT |
| 10, 15–16, 18–19, 21, 26–27, 53, 82, 96–97, 99–102 | INTEGRATION / CONCURRENCY |
| 18 | REAL lease (forced expiry, not wall clock) |
| 26 | REAL concurrent `claim_batch` (3 futures submitted before join) |
| 34, 36–38, 40 | UNIT significance |
| 41 | UNIT mocked attention store (policy unit, not PG budget) |
| 45 | INTEGRATION busy gate |
| 48–49, 51, 64, 103 | UNIT / config |
| 50, 58, 90, 91 | WEAK source-text (supporting, not sole proof) |
| 52 | FAULT-INJECTION (mocks persist **failure**, not success path) |
| 66–67 | OTP |
| 68–69 | REGRESSION / isolation |
| 73, 75 | PERF |
| 83, 86, 61–63 | SECURITY UNIT |
| 95–98 | HUD INTEGRATION |
| 104 | REGRESSION LLM wrap |

**Vacuous `or True`:** absent.

**test_18:** PG state transitions A→expired→FENCED→PENDING→B. **REAL.** `assertGreater(n, 0)` on recover count is slightly loose (other expired rows), but **this signal’s row** is asserted.

**test_26:** duplicate `signal_id` in claim tuples forbidden; PG `worker_id` checked. Independent 4-way barrier: **overlap empty**, 12/12 CLAIMED.

**test_53 / test_82:** real `upsert_delivery`, `count_deliveries == 1`, 8 threads. **REAL.**

**Gaps:** no worker-level crash between `insert_insight` and `deliver_inform`; no `process_once` with pre-seeded insight. test_100 stops at store/delivery helpers.

---

## 5. F-HUD-01

Control flow: flag off → empty. Flag on → PG `list_hud_insights` (INFORM, NORMAL, `valid_until > NOW()`), serialize, bound `MAX_HUD`. **No `hud_cards()` fallback. No dead `continue`.**

Runtime: off `count=0 enabled=false`; on `count=16` and **pre-inserted insight id present**; `limit=1` returns 1.

Private/expired/IGNORE excluded by SQL + tests 97.

PostgreSQL is list authority. `_hud` is delivery-process cache only.

**PASS** for this finding.

---

## 6. F-WRK-01 — **NOT FIXED**

Worker INFORM path:

1. `may_inform` may set `intervention = IGNORE`
2. `if intervention != INFORM: ACK PROCESSED; return`  
   **does not inspect existing insight/delivery**
3. Only then `insert_insight` / `deliver_inform`

### States

| State | INFORM lost? | Dup delivery? | Incorrect ACK? | Infinite retry? |
|-------|----------------|---------------|----------------|-----------------|
| A none/none | N (nothing to deliver) | N | IGNORE ACK OK | N |
| B insight / no delivery | **Y if retry IGNORE-ACK** | N | **Y PROCESSED without delivery** | N (worse: stops) |
| C both exist | N | N (unique) | ACK OK | N |
| D claimed, insight, crash before delivery | recoverable **if** later INFORM path runs | N | **if later IGNORE-ACK, lost delivery** | bounded attempts |
| E delivery exists, crash before ACK | N (row exists) | N | recover then ACK; runtime **dels=1 PROCESSED** | N |
| F reclaim, insight, no delivery | **same as B** | N | **Y under attention deny** | N |
| G reclaim, both exist | N | N | ACK OK | N |

**Runtime B:** `deliveries_after 0`, status **PROCESSED**.  
**Runtime E:** `dels 1`, PROCESSED. Duplicate delivery **not** created.

**Honest semantics today:** at-most-once **delivery row**; at-least-once **only while** the worker still classifies INFORM. Attention/budget converts INFORM→IGNORE and **finalizes the signal**.

---

## 7. F-WRK-02 — **FIXED**

SQL `_OWNER_PRED`: `signal_id AND worker_id AND CLAIMED AND lease_until > NOW()` on ACK and RETRY/DEAD (`FOR UPDATE` then same WHERE). No unqualified `DEAD WHERE signal_id`.

Runtime: B ACK/RETRY **False/FENCED** while A owns; after expire A ACK/RETRY **False/FENCED**; B reclaim; A still **FENCED**; B ACK **True**.

No heartbeat API (N/A). Recovery `UPDATE ... CLAIMED AND lease_until < NOW()` is ownerless by design.

**PASS.**

---

## 8. Worker / lease concurrency

Independent 4 workers + barrier: **no overlapping claims**. Sequential disjoint test_27. SKIP LOCKED + claim UPDATE in one transaction.

**I13 PASS.**

---

## 9. Delivery durability

See §6. Unique `(insight_id, channel)` prevents duplicates (runtime 8-way).  
**Loss path remains** attention-IGNORE ACK.

Crash injection used deterministic store/worker APIs (equivalent to crash+restart), not process kill. Still valid for these windows.

---

## 10. Flag lifecycle

`start` flag-off: **False**. Flag-on: thread starts. Env false: `worker_alive()` immediately false (flag conjunct). **Thread `doom-v61-proactive` gone** after ≤2.5s. Signal table **delta 0** during wait.

Mid-cycle `process_once` already running can still write once; then loop breaks. Acceptable.

**PASS.**

---

## 11. Queue bound

`INSERT ... WHERE COUNT(PENDING+CLAIMED) < PENDING_QUEUE_MAX`. Concurrent sessions can both pass the snapshot count → overshoot ≈ **number of concurrent inserters**, not unbounded growth from a single race. Duplicates at cap still resolve by idempotency key. test_102 MAX=0 rejects new unique ids.

**NON-BLOCKING** for V6.1 (operational cap, not a hard SERIALIZABLE quota).

---

## 12. Secret filtering

Dropped: `password=`, `PASSWORD=`, `passwd=`, `bearer `, `Authorization=`, `private_key`, `clientSecret` (contains `client_secret`? actually `clientSecret=abc` lowercased contains `client_secret`? `clientsecret=abc` — marker is `client_secret` with underscore. Runtime said **clientSecret DROP** — maybe `secret=` in `clientsecret=abc`? Marker `secret=` — `clientsecret=abc` contains `secret=`? `clientsecret=abc` → `secret=` as substring of `tsecret=`? **`tsecret=` no**. `secret=` in `clientsecret=abc`: ... `secret=abc` yes (`t`+`secret=abc`). OK.

**False negatives (stored):** `password : hunter2`, `pwd=`, `api-key=`, `api_key:`, `token:`, `secret:`, `private-key=`.

Legitimate `disk_percent: 91` kept.

Not V5 PRIVATE-body leakage. **MEDIUM, non-blocking** vs automatic “sensitive leak” (no SENSITIVE class stored).

---

## 13. DATA_ONLY / injection

Dropped: ignore previous, execute tool (phrase), approve action, send message, bypass governance.  
**Kept:** `execute_tool`, `call tool`, `powershell`, `cmd.exe`, `run command`, `modify system`, `shell command`.

No tool/LLM/TTS invocation in `proactive/` or in instrumented `process_once`. Stored notes are not authority.

**NON-BLOCKING** (coverage gaps, not execution).

---

## 14–16. LLM / tools / TTS

`process_once` + `process_request("What is 3 + 3?")`: **LLM=0, TOOL=0, TTS wrap=0**. Delivery does not call `cinematic_voice`. Request TTS was patched in the cognition test (speak mocked for process_request). V6 cycle TTS counter was 0 before that request.

**I2–I4 PASS** on probed path. Not every provider class was monkeypatched; `generate` on `model_router` is the production gate.

---

## 17–18. Memory / world

Memory count and importance sum unchanged across V6 cycle. Snapshot module is SELECT-only. No project UPDATEs in `proactive/`.

**PASS** this run. Relationships/lessons/strategies not all fingerprinted (tables may be empty/unused).

---

## 19–20. command_logs / legacy

No `FROM command_logs` in worker/poller/snapshot/store/ingest/delivery. `command_logs` is a forbidden payload **key**. No `DOOMAutomation` / `run_scheduler` in `proactive/`.

**PASS.**

---

## 21–23. Snapshot / significance / attention

Snapshot: derived, TTL, `is_fresh`, partial on some failures. Stale → IGNORE. MEMORY_LIFECYCLE not INFORM. Busy gate tested. Daily budget unit-tested with mock; **live budget is why crash-window B ACKed IGNORE** — attention is effective, and that effectiveness **interacts badly with durability**.

---

## 24. Observability

OTP `prompt` attr rejected. `emit` fail-open. Metadata keys only.

**PASS** fail-open / schema. No full secret-in-attributes storm beyond schema.

---

## 25. Dashboard / WS

GET status/insights only for V6.1. WS send on **new** delivery `created=True`. Duplicate delivery does not add a second PG delivery; in-memory HUD dedupes by `insight_id`. Insights API can list an insight **without** a delivery row (by design of `list_hud_insights`).

---

## 26. Production path

No `POST /api/command/clap-wake` (405 previously). `DOOMCore.process_request` isolated (`3 + 3 = 6`). Flag on during that request: cognition still worked.

---

## 27. V5 regression

`test_v61` + OTP + provider routing: **132 passed**, 0 failed. FastEmbed still missing (pre-existing env).

---

## 28. Test-quality assessment

Critical claim “ACK only after durable delivery” is **not** covered by a worker integration test. test_100/101 never set `intervention` IGNORE after insight insert. **Safety claim ≠ suite proof.** Independent runtime **disproved** the claim.

---

## 29. Failure matrix

| COMPONENT | FAILURE/RACE | OBSERVED | SAFE? | BLOCKER? |
|-----------|--------------|----------|-------|----------|
| Flag off | start/ingest/process | no-op | Y | N |
| Concurrent claim | 4 workers | disjoint | Y | N |
| Stale A vs B | ACK/RETRY | FENCED | Y | N |
| Dup delivery | 8 threads | 1 row | Y | N |
| Insight, no delivery, worker cycle | PROCESSED, 0 deliveries | **N** | **Y** |
| Delivery then reclaim | 1 row, PROCESSED | Y | N |
| PG down | enqueue "" | degrade | Y | N |
| Telemetry throw | fail-open test | Y | N |
| Secret colon variants | stored in payload | Partial | N |
| Queue COUNT race | overshoot | bounded | N |
| LLM wrap | 0 | Y | N |
| Memory fingerprint | unchanged | Y | N |
| HUD flag off | empty | Y | N |
| HUD flag on | PG ids | Y | N |

---

## 30. Architectural invariant matrix

| ID | Result | Evidence |
|----|--------|----------|
| I1 Flag OFF inert | **PASS** | ingest/worker/process_once |
| I2 No LLM | **PASS** | generate count 0 |
| I3 No tools | **PASS** | execute 0 |
| I4 No proactive TTS | **PASS** | delivery call graph + wrap 0 |
| I5 No external mutation | **PASS** | no connectors |
| I6 No ACTIVE memory | **PASS** | count unchanged |
| I7 No quality mutation | **PASS** | importance sum |
| I8 No command_logs | **PASS** | SQL |
| I9 Snapshot derived | **PASS** | source |
| I10 IGNORE/INFORM only | **PASS** | CHECK + enum |
| I11 Attention bounded | **PASS** | also causes F-RA-01 |
| I12 Retry bounded | **PASS** | MAX_ATTEMPTS |
| I13 No double claim | **PASS** | runtime |
| I14 Telemetry fail-open | **PASS** | |
| I15 Cannot break DOOM | **PASS** | process_request |
| I16 Privacy HUD | **PASS** | SENSITIVE drop; PRIVATE not listed |
| I17 No legacy scheduler | **PASS** | |
| I18 No second cognition | **PASS** | |
| Delivery durability | **FAIL** | F-RA-01 |

---

## 31. Git / scope

| Path | Class |
|------|--------|
| `proactive/*` | EXPECTED V6.1 + REMEDIATION |
| `test_v61_proactive_foundation.py` | REMEDIATION |
| `dashboard/server.py` | EXPECTED HUD |
| `doom.py` | EXPECTED flag start |
| `database/postgres_db.py` | EXPECTED tables |
| `observability/schemas.py` | EXPECTED category |
| Reports / provider markdown | UNRELATED untracked |

No provider-routing or cognition redesign in the 155-line V5 diff.

---

## 32. Blocking findings

### F-RA-01 (was F-WRK-01 residual)

- **SEVERITY:** CRITICAL  
- **LOCATION:** `proactive/worker.py` `_process_item` IGNORE ACK before deliver  
- **EVIDENCE:** insight `04be92d2-…` deliveries 0 → `process_once` → signal `21e4eb3b-…` **PROCESSED**, deliveries still 0. Cause: `may_inform` deny then ACK.  
- **IMPACT:** Crash after insight / retry under budget/cooldown/busy **never delivers** and **will not retry**.  
- **BLOCKER?** **YES** (silent delivery loss; incorrect ACK)  
- **RECOMMENDATION:** If an OPEN INFORM insight exists for the dedupe key without a delivery row, **do not ACK IGNORE**. Attempt `deliver_inform` or leave CLAIMED/PENDING. Tests must drive **worker** through that state, not only `insert_insight` helpers.

---

## 33. Non-blocking findings

| ID | SEV | LOCATION | EVIDENCE | IMPACT | BLOCKER? | RECOMMENDATION |
|----|-----|----------|----------|--------|----------|----------------|
| F-RA-02 | MEDIUM | `fence.py` markers | `password :`, `pwd=`, `api-key=`, `token:` kept | JSONB residue | N | Normalize separators |
| F-RA-03 | LOW | injection list | `execute_tool`, `cmd.exe` kept | storage only | N | optional extra markers |
| F-RA-04 | LOW | queue COUNT | documented overshoot | extra PENDING rows | N | optional lock |
| F-RA-05 | INFO | test_41, 50, 58, 90, 91 | mock/grep | incomplete suite | N | keep as supplements |
| F-RA-06 | INFO | no clap-wake route | 405 | audit path | N | `POST /api/command` |
| F-RA-07 | INFO | HUD lists insight without delivery | API vs `proactive_deliveries` | two notions of INFORM | N | document canonical surface |

---

## 34. Required remediation (do not implement here)

1. Close IGNORE-ACK vs undelivered INFORM insight (F-RA-01).  
2. Add worker integration: seeded insight, zero deliveries, `process_once`, assert delivery row **or** signal not PROCESSED.  
3. Optional: fence colon/hyphen secret forms.

Then independent re-audit again. **Do not commit until that passes.**

---

## 35. Final verdict

**C. BLOCKED — REMEDIATION REQUIRED**

Do not treat the remediation report’s “A” as forensic truth. Three of four original blockers are closed. **Delivery durability is not.**
