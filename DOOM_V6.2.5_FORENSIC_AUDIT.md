# DOOM V6.2.5 — Independent Forensic Audit

**Mode:** AUDIT ONLY — no source, schema, test, README, commit, tag, or push changes  
**Date:** 2026-09-10  
**Auditor:** independent pass over working tree vs v6.2.4 baseline  
**Implementation status:** complete, **NOT RELEASED**

This document does **not** mark V6.2.5 RELEASED. It does not authorize remediation.

---

## Executive verdict

**PASS WITH NON-BLOCKING FINDINGS — RELEASE CONDITIONALLY RECOMMENDED**

V6.2.5 SUGGEST matches the architecture contract: ACTIVE v6.2.4 predictions → pure worthiness → `world_suggestions` → deterministic templates → NORMAL HUD/WS → STOP. Dedicated tests are **36/36**. Locked v6.2.4 prediction files are byte-identical to tag `v6.2.4`. Forbidden authority paths are absent from the SUGGEST layer.

Two items from the implementation report were re-investigated independently:

1. **`test_16_conflict_emits` (and same-class v6.2.4 OWNER_ID eval failures)** — **ENVIRONMENTAL / INHERITED**, **NON-BLOCKING** for V6.2.5 release, with evidence below. Not a V6.2.5 logic defect. Not caused by `predict.py` changes (there are none).
2. **~241–340 ms at 50 candidates vs &lt;100 ms target** — **real production-path latency**, **NON-BLOCKING** for functional release. Dominated by PostgreSQL round-trips, not worthiness/fingerprinting. Worker poll interval is 2.0s; SUGGEST is isolated from the request path.

**Release condition:** owner accepts (a) local `sujal` eval-cap starvation of v6.2.4 tests that insert into `OWNER_ID`, and (b) measured SUGGEST tick cost above the &lt;100 ms measurement target without a pre-release optimization.

V6.2.6 was not started.

---

## 1. Baseline verification

| Check | Result |
|-------|--------|
| Branch | `DOOM-V5.2` tracking `origin/DOOM-V5.2` |
| `HEAD` | `26b8c169f8540f94490ff271a648225d0a8ebdec` |
| Tag `v6.2.4` object | `269139ee88a592244e0dc52ca044eba120c1cc1b` |
| Tag `v6.2.4` peeled commit | `26b8c169f8540f94490ff271a648225d0a8ebdec` |
| Tag `v6.2.5` | **absent** |
| Branch vs origin | **up to date** (V6.2.5 exists only in the working tree) |

Byte compare vs commit `26b8c16` (CRLF-normalized):

| File | Result |
|------|--------|
| `proactive/predict.py` | **MATCH** (13024 bytes) |
| `proactive/evidence.py` | **MATCH** |
| `proactive/temporal.py` | **MATCH** |
| `proactive/significance.py` | **MATCH** |
| `proactive/snapshot.py` | **MATCH** |
| `README.md` | **MATCH** |

`git diff --stat` vs that commit is empty for `core/task_engine.py`, `memory/`, `tools/`, `proactive/connectors/`.

`proactive_insights.recommended_intervention` CHECK remains `('IGNORE','INFORM')` only. No `ALTER TABLE proactive_insights`. V6.2.5 DDL is `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` plus `ALTER TABLE proactive_attention ADD COLUMN IF NOT EXISTS suggest_count`. No `DROP TABLE`. Pre-existing `project_transfer_matrix` DROP CONSTRAINT statements are outside this slice (present in baseline schema init).

README still states **v6.2.4 RELEASED** and **V6.2.5 SUGGEST PLANNED / not started**.

`store.py` vs v6.2.4: `get_attention` now SELECTs `suggest_count`; new methods appended after `gmail_connector_health`. `list_eval_commitments` / `list_eval_cal_facts` / `upsert_world_prediction` hunks were **not** rewritten (`git diff` hunks at ~594 and ~1445 only).

`delivery.py`: `deliver_inform` body **unchanged**; `deliver_suggest` appended after it.

`worker.py`: isolated `evaluate_world_suggestions()` try/except inserted **after** prediction eval and **before** `claim_batch`.

---

## 2. Architecture verification

Observed pipeline in `proactive/suggest.py` `evaluate_world_suggestions`:

1. Triple flag AND (`PROACTIVE_ENABLED` ∧ `PROACTIVE_PREDICTION_ENABLED` ∧ `PROACTIVE_SUGGEST_ENABLED`)
2. `expire_world_predictions` (existing v6.2.4 store method, owner-scoped)
3. `sync_suggestion_lifecycle`
4. `list_suggest_candidates` (ACTIVE, non-SENSITIVE, LIMIT 50)
5. `evaluate_suggest_worthiness(pred, now)` — pure
6. Fingerprint state: DISMISSED/EXPIRED/SUPERSEDED skip; DELIVERED touch only; none → INSERT; OPEN → reuse `suggestion_id` + `touch_suggestion_evaluated`
7. PRIVATE → persist, no `deliver_suggest`
8. NORMAL OPEN → `may_suggest` then `deliver_suggest`

**Absent from SUGGEST layer** (`suggest.py`, `suggest_templates.py`, `deliver_suggest`): PREPARE, ASK, LLM, ACT, TaskEngine, tools, connector WRITE, memory writes, embeddings, `ingest_signal`, `insert_insight`, `evaluate_significance`, snapshot `suggestions`, second worker loop, SUGGEST `SIGNAL_TYPES`, SUGGEST via `claim_batch`.

`claim_batch` still runs after the suggest stage and still processes INFORM signals only (`worker.py` `_process_item` still uses `evaluate_significance` / `insert_insight` for that path only).

---

## 3. Pure worthiness

`inspect.signature(evaluate_suggest_worthiness)` = `(pred, now) -> Tuple[str, str]`.

Function body: dict field reads, numeric compares, `_evidence_ids` from the in-memory prediction dict. **No** store, HTTP, filesystem, tools, models, or `may_suggest`.

`may_suggest` is invoked only after a live OPEN NORMAL row is confirmed, immediately before `deliver_suggest`.

---

## 4. OPEN delivery retry

Code + `test_v625_suggest.py` (this session **36/36**):

| Rule | Evidence |
|------|----------|
| OPEN reused | `est == "OPEN"` → same `suggestion_id`, `touch_suggestion_evaluated`; not treated as IGNORE |
| No duplicate row | UNIQUE `(owner_id, fingerprint)`; `test_12`, `test_13` |
| Later-tick delivery | `test_20`: `may_suggest` false → OPEN, no delivery row; next eval → DELIVERED |
| DISMISSED never reopens | ON CONFLICT UPDATE `WHERE status IN ('OPEN','DELIVERED')`; DISMISSED skip in evaluator; `test_17` |
| DELIVERED not delivered twice | evaluator `continue` after touch; unique `(suggestion_id, channel)`; `test_14` |
| Budget/cooldown defer OPEN | `may_suggest` false leaves OPEN; `test_15`, `test_30`; `suggest_count` not incremented |

Crash between suggestion INSERT commit and delivery commit leaves OPEN; next tick is the `test_20` path. Plan §19 recovery matches. Insert and delivery are **two** transactions (not a single combined INSERT+DELIVER txn). That is a spec tension with plan §10 “one store transaction preferred,” but recovery is implemented. Classified **NON-BLOCKING**.

WS send in `deliver_suggest` runs **after** `persist_normal_suggestion_delivery` commit. WS failure cannot roll back DELIVERED.

---

## 5. Privacy verification

| Class | Persist | HUD/GET/WS |
|-------|---------|------------|
| NORMAL | yes | yes (`list_hud_suggestions` `privacy_class='NORMAL'`) |
| PRIVATE | yes | no (`test_11`) |
| SENSITIVE | no (loader excludes; worthiness `PRIVACY_BLOCK`) | no (`test_10`) |

Suggestion `provenance` stores `prediction_id` / `prediction_fingerprint` only. `safe_params` keys ⊆ `{risk_class, horizon_hours, prediction_type, claim_code}` (`test_27` injected subject/url/body/snippet — absent from params). Templates are fixed sentences, no placeholders, no forbidden action verbs (`test_26`). OTP allowlist adds `suggestion_id` / `suggestion_type`; `body` remains forbidden (`test_32`).

---

## 6. Database verification

- Additive `world_suggestions`, `world_suggestion_deliveries`, `world_suggestion_events`
- UNIQUE `(owner_id, fingerprint)` and UNIQUE `(suggestion_id, channel)`
- Owner predicates on candidate list, get-by-fingerprint, upsert, lifecycle, HUD list, dismiss, delivery UPDATE
- Dismiss: `WHERE suggestion_id=%s AND owner_id=%s AND status IN ('OPEN','DELIVERED')` → 0 rows = 404 for other owner (`test_33`)
- Lifecycle: prediction EXPIRED / `valid_until` past → suggestion EXPIRED; SUPERSEDED/INVALIDATED → SUPERSEDED (`test_18`, `test_19`)
- Delivery FK ON DELETE CASCADE; insert only with valid `suggestion_id`
- DISMISSED conflict: UPDATE WHERE excludes DISMISSED; RETURNING empty → SELECT id, evaluator skips

`upsert_suggestion_delivery` still calls `persist_normal_suggestion_delivery(suggestion_id, OWNER_ID)` (hard-coded owner). Production path uses `persist_normal_suggestion_delivery(sid, owner)` from `deliver_suggest`. Dead wrapper; **NON-BLOCKING**.

---

## 7. F-V624-01

In `evaluate_suggest_worthiness`, before type×risk:

`STALE_OPEN_COMMITMENT` ∧ `PRIVATE` ∧ `len(evidence_ids) < 2` → IGNORE `STALE_WEAK_PRIVATE`.

`test_28` passed. `predict.py` untouched.

---

## 8. INFORM isolation

- `git diff` of `deliver_inform`: no line changes
- `GET /api/proactive/insights` still filters `intervention == INFORM` (`test_25`)
- Insights CHECK still IGNORE/INFORM only
- `SIGNAL_TYPES` has no SUGGEST/PREDICTION_EVAL (`test_29`; `schemas.py` unchanged set)
- `claim_batch` after suggest stage; suggest does not enqueue signals

`max_intervention` is SUGGEST only when all three flags are on; otherwise INFORM if proactive on, else NONE. INFORM processing is not disabled.

---

## 9. API / HUD / WS

- `GET /api/proactive/suggestions`: empty unless triple flags; SQL NORMAL + OPEN/DELIVERED
- WS card `type` = `proactive_suggestion`, `tts` false, not pushed into INFORM `_hud`
- `WorldSnapshot` has no `suggestions` field (`test_31`; `snapshot.py` MATCH)
- TTS: `TTS_PROACTIVE_ALLOWED` still false; both deliver functions abort if true

---

## 10. Security / AST

Independent string scan of `proactive/suggest.py`: `TaskEngine`, `task_engine`, `tool_registry`, `SafeHttp`, `subprocess`, `process_request`, `model_router`, `insert_insight`, `ingest_signal`, `evaluate_significance`, `PREPARE`, `connectors` — **all absent**.

`suggest_templates.py` / `deliver_suggest` likewise have no TaskEngine/tools/HTTP clients. Dashboard dismiss does not call tools. Templates have no interpolation of untrusted text (`render_suggest` ignores `params` for prose).

`test_23` / `test_34` passed this session.

---

## 11. Test validation (this audit)

| Suite | This forensic session | Notes |
|-------|------------------------|-------|
| `test_v625_suggest.py` | **36/36 OK** (2.288s; `V625_PERF_MS=260.87`) | Re-run |
| Isolated replica of `test_16` on a unique owner | **PASS** (1 conflict row) | See §12 |
| `test_v624_evidence_prediction` on `OWNER_ID=sujal` | **FAIL** `test_16` (reproduced); under parallel DB load also `test_23` / `test_36` | Same cap class; see §12 |
| `test_v61_proactive_foundation` | 72 passed + **4 errors** tests 95–98 | **F-V622-07** `TestClient`/`app=` vs httpx |
| `test_v623` / connectors / emitters / `test_v532` | Sequential **11/11, 12/12, 13/13, 30/30** at implementation time | Overlapping forensic re-run hit PostgreSQL schema/lock stall; not re-attributed to SUGGEST logic |

Tests were **not** modified.

---

## 12. V6.2.4 `test_16_conflict_emits` — forensic analysis

### A. Is v6.2.4 source unchanged?

**Yes.** `proactive/predict.py` MATCH vs `26b8c16`. Conflict rule `_rule_conflict` is the v6.2.4 2h–14d calendar mismatch emitter. Loaders `list_eval_commitments` / `list_eval_cal_facts` were not edited.

### B. Is the failing test reproducible?

**Yes.** Isolation re-run of `test_16_conflict_emits` failed with `rows == []`. Full suite also failed `test_16` in this session.

### C. Does it pass on a clean/minimal dataset?

**Yes.** Forensic replica used a unique `owner_id` (`forensic-…`), one Gmail MEETING commitment at `now+86400`, one `CAL_EVENT` at `due+8h`, same project, then `evaluate_world_predictions(iso)`. Result: **emitted ≥1**, **one ACTIVE `CAL_VS_COMMITMENT_CONFLICT`** whose `subject_key` matched that commitment.

### D. Is the failure caused by existing owner data?

**Yes, with numbers (live DB, owner `sujal`):**

| Metric | Value |
|--------|--------|
| `PREDICTION_EVAL_CAP` | **80** (v6.2.4 constant) |
| `proactive_commitments` total | 112 |
| Commitments in eval window (OPEN, due in −7d…+72h) | **101** |
| Those with `due_at < now+24h` | **94** |
| `list_eval_commitments(sujal, 80)` length | **80** |
| CAL_EVENT in eval window | 71 (≤80; **not** starved) |
| `list_eval_cal_facts` length | 71 |

Loader SQL: `ORDER BY c.due_at … LIMIT 80`. `test_16` inserts `due_at = now+86400` for **`OWNER_ID` (`sujal`)**. Rank of that due is **worse than 80** (94 sooner dues already in-window). The new commitment is **not in `commit_ev`**, so `_rule_conflict` never sees it. Calendar fact is loaded; pairing still fails without the commitment.

`test_23` (`due+5000`) and `test_36` (`due+3600`) failing in a later crowded run are the **same mechanism**: eval never considers the new `sujal` row, so no `world_predictions` row → empty SELECT. They are not a new V6.2.5 defect class.

### E. Does V6.2.5 introduce the failure?

**No.** `test_16` calls `evaluate_world_predictions()` only (not `evaluate_world_suggestions`). Suggest import cannot change the cap SQL. Isolated-owner replica using the **same** `predict.py` succeeds.

### F. Does V6.2.5 depend on the behavior covered by `test_16`?

SUGGEST will emit `CONSIDER_RECONCILE_TIME` only if a `CAL_VS_COMMITMENT_CONFLICT` **prediction row** exists. If v6.2.4 eval skipped that pair because of the cap, SUGGEST correctly stays silent. V6.2.5 does not implement conflict detection itself. No SUGGEST test requires `test_16` to pass on a polluted `sujal` corpus; type mapping is covered by inserting predictions directly (`test_06`).

### G. Would fixing the cap require changing v6.2.4?

**Yes**, if the fix is product behavior: raise `PREDICTION_EVAL_CAP`, paginate the loader, or change `ORDER BY`. That is v6.2.4 prediction-scope, **forbidden** in this slice. Test-only isolation (unique owner) would be a **test** change, also out of scope here.

### Classification

**ENVIRONMENTAL / INHERITED — NON-BLOCKING** for V6.2.5.

Not classified non-blocking because the implementation report said so. Classified after: unchanged `predict.py`, reproduced `sujal` failure, quantified LIMIT 80 starvation, and a passing isolated replica.

---

## 13. Performance forensics

Target (plan §18): measure vs **&lt;100 ms at 50 candidates** — a measurement target, not a silent pass.

### What `test_35` measures

Timer wraps **only** `evaluate_world_suggestions(owner)` after 51 predictions already exist. Fixture setup is excluded. This session: **`V625_PERF_MS=260.87`**. Implementation report: ~241 ms / ~185 ms.

### Independent decompose (50 new NORMAL DEADLINE predictions, unique owner)

Monkeypatch timings on store methods (first instrumented run, includes extra wrapper cost):

| Component | Time | Calls |
|-----------|------|-------|
| Worthiness | **0.21 ms** | 50 |
| Fingerprint SHA | **1.03 ms** | 50 |
| `list_suggest_candidates` | 2.6 ms | 1 |
| `sync_suggestion_lifecycle` | 12.9 ms | 1 |
| `expire_world_predictions` | 0.7 ms | 1 |
| `get_suggestion_by_fingerprint` | **85.6 ms** | **100** (twice per candidate) |
| `upsert_world_suggestion` | **111.5 ms** | 50 |
| `persist_normal_suggestion_delivery` | 7.1 ms | **4** (daily budget) |
| First-import residual (dashboard/WS path) | ~1.3 s in that wrapped run | n/a |

After **pre-importing** `dashboard.server` (production worker would already have it loaded if HUD is up):

| Run | ms | created |
|-----|----|---------|
| First eval (50 inserts + 4 HUD deliveries) | **340.27** | 50 |
| Second eval (OPEN retry / DELIVERED skip, no new rows) | **255.10** | 0 |

### Production-path conclusion

**~250–340 ms is actual evaluation cost on this host**, not fixture setup. CPU worthiness is negligible. Cost is **serial PostgreSQL round-trips** (especially double fingerprint SELECT + per-row upsert). Budget limits HUD writes to 4/day, but upserts still run for all 50 worthy candidates.

`POLL_INTERVAL_SEC` default **2.0**. 340 ms is ~17% of a worker tick and is **off the voice/request path**. No LLM/HTTP in the SUGGEST path.

### Classification

**NON-BLOCKING** known performance finding.

Not accepted merely because the plan called &lt;100 ms a target. Accepted because: (1) functional gates pass, (2) latency is local DB not an accidental test artifact, (3) it remains well under the 2s worker poll, (4) optimization would be a post-audit, owner-scoped change (batch SQL / drop duplicate `get_fp`) — **not done**.

---

## 14. Concurrency / recovery

- Concurrent same fingerprint → one row (`test_13`)
- OPEN without delivery recovers (`test_20`)
- Delivery UNIQUE + OPEN→DELIVERED in one `persist_normal_suggestion_delivery` commit
- WS after commit
- Expired/superseded predictions move suggestions (`test_18`, `test_19`)

---

## 15. Memory invariant

`suggest.py` has no `memory_records` / experiences / embeddings writes. `test_24` memory count invariant passed. `memory/` tree unmoved vs v6.2.4. `test_v532` was **30/30** on the implementation sequential run.

---

## 16. Feature flags

`is_suggest_enabled()` → `_bool_env("PROACTIVE_SUGGEST_ENABLED", False)`.  
`config_example.txt`: `# PROACTIVE_SUGGEST_ENABLED=false`.

Eval and GET/dismiss require **all three** flags. `test_01`/`test_02`/`test_03` passed.

---

## 17. Git integrity

- No V6.2.5 commit (`HEAD` still v6.2.4)
- No `v6.2.5` tag
- No push of this slice (`origin/DOOM-V5.2` == `26b8c16`)
- `v6.2.4` tag object unchanged
- Working tree: modified tracked files listed in the implementation report plus untracked `proactive/suggest.py`, `suggest_templates.py`, `test_v625_suggest.py`, V6.2.5 docs, and unrelated leftover markdown

---

## Blocking vs non-blocking findings

### Blocking

**None** for V6.2.5 SUGGEST correctness, privacy, authority isolation, or baseline integrity.

### Non-blocking

| ID | Finding | Class |
|----|---------|--------|
| F-V625-F01 | `sujal` v6.2.4 tests that insert into `OWNER_ID` starve at `PREDICTION_EVAL_CAP=80` (`test_16` always; `test_23`/`test_36` when window is full) | ENVIRONMENTAL / INHERITED |
| F-V625-F02 | ~250–340 ms/50-candidate production-path eval vs &lt;100 ms target; DB round-trips | PERFORMANCE |
| F-V622-07 | Starlette/httpx `TestClient(app=)` HUD tests 95–98 | INHERITED |
| F-V625-F03 | Suggestion INSERT and HUD delivery are separate commits; OPEN retry covers crash | SPEC TENSION, recovered |
| F-V625-F04 | Duplicate `get_suggestion_by_fingerprint` per candidate (latency, not correctness) | PERFORMANCE |
| F-V625-F05 | Parallel test processes can stall on PostgreSQL schema/locks | ENVIRONMENTAL |

---

## Explicit release recommendation

**RELEASE CONDITIONALLY RECOMMENDED.**

Safe to proceed to **owner review** and a later owner-requested release (commit / annotated tag / push) **without** changing V6.2.5 code, **without** changing `predict.py`, and **without** treating V6.2.5 as RELEASED until that release procedure runs.

Conditions to accept in owner review:

1. F-V625-F01 remains a local-data / v6.2.4 cap issue, not a SUGGEST blocker.  
2. F-V625-F02 remains an accepted latency finding (optimize only if owner later asks).  
3. README stays v6.2.4 released until the release prompt.

**Do not** classify this audit as a release. Next step is owner review, then release verification if authorized.

---

V6.2.5 FORENSIC AUDIT:  
**PASS WITH NON-BLOCKING FINDINGS — RELEASE CONDITIONALLY RECOMMENDED**

V6.2.5 RELEASE:  
**NOT PERFORMED**

V6.2.6:  
**NOT STARTED**

GIT:  
**NO COMMIT / NO TAG / NO PUSH**
