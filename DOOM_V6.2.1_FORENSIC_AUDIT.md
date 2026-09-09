# DOOM V6.2.1 — Independent Forensic Audit

**Mode:** AUDIT ONLY — no source/test/schema changes, no commit/tag/push  
**Date:** 2026-09-09  
**Subject:** Uncommitted V6.2.1 emitters + fence vs frozen V6.1.0

**Final verdict:** **A. PASS — V6.2.1 ACCEPTABLE (NON-BLOCKING FINDINGS)**

This is not a V6.1.0 rerelease. It does **not** authorize commit. It does **not** start V6.2.2.

---

## 1. Baseline

| Check | Observed |
|--------|----------|
| Branch | `DOOM-V5.2` |
| HEAD | `993149988c7f0edb1fa7ee96d777dda69028396a` = `v6.1.0` |
| Tracked mods | `core/task_engine.py`, `memory/lifecycle.py`, `proactive/fence.py`, `proactive/schemas.py` (+57/−1) |
| New | `proactive/emitters.py`, `test_v62_emitters.py`, reports |
| Connectors/vault/PREPARE | **absent** |

HEAD was **not** moved. Implementation remains working-tree only.

---

## 2. What was independently proven

Production path (flag on, PostgreSQL):

- `TaskEngine.fail_task` → `proactive_signals` `TASK_STATUS`, `entity_id` = task id, payload **exactly** `{"status":"FAILED"}`
- `complete_task` → `COMPLETED`; no result/goal in payload
- `require_user_approval` → `WAITING_FOR_APPROVAL`; no tool name/args
- `MemoryLifecycleManager.archive` → `MEMORY_LIFECYCLE`, `event_type=ARCHIVED`, `event_id` present; memory **content** absent
- Flag off: task still FAILED / archive still succeeds; **zero** new rows for that entity
- `ingest_signal` raise inside `emit_task_status`: task still FAILED
- Duplicate `emit_task_status(same id, FAILED)` → **one** row (`idempotency_key` + `extra_idem=status`)
- Fence drops: ignore-previous, `[/DATA_ONLY]`, `system:`, zero-width-obfuscated ignore-previous; HTML stripped

V6.2.1 suite (this audit): **13 passed** when run as `test_v62_emitters.py`.

Isolated F-RA-01 / F-WRK-02 (`test_110`, `test_111`, `test_119`): **3 passed**. Worker `_recover_undelivered_inform` and `_OWNER_PRED` **unmodified**.

---

## 3. Scope

| Forbidden | Present? |
|-----------|----------|
| `proactive/connectors/` | No |
| vault / OAuth / HTTP clients | No |
| SUGGEST / PREPARE / ASK / LLM / ACT | No |
| New PG tables / ALTER | No |
| Second worker | No |
| `process_request` from emitters | No |
| `DOOMAutomation` | No |

`emit_experience` is **API-only** (no `project_engine` hook). Matches the implementation prompt.

`ingest.py` unchanged; `extra_idem` already existed.

---

## 4. Authority / safety

Emitters call only `ingest_signal`. TaskEngine wraps emit in try/except **after** `_save_checkpoint()`. Lifecycle emit is **post-commit** (and on idempotent replay). Failures cannot roll back memory/task state.

Payloads cannot carry `goal`, `command`, `error`, or memory `content` from the production hooks (kwargs swallowed as `**_ignored`).

Fence: sanitize then fail-closed on markers. `"system:"` is a new substring drop; production emitter tokens (`FAILED`, `ARCHIVED`) do not contain it. Residual: a future string field containing `system:` would drop the **entire** payload (fail-closed).

---

## 5. V6.1 integrity

| Invariant | Result |
|-----------|--------|
| F-RA-01 recovery code | **UNCHANGED** |
| F-WRK-02 owner predicate | **UNCHANGED** |
| INFORM / attention / delivery | **UNCHANGED** |
| `PROACTIVE_ENABLED` default false | **UNCHANGED** |
| Flag-off no ingest | **PASS** |

Significance: `MEMORY_LIFECYCLE` score **0.18** (below INFORM floor). New lifecycle signals will **not** auto-INFORM. `TASK_STATUS`+`FAILED` still can INFORM when the worker runs with flag on — **intended** once operators enable the flag.

---

## 6. Findings (non-blocking)

**F-V621-01 — Test-order / queue starvation (known V6.1 helper weakness, amplified)**  
`claim_batch(limit=8)` + `_prioritize_signal` = `NOW() - 1 second`. V6.2.1 tests with flag on leave **PENDING** `TASK_STATUS` rows. Running `test_v62_emitters.py` **then** `test_110` in one session can fail with **deliveries=0** while the seeded signal stays **PENDING** (not PROCESSED). Isolated `test_110` **passes**. This is **not** `PROCESSED ∧ zero delivery`. Classification: **test isolation / backlog**, not F-RA-01 reopen. Same class as the V6.1 release-gate note.

**F-V621-02 — Incomplete task-status coverage**  
`complete_task_partial`, `pause_task`, `cancel_task` do not emit. In scope for the V6.2.1 prompt (only complete/fail/approval). Gap for later, not a blocker.

**F-V621-03 — Incomplete lifecycle coverage**  
`memory/relationship_engine.py` inserts `memory_lifecycle_events` (consolidate/decompose) and does **not** call `_emit_proactive_lifecycle`. `transition_memory` / `supersede_memory` do. Documented coverage hole.

**F-V621-04 — `related_memory_id` on public `emit_lifecycle`**  
Production hooks do not pass it. Direct callers could put a 120-char string in payload; injection markers still fail-closed.

**F-V621-05 — HUD TestClient**  
`test_95`–`98` still fail on httpx 0.28 / Starlette 0.27. **Pre-existing environment.** Not V6.2.1.

---

## 7. Test quality

`test_v62_emitters.py`: no `or True`. Fail/complete/approval/lifecycle query PostgreSQL. Emitter-fault test patches `proactive.emitters.ingest_signal` while calling real `fail_task` (correct boundary). Flag on/off both exercised.

---

## 8. Independent test accounting

| Run | Result | Class |
|-----|--------|--------|
| `test_v62_emitters.py` | 13 passed | V6.2.1 |
| `test_v62` then `test_v61` (HUD 95–98 deselected) | **1 failed** (`test_110`) + 84 passed | F-V621-01 queue, **not** I19 |
| Isolated `test_110`, `test_111`, `test_119` | 3 passed | F-RA-01 / F-WRK-02 intact |
| HUD TestClient | not re-run; known env | pre-existing |

---

## 9. Git

No commit, tag, or push performed by this audit.

---

## 10. Recommendation

V6.2.1 is **acceptable to keep as uncommitted work** and **acceptable to commit when the owner explicitly requests it**. Before a combined pytest of v62+v61, either drain PENDING test rows or strengthen `_prioritize_signal` (out of this audit’s no-fix mandate).

Do **not** start V6.2.2 from this document.

---

## Final verdict

**A. PASS — V6.2.1 ACCEPTABLE (NON-BLOCKING FINDINGS)**
