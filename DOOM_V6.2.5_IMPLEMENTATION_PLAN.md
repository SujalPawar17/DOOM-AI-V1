# DOOM V6.2.5 — Implementation Plan

**SUGGEST (recommendation only)**

**Mode:** IMPLEMENTATION CONTRACT — source, schema, and tests follow this document; no commit, tag, or push unless the owner later requests them 
**Date:** 2026-09-09  
**Authoritative architecture:** `DOOM_V6.2.5_ARCHITECTURE_AUDIT.md`  
**Audit verdict:** PASS WITH NON-BLOCKING FINDINGS  
**This plan does not authorize implementation.**

---

## Frozen baseline

| Item | Value |
|------|--------|
| Branch | `DOOM-V5.2` |
| Official release | `v6.2.4` |
| Release commit | `26b8c169f8540f94490ff271a648225d0a8ebdec` |
| Tag object | `269139ee88a592244e0dc52ca044eba120c1cc1b` |
| Immutable prior | `v6.2.3` → `391f88f3d54fde3f54e41f75cd6bee7062bfc56d` |

Do not retag `v6.2.3` or `v6.2.4`. Do not modify `proactive/predict.py`, `evidence.py`, `temporal.py`, `significance.py`, connectors, SafeHttp, TaskEngine, tools, memory, or `proactive_insights` CHECK.

---

## Objective and hard boundary

```
ACTIVE V6.2.4 prediction
  → evaluate_suggest_worthiness (pure)
  → world_suggestions upsert
  → deterministic template
  → NORMAL HUD/WS (PRIVATE persist-only)
  → STOP
```

**Not in V6.2.5:** PREPARE, ASK, LLM, ACT, tools, TaskEngine, connector WRITE, memory writes, `WorldSnapshot.suggestions`, SUGGEST rows in `proactive_insights`, SUGGEST jobs on `claim_batch`.

---

## Constants (locked)

| Name | Value |
|------|--------|
| `PROACTIVE_SUGGEST_ENABLED` | default **false**; also requires `PROACTIVE_ENABLED` and `PROACTIVE_PREDICTION_ENABLED` |
| `DAILY_SUGGEST_BUDGET` | **4** |
| `SUGGEST_COOLDOWN_SECONDS` | **14400** (4 hours) |
| `SUGGEST_CANDIDATE_CAP` | **50** |
| `SUGGEST_CONFIDENCE_FLOOR` | **0.70** |
| `SUGGEST_TTL_SECONDS` | `min(prediction.valid_until, now + 86400)` |
| `SUGGEST_RULE_VERSION` | **`v625.1`** |
| `TTS` | **false** (do not change `TTS_PROACTIVE_ALLOWED`) |

Cooldown JSON key: `suggest|{fingerprint}`.

---

# Section 1 — Current codebase audit (reuse)

Inspected live at v6.2.4.

| Asset | Location | Reuse in V6.2.5 |
|-------|----------|-----------------|
| Worker tick | `proactive/worker.py` `process_once` | After `evaluate_world_predictions()` try/except (lines ~186–190), add a **second** isolated try for `evaluate_world_suggestions()`; then existing `claim_batch` |
| Prediction read | `ProactiveStore.list_active_predictions` | **Do not change** (snapshot shape). Add **new** `list_suggest_candidates` that also returns `fingerprint`, `subject_key`, `horizon_end`, `valid_until`, `status`, `provenance.evidence_ids` |
| Expire predictions | `expire_world_predictions` | Call first inside suggest eval, then expire/supersede suggestions |
| INFORM insight insert | `insert_insight` | **Forbidden** from suggest path |
| INFORM delivery | `deliver_inform` | **Do not change semantics.** Clone pattern as `deliver_suggest` |
| HUD INFORM GET | `GET /api/proactive/insights` + `list_hud_insights` | **Unchanged.** New GET for suggestions |
| WS | `delivery.deliver_inform` inline `dashboard_loop` / `connected_clients`; also `broadcast_hud_event` | SUGGEST WS uses same `run_coroutine_threadsafe` pattern with **different** JSON `type` |
| Attention | `may_inform` / `record_inform` / `bump_attention` | Clone `may_suggest` / `record_suggest`; additive `suggest_count` |
| Templates | `proactive/templates.py` `render_inform` | **Do not reuse strings.** New `suggest_templates.py` |
| Significance | `evaluate_significance` | **Do not import** from `suggest.py` |
| Schema init | `postgres_db.ensure_schema` after V6.2.4 world_* DDL | Append V6.2.5 `CREATE TABLE IF NOT EXISTS` |
| OTP | `emit_proactive` | New event names; extend `ALLOWED_ATTR_KEYS` |
| Config flags | `_bool_env` pattern | `is_suggest_enabled()` |

**Do not modify:** `predict.py`, `evidence.py`, `temporal.py`, `significance.py`, `memory/*`, TaskEngine, `tools/`, `proactive/connectors/*`, `http_safe.py`, model router.

---

# Section 2 — File map

| File | NEW/MOD | Purpose | Deps | Security | Tests |
|------|---------|---------|------|----------|-------|
| `database/postgres_db.py` | MOD | Additive DDL + `suggest_count` | `ensure_schema` | no secrets | schema CHECKs |
| `proactive/config.py` | MOD | flag, budget, cooldown, cap, `v625.1` | env | default off | flag tests |
| `config_example.txt` | MOD | commented `PROACTIVE_SUGGEST_ENABLED` | — | no secrets | — |
| `proactive/suggest_templates.py` | NEW | Constant templates + `render_suggest` | none | allowlist params | template verb scan |
| `proactive/suggest.py` | NEW | fingerprint, worthiness, `evaluate_world_suggestions` | store, templates, attention, otp | no tools/LLM | 1–36 |
| `proactive/store.py` | MOD | candidate list + suggestion CRUD | PG | owner predicates | upsert/dismiss |
| `proactive/attention.py` | MOD | `may_suggest`, `record_suggest` | store, busy/quiet | separate budget | cooldown/budget |
| `proactive/delivery.py` | MOD | add `deliver_suggest` **below** existing INFORM; do not alter `deliver_inform` body | store, templates, otp | NORMAL only HUD | delivery retry |
| `proactive/worker.py` | MOD | isolated suggest stage | `suggest.evaluate_world_suggestions` | try/except | isolation test |
| `dashboard/server.py` | MOD | GET suggestions, POST dismiss, `suggest_enabled` on status | store, flags | owner + NORMAL | API tests |
| `observability/schemas.py` | MOD | allow `suggestion_id`, `suggestion_type` | — | still forbid body | OTP test |
| `test_v625_suggest.py` | NEW | 36 tests | PG | fixtures | all |

**Not in map:** `snapshot.py` (no `suggestions` field), `significance.py`, `predict.py`.

---

# Section 3 — Database implementation

Placement: immediately **after** V6.2.4 `world_prediction_events` statements in `ensure_schema`. `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` only. **No DROP.** **No** `ALTER` on `proactive_insights`.

Also: `ALTER TABLE proactive_attention ADD COLUMN IF NOT EXISTS suggest_count INTEGER NOT NULL DEFAULT 0;`

### 3.1 `world_suggestions`

```
suggestion_id     VARCHAR(64) PK
owner_id          VARCHAR(64) NOT NULL
project_id        VARCHAR(64) NULL REFERENCES projects(project_id) ON DELETE SET NULL
prediction_id     VARCHAR(64) NOT NULL REFERENCES world_predictions(prediction_id) ON DELETE CASCADE
suggestion_type   VARCHAR(40) NOT NULL CHECK (IN (
                    'CONSIDER_REVIEW_WORK','CONSIDER_CONFIRM_OPEN',
                    'CONSIDER_RECONCILE_TIME','CONSIDER_UNBLOCK','CONSIDER_COMPLETE_REVIEW'))
claim_code        VARCHAR(40) NOT NULL
template_id       VARCHAR(64) NOT NULL
safe_params       JSONB NOT NULL DEFAULT '{}'
priority          VARCHAR(16) NOT NULL CHECK (IN ('LOW','MEDIUM','HIGH'))
confidence        REAL NOT NULL CHECK (0..1)
risk_class        VARCHAR(16) NOT NULL CHECK (IN ('NONE','LOW','MEDIUM','HIGH'))
privacy_class     VARCHAR(16) NOT NULL CHECK (IN ('NORMAL','PRIVATE','SENSITIVE'))
fingerprint       VARCHAR(64) NOT NULL
rule_id           VARCHAR(40) NOT NULL
rule_version      VARCHAR(16) NOT NULL DEFAULT 'v625.1'
status            VARCHAR(16) NOT NULL DEFAULT 'OPEN'
                  CHECK (IN ('OPEN','DELIVERED','DISMISSED','EXPIRED','SUPERSEDED'))
valid_until       TIMESTAMPTZ NOT NULL
provenance        JSONB NOT NULL DEFAULT '{}'
created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
evaluated_at      TIMESTAMPTZ NOT NULL
dismissed_at      TIMESTAMPTZ NULL
UNIQUE (owner_id, fingerprint)
```

Indexes: `(owner_id, status, valid_until)`, `(prediction_id)`.

**Forbidden in JSON:** subject, snippet, body, url, token, repo, calendar summary, action payloads, tool names.

Application CHECK on `safe_params` keys ⊆ `{risk_class, horizon_hours, prediction_type, claim_code}`. `horizon_hours` integer 0–168. Enums must match prediction CHECKs.

**Retention:** no DELETE. EXPIRED/SUPERSEDED/DISMISSED remain for audit.

### 3.2 Type map (exact)

| `world_predictions.prediction_type` | `suggestion_type` | `template_id` |
|-------------------------------------|-------------------|---------------|
| `DEADLINE_HORIZON` | `CONSIDER_REVIEW_WORK` | `suggest_review_work` |
| `STALE_OPEN_COMMITMENT` | `CONSIDER_CONFIRM_OPEN` | `suggest_confirm_open` |
| `CAL_VS_COMMITMENT_CONFLICT` | `CONSIDER_RECONCILE_TIME` | `suggest_reconcile_time` |
| `TASK_BLOCKED_NEAR_DEADLINE` | `CONSIDER_UNBLOCK` | `suggest_unblock` |
| `OPEN_REVIEW_AGING` | `CONSIDER_COMPLETE_REVIEW` | `suggest_complete_review` |

### 3.3 `world_suggestion_deliveries`

```
delivery_id    VARCHAR(64) PK
suggestion_id  VARCHAR(64) NOT NULL REFERENCES world_suggestions(suggestion_id) ON DELETE CASCADE
channel        VARCHAR(16) NOT NULL DEFAULT 'hud' CHECK (channel IN ('hud'))
status         VARCHAR(16) NOT NULL DEFAULT 'DELIVERED' CHECK (IN ('DELIVERED','FAILED'))
attempts       INTEGER NOT NULL DEFAULT 1
created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
UNIQUE (suggestion_id, channel)
```

V6.2.5 channel is **`hud` only**. Retry: if suggestion `OPEN` and no row → insert; unique conflict → treat as delivered (idempotent). Do not insert delivery for PRIVATE.

### 3.4 `world_suggestion_events`

```
event_id       VARCHAR(64) PK
suggestion_id  VARCHAR(64) NOT NULL REFERENCES world_suggestions(suggestion_id) ON DELETE CASCADE
from_status    VARCHAR(16)
to_status      VARCHAR(16) NOT NULL
reason         VARCHAR(40) NOT NULL DEFAULT ''
created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

Append-only. Write on create, deliver, dismiss, expire, supersede (**≤1 event per transition**, not per candidate IGNORE). IGNORE worthiness = OTP only, **no** event row (avoids unbounded writes).

Retention: V6.2.5 **no DELETE job** (same class as prediction events). Document as accepted LOW volume (≤50 creates/tick worst case, typically far less).

---

# Section 4 — Worthiness engine

**File:** `proactive/suggest.py`  
**Function:** `evaluate_suggest_worthiness(pred: dict, now: float) -> Tuple[str, str]`  
Returns `("IGNORE"|"SUGGEST", reason_code)`.

**Pure.** Clock `now` is injected. No `existing` row, no `attention_ok` / `attention_reason`. Attention is **only** `may_suggest()` immediately before NORMAL HUD delivery. **Must not** import `evaluate_significance`.

**Inputs:** prediction dict from `list_suggest_candidates` (`status`, `valid_until` epoch, `privacy_class`, `confidence`, `risk_class`, `prediction_type`, `evidence_ids`).  
**Outputs:** decision + `abstain_reason`.  
**DB:** none.  
**Security:** no external text.

### Decision tree (order)

1. `status != 'ACTIVE'` → IGNORE `NOT_ACTIVE`  
2. `valid_until <= now` → IGNORE `EXPIRED_PREDICTION`  
3. `privacy_class == 'SENSITIVE'` → IGNORE `PRIVACY_BLOCK` (defense; loader already excludes)  
4. `confidence < 0.70` → IGNORE `LOW_CONFIDENCE`  

Existing-row handling is **not** in this function. The evaluator applies fingerprint state after a SUGGEST decision:

| Existing status | Evaluator |
|-----------------|-----------|
| none | INSERT then delivery eligibility |
| `OPEN` | **reuse**; continue to `may_suggest` / `deliver_suggest` (delivery retry) |
| `DELIVERED` | refresh `evaluated_at` only; **no second delivery** |
| `DISMISSED` | never reopen; skip |
| `EXPIRED` / `SUPERSEDED` | terminal; skip |

Busy/quiet/budget/cooldown run in `evaluate_world_suggestions` **only before `deliver_suggest`**. If attention blocks, leave the row **OPEN**.

**F-V624-01 gate (before type×risk):**  
If `prediction_type == 'STALE_OPEN_COMMITMENT'` AND `privacy_class == 'PRIVATE'` AND `len(evidence_ids) < 2` → IGNORE `STALE_WEAK_PRIVATE`.

**Type × risk:**

| Type | SUGGEST if | Else |
|------|------------|------|
| `DEADLINE_HORIZON` | `risk_class in (MEDIUM, HIGH)` | IGNORE `LOW_URGENCY` |
| `STALE_OPEN_COMMITMENT` | passed stale gate | IGNORE from gate |
| `CAL_VS_COMMITMENT_CONFLICT` | always | — |
| `TASK_BLOCKED_NEAR_DEADLINE` | always | — |
| `OPEN_REVIEW_AGING` | always | — |
| other | IGNORE `UNKNOWN_TYPE` | — |

Priority: HIGH risk → `HIGH`; OPEN_REVIEW_AGING → `LOW`; else `MEDIUM`.

---

# Section 5 — Fingerprint

```python
def suggestion_fingerprint(owner_id, prediction_fingerprint, suggestion_type, template_id, rule_version="v625.1") -> str:
    raw = f"{owner_id}|{prediction_fingerprint}|{suggestion_type}|{template_id}|{rule_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]
```

**Do not** include `generation`.

`get_suggestion_by_fingerprint(owner_id, fingerprint)` for DEDUP/DISMISSED.

ON CONFLICT `(owner_id, fingerprint)`:

- If existing `DISMISSED`: **DO NOTHING** (keep dismissed; return existing id; no reopen).  
- If `OPEN`: update `evaluated_at` (and metadata); **keep OPEN**; continue to delivery eligibility (`may_suggest` / `deliver_suggest`). OPEN is a delivery-retry candidate, not a stop.  
- If `DELIVERED`: update `evaluated_at`, `confidence`, `risk_class`, `valid_until` from prediction; **do not** change status; **no second delivery**.  
- If `EXPIRED`/`SUPERSEDED`: **DO NOTHING** unless prediction fingerprint is new (it cannot be — same UNIQUE). New identity only if template/type/version/prediction.fingerprint changes.

---

# Section 6 — Templates

**File:** `proactive/suggest_templates.py`

```python
SUGGEST_TEMPLATES = {
  "suggest_review_work": "A recorded deadline is approaching. Consider reviewing remaining work for that item.",
  "suggest_confirm_open": "An open commitment is past its recorded due time. Consider confirming whether it is still relevant.",
  "suggest_reconcile_time": "A calendar time and a recorded commitment disagree. Consider reconciling the schedule.",
  "suggest_unblock": "A blocked task sits near a recorded deadline. Consider unblocking or replanning that work.",
  "suggest_complete_review": "An open review is older than seven days. Consider completing or declining the review.",
}
ALLOWED_PARAM_KEYS = frozenset({"risk_class", "horizon_hours", "prediction_type", "claim_code"})
FORBIDDEN_TEMPLATE_SUBSTRINGS = ("create ", "send ", "execute", "run ", "approve", "merge", "update calendar", "open github")
```

`render_suggest(template_id, params)`: reject unknown `template_id`; copy only allowlisted keys; `horizon_hours` int clamp 0–168; format **must not interpolate** prediction_type into a URL. Prefer templates with **no `{placeholders}`** except optional `{horizon_hours}` on `suggest_review_work` only (`Consider reviewing remaining work ({horizon_hours} hours).`) — if used, still integer-only.

**Locked choice (no OWNER DECISION):** templates are **fixed sentences with zero placeholders** except `suggest_review_work` may include `{horizon_hours}` as int. Safer default: **zero placeholders** (architecture: “enums and integers” may exist in `safe_params` for HUD metadata without appearing in prose). **This plan: zero placeholders in user-visible strings.** `safe_params` still stored for API metadata.

Tests scan template values for forbidden verbs.

---

# Section 7 — Worker

**File:** `proactive/worker.py`  
**Function:** `process_once`

Insert **immediately after** the existing prediction `try/except`, **before** `claim_batch`:

```python
        try:
            evaluate_world_predictions()
        except Exception:
            emit_proactive("proactive.outcome", status="error", attributes={"reason": "prediction_eval"})
        try:
            from proactive.suggest import evaluate_world_suggestions
            evaluate_world_suggestions()
        except Exception:
            emit_proactive("proactive.outcome", status="error", attributes={"reason": "suggestion_eval"})
        batch = proactive_store.claim_batch(worker_id)
```

Import at module top is acceptable (`from proactive.suggest import evaluate_world_suggestions`) if it does not import worker (no cycle). `suggest.py` must not import `worker`.

`process_once` still returns INFORM processed count only (unchanged).

---

# Section 8 — `evaluate_world_suggestions`

**File:** `proactive/suggest.py`  
**Signature:** `evaluate_world_suggestions(owner_id: str = OWNER_ID) -> int`  
**Returns:** count of **new** suggestion rows inserted (not delivery retries). Never raises (inner try like predict).

**Steps:**

1. If not (`is_proactive_enabled()` ∧ `is_prediction_enabled()` ∧ `is_suggest_enabled()`): return 0.  
2. `now = time.time()`.  
3. `proactive_store.sync_suggestion_lifecycle(owner_id)` — EXPIRE rows whose prediction is EXPIRED/`valid_until` past; SUPERSEDE rows whose prediction is SUPERSEDED/INVALIDATED or not ACTIVE. Event rows for transitions.  
4. `cands = list_suggest_candidates(owner_id, 50)`.  
5. For each `pred`: map type → suggestion_type + template_id; compute fingerprint; `evaluate_suggest_worthiness(pred, now)` (pure). If IGNORE: OTP skipped + `abstain_reason`; continue (no row, no delivery).  
6. Load existing by fingerprint. `DISMISSED` / `EXPIRED` / `SUPERSEDED` → skip (never reopen). `DELIVERED` → optional `evaluated_at` refresh; **no** `deliver_suggest`.  
7. If none: INSERT (`upsert_world_suggestion`); event `OPEN` on insert only. If `OPEN`: **reuse the same row** (refresh `evaluated_at`); do **not** treat OPEN as terminal.  
8. If `privacy_class == 'PRIVATE'`: OTP evaluated; **no** `deliver_suggest`.  
9. If `NORMAL` and status is `OPEN` (new or reused): `ok, why = may_suggest(fp)`; if not ok: leave OPEN, OTP skipped attention (retry next tick); if ok: `deliver_suggest(suggestion)`.  
10. Never: `insert_insight`, `ingest_signal`, `claim_batch`, TaskEngine, tools, `model_router`, `evaluate_significance`.

**Inputs:** flags, PG predictions. **Outputs:** int. **DB:** suggestions + events + optional deliveries. **Security:** owner_id on every SQL.

---

# Section 9 — Attention

**File:** `proactive/attention.py`

- `may_suggest(dedupe_key, owner_id=OWNER_ID) -> Tuple[bool, str]`  
  Same `cognition_busy` / `in_quiet_hours` as INFORM. Then `get_attention`; `suggest_count >= DAILY_SUGGEST_BUDGET` → `budget`. Cooldown key `suggest|{dedupe_key}` vs `SUGGEST_COOLDOWN_SECONDS`.  
- `record_suggest(dedupe_key, owner_id)` → `bump_suggest_attention`  

**File:** `proactive/store.py`

- Extend `bump_attention` **or** new `bump_suggest_attention`: increment **`suggest_count` only** (not `inform_count`); merge cooldown `suggest|{key}`.  
- `get_attention` must SELECT `suggest_count` (default 0 if column missing during migrate — column added IF NOT EXISTS).

**Increment only** when `deliver_suggest` creates a **new** delivery row (`created=True`). Not PRIVATE, not duplicate, not persist_failed.

---

# Section 10 — Delivery

**File:** `proactive/delivery.py`  
**New:** `deliver_suggest(suggestion) -> bool`

Mirror `deliver_inform` **without editing its lines**:

- If `TTS_PROACTIVE_ALLOWED`: return False (same kill switch).  
- If `privacy_class != 'NORMAL'`: return False (do not write delivery).  
- `did, created = upsert_suggestion_delivery(suggestion_id, "hud")`.  
- Fail persist → OTP error, return False.  
- Duplicate → OTP skipped, return True (durable ack).  
- New: `record_suggest(fingerprint)`; HUD ring optional **separate** list `_suggest_hud` **or** skip in-memory ring and rely on GET (prefer **PG-only** like insights GET; in-memory INFORM ring is legacy). **Locked:** do **not** push SUGGEST cards into `_hud` INFORM list. WS send card:

```json
{
  "type": "proactive_suggestion",
  "suggestion_id": "...",
  "intervention": "SUGGEST",
  "template_id": "...",
  "message": "<render_suggest>",
  "priority": "MEDIUM",
  "privacy_class": "NORMAL",
  "tts": false
}
```

Then `UPDATE world_suggestions SET status='DELIVERED'` if currently OPEN (same transaction as delivery insert preferred).

**Atomicity:** `persist_normal_suggestion_delivery(suggestion)` in store: INSERT delivery UNIQUE + UPDATE status DELIVERED + event; one commit. `deliver_suggest` calls that then WS best-effort **after** commit (WS fail ≠ rollback).

---

# Section 11 — API

**File:** `dashboard/server.py`

### `GET /api/proactive/suggestions`

- If not (`is_proactive_enabled()` and `is_suggest_enabled()`): `{suggestions:[], count:0, enabled:false}`.  
- `list_hud_suggestions(owner_id=OWNER_ID, limit=min(limit, MAX_HUD))` — SQL: `owner_id`, `privacy_class='NORMAL'`, `status IN ('OPEN','DELIVERED')`, `valid_until > NOW()`.  
- Render message via `render_suggest`. Never 500 on bad row.

### `POST /api/proactive/suggestions/{id}/dismiss`

- Flags off → 403 or `{ok:false}` empty (match dashboard style; **locked:** return `{"ok": false, "error": "disabled"}` with 200/403 — prefer **403** if other dashboard mutating routes use it; insights are GET-only. **Use 200 `{ok:false}` for flag off, 404 if wrong owner/missing**, to avoid new auth stack).  
- `UPDATE ... SET status='DISMISSED', dismissed_at=NOW() WHERE suggestion_id=%s AND owner_id=%s AND status IN ('OPEN','DELIVERED')`  
- 0 rows → 404.  
- Event + OTP `proactive.suggestion.dismissed`.  
- No tools.

### `GET /api/proactive/status` (F-V625-A02)

Add `"suggest_enabled": is_suggest_enabled() and is_proactive_enabled() and is_prediction_enabled()`.

`max_intervention`:

- flags: proactive off → `"NONE"` (unchanged)  
- proactive on, suggest off → `"INFORM"` (**unchanged INFORM ceiling**)  
- proactive on, suggest on → `"SUGGEST"` (authority ceiling, **does not disable INFORM processing**)

INFORM `claim_batch` still runs. Tests grepping `max_intervention` were **none** as of audit.

---

# Section 12 — HUD / snapshot

- Card/WS `type` **must be** `proactive_suggestion`.  
- **Forbidden:** `proactive_event`, mixing into `/insights`.  
- **Do not** add `WorldSnapshot.suggestions`.  
- **Do not** change `proactive/snapshot.py`.  
- Do not serialize snapshot to WS.

---

# Section 13 — Privacy

| Class | Insert `world_suggestions` | Delivery / GET / WS |
|-------|----------------------------|---------------------|
| NORMAL | yes | yes |
| PRIVATE | yes | no |
| SENSITIVE | no | no |

`safe_params` and OTP: only allowlisted enums/ints. No subject/snippet/body/repo/calendar summary/URL/token.

---

# Section 14 — Security

`suggest.py` / `suggest_templates.py` / new store methods / `deliver_suggest` / dismiss:

- No `task_engine`, `tool_registry`, `SafeHttp`, connectors, `subprocess`, filesystem writes, `process_request`, `model_router`.  
- AST test on those files.  
- Template render: allowlisted keys only.

---

# Section 15 — Observability

Events: `proactive.suggestion.evaluated` | `.delivered` | `.dismissed` | `.expired`  
`evaluated` uses `status=ok|skipped|error`. Expire batch: `attributes.count` (int), not id lists.

Allowlist add: `suggestion_id`, `suggestion_type` (plus existing `prediction_id`, `rule_id`, `evidence_n` unused, `confidence_bucket`, `risk_class`, `abstain_reason`).

---

# Section 16 — Test plan (`test_v625_suggest.py`)

36 tests, class `TestV625Suggest`. PG skip if disconnected. Do not implement now.

| Test | Purpose | Expected |
|------|---------|----------|
| `test_01_suggest_flag_off_zero` | suggest flag false | 0 rows |
| `test_02_prediction_flag_off_zero` | prediction false, suggest true | 0 rows |
| `test_03_proactive_flag_off_zero` | process_once no-op | 0 rows |
| `test_04_deadline_medium_suggests` | DEADLINE + MEDIUM NORMAL | CONSIDER_REVIEW_WORK OPEN/DELIVERED |
| `test_05_stale_normal_suggests` | STALE NORMAL N≥2 | CONSIDER_CONFIRM_OPEN |
| `test_06_conflict_suggests` | CONFLICT | CONSIDER_RECONCILE_TIME |
| `test_07_task_blocked_suggests` | TASK_BLOCKED | CONSIDER_UNBLOCK |
| `test_08_review_aging_suggests` | OPEN_REVIEW_AGING | CONSIDER_COMPLETE_REVIEW |
| `test_09_deadline_low_silent` | DEADLINE LOW | no row |
| `test_10_sensitive_none` | SENSITIVE pred | no row |
| `test_11_private_persist_no_hud` | PRIVATE worthiness pass | row exists; GET suggestions empty |
| `test_12_duplicate_tick_one_row` | eval twice | count=1 |
| `test_13_concurrent_upsert_one_row` | two threads upsert | count=1 |
| `test_14_cooldown_no_second_delivery` | second deliver | suggest_count not +2 |
| `test_15_daily_budget` | 4 deliveries then suppress HUD | 5th stays OPEN or skipped deliver |
| `test_16_fingerprint_stable` | same inputs | same hash |
| `test_17_dismissed_not_reopened` | dismiss then eval | still DISMISSED |
| `test_18_prediction_expired_suggestion_expired` | expire pred | suggestion EXPIRED |
| `test_19_prediction_superseded_suggestion_superseded` | pred SUPERSEDED | suggestion SUPERSEDED |
| `test_20_delivery_retry` | OPEN no delivery then eval | delivery row appears |
| `test_21_suggest_exception_inform_runs` | patch eval raise | `process_once` still int / claim path |
| `test_22_cross_owner_hidden` | alice row | sujal GET empty |
| `test_23_ast_no_authority` | parse suggest*.py + worker delta | no TaskEngine/tools/insert_insight/ingest_signal/model_router/evaluate_significance |
| `test_24_memory_count_invariant` | before/after eval | equal |
| `test_25_insights_api_inform_only` | SUGGEST row present | `/insights` has no SUGGEST |
| `test_26_templates_no_action_verbs` | corpus scan | no forbidden verbs |
| `test_27_injection_not_in_safe_params` | pred provenance junk | safe_params keys allowlist only |
| `test_28_stale_private_single_evidence_ignore` | F-V624-01 | no suggestion |
| `test_29_no_prediction_eval_signal` | SIGNAL_TYPES | no PREDICTION_EVAL / SUGGEST_EVAL |
| `test_30_busy_or_quiet_no_budget_bump` | may_suggest false | suggest_count unchanged |
| `test_31_snapshot_has_no_suggestions_field` | WorldSnapshot dataclass | no `suggestions` |
| `test_32_otp_suggestion_id_allowed` | ALLOWED_ATTR_KEYS | suggestion_id in; body forbidden |
| `test_33_dismiss_404_other_owner` | POST other id | 404 |
| `test_34_no_significance_import` | ast/import suggest.py | `evaluate_significance` absent |
| `test_35_candidate_cap_50` | 51 ACTIVE preds | ≤50 suggestions attempted (count ≤50) |
| `test_36_v624_suite_still_imports` | import test_v624 | module loads (full 40 run in regression §17) |

---

# Section 17 — Regression (after implementation)

| Suite | Gate |
|-------|------|
| `test_v624_evidence_prediction` | 40/40 |
| `test_v623_email_commitments` | 11/11 |
| `test_v62_connectors` | 12/12 |
| `test_v62_emitters` | 13/13 |
| `test_v61_proactive_foundation` | 72 passed; HUD 95–98 TestClient **F-V622-07** tracked separately |
| `test_v532_transaction_engine` | 30/30 |

---

# Section 18 — Performance

- `list_suggest_candidates` LIMIT 50, index `(owner_id, status, valid_until)`  
- No LLM, embeddings, HTTP  
- No second worker / no `while True`  
- **Target (not a claim):** <100 ms local at 50 candidates — measure in implementation report  

---

# Section 19 — Failure / recovery

| Failure | Behavior |
|---------|----------|
| PG down | eval no-op |
| Crash after suggestion, before delivery | next tick `test_20` path |
| Duplicate tick | UNIQUE fingerprint |
| Two workers | one INSERT winner |
| WS down | GET still lists DELIVERED/OPEN NORMAL |
| Prediction expired | `sync_suggestion_lifecycle` EXPIRED + event |
| Prediction superseded | SUPERSEDED + event |
| Dismiss | DISMISSED; unique conflict does not reopen |
| Delivery insert fail | rollback delivery; suggestion remains OPEN |
| Suggestion insert fail | rollback; no delivery |

NORMAL persist+delivery: **one store transaction** (`insert suggestion` if new + `insert delivery` + status DELIVERED). If suggestion already OPEN from attention deferral, delivery-only transaction.

No orphan delivery (FK CASCADE + insert delivery only with valid suggestion_id).

---

# Section 20 — Concurrency

`UNIQUE (owner_id, fingerprint)` is the lock. Application `SELECT` before insert is insufficient. `ON CONFLICT DO UPDATE` with dismissed guard: use `WHERE world_suggestions.status <> 'DISMISSED'` on the UPDATE set so dismissed rows are not rewritten. PostgreSQL `ON CONFLICT ... DO UPDATE WHERE`.

---

# Section 21 — F-V624-01 mitigation

**Do not change V6.2.4.** In `evaluate_suggest_worthiness`:

`STALE_OPEN_COMMITMENT` + `PRIVATE` + `len(evidence_ids) < 2` → IGNORE `STALE_WEAK_PRIVATE`.

`evidence_ids` from `world_predictions.provenance.evidence_ids` (already stored by V6.2.4 `_emit`).

---

# Section 22 — F-V625-A02 `max_intervention`

See §11. INFORM `claim_batch` and `/insights` **unchanged**. Status field is a **ceiling label**, not a feature kill switch.

---

# Section 23 — Release boundary

No PREPARE/ASK/LLM/ACT modules, no insight CHECK widen, no connector writes, no README release until owner release prompt (implementation may leave README as V6.2.4 released / V6.2.5 planned — **do not mark V6.2.5 released in this slice** unless a later release prompt says so).

---

# Section 24 — Implementation order

1. DDL (`world_suggestions*`, `suggest_count`)  
2. `config.py` + `config_example.txt`  
3. Store: candidates, upsert, get-by-fingerprint, lifecycle sync, HUD list, dismiss, delivery upsert  
4. `evaluate_suggest_worthiness` + fingerprint  
5. `suggest_templates.py`  
6. `may_suggest` / `record_suggest` / bump  
7. `evaluate_world_suggestions`  
8. `worker.py` isolated call  
9. `deliver_suggest`  
10. Dashboard GET/POST/status  
11. WS card type in `deliver_suggest`  
12. OTP allowlist  
13. `test_v625_suggest.py` 36  
14. Regression §17  
15. `DOOM_V6.2.5_IMPLEMENTATION_REPORT.md`  
16. Independent forensic audit  
17. Owner-requested commit/tag/push only  

---

# Section 25 — Git

No implementation commit, tag, or push. This plan is working-tree documentation only.

---

# Section 26 — Acceptance gates (implementation checks)

| Gate | Check |
|------|--------|
| G1 | Loader = `world_predictions` only |
| G2 | No fact/commitment/signal/memory reads in `suggest.py` |
| G3 | SENSITIVE never INSERT |
| G4 | All SQL `owner_id = %s` |
| G5 | Worthiness no I/O |
| G6 | UNIQUE fingerprint |
| G7 | `suggest|` cooldown |
| G8 | `sync_suggestion_lifecycle` |
| G9–G12 | AST + no new tool/HTTP |
| G13 | memory count test |
| G14 | template allowlist |
| G15 | default false |
| G16 | OPEN without delivery retries |
| G17 | concurrent test |
| G18 | UNIQUE (suggestion_id, channel) |
| G19 | `/insights` + `deliver_inform` untouched semantics |
| G20 | no PREPARE/ASK/LLM |
| G21 | `type=proactive_suggestion` |
| G22 | no snapshot field |
| G23 | prediction flag required |
| G24 | `test_28` |

---

# Section 27 — Implementation checklist

- [ ] Architecture decisions unchanged  
- [ ] Additive schema only; insights CHECK intact  
- [ ] Flags default off; triple-flag AND  
- [ ] Worthiness + F-V624-01 gate  
- [ ] Templates advisory, no action verbs, no source text  
- [ ] Worker: predict → suggest → claim_batch; dual isolation  
- [ ] Separate suggest budget 4 / cooldown 4h  
- [ ] NORMAL HUD/WS; PRIVATE persist-only; SENSITIVE none  
- [ ] Distinct API + card type; no snapshot suggestions  
- [ ] Dismiss owner-scoped; not ASK  
- [ ] OTP metadata only  
- [ ] 36 tests + regression suites  
- [ ] No V6.2.4 file edits (`predict`/`evidence`/`temporal`)  
- [ ] No commit/tag/push until owner release  
- [ ] V6.2.6 not started  

---

## Store methods to add (exact names)

| Method | Role |
|--------|------|
| `list_suggest_candidates(owner_id, limit=50)` | ACTIVE, valid, non-SENSITIVE, extra columns |
| `get_suggestion_by_fingerprint(owner_id, fp)` | existing row or None |
| `upsert_world_suggestion(row)` | insert/conflict dismissed-safe |
| `sync_suggestion_lifecycle(owner_id)` | expire/supersede |
| `list_hud_suggestions(owner_id, limit)` | NORMAL OPEN/DELIVERED |
| `dismiss_suggestion(suggestion_id, owner_id)` | bool |
| `upsert_suggestion_delivery(suggestion_id, channel)` | `(id, created)` |
| `suggestion_delivery_exists(suggestion_id, channel)` | bool |
| `bump_suggest_attention(...)` | suggest_count++ |

---

## OWNER DECISION REQUIRED

**None.** Defaults are locked by the architecture audit and this plan.

---

V6.2.5 ARCHITECTURE:  
**PASS WITH NON-BLOCKING FINDINGS**

V6.2.5 IMPLEMENTATION:  
**COMPLETE (not released)**

V6.2.5 PLAN:  
**LOCKED (OPEN retry + pure worthiness)**

V6.2.4:  
**UNCHANGED**

V6.2.6:  
**NOT STARTED**

GIT:  
**NO COMMIT**  
**NO TAG**  
**NO PUSH**
