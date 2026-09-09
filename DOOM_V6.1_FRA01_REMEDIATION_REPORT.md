# DOOM V6.1 — F-RA-01 Remediation Report

**Status:** working-tree only. **Not committed. Not tagged. Not pushed. Not release-ready.**

**Date:** 2026-09-09  
**Baseline HEAD:** `e933c212278f5bd7e05d9375c9f73a773960dcff`

**F-RA-01:** **FIXED** (PostgreSQL worker-path evidence via new tests T1–T12)

**Verdict:** **A. REMEDIATION COMPLETE — READY FOR FINAL FORENSIC RE-AUDIT**

---

## 1. Root cause

Attention (`may_inform`) ran **before** any lookup of an existing INFORM insight. A deny (budget / cooldown / busy / quiet hours) set `intervention = IGNORE` and the worker **ACK’d PROCESSED** while `proactive_deliveries` could still be empty for a matching OPEN/DELIVERED INFORM row.

Attention is a **new-notification** gate. It was incorrectly applied to an already-created **delivery obligation**.

---

## 2. Previous control flow

```
CLAIM
→ snapshot / significance
→ may_inform ──deny──→ ACK IGNORE   ← defect
→ insert_insight / deliver / ACK
```

No `find_valid_inform_insight` before IGNORE ACK.

---

## 3. New control flow

```
CLAIM
→ find_valid_inform_insight(owner, entity_id, signal_type)
     INFORM + NORMAL + OPEN|DELIVERED + valid_until > NOW()
→ IF found:
     IF delivery_exists(hud) → ACK (owner SQL)
     ELSE deliver_inform → ACK iff delivery row else RETRY/DEAD
     RETURN  (attention not consulted)
→ ELSE:
     snapshot / significance / may_inform
     IGNORE → ACK
     INFORM → insert + deliver → ACK iff durable
```

`may_inform` is **unchanged**. Budget/cooldown/busy/quiet still suppress **new** INFORM.

---

## 4. Files changed

- `proactive/worker.py` — obligation recovery first
- `proactive/store.py` — `find_valid_inform_insight`
- `proactive/schemas.py` — trivial extra credential substrings
- `test_v61_proactive_foundation.py` — T1–T12 worker/PG tests
- this report

---

## 5. Transaction behavior

Unchanged: claim SKIP LOCKED; ACK/RETRY/DEAD still `_OWNER_PRED`; `upsert_delivery` unique `(insight_id, channel)`.

---

## 6. Existing insight recovery

Lookup is PostgreSQL-only (not `_hud`). Match is `owner_id + entity_id + insight_type(=signal_type) + INFORM + NORMAL + unexpired`. Expired rows are ignored (normal IGNORE path may ACK).

---

## 7. Delivery durability

`INSIGHT_EXISTS != DELIVERY_EXISTS`. Recovery calls `deliver_inform` even when `may_inform` would deny. ACK PROCESSED only if a hud delivery row exists **or** this is a true IGNORE with **no** valid INFORM insight.

---

## 8. Attention interaction

| Situation | Attention | Outcome |
|-----------|-----------|---------|
| New FAILED task, budget exhausted, no insight | deny | IGNORE ACK, no insight (T6) |
| Existing INFORM, 0 deliveries, budget exhausted | deny for new keys | **still delivers** (T2) |
| Same + cooldown on dedupe key | deny | **still delivers** (T3) |
| Same + cognition PROCESSING | busy | **not PROCESSED+0** (T4; this implementation still delivers HUD persist) |

---

## 9. Worker ownership

No change to F-WRK-02 predicates. T10: Bob FENCED.

---

## 10. Crash windows

| Case | Behavior |
|------|----------|
| A none | normal path |
| B/H insight, 0 delivery | recovery delivers (T1, T11) |
| C/I insight + delivery | ACK, 1 row (T5, T12) |
| D/E crash after insight | same as B |
| F/G delivery then reclaim | T5/T12 |
| Delivery persist fail | no PROCESSED (T7, injected failure) |

---

## 11. Test matrix

| ID | Test | Result |
|----|------|--------|
| T1 | `test_110` worker delivers | pass |
| T2 | `test_111` budget + obligation | pass |
| T3 | `test_112` cooldown + obligation | pass |
| T4 | `test_113` busy I19 | pass |
| T5 | `test_114` existing delivery | pass |
| T6 | `test_115` new IGNORE | pass |
| T7 | `test_116` persist fail no ACK | pass |
| T8 | `test_117` success ACK | pass |
| T9 | `test_118` 8× `process_once` → 1 delivery | pass |
| T10 | `test_119` stale fence | pass |
| T11 | `test_120` lease crash recover | pass |
| T12 | `test_121` pre-ACK delivery unique | pass |

Critical assertion: `not (status==PROCESSED and deliveries==0)` in `_assert_i19`.

Suite: **76 passed**. OTP + routing: **68 passed**. Memory + governance: run with this remediation.

---

## 12. PostgreSQL evidence

T2: `may_inform` returns `budget`, then `process_once` still creates a `proactive_deliveries` row and PROCESSED. T6: same budget, **no** matching insight, PROCESSED with no INFORM row.

T9: `count_deliveries == 1` under concurrent workers.

---

## 13. Regression

- `test_v61_proactive_foundation.py`: 76 passed
- `test_v5374_observability.py` + `test_provider_routing_scenarios.py`: 68 passed
- `test_v51_memory.py` + `test_v5373_governance_transfer.py`: **80 passed**

`process_request` still covered by existing tests 68/69.

---

## 14. Remaining risks

- Valid INFORM lookup is per `(owner, entity_id, signal_type)` latest unexpired row — two templates for one entity could theoretically pick the newest only.
- Expired INFORM is not recovered (by design).
- T7 injects persist failure (not a fake success).
- Optional secret markers expanded slightly; colon/hyphen gaps may remain.
- Queue COUNT overshoot unchanged.

---

## 15. Final self-audit — ACK paths

Every `_ack_or_retry(..., True)` in `worker.py`:

| Line | Condition | Can be PROCESSED + 0 delivery for valid INFORM? |
|------|-----------|--------------------------------------------------|
| `_complete_inform_delivery` True | delivery exists or `deliver_inform` True | **No** |
| recovery, delivery already exists | `delivery_exists` | **No** |
| stale snapshot | only after recovery returned **False** | **No** (no valid INFORM) |
| IGNORE after attention | only after recovery **False** | **No** |

`_ack_or_retry(..., False)`: insert fail or missing delivery → not PROCESSED.

**I19:** existing undelivered INFORM cannot be ACK’d as IGNORE.

**F-RA-01: FIXED**
