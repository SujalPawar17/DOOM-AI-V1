# DOOM V6.2.5 — Architecture Audit

**SUGGEST (recommendation only)**

**Mode:** AUDIT ONLY — no source, schema, tests, config, README, commit, tag, or push  
**Date:** 2026-09-09  
**Auditor role:** Principal architect / independent architecture reviewer  
**Baseline release:** tag `v6.2.4` → `26b8c169f8540f94490ff271a648225d0a8ebdec`  
**Tag object:** `269139ee88a592244e0dc52ca044eba120c1cc1b`  
**Prior immutable release:** `v6.2.3` → `391f88f3d54fde3f54e41f75cd6bee7062bfc56d`  
**Branch:** `DOOM-V5.2`

**Final verdict:** **PASS WITH NON-BLOCKING FINDINGS**

V6.2.5 is **not started**. This document does **not** authorize implementation.

---

## 1. Executive Summary

V6.2.4 answers *what is happening?* (deterministic `world_predictions` on a derived snapshot). V6.2.5 should answer *given that, what would be useful to consider?* and **stop**.

**Recommendation:** consume **ACTIVE, non-SENSITIVE** V6.2.4 predictions only. Apply a **new deterministic suggest-worthiness policy** (do not extend `evaluate_significance` on `proactive_signals`). Persist **`world_suggestions`** (not `proactive_insights`). Render **fixed templates** (no LLM). Deliver NORMAL suggestions on the existing HUD/WS path as a **distinct card type**. Run eval as a **third isolated stage** of `process_once` after `evaluate_world_predictions()`, never on `claim_batch`.

**Authority:** OBSERVE → ANALYZE → INFORM → **SUGGEST → STOP**. PREPARE, ASK, ACT, tools, connector WRITE, TaskEngine, and memory writes are **out**.

---

## 2. Current V6.2.4 Baseline

Inspected live (not release-report claims as proof): `proactive/predict.py`, `evidence.py`, `temporal.py`, `store.py` (world_* + insights + attention + HUD list), `worker.py`, `significance.py`, `attention.py`, `delivery.py`, `templates.py`, `schemas.py`, `config.py`, `snapshot.py`, `dashboard/server.py` (`/api/proactive/status`, `/insights`), `database/postgres_db.py` CHECKs.

| Fact | Live |
|------|------|
| Predictions | Five types; C floor 0.70; email N=1 C≤0.55; `probability` NULL |
| Snapshot | `commitments` / `calendar_facts` / `predictions`; HUD does **not** read predictions |
| Worker | poll → `evaluate_world_predictions()` try/except → `claim_batch` INFORM (limit 8) |
| Insights CHECK | `recommended_intervention IN ('IGNORE','INFORM')` only |
| HUD insights | `privacy_class=NORMAL` **and** `INFORM` only |
| Attention | `inform_count` + JSON cooldowns; busy + quiet hours |
| Significance | **Signal-typed**; PRIVATE/SENSITIVE → IGNORE; calendar/github scores **below** INFORM floor except review 0.55 (still below 0.65) |

V6.2.4 **must not** be rewritten. F-V624-01 (untimed STALE calendar extras) remains; V6.2.5 should **gate STALE suggestions** rather than patch prediction (see §12, F-V625-A01).

---

## 3. V6.2.5 Objective

Smallest capability that turns a trustworthy prediction into **advice**:

```
ACTIVE world_prediction
  → suggest-worthiness (deterministic)
  → world_suggestions upsert (fingerprint)
  → NORMAL HUD/WS delivery (optional persist-only for PRIVATE)
  → STOP
```

Not: agent, executor, approver, LLM orchestrator, or spam engine.

---

## 4. Proposed Authority Boundary

| May | Must not |
|-----|----------|
| Read ACTIVE predictions (owner-scoped) | TaskEngine create/execute |
| Insert/update `world_suggestions` + delivery rows | Any `tools/` invocation |
| Render template text from **claim_code / type / risk** only | Connector WRITE, Gmail send, cal/GH mutate |
| HUD/WS **recommendation** cards | Filesystem, shell, automation |
| User **dismiss** (attention write on the suggestion row) | ASK / CSRF-bound execution (V6.2.7) |
| OTP metadata | Change evidence, C, or prediction claim as a side effect of SUGGEST (expire/supersede **mirroring** prediction status is allowed) |

**Dismiss ≠ ASK.** Dismiss records “user declined this advice.” It does not authorize work.

---

## 5. Existing Architecture Reuse Audit

| Asset | Reuse? | Why |
|-------|--------|-----|
| `world_predictions` | **Read-only input** | Authoritative claims |
| `evaluate_significance` | **Do not extend** | Bound to `proactive_signals`; PRIVATE email already IGNORE; calendar scores too low; mixing SUGGEST here would INFORM-from-prediction or SUGGEST-from-raw-signals |
| `proactive_insights` | **Do not store SUGGEST** | CHECK is INFORM-only; `source_signal_ids` is the INFORM lineage; HUD and recovery assume INFORM; widening CHECK is V6.1 regression risk (G19) |
| `proactive_signals` / `claim_batch` | **Do not enqueue SUGGEST** | F-V622-03 starvation; V6.2.4 already forbade `PREDICTION_EVAL` on this queue |
| `proactive_deliveries` | **Do not FK-reuse** | `insight_id REFERENCES proactive_insights` — cannot point at suggestions without a breaking/polymorphic change |
| `proactive_attention` | **Extend additively** | Same busy/quiet; **separate** `suggest_count` so INFORM budget is not stolen |
| `may_inform` / `deliver_inform` | **Clone pattern, new functions** | `may_suggest` / `deliver_suggest`; do not pass SUGGEST through `deliver_inform` (hardcodes `intervention: INFORM`) |
| `render_inform` / INFORM templates | **Do not reuse strings** | INFORM is factual (“disk is 90%”). SUGGEST is advisory (“consider reviewing…”) |
| `WorldSnapshot.predictions` | **Optional read; do not add `suggestions` in V6.2.5** | F-V624-04: snapshot must not become a HUD dump of PRIVATE rows |
| SafeHttp / connectors / vault | **Unchanged** | No new HTTP |
| Memory / governance / router | **Unchanged** | |

---

## 6. Prediction → Significance → Suggestion Flow

**Chosen design (Question #3):** on every enabled worker tick, **after** prediction eval, scan **existing ACTIVE** predictions (cap 50, same as snapshot). Not “only the ones just inserted this tick,” so a prediction that became ACTIVE while SUGGEST was off can later mint a suggestion when the flag turns on.

```
process_once
  recover / expire / poll_internal / poll_connectors
  try: evaluate_world_predictions()     # V6.2.4
  try: evaluate_world_suggestions()     # V6.2.5
  claim_batch → INFORM path unchanged
```

`evaluate_world_suggestions`:

1. Return 0 unless `PROACTIVE_ENABLED` ∧ `PROACTIVE_PREDICTION_ENABLED` ∧ `PROACTIVE_SUGGEST_ENABLED`.
2. `SELECT` ACTIVE predictions for `OWNER_ID` with `valid_until > NOW()`, `privacy_class <> 'SENSITIVE'`, LIMIT 50.
3. For each row, compute worthiness → IGNORE (no row) or SUGGEST.
4. Fingerprint upsert; deliver NORMAL only.
5. Expire/supersede suggestions whose predictions are no longer ACTIVE.
6. Never `ingest_signal`, `insert_insight`, TaskEngine, or tools.

**No raw Gmail/Calendar/GitHub/memory path.** If a prediction is missing, SUGGEST is silent. Exception audit: **none required.**

---

## 7. Suggestion Object Model

Canonical row (`world_suggestions`):

| Field | Role |
|-------|------|
| `suggestion_id` | PK uuid |
| `owner_id` | Isolation |
| `project_id` | Copied from prediction (nullable) |
| `prediction_id` | FK `world_predictions` ON DELETE CASCADE |
| `suggestion_type` | Closed CHECK, 1:1 with prediction type for V6.2.5 (same five names prefixed `SUGGEST_` **or** reuse prediction_type + `kind=SUGGEST` — **prefer** `suggestion_type` = `REVIEW_DEADLINE` / `RECONCILE_SCHEDULE` / `UNBLOCK_TASK` / `COMPLETE_REVIEW` / `CONFIRM_STALE` so templates are intent, not a copy of prediction_type) |
| `claim_code` | Copied (`DUE_72H`, …) |
| `template_id` | Key into SUGGEST template table |
| `safe_params` | Scalars only: `risk_class`, `horizon_hours` (int), `prediction_type`, `claim_code` — **no** subject, snippet, body, repo name, email |
| `priority` | `LOW\|MEDIUM\|HIGH` derived from K (not a second C) |
| `confidence` | Copy of prediction C (display; not recomputed) |
| `risk_class` | Copy of K |
| `privacy_class` | Copy of prediction |
| `fingerprint` | UNIQUE with owner |
| `rule_id` / `rule_version` | `v625.1` |
| `status` | See §8 |
| `valid_until` | `min(prediction.valid_until, now+TTL)` |
| `provenance` | `{prediction_id, prediction_fingerprint}` ids only |
| `created_at` / `evaluated_at` | Clocks |
| `dismissed_at` | Nullable |

**Omit:** rendered message as stored prose (render at delivery like INFORM); LLM fields; `action_payload`; tool names; URLs.

**suggestion_type (closed):**

| Prediction type | suggestion_type | Template intent |
|-----------------|-----------------|-----------------|
| `DEADLINE_HORIZON` | `CONSIDER_REVIEW_WORK` | Consider reviewing remaining work before the recorded horizon |
| `STALE_OPEN_COMMITMENT` | `CONSIDER_CONFIRM_OPEN` | Consider confirming whether the open item is still relevant |
| `CAL_VS_COMMITMENT_CONFLICT` | `CONSIDER_RECONCILE_TIME` | Consider reconciling calendar vs recorded commitment |
| `TASK_BLOCKED_NEAR_DEADLINE` | `CONSIDER_UNBLOCK` | Consider unblocking or replanning the blocked task |
| `OPEN_REVIEW_AGING` | `CONSIDER_COMPLETE_REVIEW` | Consider completing or declining the aging review |

Template English **must not** contain: create, send, execute, open GitHub, update calendar, run, approve, merge.

---

## 8. Suggestion Lifecycle

| Status | Why it exists |
|--------|----------------|
| `OPEN` | Persisted, eligible for delivery |
| `DELIVERED` | Durable `world_suggestion_deliveries` row exists for `hud` |
| `DISMISSED` | Owner declined; suppress fingerprint until prediction fingerprint changes |
| `EXPIRED` | `valid_until` passed or prediction EXPIRED |
| `SUPERSEDED` | Prediction SUPERSEDED/INVALIDATED or suggestion rule version replaced the claim |

**Not used in V6.2.5:** `CREATED` vs `PENDING` (INFORM already collapses this), `CANCELLED` (dismiss covers user stop).

**Ack:** delivery ack = unique delivery row (same as INFORM). WS disconnect ≠ undelivered if PG row exists (HUD GET is source of truth). If insert succeeds and delivery row fails, next tick retries delivery (OPEN + no delivery row).

---

## 9. Suggestion Persistence

**Table: `world_suggestions`** (name aligned with `world_predictions`, not `proactive_insights`).

**Table: `world_suggestion_deliveries`** `(delivery_id PK, suggestion_id FK CASCADE, channel, status, attempts, created_at, UNIQUE(suggestion_id, channel))`.

**Table: `world_suggestion_events`** append-only status transitions (mirror prediction events). Retention: 14 days optional later; V6.2.5 unbounded is **LOW** (same class as F-V624-11).

Do **not** store suggestions inside `world_predictions.outcome` (that column is for future prediction scoring, not user advice).

---

## 10. Suggestion Delivery

- **NORMAL + OPEN/DELIVERED + valid:** HUD GET **new** `/api/proactive/suggestions` (do **not** stretch `/insights` to mixed interventions).
- Card: `{type: "proactive_suggestion", suggestion_id, intervention: "SUGGEST", template_id, message, urgency/priority, tts: false}` — **never** `type: "proactive_event"` (INFORM clients).
- WS: best-effort fanout like INFORM; PG remains authoritative.
- **PRIVATE:** persist; **no** HUD, **no** WS. Owner-local later; V6.2.5 does not add a PRIVATE UI.
- **SENSITIVE:** never persist a suggestion.
- TTS: remain forbidden (`TTS_PROACTIVE_ALLOWED = False`).
- Dismiss: `POST /api/proactive/suggestions/{id}/dismiss` owner-scoped, flag-gated; sets DISMISSED; no tool side effects.

`/api/proactive/status`: add `suggest_enabled` boolean. Keep `max_intervention` as **INFORM** unless SUGGEST flag is on, then **SUGGEST** (describes ceiling, not that INFORM died). **F-V625-A02:** confirm no V6.1 test freezes `max_intervention` (grep of `test_*.py` found **none**).

---

## 11. Privacy Model

| Class | Persist suggestion? | HUD/WS |
|-------|---------------------|--------|
| NORMAL | Yes | Yes |
| PRIVATE | Yes (owner PG only) | **No** |
| SENSITIVE | **No** | No |

OTP/WS/API: no subject, snippet, body, tokens, URLs, repo titles, calendar summaries. `safe_params` are enums and integers.

Do **not** add `snap.suggestions`. Do **not** JSON-serialize `WorldSnapshot` to the dashboard.

---

## 12. Confidence / Risk / Significance Policy

V6.1 INFORM policy **unchanged**. SUGGEST uses a **separate** function `evaluate_suggest_worthiness(prediction_row) → IGNORE | SUGGEST` with reason codes.

**Hard IGNORE (any one):**

- Flags off  
- `status ≠ ACTIVE` or `valid_until ≤ now`  
- `privacy_class = SENSITIVE`  
- `confidence < 0.70` (defense in depth; ACTIVE should already satisfy emit floor)  
- Existing DISMISSED/OPEN/DELIVERED row with same fingerprint  
- `STALE_OPEN_COMMITMENT` **and** `privacy_class = PRIVATE` **and** single-evidence email-shaped provenance (`evidence_ids` length 1) — mitigates F-V624-01 feeding gmail-only STALE that gained a random same-project calendar extra. If provenance only lists one id **or** prediction C was calendar-boosted without time alignment, **prefer IGNORE STALE** unless `risk_class=HIGH` **and** N cannot be known — **practical rule:** STALE SUGGEST only if `privacy_class=NORMAL` **or** `len(evidence_ids) ≥ 2`. PRIVATE email STALE → **no HUD and no PRIVATE suggestion spam**; IGNORE.  
- Attention: busy / quiet / suggest budget / suggest cooldown  

**Type × risk (all remaining):**

| Type | SUGGEST | Silent |
|------|---------|--------|
| `DEADLINE_HORIZON` | K ∈ {MEDIUM, HIGH} (due ≤ 24h / 2h) | K = LOW (72h far) or NONE |
| `STALE_OPEN_COMMITMENT` | After STALE extra gate; K MEDIUM (stale default) | Failed extra gate |
| `CAL_VS_COMMITMENT_CONFLICT` | Always (K HIGH) | — |
| `TASK_BLOCKED_NEAR_DEADLINE` | Always | — |
| `OPEN_REVIEW_AGING` | Always; priority LOW | — |

**Never INFORM** from this path. **Never SUGGEST** from `evaluate_significance`.

INFORM example (unchanged): “Host disk utilization is at 92 percent.”  
SUGGEST example: “A blocked task sits near a recorded deadline. Consider unblocking or replanning that work.”  
Silent: LOW-risk deadline 60 hours out; SENSITIVE; flag off; dismissed fingerprint.

---

## 13. Deduplication / Fingerprinting

```
sha256(owner_id | prediction.fingerprint | suggestion_type | template_id | rule_version)[:48]
UNIQUE (owner_id, fingerprint)
```

Do **not** include `generation` (V6.2.4 bumps generation every eval → would spam).

ON CONFLICT: refresh `evaluated_at`; do **not** reopen DISMISSED; do **not** lower privacy; do not reset DELIVERED to OPEN.

---

## 14. Attention Budget / Cooldown

**Separate SUGGEST budget.** Sharing `inform_count` (default 8/day) would either starve INFORM or mix advice into the operational cap.

Additive on `proactive_attention`:

- `suggest_count INTEGER NOT NULL DEFAULT 0`  
- Cooldown keys `suggest|{fingerprint}` inside existing `cooldowns` JSON **or** a second JSON `suggest_cooldowns` (prefer **same JSON** with prefix to avoid a second column)

`may_suggest`: same `cognition_busy` + `in_quiet_hours` as INFORM; `suggest_count >= DAILY_SUGGEST_BUDGET` (default **4**); cooldown default **4 hours** (`PROACTIVE_SUGGEST_COOLDOWN_SECONDS`, distinct from INFORM 3600s).

Bump **only** on **new** HUD delivery (not on PRIVATE persist, not on duplicate delivery).

---

## 15. Worker Integration

**Question #2 answer: B — another stage of the existing worker.** Not a second daemon. Not `claim_batch`. Isolated `try/except` so INFORM still runs if SUGGEST throws (mirror prediction isolation).

Requires prediction flag: SUGGEST-on + prediction-off → **zero** suggestion SQL (G2).

---

## 16. Concurrency / Idempotency

Single worker thread today; still use UNIQUE fingerprint + `ON CONFLICT`. Two processes: one winner insert; loser SELECT existing id and may retry delivery iff OPEN and no delivery row. No resurrection of DISMISSED.

Suggestion + delivery insert: **one transaction**. Rollback → no orphan delivery.

---

## 17. Failure / Recovery

| Failure | Behavior |
|---------|----------|
| PG down | no-op |
| Worker crash after suggestion insert, before delivery | next tick: OPEN + missing delivery → `deliver_suggest` |
| Duplicate tick | UNIQUE / duplicate delivery skip |
| WS down | HUD GET still shows DELIVERED/OPEN NORMAL rows |
| Prediction expired | batch EXPIRE suggestions |
| Suggest eval exception | OTP error; INFORM continues |

Durable delivery **is** required for NORMAL HUD suggestions (same I19 spirit as INFORM). PRIVATE persist-only is not a HUD obligation.

---

## 18. Security / Prompt Injection

Suggestions are filled from **enums and integers**, never from email/cal/GH text. Templates are constants in `proactive/suggest_templates.py` (name indicative). Injection strings in source systems cannot reach `str.format` inputs except via attacker-controlled **claim_code** if CHECK is bypassed — keep `claim_code` / `prediction_type` on a CHECK/allowlist before format.

LLM: **not in V6.2.5**. If added later (V6.2.8), output is DATA_ONLY, no tools, cannot write suggestions as ACT.

No tool, write, command, approval, or TaskEngine authority.

---

## 19. Memory Boundary

**Zero writes** to `memory_records`, `memory_evidence`, experiences, lessons, strategies, embeddings.

**Question #5:** do **not** learn from dismiss/accept in V6.2.5. Persist `DISMISSED` + events only. Future (not this release): optional `world_suggestion_outcomes` → later experience ingest on the **request path**, never from the worker. That is an architecture fence, not a table to build now.

---

## 20. Database Changes

**Planned, additive only. No DROP. No CHECK widening on `proactive_insights`.**

1. `world_suggestions` as §7–8  
2. `world_suggestion_deliveries`  
3. `world_suggestion_events`  
4. `ALTER TABLE proactive_attention ADD COLUMN IF NOT EXISTS suggest_count INTEGER NOT NULL DEFAULT 0`

Indexes: `(owner_id, status, valid_until)`, UNIQUE `(owner_id, fingerprint)`, `(prediction_id)`.

FK `prediction_id → world_predictions(prediction_id) ON DELETE CASCADE`.

---

## 21. API / HUD Changes

| Surface | V6.2.5 |
|---------|--------|
| `GET /api/proactive/insights` | **Unchanged** (INFORM only) |
| `GET /api/proactive/suggestions` | New; NORMAL; flag-gated; empty list if off |
| `POST .../suggestions/{id}/dismiss` | New; owner_id match; 404 cross-owner |
| `GET /api/proactive/status` | Add `suggest_enabled` |
| WS | New event type only |
| Snapshot | **No** `suggestions` field |

HUD UI may render SUGGEST cards distinct from INFORM (implementation plan later). Architecture requires the **API split** so INFORM regression tests stay valid.

---

## 22. Observability

Minimum OTP events:

- `proactive.suggestion.evaluated` (created / skipped + `abstain_reason`)  
- `proactive.suggestion.delivered`  
- `proactive.suggestion.dismissed`  
- `proactive.suggestion.expired` (batch, count not ids blob)

Attrs allowlist add: `suggestion_id` (same treatment as `prediction_id`). Forbidden: body, snippet, subject, prompt, CoT, token, url.

---

## 23. Feature Flag

```
PROACTIVE_SUGGEST_ENABLED=false   # default
```

Also requires `PROACTIVE_ENABLED` and `PROACTIVE_PREDICTION_ENABLED`. Naming matches existing `PROACTIVE_*_ENABLED` pattern.

---

## 24. Test Architecture

**File (future):** `test_v625_suggest.py` — **36 tests**. Categories:

| # | Category |
|---|----------|
| 1–3 | Flags off / prediction off / suggest off → zero `world_suggestions` |
| 4–8 | One suggestion per type when gates pass (NORMAL fixture prediction) |
| 9 | LOW-risk DEADLINE → no suggestion |
| 10 | SENSITIVE prediction → none |
| 11 | PRIVATE → row allowed, HUD list empty |
| 12–13 | Duplicate tick / concurrent upsert → one row |
| 14 | Cooldown second delivery skipped |
| 15 | Daily suggest budget |
| 16 | Fingerprint stable |
| 17 | Dismissed not resurrected |
| 18 | Prediction EXPIRED → suggestion EXPIRED |
| 19 | Prediction SUPERSEDED → suggestion SUPERSEDED |
| 20 | Delivery persist fail then retry |
| 21 | Worker exception in suggest does not block `claim_batch` |
| 22 | Cross-owner HUD hide |
| 23 | AST: no TaskEngine, tools, SafeHttp write, insert_insight, ingest_signal, model_router |
| 24 | memory_records count invariant |
| 25 | `/insights` still INFORM-only with SUGGEST rows present |
| 26 | Template corpus has no action verbs (create/send/execute/…) |
| 27 | Injection string in unused payload never in `safe_params` |
| 28 | STALE PRIVATE single-evidence → no suggestion (F-V624-01 mitigation) |
| 29 | `PREDICTION_EVAL` still absent |
| 30 | Busy/quiet suppress HUD bump |
| 31 | Snapshot has no `suggestions` attr |
| 32 | OTP keys no snippet |
| 33 | Dismiss 404 other owner |
| 34 | Suggest does not call `evaluate_significance` |
| 35 | Cap 50 ACTIVE predictions scanned |
| 36 | Regression: V6.2.4 40 tests still pass (run, not duplicate) |

---

## 25. Performance

- Scan ≤ 50 ACTIVE predictions / tick (indexed `owner_id, status, valid_until`)  
- No LLM, no embeddings, no per-row HTTP  
- Target: eval ≪ 100 ms local at 50 rows  
- Dedup: UNIQUE fingerprint, not application loops  
- No `while True`

---

## 26. Threat Model

| Threat | Control |
|--------|---------|
| Suggestion executes work | No code path to tools/TaskEngine |
| Prompt injection via email | Templates not fed source text |
| PRIVATE leak via HUD | Filter + separate API |
| Snapshot dump | No suggestions on snapshot |
| Spam | Budget 4 + cooldown + fingerprint |
| Confused deputy dismiss | owner_id predicate |
| CHECK widen INFORM | Not done |
| Queue starvation | Not on `claim_batch` |

---

## 27. Alternatives Considered

| Alt | Outcome |
|-----|---------|
| A. Store SUGGEST in `proactive_insights` + widen CHECK | Rejected — G19, FK deliveries, signal lineage |
| B. `SUGGEST` signal type + claim_batch | Rejected — H4/F-V622-03 |
| C. LLM draft in 6.2.5 | Rejected — V6.2.8; unbounded; injection |
| D. Second worker/queue | Rejected — durability already PG+tick retry |
| E. Generate only on prediction insert | Rejected — misses flag-on-later; tick scan is simpler |
| F. INFORM the prediction then “upgrade” | Rejected — V6.2.4 forbade INFORM-from-prediction |
| G. Put suggestions on WorldSnapshot | Rejected — F-V624-04 serialization |

---

## 28. Rejected Designs

- Auto-creating tasks from TASK_BLOCKED  
- “Open this GitHub URL” as a suggestion action  
- Reusing INFORM templates  
- Shared daily cap with INFORM  
- Learning loops into `experiences`  
- PREPARE/ASK cards  
- Generic “AI summary of your week”

---

## 29. V6.2.5 Acceptance Gates

| Gate | Requirement |
|------|-------------|
| G1 | SUGGEST reads `world_predictions` only |
| G2 | No SUGGEST from raw facts/commitments/signals/memory |
| G3 | No SENSITIVE suggestions |
| G4 | All queries `owner_id`-predicated |
| G5 | Worthiness is a pure function of stored prediction fields + clocks + attention |
| G6 | UNIQUE fingerprint; one row per identity |
| G7 | Distinct suggest cooldown |
| G8 | Expire/supersede follows prediction status |
| G9 | No TaskEngine |
| G10 | No tool registry calls |
| G11 | No connector WRITE / SafeHttp new verbs |
| G12 | No ACT |
| G13 | No memory mutation |
| G14 | Templates not sourced from external text |
| G15 | `PROACTIVE_SUGGEST_ENABLED` default false |
| G16 | Crash: OPEN without delivery retries |
| G17 | Concurrent upsert one row |
| G18 | Delivery unique (suggestion_id, channel) |
| G19 | `/insights` and INFORM worker unchanged in behavior |
| G20 | No PREPARE/ASK/LLM/ACT modules |
| G21 | HUD card type ≠ INFORM event type |
| G22 | No `WorldSnapshot.suggestions` |
| G23 | Suggest requires prediction flag |
| G24 | STALE PRIVATE single-evidence suppressed |

---

## 30. Future Boundary — V6.2.6+

| Version | Still forbidden in 6.2.5 |
|---------|--------------------------|
| V6.2.6 PREPARE | Drafts, staged payloads, no send |
| V6.2.7 ASK | Authenticated approval |
| V6.2.8 LLM | Bounded copy; DATA_ONLY |
| V6.3 ACT | External side effects |

Dismiss/accept as **training labels** wait for an explicit later design. Do not overload `world_predictions.outcome` in 6.2.5.

---

## 31. Implementation File Map

| File | NEW/MOD | Purpose |
|------|---------|---------|
| `database/postgres_db.py` | MOD | Additive tables + `suggest_count` |
| `proactive/config.py` | MOD | Flag, budget, cooldown, TTL |
| `config_example.txt` | MOD | Commented flag |
| `proactive/suggest.py` | NEW | Worthiness + eval + fingerprint |
| `proactive/suggest_templates.py` | NEW | Advisory strings |
| `proactive/store.py` | MOD | CRUD suggestions/deliveries |
| `proactive/delivery.py` | MOD | `deliver_suggest` only; do not alter INFORM semantics |
| `proactive/attention.py` | MOD | `may_suggest` / `record_suggest` |
| `proactive/worker.py` | MOD | Isolated stage after prediction eval |
| `dashboard/server.py` | MOD | suggestions GET + dismiss; status field |
| `observability/schemas.py` | MOD | `suggestion_id` allow |
| `test_v625_suggest.py` | NEW | 36 tests |

**Do not modify:** `significance.py` INFORM floors, connectors, `predict.py` rules (except no import of suggest into predict), memory, TaskEngine, router.

---

## 32. Implementation Order

1. DDL + store CRUD + fingerprint  
2. Worthiness table + templates + flag  
3. Worker stage + isolation  
4. Delivery + attention  
5. HUD API + dismiss + WS type  
6. Tests 36 + V6.2.4/V6.1 regression  
7. Implementation report — **then** forensic — **then** owner release  

---

## 33. Open Questions

None that **block** the architecture. Defaults below are specified; changing them is operator config, not OWNER DECISION REQUIRED:

- Daily suggest budget default **4**  
- Suggest cooldown default **4h**  
- STALE PRIVATE single-evidence **IGNORE** (F-V624-01 mitigation)

**Not owner-blocking:** dismiss without CSRF is acceptable for local single-operator HUD (same trust as existing dashboard). V6.2.7 must not treat this POST as ASK.

---

## 34. Final Architecture Verdict

**PASS WITH NON-BLOCKING FINDINGS**

V6.2.5 can be layered on v6.2.4 without changing V5 governance, V6.1 INFORM scoring, V6.2.2/3 connectors, or V6.2.4 prediction rules, provided suggestions live in **`world_suggestions`**, generate from **ACTIVE predictions** on the **existing worker**, and render **deterministic templates**.

### Findings

**BLOCKER:** none  
**HIGH:** none for the SUGGEST≠ACT boundary

**F-V625-A01 — MEDIUM — inherited F-V624-01**  
Untimed STALE calendar extras can create ACTIVE STALE predictions. Mitigate at SUGGEST: no STALE suggestion for PRIVATE + `len(evidence_ids)<2`. Do not retag v6.2.4.

**F-V625-A02 — LOW — status `max_intervention`**  
When suggest flag is on, ceiling becomes SUGGEST. No current test asserts INFORM-only on that field; still document in implementation.

**F-V625-A03 — LOW — PRIVATE suggestions persist without UI**  
Intentional; avoids HUD leak. Operators may think SUGGEST is “broken” for email-backed PRIVATE predictions — those stay silent on HUD (correct).

**F-V625-A04 — INFORMATIONAL — no learning from dismiss**  
By design.

**F-V624-04 — inherited LOW**  
Do not put suggestions on WorldSnapshot.

---

V6.2.5 ARCHITECTURE:  
**PASS WITH NON-BLOCKING FINDINGS**

IMPLEMENTATION:  
**NOT STARTED**

DATABASE CHANGES:  
**PLANNED** (additive `world_suggestions`, deliveries, events; `suggest_count` on attention)

SUGGEST AUTHORITY:  
**Recommend only from ACTIVE non-SENSITIVE V6.2.4 predictions via templates; HUD for NORMAL; persist-only for PRIVATE; STOP. No execute.**

PREPARE:  
**NOT INCLUDED**

ASK:  
**NOT INCLUDED**

LLM:  
**NOT PLANNED** (defer to V6.2.8)

ACT:  
**NOT INCLUDED**

V6.2.4:  
**UNCHANGED**

V6.2.6:  
**NOT STARTED**

GIT:  
**NO COMMIT**  
**NO TAG**  
**NO PUSH**
