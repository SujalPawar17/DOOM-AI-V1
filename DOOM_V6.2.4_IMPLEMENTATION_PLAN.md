# DOOM V6.2.4 — Implementation Plan

**Evidence + Prediction Intelligence**

**Mode:** PLANNING ONLY — no source, schema, tests, commit, tag, or push  
**Date:** 2026-09-09  
**Authoritative architecture:** `DOOM_V6.2.4_ARCHITECTURE_AUDIT.md`  
**Audit verdict:** PASS WITH NON-BLOCKING FINDINGS  
**This plan does not authorize implementation.**

---

## Frozen baseline

| Item | Value |
|------|--------|
| Branch | `DOOM-V5.2` |
| HEAD (docs) | `78b04338647925f4400439f666f4487163e14e3a` |
| Official release | `v6.2.3` |
| Immutable release commit | `391f88f3d54fde3f54e41f75cd6bee7062bfc56d` |

Do not retag `v6.2.3`. Do not rewrite F-RA-01, `_OWNER_PRED`, SafeHttp, Gmail READ, or INFORM CHECK.

---

## Objective and hard boundary

```
READ → EVIDENCE (cite) → CORRELATE (independence) → TEMPORAL GATES
     → DETERMINISTIC PREDICT → WorldSnapshot.predictions
     → STOP
```

**Not in V6.2.4:** SUGGEST, PREPARE, ASK, LLM, ACT, connector WRITE, TaskEngine create, `memory_records` writes, HUD prediction cards, `proactive_insights` INSERT from predictions, `proactive_signals` as a prediction queue.

### Mandatory high findings (H1–H4)

| ID | Rule | Enforcement in this plan |
|----|------|---------------------------|
| H1 | Do not reuse `memory_evidence` | New `world_evidence`; no FK to `memory_records`; no import of `MemoryEvolutionEngine` |
| H2 | Do not INFORM from predictions | `evaluate_world_predictions` must not call `insert_insight`, `ingest_signal`, or `deliver_inform` |
| H3 | Single-source email safety | `C_max=0.55` when N=1 and only `gmail_extract`; emit floor 0.70 → ABSTAIN |
| H4 | No INFORM outbox for eval | Direct call from `process_once` after `poll_connectors`; no `PREDICTION_EVAL` signal type |

---

## Constants (implementation)

| Name | Value |
|------|--------|
| `PROACTIVE_PREDICTION_ENABLED` | default **false**; also requires `PROACTIVE_ENABLED` |
| `PREDICTION_EMIT_FLOOR` | **0.70** |
| `C_MAX_EMAIL_SINGLE` | **0.55** |
| `C_MAX_DEFAULT` | **0.95** |
| `N_CAP` | **3** |
| `EVAL_WINDOW` | due/start in **[-7d, +72h]** for candidates; not full history |
| `MAX_ACTIVE_PREDICTIONS_PER_OWNER` | **50** |
| `RULE_ID` prefix | `v624.*` |
| `RULE_VERSION` | `v624.1` |
| `PREDICTION_EVAL_CAP` | max **80** source rows loaded per tick |

Source reliability **R**:

| Class | R |
|-------|---|
| `task_engine` | 0.90 |
| `calendar` | 0.85 |
| `host` | 0.80 (unused in five types) |
| `github` | 0.70 |
| `gmail_extract` | 0.45 |

Evidence strength **S** (stored on `world_evidence.strength`):

| Source | S |
|--------|---|
| Task checkpoint status | 1.00 |
| CAL_EVENT fact | 0.85 |
| GH_REVIEW / GH_PR fact | 0.70 |
| Keyword commitment (`source_connector=gmail`) | 0.55 |
| Commitment citing calendar fact | 0.80 |

**C (prediction confidence):**

```
C_raw = min(C_MAX_DEFAULT, (max_i R_i * S_i) + 0.10 * (N - 1))
if only_gmail_extract and N == 1:
    C = min(C_MAX_EMAIL_SINGLE, C_raw)   # ≤ 0.55
else:
    C = min(C_MAX_DEFAULT, C_raw)
N = distinct independence_key count, capped at N_CAP
```

Do **not** add commitment-extractor keyword bonuses again.

**Risk K** (independent of C):

| Condition | K |
|-----------|---|
| due/horizon within 2h | HIGH |
| within 24h | MEDIUM |
| within 72h | LOW |
| stale (due_at < now) | MEDIUM (STALE_OPEN); not “user failed” |
| conflict type | HIGH |

`probability` column: **NULL** in V6.2.4 (no fake Bayes).

---

## Phase 1 — Evidence foundation (`world_evidence`)

Citation rows only. Payload = structured ids/hashes already on the source. **No** subject, snippet, body.

### Required columns (audit-trimmed)

| Column | Type | Notes |
|--------|------|--------|
| `evidence_id` | VARCHAR(64) PK | uuid |
| `owner_id` | VARCHAR(64) NOT NULL | isolation |
| `project_id` | VARCHAR(64) NULL | FK `projects` ON DELETE SET NULL |
| `source_kind` | VARCHAR(32) NOT NULL | CHECK: `EXTERNAL_FACT`, `COMMITMENT`, `TASK_CHECKPOINT` |
| `source_id` | VARCHAR(128) NOT NULL | fact_id / commitment_id / task_id |
| `source_record_id` | VARCHAR(128) NOT NULL DEFAULT '' | provider id (gmail message, cal event, gh issue) |
| `connector_type` | VARCHAR(32) NOT NULL DEFAULT '' | `gmail`, `calendar_google`, `github`, `task_engine` |
| `reliability_class` | VARCHAR(32) NOT NULL | `gmail_extract`, `calendar`, `github`, `task_engine` |
| `strength` | REAL NOT NULL | S in [0,1] |
| `privacy_class` | VARCHAR(16) NOT NULL | NORMAL/PRIVATE/SENSITIVE |
| `occurred_at` | TIMESTAMPTZ NULL | event time; NULL → prediction ABSTAIN if required |
| `observed_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | ingest/eval wall clock |
| `valid_until` | TIMESTAMPTZ NULL | copied from source or +7d |
| `independence_key` | VARCHAR(80) NOT NULL | see Phase 3 |
| `transform_id` | VARCHAR(40) NOT NULL DEFAULT 'cite_v624' | |
| `transform_version` | VARCHAR(16) NOT NULL DEFAULT 'v624.1' | |
| `status` | VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' | ACTIVE/SUPERSEDED/INVALIDATED/EXPIRED |
| `idempotency_key` | VARCHAR(160) NOT NULL UNIQUE | `owner|source_kind|source_id|content_sha` |
| `content_sha256` | VARCHAR(64) NOT NULL DEFAULT '' | from fact or commitment fingerprint |
| `provenance` | JSONB NOT NULL DEFAULT '{}' | ids only |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |

**Omit:** `updated_at` on payload (append-only); `expires_at` (use `valid_until`); `generation` (status transitions via new row or `status`+`invalidated_at` without rewriting provenance); `evidence_type` enum beyond `source_kind`; `confidence` column (use `strength` + `reliability_class`, not a third number).

**Immutable after insert:** all columns except `status` (and optional `invalidated_at`). Never rewrite `provenance` / hashes.

**Lifecycle:** ACTIVE → SUPERSEDED (new row cites same source with new hash) | INVALIDATED | EXPIRED (batch job: `valid_until < NOW()`).

**Skip evidence** if `source_id` empty (F-V623-08).

**No `MEMORY` source_kind.** Memory-derived world evidence is **out of scope**.

---

## Phase 2 — Evidence relationships

**Do not** build a general DAG (`related_to`, `derived_from` mesh).

V6.2.4 uses:

1. **Independence collapse** (Phase 3) instead of a `duplicates` graph.
2. **`world_prediction_evidence`** as the only relationship table: role `SUPPORTING` | `CONFLICTING`.
3. **Supersession** of evidence = new `world_evidence` row + old `status=SUPERSEDED`. No self-FK cycle table.

Contradiction is a **prediction type** (`CAL_VS_COMMITMENT_CONFLICT`) with two SUPPORTING/CONFLICTING links, not a free-form evidence graph.

Privacy: link rows inherit `max(privacy)` of endpoints; queries always `owner_id` scoped.

---

## Phase 3 — Correlation / `independence_key`

Canonical string (then `sha256` hex[:48] stored):

```
owner_id | source_kind | connector_type | source_record_id | content_sha256
```

If `source_record_id` empty, use `source_id`.

| Source | Independence group |
|--------|-------------------|
| Gmail message | `gmail` + Gmail `id` + EMAIL_META `content_sha256` |
| Calendar event | `calendar_google` + event id + CAL_EVENT sha |
| GitHub item | `github` + issue/notif id + sha |
| Task checkpoint | `task_engine` + `task_id` + status string hash |
| Same email polled 10× | **same key** → one ACTIVE `world_evidence` (UNIQUE idempotency) |
| Calendar + email same Friday | **two keys** → N=2 |

**N** for a prediction = count of distinct `independence_key` among linked ACTIVE evidence.

---

## Phase 4 — Evidence quality

Stored: `reliability_class`, `strength`.  
Derived at prediction time: N, C, K.

| Quality | Mechanism |
|---------|-----------|
| Corroboration | N≥2 distinct keys |
| Freshness | skip if `valid_until < evaluated_at` → `STALE_EVIDENCE` |
| Weak | gmail_extract S=0.55 |
| User-confirmed | **not in V6.2.4** |
| Contradiction | CONFLICTING links / conflict rule |

Reads of `world_evidence` **must not** UPDATE strength (`dS/dN_retrieval = 0`).

---

## Phase 5 — Temporal engine

Module: `proactive/temporal.py` (pure functions, no PG).

| Clock | Use |
|-------|-----|
| `occurred_at` | event |
| `observed_at` | citation time |
| `due_at` | from commitment (not stored on evidence; join source) |
| `valid_until` | stale gate |
| `evaluated_at` | worker `time.time()` — **evaluation clock only** |
| `predicted_for` | `horizon_start`/`horizon_end` on prediction |
| `created_at` | audit |

**Rules:**

- Missing `occurred_at`/`due_at` when the rule needs it → ABSTAIN `MISSING_SOURCE_TIME`. **Never** call `datetime.now` as a substitute deadline (closes F-V623-05 for this layer).
- Naive TZ: treat stored timestamptz as UTC.
- Relative dates: **do not re-parse email**. Use commitment `due_at` already extracted.
- Recurrence: if fact has `recurring_event_id` and no single instance time → ABSTAIN `INSUFFICIENT_HISTORY`.
- Cancelled/completed: no writers in V6.2.3; do not invent CANCELLED. STALE_OPEN uses OPEN ∧ `due_at < evaluated_at`.
- Reschedule: same calendar `source_record_id`, new `content_sha256` → new evidence, supersede old; conflict rule compares latest ACTIVE cal vs commitment.

---

## Phase 6 — Prediction foundation (`world_predictions`)

| Column | Type | Notes |
|--------|------|--------|
| `prediction_id` | VARCHAR(64) PK | |
| `owner_id` | VARCHAR(64) NOT NULL | |
| `project_id` | VARCHAR(64) NULL | mixed projects → ABSTAIN |
| `prediction_type` | VARCHAR(40) NOT NULL | CHECK five types |
| `subject_key` | VARCHAR(160) NOT NULL | e.g. `commitment:{id}` |
| `claim_code` | VARCHAR(40) NOT NULL | `DUE_24H`, `STALE_OPEN`, `CAL_MISMATCH`, `TASK_BLOCKED_DUE`, `REVIEW_AGING` |
| `horizon_start` / `horizon_end` | TIMESTAMPTZ NULL | `predicted_for` |
| `confidence` | REAL NOT NULL | C |
| `probability` | REAL NULL | **always NULL** V6.2.4 |
| `risk_class` | VARCHAR(16) NOT NULL | NONE/LOW/MEDIUM/HIGH |
| `status` | VARCHAR(16) NOT NULL | ACTIVE/SUPERSEDED/EXPIRED/INVALIDATED |
| `privacy_class` | VARCHAR(16) NOT NULL | max(evidence) |
| `fingerprint` | VARCHAR(64) NOT NULL | |
| `rule_id` | VARCHAR(40) NOT NULL | |
| `rule_version` | VARCHAR(16) NOT NULL | `v624.1` |
| `generation` | INTEGER NOT NULL DEFAULT 1 | |
| `evaluated_at` | TIMESTAMPTZ NOT NULL | |
| `valid_until` | TIMESTAMPTZ NOT NULL | horizon_end + 24h or +7d |
| `provenance` | JSONB NOT NULL | `{evidence_ids, independence_keys}` ids only |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT NOW() | |
| `outcome` | VARCHAR(16) NULL | **NULL** V6.2.4 |

**ABSTAINED:** **do not insert** a row (avoid table spam). OTP `abstain_reason` only. If an ACTIVE row no longer qualifies → `SUPERSEDED` or `EXPIRED`.

**Not facts.** Snapshot `record_kind=PREDICTION`.

Optional audit: `world_prediction_events` (`event_id`, `prediction_id`, `from_status`, `to_status`, `reason`, `created_at`) append-only.

---

## Phase 7 — Idempotency

```
fingerprint = sha256(
  owner_id | prediction_type | subject_key | horizon_bucket | rule_id | rule_version
)[:48]
UNIQUE (owner_id, fingerprint)
```

`horizon_bucket`: UTC calendar date of `horizon_end` (deadline/stale/conflict/task); ISO week for `OPEN_REVIEW_AGING`.

**Upsert:**

```sql
INSERT ... ON CONFLICT (owner_id, fingerprint) DO UPDATE
SET confidence = EXCLUDED.confidence,
    risk_class = EXCLUDED.risk_class,
    evaluated_at = EXCLUDED.evaluated_at,
    valid_until = EXCLUDED.valid_until,
    provenance = EXCLUDED.provenance,
    generation = world_predictions.generation + 1,
    status = 'ACTIVE'
WHERE world_predictions.status IN ('ACTIVE','EXPIRED')
```

Replace link rows in the **same transaction**. Identical inputs: still bump `evaluated_at`/`generation`; C must not change from poll count.

---

## Phase 8 — Five prediction types

`rule_id` = type name. All require `PROACTIVE_PREDICTION_ENABLED`.

### 1. `DEADLINE_HORIZON`

- **Claim:** OPEN commitment with `due_at` in (now, now+72h].
- **Evidence:** cite commitment; optional CAL_EVENT same `project_id` within 2h of due.
- **Email-only (connector gmail, N=1):** C≤0.55 → **ABSTAIN** `LOW_CONFIDENCE`.
- **Calendar-backed or N≥2:** emit if C≥0.70.
- **Risk:** 2h HIGH / 24h MEDIUM / else LOW.
- **Expire:** `horizon_end` = due_at.
- **Example:** cal event Friday 15:00 + commitment due Friday → ACTIVE.
- **Negative:** one Gmail “please send next Friday” only → no ACTIVE row.

### 2. `STALE_OPEN_COMMITMENT`

- **Claim:** OPEN ∧ `due_at` < evaluated_at (not “user failed”).
- **Evidence:** commitment only (N=1 allowed); C = min(0.72, R*S) with R from connector class; emit if ≥0.70 **or** allow emit at 0.70 from calendar R; **gmail_extract** C=0.45*0.55=0.25 → **ABSTAIN** unless second source.
- **Horizon:** `horizon_end` = due_at.
- **Negative:** due in the future.

### 3. `CAL_VS_COMMITMENT_CONFLICT`

- **Claim:** |cal.start − commitment.due_at| > 2 hours, same `project_id` or overlapping `source` window 72h, both ACTIVE.
- **Evidence:** N=2 required (cal fact + commitment).
- **C:** max(R_cal*S_cal, R_other*S) + 0.10.
- **K:** HIGH.
- **Abstain:** missing times; same instant; different projects.
- **Negative:** same Friday 15:00 both.

### 4. `TASK_BLOCKED_NEAR_DEADLINE`

- **Claim:** `task_checkpoints.status` ∈ {FAILED, PAUSED, WAITING_FOR_APPROVAL, ERROR} AND a commitment or CAL_EVENT with same `project_id` due/start within 72h.
- **N=2 required.**
- **C:** task S=1.0, R=0.90 plus deadline source.
- **Negative:** blocked task, no project-linked due.

### 5. `OPEN_REVIEW_AGING`

- **Claim:** GH_REVIEW fact or `REVIEW_REQUIRED` commitment with `occurred_at`/`source_ts` older than **72h** and still OPEN/relevant.
- **N=1 allowed**; R=0.70 → C≈0.49 for gmail review extract → ABSTAIN; GitHub fact C≈0.70*0.70=0.49 → **raise S to 0.85 for GH_REVIEW** so C≈0.60 still below 0.70 → **emit floor exception:** this type uses **FLOOR 0.60** *or* keep 0.70 and require aging **7d** with C=min(0.75, R*S+0.10).
- **Decision (lock):** GH_REVIEW fact, age ≥ **7 days**, C = min(0.75, 0.70*0.85 + 0.10)=0.695 → round emit at **≥0.68 for this type only** documented as `OPEN_REVIEW_AGING_FLOOR=0.68`. Gmail REVIEW_REQUIRED alone still ABSTAIN (C_max 0.55).
- **Negative:** review created yesterday.

---

## Phase 9 — Single-source email (mandatory)

| Mechanism | Behavior |
|-----------|----------|
| Duplicate poll | same `independence_key` / idempotency → one evidence row |
| Strength | never incremented on upsert of same key |
| N=1 gmail_extract | `C_max=0.55` < 0.70 → no ACTIVE DEADLINE_HORIZON |
| Speculative/historical | already abstained at V6.2.3 extract; no commitment → no evidence |
| Quoted | V6.2.3 strip; V6.2.4 does not re-read text |
| Ambiguous | no `due_at` → `MISSING_SOURCE_TIME` |
| Corroboration | calendar/task second key may emit |

Repeated poll **must not** increase S, N, or C.

---

## Phase 10 — WorldSnapshot

Changes in `snapshot.py`:

- `calendar_facts: List[dict]` — NORMAL CAL_EVENT 72h (`record_kind=FACT`)
- `commitments` — PRIVATE commitment metadata only (`record_kind=COMMITMENT`); **stop appending** cal rows (fix F-V623-02)
- `predictions` — ACTIVE rows for owner (`record_kind=PREDICTION`): id, type, claim_code, horizon, C, K, privacy_class, evidence_ids
- Optional `evidence_ids` on snapshot: **omit list of all evidence** (too large); predictions already cite ids

TTL unchanged (60s). `invalidate_snapshot()` after eval.

**HUD:** do not add prediction cards. PRIVATE predictions: include in in-process snapshot for owner only; never pass to `evaluate_significance` as INFORM candidates. **Do not** change significance scores based on predictions in V6.2.4.

---

## Phase 11 — Worker integration

`process_once` order:

1. `recover_expired_leases` / `expire_stale`
2. `poll_internal_sources`
3. `poll_connectors`
4. `evaluate_world_predictions()`  — try/except; never raise into INFORM
5. `claim_batch` INFORM (unchanged)

`evaluate_world_predictions`:

- return if not (`PROACTIVE_ENABLED` and `PROACTIVE_PREDICTION_ENABLED`)
- load ≤80 candidate source rows
- cite evidence (upsert)
- run five rules
- upsert predictions + links in **one transaction per fingerprint** (or one transaction per owner tick with savepoints)

No lease row for prediction. Crash → next tick idempotent upsert.

---

## Phase 12 — Privacy

- Evidence `privacy_class` copied from source (EMAIL/commitment → PRIVATE).
- Prediction privacy = max(linked evidence) with SENSITIVE > PRIVATE > NORMAL.
- SENSITIVE source → do not create prediction (quarantine).
- Provenance JSON: ids/hashes only.
- Store list methods: `WHERE owner_id = %s` (F-WRK-02 style).
- Snapshot predictions with PRIVATE stay in-process; HUD unchanged (NORMAL INFORM only).

---

## Phase 13 — Governance

Apply **spirit of gates 3, 5, 12**: privacy quarantine, valid project_id or NULL, no DERIVED_CONTEXT as supporting memory evidence (N/A — no memory).

**No** tool, WRITE, ACT, CognitiveBridge, `process_request`.

Do not migrate `recommended_intervention` CHECK.

DATA_ONLY: prediction `provenance` and snapshot dicts are already id-only; fence if any unexpected keys.

---

## Phase 14 — Observability

Extend `ALLOWED_ATTR_KEYS` with: `prediction_id`, `rule_id`, `evidence_n`, `confidence_bucket`, `risk_class`, `abstain_reason` (short enum).

Event: `proactive.prediction.evaluated`  
Forbidden: existing OTP list + snippet/subject.

---

## Phase 15 — Database (additive, not executed now)

```sql
CREATE TABLE IF NOT EXISTS world_evidence (
  evidence_id VARCHAR(64) PRIMARY KEY,
  owner_id VARCHAR(64) NOT NULL,
  project_id VARCHAR(64) REFERENCES projects(project_id) ON DELETE SET NULL,
  source_kind VARCHAR(32) NOT NULL
    CHECK (source_kind IN ('EXTERNAL_FACT','COMMITMENT','TASK_CHECKPOINT')),
  source_id VARCHAR(128) NOT NULL,
  source_record_id VARCHAR(128) NOT NULL DEFAULT '',
  connector_type VARCHAR(32) NOT NULL DEFAULT '',
  reliability_class VARCHAR(32) NOT NULL,
  strength REAL NOT NULL CHECK (strength >= 0 AND strength <= 1),
  privacy_class VARCHAR(16) NOT NULL
    CHECK (privacy_class IN ('NORMAL','PRIVATE','SENSITIVE')),
  occurred_at TIMESTAMPTZ,
  observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  valid_until TIMESTAMPTZ,
  independence_key VARCHAR(80) NOT NULL,
  transform_id VARCHAR(40) NOT NULL DEFAULT 'cite_v624',
  transform_version VARCHAR(16) NOT NULL DEFAULT 'v624.1',
  status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('ACTIVE','SUPERSEDED','INVALIDATED','EXPIRED')),
  idempotency_key VARCHAR(160) NOT NULL UNIQUE,
  content_sha256 VARCHAR(64) NOT NULL DEFAULT '',
  provenance JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_we_owner_status ON world_evidence (owner_id, status);
CREATE INDEX IF NOT EXISTS idx_we_indep ON world_evidence (independence_key);
CREATE INDEX IF NOT EXISTS idx_we_source ON world_evidence (source_kind, source_id);

CREATE TABLE IF NOT EXISTS world_predictions (
  prediction_id VARCHAR(64) PRIMARY KEY,
  owner_id VARCHAR(64) NOT NULL,
  project_id VARCHAR(64) REFERENCES projects(project_id) ON DELETE SET NULL,
  prediction_type VARCHAR(40) NOT NULL
    CHECK (prediction_type IN (
      'DEADLINE_HORIZON','STALE_OPEN_COMMITMENT','CAL_VS_COMMITMENT_CONFLICT',
      'TASK_BLOCKED_NEAR_DEADLINE','OPEN_REVIEW_AGING')),
  subject_key VARCHAR(160) NOT NULL,
  claim_code VARCHAR(40) NOT NULL,
  horizon_start TIMESTAMPTZ,
  horizon_end TIMESTAMPTZ,
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  probability REAL CHECK (probability IS NULL OR (probability >= 0 AND probability <= 1)),
  risk_class VARCHAR(16) NOT NULL
    CHECK (risk_class IN ('NONE','LOW','MEDIUM','HIGH')),
  status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('ACTIVE','SUPERSEDED','EXPIRED','INVALIDATED')),
  privacy_class VARCHAR(16) NOT NULL
    CHECK (privacy_class IN ('NORMAL','PRIVATE','SENSITIVE')),
  fingerprint VARCHAR(64) NOT NULL,
  rule_id VARCHAR(40) NOT NULL,
  rule_version VARCHAR(16) NOT NULL,
  generation INTEGER NOT NULL DEFAULT 1,
  evaluated_at TIMESTAMPTZ NOT NULL,
  valid_until TIMESTAMPTZ NOT NULL,
  provenance JSONB NOT NULL DEFAULT '{}',
  outcome VARCHAR(16),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (owner_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_wp_owner_status_valid
  ON world_predictions (owner_id, status, valid_until);
CREATE INDEX IF NOT EXISTS idx_wp_owner_type_horizon
  ON world_predictions (owner_id, prediction_type, horizon_end);

CREATE TABLE IF NOT EXISTS world_prediction_evidence (
  prediction_id VARCHAR(64) NOT NULL
    REFERENCES world_predictions(prediction_id) ON DELETE CASCADE,
  evidence_id VARCHAR(64) NOT NULL
    REFERENCES world_evidence(evidence_id) ON DELETE CASCADE,
  role VARCHAR(16) NOT NULL CHECK (role IN ('SUPPORTING','CONFLICTING')),
  PRIMARY KEY (prediction_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS world_prediction_events (
  event_id VARCHAR(64) PRIMARY KEY,
  prediction_id VARCHAR(64) NOT NULL
    REFERENCES world_predictions(prediction_id) ON DELETE CASCADE,
  from_status VARCHAR(16),
  to_status VARCHAR(16) NOT NULL,
  reason VARCHAR(40) NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_wpe_pred ON world_prediction_events (prediction_id, created_at);
```

Migration vehicle: same as V6.1–6.2.3 — `CREATE TABLE IF NOT EXISTS` in `PostgresManager` init. **No DROP.**

---

## Phase 16 — API

**None required.** Do not add `/api/proactive/predictions`. Do not extend insights HUD.

---

## Phase 17 — Tests

New file: `test_v624_evidence_prediction.py` — **exactly 40 tests** (target).

Suggested IDs:

1–4 evidence upsert/idempotency/skip empty source/provenance ids  
5–6 independence: 10 polls → 1 evidence; cal+email → N=2  
7–8 freshness/expiry skip  
9–10 conflict vs same-time non-conflict  
11 missing timestamp ABSTAIN  
12–16 five types positive (email+cal deadline; stale cal-backed; conflict; task blocked; gh review 7d)  
17–21 five types negative (email-only deadline; future due; matching cal; no due; review 1d)  
22 C does not rise on second poll  
23 fingerprint upsert count=1  
24 concurrent upsert (thread) one row  
25 PRIVATE not in HUD insights  
26 cross-owner hidden  
27 mixed project ABSTAIN  
28 worker exception still claims INFORM  
29 flags off → zero world_predictions inserts  
30 `memory_records` count invariant  
31 AST: predict.py no model_router/process_request  
32 no insert_insight from eval (patch spy)  
33 no ingest_signal PREDICTION  
34 snapshot record_kind split (no cal in commitments)  
35 OTP attrs lack snippet  
36 SENSITIVE skip  
37 generation increments  
38 EXPIRE/SUPERSEDE when due passes  
39 SafeHttp unchanged (reuse connector test)  
40 TRANSACTION: eval rollback leaves no orphan links  

Regression: V6.2.3 11, connectors 12, emitters 13, V6.1 HUD-deselected 72, transaction 30.

---

## Phase 18 — Performance (targets, not claims)

| Scale | Target |
|-------|--------|
| 100 rows | eval < 100 ms local |
| 1,000 | eval < 500 ms; indexed owner scans |
| 10,000 | window 72h + cap 80 candidates/tick |
| 100,000 | same cap; do not full-scan; defer partition |

Worker: prediction eval **after** connectors, budget ~1s; INFORM path unchanged.

---

## Phase 19 — Failure / recovery

| Failure | Behavior |
|---------|----------|
| PG down | eval no-op |
| Connector fail | existing backoff; eval on last facts |
| Malformed source | skip cite |
| Duplicate | UNIQUE |
| Insert fail | rollback prediction+links |
| Crash mid-eval | next tick upsert |
| Concurrent | UNIQUE fingerprint |
| Stale generation | last writer wins via generation++ |

No PROCESSED-without-artifact (predictions are not INFORM obligations).

---

## Phase 20 — File map

| File | NEW/MOD | Purpose | Deps | Security | Tests |
|------|---------|---------|------|----------|-------|
| `database/postgres_db.py` | MOD | Additive DDL | existing init | no secrets | schema CHECKs |
| `proactive/config.py` | MOD | flags + floors | env | default off | flag tests |
| `config_example.txt` | MOD | commented flags | — | no secrets | — |
| `proactive/evidence.py` | NEW | cite + independence | store, fence | ids only | 1–8 |
| `proactive/temporal.py` | NEW | clocks/abstain time | none | no now-as-due | 11 |
| `proactive/predict.py` | NEW | orchestrate five rules | evidence, store | no LLM | 12–24 |
| `proactive/store.py` | MOD | CRUD world_* | PG | owner pred | 23–27 |
| `proactive/snapshot.py` | MOD | split lists + predictions | store | no HUD leak | 34 |
| `proactive/worker.py` | MOD | call eval after poll_connectors | predict | try/except | 28–33 |
| `observability/schemas.py` | MOD | allow prediction metadata keys | — | still forbid body | 35 |
| `test_v624_evidence_prediction.py` | NEW | 40 tests | PG | fixtures | all |

**Do not modify:** `memory/evolution_engine.py`, `memory/evidence_transaction.py`, `significance.py` INFORM floors, connectors HTTP, Gmail extractor (except read).

---

## Phase 21 — Implementation order

| Step | Work |
|------|------|
| A | Schema + store CRUD + flags |
| B | `evidence.py` cite + independence |
| C | `temporal.py` |
| D | `predict.py` skeleton upsert |
| E | Five rules |
| F | Snapshot split + predictions list |
| G | Worker hook |
| H | OTP keys |
| I | 40 tests + regression |
| J | Independent forensic audit |

No SUGGEST in any step.

---

## Phase 22 — Acceptance gates

Implementation is incomplete unless:

- [ ] H1–H4 hold (grep + tests)
- [ ] independence: 10 polls ≠ 10 N
- [ ] email-only deadline not ACTIVE
- [ ] five rules + negatives pass
- [ ] fingerprint unique
- [ ] snapshot FACT/COMMITMENT/PREDICTION split
- [ ] memory_records unchanged
- [ ] no insert_insight / no new signal types for eval
- [ ] PRIVATE not on HUD
- [ ] flags default off
- [ ] regression suites green
- [ ] no LLM import in new modules

---

## Phase 23 — Security checklist

- [ ] No tokens in world_* JSON
- [ ] No email body/snippet columns
- [ ] Cross-owner isolation
- [ ] Cross-project ABSTAIN
- [ ] Injection in old snippets cannot reach tools (no re-parse; no LLM)
- [ ] Parameterized SQL only
- [ ] No prediction read API (no unauthorized fetch)
- [ ] DATA_ONLY provenance
- [ ] Unique fingerprint prevents prediction flooding
- [ ] SENSITIVE quarantined

---

## Phase 24 — Future release process (do not run)

Implementation → tests → independent forensic audit → blockers only → acceptance → commit → annotated `v6.2.4` → push branch + tag → remote verify → release report.

Do not force-push. Do not retag `v6.2.3`.

---

## Non-blocking findings — treatment

| Finding | Impact | Treatment | Deferred? |
|---------|--------|-----------|-----------|
| H1 memory_evidence | Wrong bound | New tables | No — in plan |
| H2 INFORM | Scope breach | No insight insert | No |
| H3 email single-source | False predict | C_max 0.55 | No |
| H4 outbox | F-RA-01 starve | Direct eval | No |
| F-V623-05 now() | Bad due | ABSTAIN missing time | No for predictions; extractor fallback **deferred** (not this slice) |
| F-V623-02 snapshot mix | Confused facts | Split lists | No — in snapshot phase |
| F-V623-08 empty fact_id | Orphan | Skip cite | No |
| F-V623-01 OPEN forever | Noise | STALE_OPEN type | No (prediction, not status writer) |
| F-V623-04 GET send path | Residual HTTP | **Deferred** (not prediction) | Yes |
| F-V622-01 redirects | Residual | Deferred | Yes |
| F-V622-03 claim_batch | INFORM flake | Don’t use outbox | Partial |
| Recurrence | Overbuild | Abstain | Yes |
| HUD 95–98 | Pre-existing | Deferred | Yes |

Do not expand into SUGGEST, completion inference writers, or SafeHttp rewrite unless a later dedicated slice.

---

## Exact phase boundary reminder

V6.2.4 **does:** world evidence citations, independence, five deterministic predictions, C/K split, snapshot discrimination, worker hook, tests.

V6.2.4 **does not:** SUGGEST/PREPARE/ASK/LLM/ACT/writes/memory auto-write/HUD prediction/INTERVENTION CHECK expansion.

---

V6.2.4 IMPLEMENTATION PLAN STATUS:  
READY FOR OWNER REVIEW

V6.2.4 IMPLEMENTATION STATUS:  
NOT STARTED

V6.2.4 RELEASE STATUS:  
NOT RELEASED

V6.2.5:  
NOT STARTED
