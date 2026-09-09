# DOOM V6.1 — Remediation Report

**Status:** remediations applied in the working tree. **Not committed. Not tagged. Not pushed. Not release-ready.**

**Date:** 2026-09-09  
**Baseline HEAD:** `e933c212278f5bd7e05d9375c9f73a773960dcff` (`DOOM-V5.2`)  
**Prior forensic verdict:** C. BLOCKED — REMEDIATION REQUIRED (`DOOM_V6.1_FORENSIC_AUDIT.md`)

**This verdict:** **A. REMEDIATION COMPLETE — READY FOR FORENSIC RE-AUDIT**

---

## 1. Original blockers

| ID | Title | This report |
|----|--------|-------------|
| F-TEST-01 | Vacuous / duplicate / mocked safety tests | **FIXED** |
| F-HUD-01 | `/api/proactive/insights` dead code; flag-on count 0 | **FIXED** |
| F-WRK-01 | Insight conflict skipped delivery then ACK (lost INFORM) | **FIXED** |
| F-WRK-02 | `retry_or_dead` DEAD by `signal_id` without live owner | **FIXED** |

---

## 2–4. Root causes, fixes, files

### F-TEST-01

**Root cause:** Assertions that cannot fail (`or True`, `n >= 0`), tests calling other tests, `insert_delivery` mocked away.

**Fix:** Replaced the suite with **64** tests that inspect PostgreSQL rows for claim ownership, lease expiry, fencing, delivery uniqueness, and HUD. Removed vacuous helpers. Count is lower on purpose.

### F-HUD-01

**Root cause:** `continue` then unreachable `out.append`; fallback to in-memory `_hud` as a second authority.

**Fix:** Endpoint serializes `list_hud_insights` from PostgreSQL only. Flag off returns `enabled: false`, `count: 0`. Per-row try/except. Bound `MAX_HUD`.

### F-WRK-01

**Root cause:** `insert_insight` ON CONFLICT returned `""`; worker treated that as done and ACK’d without a delivery row.

**Fix:** `insert_insight` returns the existing `insight_id`. Worker delivers if no durable HUD delivery exists. ACK only after IGNORE **or** a delivery row exists. Persist failure → `retry_or_dead`, not ACK.

### F-WRK-02

**Root cause:** Missing owner row defaulted `attempts = MAX_ATTEMPTS` and `UPDATE ... DEAD WHERE signal_id = %s`.

**Fix:** All ACK / RETRY / DEAD updates use SQL:

`signal_id AND worker_id AND status = 'CLAIMED' AND lease_until IS NOT NULL AND lease_until > NOW()`  
(`FOR UPDATE` on the retry path). Else outcome **`FENCED`**.

### Files changed

- `proactive/store.py`, `worker.py`, `delivery.py`, `config.py`, `schemas.py`, `fence.py`
- `dashboard/server.py`
- `test_v61_proactive_foundation.py`
- this report

No V5 cognition / router / governance / TaskEngine redesign. No LLM/tools/ACT/TTS.

---

## 5. Database / schema

No new tables. Existing unique keys used as authority:

- `proactive_signals.idempotency_key UNIQUE`
- `proactive_insights.dedupe_key UNIQUE`
- `proactive_deliveries (insight_id, channel) UNIQUE`

Ownership is enforced in **UPDATE/SELECT WHERE**, not a new constraint (leases are time-varying).

Enqueue backpressure: `INSERT ... SELECT ... WHERE (COUNT PENDING+CLAIMED) < PENDING_QUEUE_MAX` then `ON CONFLICT DO NOTHING`; duplicates still resolve by idempotency key.

---

## 6. Transaction semantics

Claim: `SELECT ... FOR UPDATE SKIP LOCKED` then `UPDATE` to `CLAIMED` in the same transaction.

Retry/DEAD: `SELECT ... FOR UPDATE` with owner predicate, then `UPDATE` with the same predicate, then commit.

Insight/delivery inserts: insert + conflict + select existing in one transaction, then commit.

Honest delivery semantics: **at-most-once delivery row**; **at-least-once attempt** until that row exists or attempts exhaust to `DEAD`.

---

## 7. Worker ownership

| Actor | ACK | RETRY | DEAD |
|-------|-----|-------|------|
| Live lease owner | yes | yes | yes (if attempts ≥ max) |
| Other worker | `False` / `FENCED` | `FENCED` | `FENCED` |
| Expired lease (before recover) | fenced (`lease_until > NOW()` fails) | `FENCED` | `FENCED` |
| After recover + reclaim | new owner only | new owner | new owner |
| `DEAD` row | not `CLAIMED` | `FENCED` | not reclaimed |

---

## 8. Delivery durability

Invariant: **INSIGHT_EXISTS ≠ DELIVERY_EXISTS**.

| Crash window | Behavior |
|--------------|----------|
| A before insight | lease recover; retry creates insight |
| B after insight, before delivery | existing insight id; `deliver_inform`; no duplicate insight |
| C after delivery persist, before ACK | `deliver_inform` sees existing row (`created=False`); ACK; no second delivery row |
| D retry after insight | same as B |
| E retry after delivery | ACK path; unique constraint |

HUD/WS is **not** authoritative; it runs only when `created=True`.

---

## 9. HUD correction

`GET /api/proactive/insights`:

- Flag off: empty, `enabled: false`
- Flag on: PG INFORM + NORMAL + `valid_until > NOW()`, bounded
- No in-memory fallback as source of truth

Tests: flag off; flag on includes inserted insight; private/expired/IGNORE excluded; `limit=9999` capped.

---

## 10–11. Test replacements / weak tests

Removed/replaced: test_18 vacuous lease, test_26 `or True`, test_22/53/69/79/80 call-chains, test_82 mock + no assert, hasattr-only router/task/gov tests, `n >= 0` processing.

Kept as **explicit source guards** (not claimed as runtime isolation proofs): no `FROM command_logs`, no `DOOMAutomation`, no `model_router` in `proactive/`. Runtime LLM count is `test_104` wrapping `generate`.

**64 passed** (`pytest test_v61_proactive_foundation.py`).

---

## 12. Flag lifecycle

`PROACTIVE_ENABLED` is re-read each worker loop tick (existing poll interval; no extra busy-wait).

- `start_proactive_worker()` if false → `stop_proactive_worker()`, return false
- Loop **breaks** when flag is false → thread exits
- `worker_alive()` requires flag **and** live thread
- `test_04` waits until `doom-v61-proactive` is gone (≤5s)

Other config (lease, TTL) remains import-time. Documented in `is_proactive_enabled()` docstring.

---

## 13. Queue-bound policy

`PROACTIVE_PENDING_QUEUE_MAX` default **256**.

- New unique signals refused when `PENDING+CLAIMED >= max` (empty ingest id)
- Existing idempotency keys still map to the stored row (no silent delete of valid PENDING)
- Approximate under concurrent insert (two sessions can both pass the count check)
- Not an in-memory queue

---

## 14. Secret / DATA_ONLY filtering

Value markers now include `password=`, `token=`, `api_key=`, `client_secret`, `private_key`, `bearer `, plus prior key prefixes.

Injection markers include `approve action`, `send message`, `delete memory`, `change policy`.

Entire signal dropped (fail-closed), not stored as note text.

---

## 15. Regression verification

- `test_v61_proactive_foundation.py`: **64 passed**
- `test_v5374_observability.py` + `test_provider_routing_scenarios.py`: **68 passed**
- `process_request("What is 2 + 2?")` still covered
- Instrumented `model_router.generate` during `process_once`: **0** calls

---

## 16. Runtime evidence (this remediation)

| Property | Evidence |
|----------|----------|
| Concurrent claim at most one owner | `test_26` PG `worker_id` |
| Lease expiry + A fenced + B reclaim | `test_18` |
| Duplicate delivery one row | `test_53`, `test_82` (8 threads, `count_deliveries == 1`) |
| Bob cannot DEAD Alice | `test_99` |
| Insight without delivery then deliver | `test_100` |
| Flag on HUD contains PG insight | `test_96` |
| Flag off HUD empty | `test_95` |
| Queue overflow reject | `test_102` (`PENDING_QUEUE_MAX=0`) |
| `password=hunter2` dropped | `test_61` |

---

## 17. Remaining risks (non-blocking for re-audit)

- Queue depth check is **not** a SERIALIZABLE cap (can overshoot by concurrent inserts).
- Worker thread may live up to one poll interval after env flip; `worker_alive()` is false immediately.
- Attention bump on new delivery only; crash-retry after delivery does not double the unique row but could theoretically bump if `created` raced (unique insert prevents two creates).
- `expire_lease_now` is a test/ops helper (not a worker API).
- Poller still only emits host/circuit/inactivity (V6.1 foundation, not V6.2).
- FastAPI still 422s non-integer `limit` query params (framework); handler survives `limit=0` / large ints.

---

## 18. Final remediation verdict

**A. REMEDIATION COMPLETE — READY FOR FORENSIC RE-AUDIT**

Do **not** treat this as release-ready. Next step is an independent forensic pass against these fixes, then explicit human approval before commit/tag/push.
