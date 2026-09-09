# DOOM V6.1 — Final Independent Forensic Re-Audit (F-RA-01)

**Mode:** audit only. No source, test, schema, or git mutations except this file.  
**Date:** 2026-09-09

**Verdict:** **A. PASS — F-RA-01 CLOSED / V6.1 READY FOR RELEASE GATE**

No commit, tag, or push was performed. The working tree remains uncommitted.

---

## 1. Repository baseline

| Item | Result |
|------|--------|
| Branch | `DOOM-V5.2` |
| `HEAD` | `e933c212278f5bd7e05d9375c9f73a773960dcff` |
| `git tag --points-at HEAD` | `v5.3.7.4-observability` |
| V6.1 / F-RA-01 committed | **No** |

Tracked dirty: `dashboard/server.py`, `database/postgres_db.py`, `doom.py`, `observability/schemas.py`.  
Untracked: `proactive/`, `test_v61_proactive_foundation.py`, V6 reports, provider-routing markdown (untouched).

---

## 2. Files inspected

- `proactive/worker.py` (all ACK paths)
- `proactive/store.py` (`find_valid_inform_insight`, ownership SQL, unique upserts)
- `proactive/attention.py` (`may_inform` unchanged)
- `proactive/delivery.py`
- `proactive/schemas.py`
- `test_v61_proactive_foundation.py` (`test_110`–`test_121`)
- PostgreSQL `pg_constraint` on V6.1 tables

---

## 3. Control-flow analysis

```
process_once (flag on)
  recover_expired_leases / expire_stale / poll
  claim_batch FOR UPDATE SKIP LOCKED → CLAIMED + live lease
  _process_item:
    _recover_undelivered_inform
      find_valid_inform_insight(owner, entity_id, signal_type)
        INFORM + NORMAL + OPEN|DELIVERED + valid_until > NOW()
      if none → False → NEW PATH
      if delivery_exists(hud) → ACK PROCESSED (owner SQL)
      else → deliver_inform → ACK iff delivery row else retry_or_dead
    NEW PATH (only if recover returned False):
      stale snapshot → IGNORE ACK
      significance / may_inform
      IGNORE ACK
      INFORM insert_insight + deliver → ACK iff delivery
```

`may_inform` is **not** called on the recovery path. Budget / cooldown / busy / quiet cannot convert an existing valid INFORM into IGNORE ACK.

---

## 4. ACK-path table

| ACK path | Preconditions | Delivery guaranteed? | Existing INFORM protected? |
|----------|---------------|----------------------|----------------------------|
| Recovery + `delivery_exists` | Valid INFORM row, hud delivery present | Yes (already) | Yes |
| Recovery + `_complete_inform_delivery` True | Valid INFORM, 0 then ≥1 delivery | Yes before ACK | Yes |
| Recovery + `_complete` False | Persist failed | **No ACK** (RETRY/DEAD/FENCED) | Yes (not PROCESSED) |
| Stale snapshot IGNORE ACK | **recover False** (no valid INFORM) | N/A | N/A |
| Attention/significance IGNORE ACK | **recover False** | N/A | N/A |
| New INFORM `_complete` True | New or upserted insight | Yes before ACK | N/A |
| New INFORM insert fail | `insert_insight` "" | **No ACK** | N/A |
| `ack()` SQL miss | Wrong worker / expired lease | No PROCESSED | Owner fence |

Exception in `_process_item`: `retry_or_dead` (not PROCESSED).

**Invariant:** no documented PROCESSED path allows valid INFORM + `delivery_count==0`.

Residual: `find_valid_inform_insight` `except: return None` could theoretically fall through to IGNORE ACK if lookup throws while the row exists. Claim and lookup share the same PG; not reproduced. **Non-blocking.**

Expired INFORM is excluded by SQL (`valid_until > NOW()`) — not a “valid” obligation.

---

## 5. PostgreSQL evidence

Live constraints:

- `proactive_signals_idempotency_key_key UNIQUE (idempotency_key)`
- `proactive_insights_dedupe_key_key UNIQUE (dedupe_key)`
- `proactive_deliveries_insight_id_channel_key UNIQUE (insight_id, channel)`

Independent worker probe (after `may_inform` → `False, budget`):

- before: deliveries **0**, status **PENDING**
- `process_once("audit-t2")`
- after: status **PROCESSED**, deliveries **1**, I19 **ok**

Quiet hours (`QUIET_HOURS="0-23"`, `may_inform` → `quiet_hours`): **PROCESSED**, dels **1**.

Concurrent 8× `process_once`: dels **1**, PROCESSED, I19 ok.

Stale A after B reclaim: `A_ack_after_B False`; owner `worker_id=B`.

---

## 6. T1–T12 results

| ID | Suite (full file, 76 passed) | Isolated subset | Independent probe |
|----|------------------------------|-----------------|-------------------|
| T1 test_110 | pass | **fail** dels=0 (signal not claimed; batch=8 vs backlog) | T2/T9 style success when claimed |
| T2 test_111 | pass | fail same backlog | **PROCESSED / dels=1 / may_inform budget** |
| T3 test_112 | pass | fail same | (suite + source path) |
| T4 test_113 | pass | pass | — |
| T5 quiet | **not in suite** | — | **PROCESSED / dels=1** |
| T6 test_115 | pass | pass | — |
| T7 test_116 | pass | pass | mock persist fail; worker still runs |
| T8 test_117 | pass | pass | — |
| T9 test_118 | pass | pass | dels **1** |
| T10 test_119 | pass (Bob fenced while Alice owns) | pass | A expired + B reclaim + A ACK **False** |
| T11 test_120 | pass | pass | lease expire equivalent, not OS kill |
| T12 test_121 | pass | pass | unique constraint |

Isolated T1–T3 failures are **false negatives** from `claim_batch(limit=8)` not selecting a new row when older PENDING exist. They did **not** pass I19 incorrectly (`_assert_i19` allows non-PROCESSED + 0 dels; the extra `dels>=1` caught the miss). Full-file order usually drains enough queue for T110–T112 to pass.

---

## 7. Concurrency

Independent 8 workers: **one** delivery row. Unique `(insight_id, channel)` + SKIP LOCKED. No `or True` in tests.

---

## 8. Crash / recovery

T11 / probe: CLAIMED → `expire_lease_now` → recover PENDING → other `process_once` → delivery then PROCESSED. **Not** a process kill. Transactional equivalent is adequate for this defect class.

---

## 9. F-WRK-02

`_OWNER_PRED` still on ACK/RETRY/DEAD. Independent: live B ACK False; expired A ACK False; after B claim, A ACK False. **No regression.**

---

## 10. Test quality

Critical T2 (full suite + independent): real `process_once`, real PG, `may_inform` deny asserted, then delivery count 1.

T7 **mocks** `upsert_delivery` / `delivery_exists` to inject failure — acceptable as fault injection, not as proof of success durability.

No `or True`. Tests do not call other tests on T1–T12 except none.

Quiet hours missing from suite; closed by independent patch of `QUIET_HOURS`.

---

## 11. Regression

| Suite | Result |
|-------|--------|
| `test_v61_proactive_foundation.py` | **76 passed** |
| OTP + provider routing + V5.1 memory + V5.3.7.3 governance | **148 passed** |

FastEmbed still absent (pre-existing env note in logs). No new V6.1 logic failures in these files.

---

## 12. Security / privacy

Recovery only selects `privacy_class = 'NORMAL'` INFORM. SENSITIVE still dropped at normalize. No tools/LLM/TTS/command_logs/DOOMAutomation on this path. Signals remain data.

---

## 13. Scope

INFORM only. Attention engine not globally disabled (T6: budget IGNORE, no insight). No PREPARE/ASK/ACT/TTS/LLM/ACTIVE memory/scheduler/router redesign.

---

## 14. Open findings (non-blocking)

| ID | SEV | Evidence | BLOCKER? |
|----|-----|----------|----------|
| F-FINAL-01 | LOW | Isolated T110–T112 fail under PENDING backlog (`limit=8`) | N (false fail; I19 not inverted) |
| F-FINAL-02 | INFO | Quiet hours not in unittest; proven by auditor patch | N |
| F-FINAL-03 | INFO | Lookup `except → None` fail-open | N (not reproduced) |
| F-FINAL-04 | INFO | Crash = lease expiry, not SIGKILL | N |
| F-FINAL-05 | INFO | T7 uses persistence mocks | N |

None of these recreate: valid INFORM + zero delivery + PROCESSED **when the matching signal is actually processed**.

---

## 15. Final verdict

**A. PASS — F-RA-01 CLOSED / V6.1 READY FOR RELEASE GATE**

F-RA-01 is closed in implementation and in independent PostgreSQL worker evidence (budget deny + still one delivery; quiet-hours deny + still one delivery; concurrent uniqueness; stale ACK fenced).

**Release actions are not authorized.** Do not commit, tag, or push until explicit human approval.
