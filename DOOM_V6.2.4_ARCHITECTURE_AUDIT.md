# DOOM V6.2.4 — Architecture Audit

**Evidence + Prediction Intelligence**

**Mode:** AUDIT ONLY — no implementation, schema, tests, commit, tag, or push  
**Date:** 2026-09-09  
**Auditor role:** Principal architect / forensic auditor  
**Baseline release:** tag `v6.2.3` → `391f88f3d54fde3f54e41f75cd6bee7062bfc56d`  
**Branch HEAD (docs):** `78b04338647925f4400439f666f4487163e14e3a` on `DOOM-V5.2`  
**Roadmap:** unchanged (V6.2.4 → SUGGEST → PREPARE → ASK → V6.2.8 LLM → V6.3 ACT → V7)

**Final verdict:** **PASS WITH NON-BLOCKING FINDINGS**

V6.2.4 is **not started**. This document is an implementation-ready specification. It does **not** authorize code.

The question answered: *Is the architecture sufficiently understood and bounded to implement V6.2.4 safely?* **Yes**, if the phase boundary and table-separation rules below are followed.

---

## 0. Audit method

Inspected live modules (not release-report claims as proof): `proactive/*`, `memory/evolution_*`, `memory/evidence_transaction.py`, `memory/governance.py`, `memory/project_engine.py`, `database/postgres_db.py` (proactive + memory_evidence + projects), `dashboard/server.py` HUD APIs, `core/model_router.py` (no proactive import), `observability/schemas.py`.

Tag `v6.2.3` was **not** modified. Application files were **not** modified.

---

## 1. Current architecture inventory

| Component | File / table | Authority | R/W | Notes |
|-----------|--------------|-----------|-----|--------|
| Signal model | `proactive/schemas.py` `ProactiveSignal`; `proactive_signals` | PG | ingest write | Typed; UNIQUE `idempotency_key`; lease/claim |
| Ingest / normalize | `proactive/ingest.py` | Flag-gated | write signals | Fence-first; SENSITIVE dropped; `occurred_at` must be >0 and ≤ now+1d |
| Dedup | `compute_idempotency_key` + UNIQUE | PG | — | Time-bucketed; not semantic correlation |
| Correlation | **absent** | — | — | Snapshot lists only; no first-class graph |
| WorldSnapshot | `proactive/snapshot.py` | **derived TTL cache** | read PG | Mixes PRIVATE commitments + NORMAL `CAL_EVENT` in one `commitments` list |
| External facts | `external_facts` | PG | upsert | Kinds: CAL_EVENT, GH_*, EMAIL_META; `content_sha256`; UNIQUE idempotency |
| Commitments | `proactive_commitments` | PG | upsert | PRIVATE; fingerprint UNIQUE per account; status OPEN never auto-completed |
| Projects | `projects` | PG | V5 | Snapshot loads NORMAL project ids only |
| Canonical memory | `memory_records` + lifecycle | PG | MemoryManager | **Must not** be written by V6 poller |
| Memory evidence | `memory_evidence` | PG, FK **memory_id** | evolution write | Epistemic evidence **for memories**, not world events |
| Experience evidence | `experiences` + `EvidenceTransaction` | PG | request path | Bayesian strategy updates; not world prediction |
| Worker | `proactive/worker.py` `process_once` | single thread | poll + claim 8 | INFORM-only CHECK on insights |
| Outbox | `proactive_signals` PENDING | PG SKIP LOCKED | — | Known starvation at `limit=8` |
| Privacy | fence + class | — | — | PRIVATE email never `ingest_signal` |
| Confidence | memory evolution α/β; fact default 0.8; commitment additive score; significance floors | mixed | — | **Not** a shared prediction model |
| Freshness | memory `FreshnessClass`; fact `valid_until`; snapshot TTL 60s | mixed | — | No world-evidence freshness class |
| Temporal | fact `occurred_at`/`observed_at`; commitment `due_at`/`source_ts`; `_source_dt` **now() fallback** | — | — | F-V623-05 |
| Observability | `proactive/otp.py` → OTP | metadata | — | Forbidden body/token keys |
| Routing | `core/model_router.py` | request path | — | Unused by proactive worker |
| HUD | `/api/proactive/status`, `/insights` | NORMAL INFORM only | read | No commitments/predictions |

**Signal types (live):** TASK_STATUS, MEMORY_LIFECYCLE, PROJECT_CHANGE, EXPERIENCE_CREATED, STRATEGY_FAILURE, REQUEST_COMPLETED, HOST_TELEMETRY, PROVIDER_CIRCUIT, INACTIVITY, CALENDAR_EVENT, GITHUB_*. **No EMAIL signal type.** Gmail is fact+commitment only.

**Sources (ingest allowlist):** does **not** include `gmail`. Correct for V6.2.3 privacy.

**Significance** (`evaluate_significance`): PRIVATE → IGNORE. Does **not** read `snap.commitments`. Calendar/GitHub scores sit **below** INFORM floor except review (0.55 still below 0.65). Predictions must **not** hitch a ride on INFORM by inflating these scores in V6.2.4.

---

## 2. Evidence architecture audit

**Finding:** DOOM has **three** evidence-shaped systems. None is a world-model evidence object.

| Store | Bound to | Independent of memory? | Suitable for V6.2.4 world evidence? |
|-------|----------|------------------------|-------------------------------------|
| `memory_evidence` | `memory_id` CASCADE | No | **No** — would force a memory row; evolution **mutates** confidence (violates read-only query invariant) |
| Experience `verification_evidence` JSON | `experiences` | Partial | **No** — task-outcome domain |
| Commitment `evidence_ref` / `provenance` | fact_id + message_id | Yes as citation | **Citation only**, not a first-class evidence record |

**Recommendation — new first-class `world_evidence` (name to be finalized in implementation plan):**

- **Immutable observation rows** (append-only). Correction = new row + invalidate/supersede pointer, not UPDATE of payload.
- Identity: `evidence_id` PK.
- Cite existing records by id: `source_kind` ∈ `{EXTERNAL_FACT, COMMITMENT, TASK_CHECKPOINT, SIGNAL, PROJECT, HOST}` + `source_id`.
- Do **not** duplicate email snippets. Payload = typed scalars + hashes already on the source row.
- Lifecycle: `ACTIVE` | `SUPERSEDED` | `INVALIDATED` | `EXPIRED` (append-only status via new row or status+`invalidated_at` with no payload rewrite).
- **Do not** attach `world_evidence` to `memory_records`.

Reuse patterns: UNIQUE idempotency (facts), observation_hash (memory_evidence), FOR UPDATE (evolution), skip embeddings.

---

## 3. Evidence provenance

Today a commitment can answer “which fact_id / message_id?” via `provenance` JSON. It cannot answer:

- transformation version (extractor rule id)
- independence class (same Gmail message vs calendar vs task)
- freshness class at observation time
- whether ten polls of the same message were collapsed

**Provenance required on every world_evidence and every prediction:**

`source_kind, source_id, source_record_id, connector_type, occurred_at, observed_at, ingested_at, transform_id, transform_version, privacy_class, owner_id, project_id, independence_key`

**Loss paths today:**

- Snapshot drops `account_id` from listed commitments.
- `persist_email_commitment` may run with empty `fact_id` (F-V623-08).
- Ephemeral subject/snippet never stored (correct) but also never hashed into commitment provenance.

V6.2.4 must refuse to emit a prediction whose evidence set lacks `source_id` or `independence_key`.

---

## 4. Evidence quality / independence

**Must not:** 10 `EMAIL_META` upserts of the same `source_record_id` = 10 independent supports.

**Independence key (canonical):**

`owner_id | source_kind | connector_type | source_record_id | content_sha256`

Same Gmail message → one independence group regardless of poll count.

Calendar event id vs email message id are **independent** if both exist (different source_record_id and connector). That is legitimate corroboration.

**Quality dimensions (separate fields, not one score):**

| Dimension | Meaning |
|-----------|---------|
| Source reliability | Connector/class prior (calendar provider > keyword email) |
| Evidence strength | How strongly this observation supports a *claim* |
| Corroboration count | Count of **distinct independence_key** |
| Freshness | `valid_until` / age vs `occurred_at` |
| Contradiction set | Open conflicts in the same subject key |

Keyword commitments are **weak, derived** evidence (`DERIVED_EXTRACT`), not user-confirmed.

---

## 5. Contradiction / conflict

**No contradiction object exists.** Commitment status CHECK includes CANCELLED/COMPLETED but nothing writes those transitions. Calendar `status` may appear in fact payload; snapshot calendar rows omit summary and cancellation semantics.

**V6.2.4 model:** `world_conflicts` **or** prediction-local `conflict_refs` JSON of evidence_ids.

Resolution policy (deterministic):

1. Same `subject_key`, later `occurred_at` from **same independence group** → supersede (reschedule), do not double-count.
2. Same subject, **different** independent sources, incompatible claims (Friday vs cancelled) → `UNRESOLVED_CONFLICT` unless one source class outranks (calendar CANCELLED vs email MEETING).
3. Semantic similarity ≠ contradiction (two emails both “Friday” is corroboration).
4. Unresolved conflict → **ABSTAIN** or prediction with `risk=CONFLICT` and **capped** confidence (see §10), never “certain meeting Friday.”

Calendar CANCELLED vs OPEN email MEETING: prefer calendar for *event existence*; keep email as weak follow-up evidence; do not INFORM.

---

## 6. Temporal reasoning

| Clock | Exists | Use in V6.2.4 |
|-------|--------|----------------|
| `occurred_at` | facts, signals | Event time |
| `observed_at` | facts DEFAULT NOW() | Ingest wall clock |
| `due_at` | commitments | Horizon |
| `valid_from` / `valid_until` | commitments, facts | Validity |
| `created_at` / `updated_at` | commitments | Audit only |
| `predicted_for` | **missing** | New on predictions |
| `expires_at` | insight `valid_until` | New on predictions |

**Dangerous fallback:** `commitments._source_dt` uses `datetime.now(UTC)` if Date and `occurred_at` missing (**F-V623-05**).  

**V6.2.4 rule:** prediction and evidence **ABSTAIN** if source time required and missing. Do **not** use wall clock as a substitute deadline. Wall clock is allowed only as `evaluated_at` / `observed_at` for the evaluation cycle itself.

Relative dates already resolved at extract time; prediction must not re-parse email text (no second keyword pass on bodies — bodies are not stored).

Recurring events: calendar payload may include `recurring_event_id`; V6.2.4 may treat RRULE as **out of scope** (abstain `INSUFFICIENT_STRUCTURE`) rather than invent instances.

Completed/cancelled: no writers; prediction “stale OPEN past due” is valid **without** claiming the user missed it as ground truth.

Timezone: naive headers forced to UTC at extract. Predictions store `timezone` from source or `UTC` + `tz_inferred=true`. Do not invent named zones.

---

## 7. Prediction entity (proposed)

First-class row **`world_predictions`** (name), **not** an insight, **not** a memory.

| Field | Role |
|-------|------|
| `prediction_id` | PK |
| `owner_id` | Isolation |
| `project_id` | Nullable; empty = personal/global |
| `prediction_type` | Closed CHECK (see §12) |
| `subject_key` | Stable subject (e.g. `commitment:{id}`, `cal:{fact_id}`, `task:{id}`) |
| `claim_code` | Short machine claim (`DUE_WITHIN_24H`) |
| `horizon_start` / `horizon_end` | `predicted_for` window |
| `confidence` | Prediction confidence 0–1 |
| `probability` | Optional; same as confidence unless calibrated later — **do not invent a second uncalibrated number**; if unused, store NULL |
| `risk_class` | `NONE\|LOW\|MEDIUM\|HIGH` — **not** a restatement of confidence |
| `status` | See §8 |
| `abstain_reason` | Nullable |
| `fingerprint` | UNIQUE with owner |
| `rule_id` / `rule_version` | Explainability |
| `privacy_class` | Max privacy of supporting evidence |
| `provenance` | JSON ids only |
| `created_at` / `evaluated_at` / `valid_until` | Clocks |
| `generation` | Monotonic on update (lost-update control) |
| `outcome` | NULL until later phases; V6.2.4 may leave NULL |

**Do not store:** subject lines, snippets, tokens, CoT.

**Probability:** V6.2.4 is rule-based. Store `confidence` only unless a type has an empirical base rate; do not fake Bayes.

---

## 8. Prediction lifecycle

Recommended states (justified, not cargo-cult):

| Status | Meaning |
|--------|---------|
| `ACTIVE` | Current best claim for fingerprint |
| `SUPERSEDED` | Replaced by newer evaluation (same fingerprint, new generation or new row) |
| `EXPIRED` | `valid_until` passed; retained for audit |
| `INVALIDATED` | Evidence retracted / privacy / rule bug |
| `ABSTAINED` | Durable “we refused to claim” (optional; may be omit-row instead) |

**Omit CONFIRMED/DISCONFIRMED in V6.2.4** — those are outcome learning (closer to V6.2.5+ / experiences). Recording outcomes would tempt INFORM/SUGGEST.

**Recalc:** each worker cycle after connector poll, for enabled owners, evaluate **bounded** candidate set (open commitments due in 72h, open FAILED/PAUSED tasks, NORMAL cal events 72h). Not every historical EMAIL_META.

**History:** SUPERSEDED/EXPIRED rows remain. Do not DELETE.

**CREATE→EVALUATE** is one transaction: upsert by fingerprint.

---

## 9. Idempotency / fingerprint

Canonical fingerprint:

`sha256(owner_id | prediction_type | subject_key | horizon_bucket | rule_id | rule_version)`

`horizon_bucket` = UTC hour or calendar date of `horizon_end`, type-specific.

Repeated polling **ON CONFLICT** updates `evaluated_at`, `confidence`, `generation`, `evidence_link` set — **does not** insert duplicates.

Different accounts/messages → different `subject_key`.

---

## 10. Confidence model

**Do not** reuse memory evolution α/β on world predictions (that path writes `memory_records`).

**Do not** add 0.15 per keyword hit without a cap and independence collapse (commitment extractor already does this; prediction must **not** add the same additives again).

Layers (all stored or reconstructable):

| Layer | Symbol | Source |
|-------|--------|--------|
| Source reliability | R | Table: calendar 0.85, github 0.70, gmail_extract 0.45, task_engine 0.90, host 0.80 |
| Evidence strength | S | 1.0 for verified task status; ≤0.55 for keyword commitment |
| Independence n | N | Distinct independence_keys, cap N≤3 |
| Prediction confidence | C | `min(C_max, f(R,S,N))` with **hard cap** if N=1 derived email (`C_max=0.55`) |
| Risk | K | Horizon + type (due in 2h HIGH even if C=0.5) |

**Invariant:** querying/evaluating predictions **must not** change evidence rows, memory importance, or C except via the explicit evaluation transaction (`dC/dN_retrieval = 0` for reads).

Floor: if C < `PREDICTION_EMIT_FLOOR` (recommend **0.70** for ACTIVE emit; **0.55–0.70** → ABSTAIN `LOW_CONFIDENCE`). Single-email derived claims never reach 0.70 without a second independent source.

---

## 11. False positive / abstain

**PREDICT** only if: independence-collapsed evidence, no unresolved conflict (or conflict type that is itself the prediction), source time present when required, C ≥ emit floor, privacy policy allows snapshot inclusion.

**ABSTAIN reasons (adopt):**

| Code | When |
|------|------|
| `INSUFFICIENT_EVIDENCE` | N=0 or only weak extract |
| `LOW_CONFIDENCE` | C below floor |
| `UNRESOLVED_CONFLICT` | Incompatible independent claims |
| `STALE_EVIDENCE` | `valid_until` passed or age > type SLA |
| `MISSING_SOURCE_TIME` | Would have used `now()` |
| `PRIVACY_BLOCK` | Would require leaking PRIVATE into NORMAL surface |
| `CONTEXT_AMBIGUOUS` | Subject_key cannot be bound |
| `INSUFFICIENT_HISTORY` | Recurrence claims without ≥k independent occurrences (**defer recurrence types**) |

Keyword-only “maybe a deadline” **must ABSTAIN** at prediction layer even if a commitment row exists (commitment ≠ prediction).

---

## 12. Prediction types (V6.2.4 closed set)

Implement **five** types. Reject a generic engine.

| Type | Claim | Inputs | Independent sources needed |
|------|--------|--------|----------------------------|
| `DEADLINE_HORIZON` | Open commitment due within 24h/72h | `proactive_commitments` | 1 if calendar-backed; **2** if email-derived (calendar/task) else ABSTAIN |
| `STALE_OPEN_COMMITMENT` | OPEN and `due_at` < evaluated_at | commitments | 1 (status is the claim; still not “user failed”) |
| `CAL_VS_COMMITMENT_CONFLICT` | Cal time vs commitment due disagree | facts + commitments | 2 by definition |
| `TASK_BLOCKED_NEAR_DEADLINE` | Task FAILED/PAUSED/WAITING + project-linked due | `task_checkpoints` + commitment/cal | 2 |
| `OPEN_REVIEW_AGING` | GH_REVIEW / REVIEW_REQUIRED older than SLA | github fact or commitment | 1 (tooling source R=0.70; cap C) |

**Out of V6.2.4:** likely reply, personality, “will miss meeting,” recurrence, project health scores, LLM summaries.

---

## 13. WorldSnapshot integration

Add `snap.predictions: List[dict]` **separate** from `commitments`.

Each prediction item: ids, type, claim_code, horizon, C, K, privacy_class, evidence_id[] — **no** email text.

TTL remains snapshot-level (60s). Predictions in snapshot are **non-authoritative copies** of ACTIVE PG rows for `owner_id`, filtered:

- Never copy PRIVATE predictions into a structure later serialized to HUD.
- V6.2.4 HUD **does not** render predictions (no SUGGEST). Snapshot is for **later** significance/SUGGEST and in-process inspection.

Distinguish kinds with explicit `record_kind`: `FACT` | `COMMITMENT` | `PREDICTION`. Fix F-V623-02 in V6.2.4 snapshot builder by **splitting** calendar facts out of `commitments` (documentation-compatible refactor of snapshot shape — **allowed** as V6.2.4 world-model work, not SUGGEST).

---

## 14. Memory boundary

**V6.2.4 default: zero writes** to `memory_records`, `memory_evidence`, `experiences`, `strategies`, embeddings, vector_sync_queue.

Predictions **do not** become memory. A future user-confirm path is V6.2.5+ / request-path MemoryManager only.

Tests: count `memory_records` before/after prediction cycle.

---

## 15. Project intelligence

- `project_id` on prediction from commitment/fact/task; if mixed projects → ABSTAIN `CONTEXT_AMBIGUOUS` or split into two predictions.
- Cross-project evidence **forbidden** unless both ids equal or both empty (personal).
- Do not call governance transfer (Gate 1–14) to copy strategies into predictions.
- SENSITIVE projects: do not emit snapshot predictions (quarantine).

Governance 14 gates remain for **memory/strategy transfer**, not for creating world_predictions. Prediction has **no** tool/ACT path, so those gates are not an execution authority — still apply **privacy + project isolation** analogously (Gates 3, 5, 12 spirit).

---

## 16. Privacy / security threat model

| Surface | Risk | Control |
|---------|------|---------|
| PRIVATE EMAIL_META | Leak via prediction payload | Ids only; privacy_class = max(evidence) |
| Snapshot + HUD | Accidental INFORM | Do not ingest prediction as NORMAL signal; HUD skip non-NORMAL; **no new HUD cards in 6.2.4** |
| OTP | Snippet in attrs | Allowed keys only; no subject |
| WS | Same as HUD | No prediction channel |
| Cross-owner | list by owner_id | Copy F-WRK-02 style predicates |
| LLM | Prompt injection from email | **No LLM** |
| Tools | Prediction triggering shell | No CognitiveBridge from worker |

PRIVATE predictions may exist in PG for the owner. They must **not** auto-INFORM (significance already IGNORE on PRIVATE).

---

## 17. Governance boundary

Prediction generation: **DATA_ONLY** derived rows.  

**No tool authority, no connector WRITE, no TaskEngine create, no ASK.**

Do not expand `proactive_insights.recommended_intervention` CHECK to SUGGEST in this release.

---

## 18. Worker / background

Reuse `process_once`:

```
recover leases → expire stale → poll_internal → poll_connectors
→ evaluate_world_predictions()   # NEW, flag-gated, bounded
→ claim_batch (INFORM path unchanged)
```

`evaluate_world_predictions` must **catch** exceptions (connector pattern) so INFORM recovery still runs.

**Flag:** `PROACTIVE_PREDICTION_ENABLED` default **false**; also require `PROACTIVE_ENABLED`. Global off → zero prediction SQL writes.

**Do not** put prediction jobs on `proactive_signals` outbox unless a typed `PREDICTION_EVAL` signal is added — prefer **direct bounded eval** to avoid starving F-RA-01 (`claim_batch` limit 8). If outbox is used, **separate table** with own SKIP LOCKED, not the INFORM queue.

Lease: evaluation is synchronous in the worker tick; no second daemon.

---

## 19. Observability

Emit `proactive.prediction.evaluated` with: cycle_id, prediction_id, rule_id, evidence_n, confidence_bucket, risk_class, status, latency_ms, abstain_reason.

**Forbidden:** body, snippet, subject, token, prompt, CoT, evidence payload blobs.

---

## 20. LLM boundary

| Use | Class |
|-----|--------|
| Extract commitments | Already deterministic; **NOT NEEDED** |
| Predict deadlines | Rule; **NOT NEEDED** |
| Summarize why | **FUTURE V6.2.8** |
| Resolve ambiguous language | **FUTURE V6.2.8** optional |
| SUGGEST copy | **V6.2.5** not 6.2.4 |

---

## 21. Performance

| Scale | Expectation |
|-------|-------------|
| 100 evidence | Full scan of 72h windows cheap |
| 1,000 | Indexes on (owner, status, due_at), (owner, type, valid_until) |
| 10,000 | Keep eval window 72h; cap 50 ACTIVE predictions/owner |
| 100,000 | Partition-by-time later; **not** required now |

Avoid N+1: one query open commitments, one cal facts, one blocked tasks, in-memory join.

No embeddings.

---

## 22. Failure modes

| Failure | Safe outcome |
|---------|----------------|
| PG down | eval no-op; no fake ACTIVE |
| Connector down | stale facts; STALE_EVIDENCE / skip |
| Duplicate facts | UNIQUE idempotency |
| Conflict | ABSTAIN or CONFLICT type |
| Insert fail | rollback; no half prediction without links |
| Worker crash | next tick re-eval; fingerprint upsert |
| Lease expiry | INFORM path only; prediction eval has no lease row |
| Concurrent eval | UNIQUE fingerprint + generation |

No “completed” because a model said so.

---

## 23. Concurrency

`INSERT ... ON CONFLICT (owner_id, fingerprint) DO UPDATE SET generation = world_predictions.generation + 1, ... WHERE generation = EXCLUDED.generation` **or** `SELECT FOR UPDATE` on fingerprint.

Do not lower confidence on a no-op re-eval of identical inputs (idempotent refresh of `evaluated_at` only).

---

## 24. Database design (proposed — do not migrate yet)

**Existing (do not overload):** `memory_evidence`, `external_facts`, `proactive_commitments`, `proactive_signals`, `proactive_insights`.

**Proposed additive:**

1. `world_evidence` — append-only observations citing existing ids; UNIQUE `idempotency_key`; privacy CHECK; owner_id; independence_key; no FK to `memory_records`.
2. `world_predictions` — as §7; UNIQUE `(owner_id, fingerprint)`; generation; privacy CHECK; optional `project_id` FK `projects`.
3. `world_prediction_evidence` — `(prediction_id, evidence_id)` PK; role `SUPPORTING|CONFLICTING`.

Indexes: `(owner_id, status, valid_until)`, `(owner_id, prediction_type, horizon_end)`, `(independence_key)`.

No DROP. No V5 memory CHECK rewrites.

Optional: `world_prediction_events` append-only audit (status transitions) — recommended for explainability.

---

## 25. API / UI

V6.2.4: **no required user-facing API.** Optional operator-only read later: ids + C + type, owner-scoped, no PRIVATE bodies.

Do **not** extend `/api/proactive/insights` to predictions (that is SUGGEST-shaped).

Agent Studio / IDE chat remain **out of band** (unsafe ACT residual).

---

## 26. Test architecture

Dedicated `test_v624_evidence_prediction.py` — **target 40 scenarios** (not 200):

Evidence create/idempotency/provenance/expiry; independence collapse (10 polls ≠ 10 supports); contradiction Friday vs cancel; missing timestamp ABSTAIN; DEADLINE_HORIZON with/without second source; stale OPEN; cal vs commitment conflict; task blocked + due; fingerprint upsert; C cap for email-only; privacy PRIVATE not in HUD; cross-owner hidden; project isolation; concurrent upsert; worker exception isolation; memory_records count invariant; AST/no SafeHttp write; no process_request; flags off zero writes; snapshot `record_kind` discrimination; OTP forbidden keys.

Plus regression: V6.2.3 11, connectors 12, emitters 13, V6.1 HUD-deselected 72, transaction 30.

---

## 27. Regression safety

Must not break: Gmail/Calendar/GitHub GET-only, SafeHttp allowlists, DPAPI, PRIVATE skip ingest, F-RA-01 recovery, F-WRK-02 ACK, INFORM CHECK, attention budget, emitters, vector generation, 14 governance gates, provider router, OTP redaction.

Do not classify 403 as prediction success.

Do not put prediction work on the INFORM claim queue.

---

## 28. Gap analysis

| Area | Current | Required V6.2.4 | Gap | Risk | Recommendation |
|------|---------|-----------------|-----|------|----------------|
| World evidence | Absent / mis-bound memory_evidence | First-class citations | **HIGH** | Wrong FK to memory | New tables |
| Prediction store | Absent | Fingerprinted rows | **HIGH** | Duplicate spam | UNIQUE fingerprint |
| Correlation | Snapshot lists | Independence + subject_key | MEDIUM | False corroboration | independence_key |
| Temporal | now() fallback | Abstain if no source time | MEDIUM | Hallucinated due | Hard ABSTAIN |
| Conflict | None | Explicit type or abstain | MEDIUM | False certainty | CAL_VS_COMMITMENT |
| Snapshot mix | F-V623-02 | Split FACT/COMMITMENT/PREDICTION | LOW | Confused consumers | Split lists |
| INFORM coupling | Significance ignores commitments | Keep decoupled | **HIGH if ignored** | Silent SUGGEST | No insight insert |
| Queue starvation | claim_batch 8 | Don’t enqueue eval | MEDIUM | F-RA-01 flake | Direct eval |
| Commitment OPEN forever | F-V623-01 | STALE_OPEN type | LOW | Noise | Cap C; not ACT |
| Empty evidence_ref | F-V623-08 | Skip evidence if no source_id | LOW | Orphan prediction | Gate |
| LLM temptation | None | Keep none | INFORMATIONAL | Scope creep | Flag later |
| Recurrence | Partial cal fields | Out of scope | INFORMATIONAL | Overbuild | Abstain |

**BLOCKER for implementation start:** none, provided new tables are used and INFORM is not extended.

---

## 29. Architecture proposal (implementation-ready)

### 29.1 Components

`proactive/evidence.py` — build/upsert world_evidence from facts/commitments/tasks (no email text).  
`proactive/predict.py` — deterministic rules, abstain, fingerprint.  
`proactive/store.py` — CRUD only.  
`poller`/`worker` — call eval after connectors.  
`snapshot.py` — typed lists.  
**No** `models/` LLM.

### 29.2 Data flow

```
connectors/emitters → existing facts/commitments/tasks
        → world_evidence (cite, collapse independence)
        → rules → world_predictions (or ABSTAIN)
        → WorldSnapshot.predictions (derived)
        → STOP  (no INFORM, no tools)
```

### 29.3–29.14

Covered in §2–§26. Worker: §18. DB: §24. API: none required. OTP: §19. Tests: §26.

---

## 30. Phase boundary

**V6.2.4 DOES:** evidence citations, quality/independence, light correlation, temporal gates, five deterministic prediction types, confidence/risk split, lifecycle + fingerprint, snapshot discrimination, tests, flags default off.

**V6.2.4 DOES NOT:** SUGGEST, PREPARE, ASK, LLM, ACT, external writes, TaskEngine create, canonical memory auto-write, HUD prediction cards, expanding insight INTERVENTIONS CHECK, Gmail/Calendar/GitHub writes, outcome CONFIRMED/DISCONFIRMED learning.

---

## 31. Recommended implementation phases (after owner approval)

1. Additive schema + store + flags (no rules).  
2. Evidence builder + independence tests.  
3. Five rules + abstain + snapshot split.  
4. Worker hook + OTP + regression.  
5. Forensic audit.

Do **not** start until the owner requests a V6.2.4 **implementation plan**.

---

## Findings summary

**BLOCKER:** none for *starting a bounded implementation plan*.

**HIGH (must be in the plan, not drive-by later):**  
H1 — Do not reuse `memory_evidence`.  
H2 — Do not INSERT `proactive_insights` / INFORM from predictions.  
H3 — Email-derived single-source claims must ABSTAIN or C-cap below emit floor.  
H4 — Do not use INFORM `claim_batch` as prediction queue.

**MEDIUM:** H5 missing source time → ABSTAIN; H6 conflict model; H7 snapshot list split; H8 inherited claim_batch starvation.

**LOW / INFORMATIONAL:** OPEN commitments never complete; F-V623-04 GET `send` path; F-V622-01 redirects; HUD TestClient 95–98.

---

## Audit verdict

**PASS WITH NON-BLOCKING FINDINGS**

Architecture is understood: V6.2.3 supplies **observations** (facts, commitments, tasks) but **not** world evidence or predictions. V6.2.4 can add a **read-only intelligence layer** on the existing worker if it stays **READ → WORLD MODEL → PREDICT** and never **PREDICT → INFORM/SUGGEST/ACT**.

**V6.2.4 IMPLEMENTATION: NOT AUTHORIZED BY THIS DOCUMENT**

**V6.2.3 tag: immutable**  
**V6.2.4 code: not started**

---

## Git (end of audit)

Expected: only this file added/updated. No application diffs.
