# DOOM V6.3 — ACT Implementation Report

**Controlled real-world execution (ActionEngine)**

**Date:** 2026-09-10  
**Baseline:** `v6.2.8` `d86ebde13de11448dcb0f5e8688043e06d913169` on `DOOM-V5.2`  
**Status:** **V6.3 IMPLEMENTED — NOT RELEASED**

No commit, tag, or push. **APPROVED does not execute.** RUN enqueues only. Flags default **off**.

---

## 1. Implementation status

ActionEngine owns execution. Flow:

```
Action spec + action_hash
  → ACT-eligible ASK (rule_version v63.1)
  → APPROVED (authorization only → action APPROVED_NOT_RUN)
  → POST /actions/{id}/run (CSRF) → RUN_REQUESTED
  → worker claims RUN_REQUESTED only
  → preconditions → lease → typed writer → read-back
  → COMPLETED | FAILED | UNKNOWN_OUTCOME
```

Legacy v626.1 approvals have empty `action_hash` and are rejected for execution.

Worker never maps APPROVED → writer.

---

## 2. Files created

- `proactive/act.py`
- `proactive/act_spec.py`
- `proactive/act_engine.py`
- `proactive/act_verify.py`
- `proactive/act_policy.py`
- `proactive/writers/__init__.py`
- `proactive/writers/internal_ledger.py`
- `proactive/writers/calendar_hold.py`
- `test_v63_act.py`
- `DOOM_V6.3_ACT_IMPLEMENTATION_REPORT.md` (this file)

Architecture audit `DOOM_V6.3_ACT_ARCHITECTURE_AUDIT.md` was already untracked from the planning phase (not overwritten as product code).

---

## 3. Files modified

- `database/postgres_db.py` — additive ACT tables + `action_hash` on approvals
- `proactive/config.py` — ACT flags/budgets/lease
- `config_example.txt` — commented flags default off
- `proactive/store.py` — action persist/lease/idempotency/receipts; approve hook sets `APPROVED_NOT_RUN` only
- `proactive/ask.py` — additive v63.1 binding when a spec materializes
- `proactive/ask_decisions.py` — **new** `binding_hash_for_act` / `canonical_binding_string_v63`; **v626.1 `binding_hash_for` unchanged**
- `proactive/worker.py` — `evaluate_world_actions` after ASK expire, isolated `try/except`
- `dashboard/server.py` — GET action, POST run, POST cancel (enqueue only)
- `dashboard/ask_session.py` — ASK plane prefix `/api/proactive/actions`
- `observability/schemas.py` — allowlist `action_id`, `capability_id`, `attempt_n`, `outcome_code`, `idempotency_hit`, `lease_id`

**Not modified:** `http_safe.py`, TaskEngine, orchestrator, tools, memory, predict, suggest, prepare TYPE_MAP/worthiness, draft modules, README.

---

## 4. Schema (additive)

- `world_actions` — UNIQUE `(owner_id, action_hash)`, UNIQUE `(owner_id, idempotency_key)`, FK prep/approval
- `world_action_events` — INSERT-only
- `world_action_attempts`
- `world_action_verifications`
- `world_action_idempotency`
- `world_action_receipts` — ACT-0 PG receipt/hash (architecture verify target)
- `world_approval_requests.action_hash` — default `''` for legacy rows

Preparation status is not used as an execution state.

---

## 5. Capabilities implemented

| ID | Capability | Flag (default false) |
|----|------------|----------------------|
| ACT-0 | `INTERNAL_LEDGER_NOTE` | `PROACTIVE_ACT_INTERNAL_ENABLED` |
| ACT-1 | `CALENDAR_CREATE_HOLD` | `PROACTIVE_ACT_CALENDAR_HOLD_ENABLED` |

Master: `PROACTIVE_ACT_ENABLED=false`.

ACT-1 writer uses a **fixed** Calendar events URL template via `urllib` POST — **not** `SafeHttp.request`. Verify uses existing **GET** `SafeHttp`.

If RFC3339 start/end are absent from structured params, **materialize refuses**. Prepare allowlist still does not store those keys, so production ACT-1 specs are inserted only when params exist; tests inject specs.

---

## 6. ASK binding

- NONE / non-materialized MUTATION asks: **v626.1** formula unchanged (`canonical_binding_string` / `binding_hash_for`).
- Materialized ACT specs: **v63.1** includes `action_hash`.
- `_recompute` branches on `rule_version`.

No migration upgrades legacy APPROVED rows.

---

## 7. Test results

`test_v63_act.py` covers hash determinism, binding split, flags, AST firewall (no TaskEngine / `process_request` / `model_router` / draft / subprocess), no `/execute`, ACT-0 complete+idempotent RUN, CSRF/session RUN, UNKNOWN_OUTCOME no retry, ACT-1 mocked read-back success/mismatch, worker isolation, SENSITIVE refuse, calendar materialize refuse without RFC3339.

**Live unittest invocation in this implementation session hung during collection/import** (same overlapping CPython/Postgres pattern seen in V6.2.8 forensic F-V628-N02). `python -m py_compile` succeeded for all new/changed modules (`COMPILE_OK`, `STORE_OK`).

Forensic audit should re-run:

```
python -m unittest test_v63_act -v
python -m unittest test_v628_llm_draft -q
python test_v626_prepare_ask.py
```

Do not treat inherited F-V622-07 / F-V625-F01 / F-V628-I01 as V6.3 defects.

---

## 8. AST / security

ActionEngine and writers must not import TaskEngine, orchestrator, `model_router` (for execution), draft modules, or subprocess. Calendar writer does not import `SafeHttp` for POST. `http_safe.py` write policy unchanged vs HEAD.

HTTP handler `run_proactive_action` calls `request_run` only (enqueue).

---

## 9. Known findings / deviations

| ID | Notes |
|----|--------|
| F-V63-I01 | Extra table `world_action_receipts` for ACT-0 hash verify (justified) |
| F-V63-I02 | ACT-1 not auto-materialized from current `safe_params` allowlist (refuse-if-incomplete, as designed) |
| F-V63-I03 | Implementation-session live unittest hung; compile-checked; forensic must re-run |
| F-V63-I04 | Unrelated dirty v6.2.5/v6.2.6/v6.2.8 release-report SHA fill-ins **not** touched |
| Inherited | CORS `*`, F-V622-07, F-V625-F01, F-V628-I01 |

No EMAIL_SEND, GitHub write, shell, browser, V7, autonomous ACT, or generic SafeHttp POST.

---

## 10. Git status

HEAD remains `d86ebde` `release: DOOM v6.2.8`. V6.3 sources uncommitted. Unrelated markdown dirt left in place.

---

## 11. Confirmations

- **No commit**
- **No tag**
- **No push**
- **RELEASE NOT AUTHORIZED**
- **APPROVED ≠ EXECUTED**
- **Worker does not execute APPROVED**
- **ACT flags default false**
- **V6.2.8 behavior preserved when ACT is off**
- **V7 not implemented**

**V6.3 IMPLEMENTED — NOT RELEASED**
