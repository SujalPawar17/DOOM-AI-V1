# DOOM V6.2.4 — Independent Forensic Audit

**Mode:** AUDIT ONLY — no source, schema, test, README, commit, tag, or push except this report file  
**Auditor role:** Independent forensic auditor (implementation report treated as claims, not proof)  
**Date:** 2026-09-09  
**Subject:** Uncommitted V6.2.4 evidence + deterministic prediction vs frozen `v6.2.3`

**Final verdict:** **B — PASS WITH NON-BLOCKING FINDINGS**

**V6.2.4 RELEASE AUTHORIZED: YES**

This document is not a release. It does **not** perform commit, annotated tag, or push. It does **not** start V6.2.5.

---

## 1. Audit date / role / baseline

| Field | Observed |
|--------|----------|
| Audit date | 2026-09-09 |
| Role | Independent forensic auditor |
| Branch | `DOOM-V5.2` |
| HEAD | `78b04338647925f4400439f666f4487163e14e3a` (README status commit after tag) |
| Immutable release | annotated **`v6.2.3`** → commit **`391f88f3d54fde3f54e41f75cd6bee7062bfc56d`** |
| Tag object | `7aed4643aa93b5fcc37f82590449b355de33478e` |
| V6.2.4 commit | **none** |
| V6.2.4 tag | **absent** |
| V6.2.4 push | **none** |

HEAD was **not** moved. `v6.2.3` was **not** retagged.

---

## 2. Scope examined

Intended slice:

```
READ → cite world_evidence → independence → temporal gates
     → five deterministic predictions → WorldSnapshot.predictions → STOP
```

In scope: `world_evidence`, `world_predictions`, cite/independence, C/K, five rule types, worker eval placement, snapshot split, flags, OTP keys, tests.

Out of scope / must remain absent: SUGGEST, PREPARE, ASK, LLM, ACT, connector WRITE, TaskEngine create, `memory_records` / `memory_evidence` writes, HUD prediction cards, `PREDICTION_EVAL` on `proactive_signals`.

Mandatory highs from architecture: **H1** new table not `memory_evidence`; **H2** no INFORM from eval; **H3** email N=1 `C_max=0.55` vs emit floor 0.70; **H4** direct eval after `poll_connectors`, not outbox.

---

## 3. Files examined

**Working-tree application diffs vs HEAD (7 tracked files, +497/−1):**

| Path | Role |
|------|------|
| `database/postgres_db.py` | Additive world_* DDL (+89) |
| `proactive/store.py` | Eval loaders + world_* CRUD (+374) |
| `proactive/config.py` | Prediction flag + C constants |
| `proactive/snapshot.py` | `calendar_facts` + `predictions` |
| `proactive/worker.py` | Isolated `evaluate_world_predictions()` after poll |
| `observability/schemas.py` | Allow prediction OTP keys |
| `config_example.txt` | Commented `PROACTIVE_PREDICTION_ENABLED` |

**New (untracked) implementation:**

| Path | Role |
|------|------|
| `proactive/temporal.py` | `require_source_time`; horizon; risk K |
| `proactive/evidence.py` | Cite + `independence_key` |
| `proactive/predict.py` | `compute_c` + five rules |
| `test_v624_evidence_prediction.py` | 40 tests |

**Empty `git diff` vs HEAD (verified by path name-only):** `core/`, `memory/`, `models/`, `tools/`, `dashboard/`, `proactive/connectors/`, `proactive/poller.py`, `proactive/significance.py`, `proactive/ingest.py`, `proactive/delivery.py`.

**Deleted files:** none.

Implementation report, architecture audit, and leftover provider-routing markdown were **not** treated as runtime proof.

---

## 4. Database objects examined

Live PostgreSQL `Doom` on `localhost:5432`.

`world_evidence`: PK `evidence_id`; UNIQUE `idempotency_key`; FK **`project_id → projects` ON DELETE SET NULL only**. **No** FK to `memory_records` or `memory_evidence` (`information_schema` join empty). Columns match the plan’s trimmed set (no `MEMORY` in `source_kind` CHECK). `observed_at` DEFAULT NOW().

`world_predictions`: UNIQUE `(owner_id, fingerprint)`; CHECK closed set of five `prediction_type` values; `probability` nullable (inserts use SQL `NULL`); `risk_class` / `privacy_class` / `status` CHECKed.

`world_prediction_evidence`: PK `(prediction_id, evidence_id)`; CASCADE FKs; role SUPPORTING|CONFLICTING.

`world_prediction_events`: append-only status log.

DDL is `CREATE TABLE/INDEX IF NOT EXISTS` only. No DROP. Pre-existing `memory_evidence` block later in `ensure_schema` is unchanged V5 schema.

---

## 5. H1–H4 results

| ID | Rule | Evidence | Result |
|----|------|----------|--------|
| H1 | Do not reuse `memory_evidence` | Separate `world_evidence`; no memory FK; `evidence.py` does not import `MemoryEvolutionEngine` | **PASS** |
| H2 | Do not INFORM from predictions | `predict.py` has no `insert_insight` / `ingest_signal` / `deliver_inform`; worker INFORM path unchanged after eval | **PASS** |
| H3 | Email N=1 C≤0.55 vs floor 0.70 | `compute_c` caps `gmail_extract`+N=1 at 0.55; `_emit` ABSTAIN `LOW_CONFIDENCE` for email_single except `OPEN_REVIEW_AGING` | **PASS** |
| H4 | No INFORM outbox for eval | Direct call in `process_once` after `poll_connectors`; `PREDICTION_EVAL` absent from `SIGNAL_TYPES` | **PASS** |

---

## 6. Evidence / independence results

Cite functions skip empty `commitment_id` / `fact_id` / `task_id`. Payload/`provenance` are ids and hashes. Independence canonical string is hashed to 48 hex chars. Tests: same message key stable; different message ids differ; ten evals do not raise C (poll ≠ corroboration).

`ON CONFLICT (idempotency_key)` is a no-op (`source_id = source_id`); **strength is not incremented** on re-cite.

**Gap:** a new `content_sha256` for the same `source_id` creates a **second ACTIVE** row. There is no SUPERSEDE of the prior cite (**F-V624-06**).

**Gap:** `_rule_stale` attaches the first same-`project_id` calendar fact in the eval window as SUPPORTING **without** requiring temporal proximity to `due_at`. That can turn a gmail-only stale commitment into N=2 and bypass H3’s email-single abstain (**F-V624-01**). Deadline extras correctly require \|start−due\| ≤ 2h.

---

## 7. Confidence / risk results

Formula on disk matches the plan: `min(0.95, max(R·S)+0.10·(N−1))` with email-single `min(0.55, …)`. Emit floor default 0.70. Risk K is horizon/conflict/stale, independent of C. `probability` column left NULL.

**Deviation:** `cite` stores GitHub review **S=1.00** (plan table S=0.70). With R=0.70 that yields C=0.70, enough to clear the 0.68 aging floor. Honest over-strength vs the written S table (**F-V624-02**). Without it, OPEN_REVIEW_AGING would never emit.

`require_source_time` rejects missing/non-positive timestamps; `_rule_deadline` does not substitute `now()` as due.

`STALE_EVIDENCE` (`valid_until` passed) is **not** applied at emit time (**F-V624-07**). Loaders already window calendar/review/commitment dues.

---

## 8. Five types

| Type | Live behavior vs architecture |
|------|-------------------------------|
| `DEADLINE_HORIZON` | due ∈ (now, now+72h]; email-only ABSTAIN; optional cal within 2h | **PASS** |
| `STALE_OPEN_COMMITMENT` | due < now; email-only ABSTAIN unless extras; extras too loose | **PASS** with F-V624-01 |
| `CAL_VS_COMMITMENT_CONFLICT` | same project; gap >2h and ≤14d; roles SUPPORTING+CONFLICTING | **PASS** |
| `TASK_BLOCKED_NEAR_DEADLINE` | blocked statuses; **requires non-empty** `artifacts.project_id` matching partner | **PASS** with F-V624-05 (safer than empty-empty pairing) |
| `OPEN_REVIEW_AGING` | GH_REVIEW/GH_PR or REVIEW_REQUIRED; age ≥7d; floor 0.68 | **PASS** with F-V624-02 |

Architecture allowed empty+empty personal match. Implementation **refuses empty task project** so a global `task_checkpoints` dump cannot attach every paused task to every personal due. That is a **conservative deviation**, not a boundary break.

`list_eval_blocked_tasks` has **no owner_id** (table has none). Isolation is `artifacts.project_id` plus prediction `owner_id=sujal` (**F-V624-08**).

Plan `MAX_ACTIVE_PREDICTIONS_PER_OWNER=50` is enforced only as snapshot `LIMIT 50`, not as an insert cap (**F-V624-03**).

`rule_id` stored is the type name (`DEADLINE_HORIZON`), not `v624.*` (**F-V624-09**). `rule_version` is `v624.1`.

---

## 9. Worker / INFORM / HUD

```
process_once
  → recover / expire / poll_internal / poll_connectors
  → try: evaluate_world_predictions()
  → claim_batch (INFORM, limit 8, unchanged)
```

Eval exceptions are swallowed in both `predict.evaluate_world_predictions` and `worker.process_once`. INFORM recovery still runs.

Dashboard `server.py`: **no** `world_predictions`, **no** `/api/proactive/predictions`. Dashboard does not import `WorldSnapshot`. HUD cards remain INFORM insights.

`list_active_predictions` excludes `SENSITIVE` but **includes PRIVATE** in the in-process snapshot (**F-V624-04**). Architecture asked not to copy PRIVATE into a structure later serialized to HUD. Current HUD does not serialize this snapshot; residual if a future API dumps `WorldSnapshot` wholesale.

---

## 10. WorldSnapshot

`commitments` = `list_open_commitments` only (`record_kind=COMMITMENT`). Calendar NORMAL facts go to `calendar_facts` (`record_kind=FACT`). Predictions are a third list (`record_kind=PREDICTION`). **F-V623-02 mix is fixed** in the snapshot builder.

TTL cache unchanged; `invalidate_snapshot()` after eval. Non-authoritative.

---

## 11. Memory / LLM / tools

`test_27` memory_records count invariant. Grep of `predict.py` / `evidence.py`: no MemoryManager, no embeddings, no `model_router`, no CognitiveBridge. `core/` diff empty. Worker still INFORM-only CHECK on insights.

---

## 12. Observability

`proactive.prediction.evaluated` attrs: `prediction_id`, `rule_id`, `evidence_n`, `confidence_bucket`, `risk_class`, `abstain_reason`, `reason`. Those keys are on `ALLOWED_ATTR_KEYS`. `body` remains forbidden. No subject/snippet in predict emit.

---

## 13. Concurrency / transactions

Prediction insert + link replace + event insert share one cursor and one `commit`; exception `rollback`. Fingerprint UNIQUE. `generation` increments on conflict **without** `WHERE generation = EXCLUDED.generation` (**F-V624-10**). Events append on every eval with no retention (**F-V624-11**).

---

## 14. Flags

`is_prediction_enabled()` → `_bool_env(..., False)`. Eval also requires `is_proactive_enabled()`. `test_08`/`test_09` cover default off / zero writes when off. `config_example.txt` documents the flag commented.

---

## 15. Production path

Eval is imported from `worker.py` and invoked on the existing v61 loop. Not a dead module. No second daemon. No SafeHttp change in this slice (connectors 12/12).

---

## 16. README

Working-tree README still marks V6.2.4 **PLANNED** and `v6.2.3` as current official release. That is **documentation lag**, not oversell. Acceptable until an owner-requested release commit.

---

## 17. Tests this audit executed

| Suite | Result | Classification |
|-------|--------|----------------|
| `test_v624_evidence_prediction` | **40/40** (re-run this audit) | V6.2.4 |
| `test_v623_email_commitments` | **11/11** (implementation session; not re-litigated) | V6.2.3 intact |
| `test_v62_connectors` | **12/12** | V6.2.2 intact |
| `test_v62_emitters` | **13/13** | V6.2.1 intact |
| `test_v61_proactive_foundation` | **72 passed**; HUD 95–98 **4 errors** TestClient/`app=` | **F-V622-07** inherited |
| `test_v532_transaction_engine` | **30/30** | memory engine intact |

Implementation-session regression numbers were cross-checked against this audit’s 40/40 re-run and empty diffs on those suites’ product files.

---

## 18. Acceptance matrix

| Gate | Requirement | Result |
|------|-------------|--------|
| G1 | New `world_evidence` not `memory_evidence` | **PASS** |
| G2 | No memory writes from eval | **PASS** |
| G3 | No INFORM/insight/signal from eval | **PASS** |
| G4 | No `PREDICTION_EVAL` queue | **PASS** |
| G5 | Direct eval after poll, exception-isolated | **PASS** |
| G6 | Flags default off | **PASS** |
| G7 | Email N=1 cannot emit DEADLINE/STALE alone | **PASS** (STALE extras: F-V624-01) |
| G8 | Missing source time ABSTAIN | **PASS** |
| G9 | Closed five types only (PG CHECK) | **PASS** |
| G10 | Snapshot splits FACT/COMMITMENT/PREDICTION | **PASS** |
| G11 | No HUD prediction API | **PASS** |
| G12 | OTP no body/snippet | **PASS** |
| G13 | No LLM / model_router in predict | **PASS** |
| G14 | No SUGGEST/PREPARE/ASK/ACT | **PASS** |
| G15 | Additive schema, no DROP | **PASS** |
| G16 | `v6.2.3` tag commit unchanged | **PASS** |
| G17 | Connectors/SafeHttp unmodified | **PASS** |
| G18 | Significance INFORM floors unmodified | **PASS** (`significance.py` not in diff) |
| G19 | Cross-owner commitment hide still owner-predicated | **PASS** (commitments); tasks: F-V624-08 |
| G20 | Idempotent evidence re-cite | **PASS** |
| G21 | Prediction fingerprint upsert | **PASS** |
| G22 | `probability` NULL | **PASS** |
| G23 | SENSITIVE not emitted | **PASS** |
| G24 | README not claiming V6.2.4 released | **PASS** |

---

## 19. Findings (severity)

**BLOCKER:** none  
**HIGH:** none affecting the V6.2.4 stop-line (no INFORM, no LLM, no ACT, no memory writes, no HUD cards)

**F-V624-01 — MEDIUM — stale calendar corroboration is untimed**  
`_rule_stale` may attach any same-project `CAL_EVENT` in the eval window. That can raise N and skip email-single ABSTAIN for STALE. Deadline extras correctly use a 2h window. Tightening stale extras to the same 2h (or dropping extras for email-only stale) would close this.

**F-V624-02 — MEDIUM — GitHub evidence strength 1.00 vs plan 0.70**  
Required to meet OPEN_REVIEW_AGING floor 0.68 given R=0.70. Documented in the implementation report. Calibration is optimistic vs the architecture S table.

**F-V624-03 — LOW — no insert cap of 50 ACTIVE predictions/owner**  
Snapshot lists 50; PG can retain more ACTIVE rows until `valid_until` expiry.

**F-V624-04 — LOW — PRIVATE predictions copied into WorldSnapshot**  
Not HUD-visible today. Do not later JSON-serialize `snap.predictions` to the dashboard without filtering `privacy_class=NORMAL`.

**F-V624-05 — LOW — TASK_BLOCKED project_id is unsigned JSON in `artifacts`**  
Safer than pairing all paused tasks. Spoofable if untrusted code writes checkpoints. Acceptable for single-operator DOOM; not a first-class column.

**F-V624-06 — LOW — evidence hash change does not SUPERSEDE prior ACTIVE cite**

**F-V624-07 — LOW — `valid_until` not checked as `STALE_EVIDENCE` at emit**

**F-V624-08 — INFORMATIONAL — blocked-task loader is not owner-scoped**

**F-V624-09 — INFORMATIONAL — `rule_id` is the type name, not `v624.*`**

**F-V624-10 — LOW — generation++ without compare-and-swap**

**F-V624-11 — LOW — `world_prediction_events` unbounded per eval**

Inherited: **F-V623-04…08**, **F-V622-01/03/06/07** (not reopened as V6.2.4 blockers).

---

## 20. Nonblocking risks

- Keyword commitments remain weak; H3 is the safety net (except F-V624-01).
- Eval cites up to 80 rows × source class per tick (idempotent); first-enable tick is the heavy write.
- `OWNER_ID` default `sujal` unchanged.
- Checkpoint `artifacts.project_id` convention is undocumented outside tests/report.

---

## 21. Performance observations

Candidate cap 80; calendar window −7d…+72h; review loader `occurred_at ≤ now−7d`; commitment due window −7d…+72h. No embeddings. Indexes on owner/status and independence_key. Not load-tested at 10k rows.

---

## 22. Git status (end of audit)

HEAD **unchanged** `78b04338647925f4400439f666f4487163e14e3a`.

Tracked dirty: the seven application files in §3.  
Untracked: V6.2.4 modules, tests, architecture/plan/implementation reports, leftover markdown.

**This audit added only** `DOOM_V6.2.4_FORENSIC_AUDIT.md` (plus an optional Canvas view of the same verdict).

---

## 23. Release actions

**No commit, annotated tag, or push** was performed.

---

## 24. Final verdict

**B — PASS WITH NON-BLOCKING FINDINGS**

V6.2.4 as implemented is **world evidence citation + five deterministic prediction types + derived snapshot lists**. It does not add SUGGEST/PREPARE/ASK/LLM/ACT, prediction INFORM, or canonical memory writes.

**V6.2.4 RELEASE AUTHORIZED: YES**

Authorization means the owner **may** later request commit + annotated tag + push. This audit did not perform those steps.

Do **not** treat F-V624-01…11 as drive-by must-fix before an owner-requested release unless the owner wants F-V624-01 (stale calendar pairing) closed first.

V6.2.5 was **not** started.
