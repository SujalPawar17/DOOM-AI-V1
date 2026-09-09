# DOOM V6.2.5 — Implementation Report

**Status:** IMPLEMENTED, **NOT RELEASED**  
**Date:** 2026-09-09  
**Branch:** `DOOM-V5.2`  
**Baseline:** v6.2.4 `26b8c169f8540f94490ff271a648225d0a8ebdec`  
**Git:** no commit, no tag, no push

This report does **not** claim V6.2.5 RELEASED. README remains v6.2.4 released / v6.2.5 planned.

---

## Implementation summary

ACTIVE V6.2.4 predictions are scored by pure `evaluate_suggest_worthiness(pred, now)` (no attention, no existing-row args). Worthy rows upsert into `world_suggestions`. OPEN rows are reused and may retry HUD delivery. DISMISSED never reopens. DELIVERED is not delivered twice. Attention (`may_suggest`) runs only immediately before NORMAL `deliver_suggest`. PRIVATE persists without HUD/WS. SENSITIVE is not persisted. Worker order: predict → suggest → `claim_batch`. INFORM is unchanged.

F-V624-01: `STALE_OPEN_COMMITMENT` + PRIVATE + fewer than two `evidence_ids` → IGNORE `STALE_WEAK_PRIVATE`.

---

## Exact files changed

**New**
- `proactive/suggest.py`
- `proactive/suggest_templates.py`
- `test_v625_suggest.py`
- `DOOM_V6.2.5_IMPLEMENTATION_REPORT.md` (this file)

**Modified**
- `DOOM_V6.2.5_IMPLEMENTATION_PLAN.md` (OPEN delivery retry; pure worthiness; contract header)
- `database/postgres_db.py` (additive DDL + `suggest_count`)
- `proactive/config.py`
- `config_example.txt`
- `proactive/store.py`
- `proactive/attention.py`
- `proactive/delivery.py` (`deliver_suggest` only; `deliver_inform` body unchanged)
- `proactive/worker.py`
- `dashboard/server.py` (GET suggestions, POST dismiss, status fields)
- `observability/schemas.py` (`suggestion_id`, `suggestion_type`)

**Not modified (locked)**
- `proactive/predict.py`, `evidence.py`, `temporal.py`, `significance.py`, `snapshot.py`
- `README` (still v6.2.4 released)
- connectors, TaskEngine, tools, memory, `proactive_insights` CHECK
- no V6.2.6 modules

---

## Exact schema changes

Additive only. No DROP. No ALTER on `proactive_insights`.

- `CREATE TABLE IF NOT EXISTS world_suggestions` (UNIQUE `(owner_id, fingerprint)`; status OPEN/DELIVERED/DISMISSED/EXPIRED/SUPERSEDED)
- indexes `(owner_id, status, valid_until)`, `(prediction_id)`
- `CREATE TABLE IF NOT EXISTS world_suggestion_deliveries` UNIQUE `(suggestion_id, channel)`
- `CREATE TABLE IF NOT EXISTS world_suggestion_events`
- `CREATE INDEX IF NOT EXISTS idx_wse_sug`
- `ALTER TABLE proactive_attention ADD COLUMN IF NOT EXISTS suggest_count INTEGER NOT NULL DEFAULT 0`

---

## Dedicated tests

`python test_v625_suggest.py` → **36/36 OK** (2.443s, PostgreSQL connected)

Includes OPEN delivery retry (`test_20`), dismiss never reopen (`test_17`), concurrent fingerprint (`test_13`), AST/no significance (`test_23`/`test_34`), memory count invariant (`test_24`).

HUD HTTP TestClient is not used (Starlette/httpx `app=` incompatibility, same class as F-V622-07). Dismiss 404 and INFORM-only insights are checked via store + async route call.

---

## Regression

| Suite | Result |
|-------|--------|
| `test_v624_evidence_prediction` | **39/40** — `test_16_conflict_emits` failed |
| `test_v623_email_commitments` | **11/11** |
| `test_v62_connectors` | **12/12** |
| `test_v62_emitters` | **13/13** |
| `test_v61_proactive_foundation` | **72 passed**, 4 errors on TestClient HUD tests **F-V622-07** (unchanged class) |
| `test_v532_transaction_engine` | **30/30** |

`test_16_conflict_emits` was re-run in isolation and still failed. V6.2.4 prediction code was not edited. Local `proactive_commitments` for `sujal` counted **112** OPEN-capable rows and `external_facts` **99**; eval lists are capped at `PREDICTION_EVAL_CAP=80` (`ORDER BY due_at LIMIT 80`), so a newly inserted conflict pair can fall outside the loader. This is an existing OWNER_ID data/cap interaction, not a SUGGEST logic change.

---

## Security / AST

`test_23` / `test_34` parse `proactive/suggest.py` and scan for `TaskEngine`, `task_engine`, `tool_registry`, `insert_insight`, `ingest_signal`, `model_router`, `evaluate_significance`, `SafeHttp`.

`suggest.py` does not import significance, worker, TaskEngine, tools, or HTTP clients. Delivery WS is best-effort after commit. Dismiss is owner-scoped SQL. Templates have zero placeholders and no forbidden action verbs (`test_26`). `safe_params` keys are allowlisted (`test_27`).

OTP: `suggestion_id` / `suggestion_type` allowed; `body` remains forbidden (`test_32`).

---

## Performance

Target (not a claim): &lt;100 ms at 50 candidates.

Measured in `test_35_candidate_cap_50` (`evaluate_world_suggestions` for 51 ACTIVE predictions, cap 50): **V625_PERF_MS ≈ 241 ms** on this host (also observed ~185 ms on an earlier run). **Does not meet the &lt;100 ms target** on this machine; no LLM/HTTP in the path. Work is local PG upserts + worthiness.

---

## Known findings

1. **F-V622-07** — FastAPI `TestClient(app)` keyword `app=` vs current httpx; V6.1 HUD tests 95–98 still error; V6.2.5 tests avoid TestClient.
2. **V6.2.4 `test_16`** — 39/40 locally; conflict eval vs `LIMIT 80` on a large `sujal` commitment/fact corpus (see regression).
3. **Suggest eval latency** — ~241 ms at 50 candidates vs &lt;100 ms target.
4. **Candidate cap 50** — `list_suggest_candidates` is per-owner LIMIT 50 ordered by `horizon_end`; older ACTIVE predictions can starve newer ones for that owner (same class as prediction eval cap). Tests isolate owners.
5. Schema init log during parallel test runs can show a transient PostgreSQL deadlock; `_create_tables` is IF NOT EXISTS and subsequent connects succeed.

---

## Confirmations

- V6.2.6 was **not** started (no PREPARE/ASK/LLM/ACT).
- No Git commit, tag, or push was performed for this slice.
- V6.2.4 tag/commit was not modified.
- `WorldSnapshot.suggestions` was not added.
- SUGGEST is not stored in `proactive_insights` and is not claimed via `claim_batch`.
