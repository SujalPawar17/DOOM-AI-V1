# DOOM V6.2.8 — Implementation Plan

**LLM-assisted preparation / drafting (non-authoritative; no execution)**

**Mode:** PLANNING ONLY — no source, schema, tests, config, README, commit, tag, or push  
**Date:** 2026-09-10  
**Authoritative architecture:** `DOOM_V6.2.8_ARCHITECTURE_AUDIT.md`  
**Architecture verdict:** PASS WITH NON-BLOCKING FINDINGS — IMPLEMENTATION READY  
**This plan does not authorize implementation.**

---

## 1. Executive Summary

V6.2.8 adds **bounded cognition**, not power.

```
V6.2.6 deterministic PREPARE (unchanged authority)
  → optional LLM draft (presentation JSON)
  → deterministic validation
  → persist world_preparation_drafts (if ACCEPTED)
  → existing ASK (unchanged binding)
  → STOP
```

**APPROVED** still means: the owner authorized the exact deterministic `safe_params` / `param_hash`. It does **not** mean execute the LLM body.

**Plan verdict:** **IMPLEMENTATION PLAN READY**

Non-blocking architecture findings F-V628-A01–A09 are in-slice requirements, not blockers. Inherited F-V626 / F-V625 / F-V622 findings stay out of scope.

---

## 2. Authoritative Baseline

| Item | Value |
|------|--------|
| Branch | `DOOM-V5.2` |
| Released | `v6.2.6` `7868bcdb1280607cd255013a36b8f0ac191d6625` |
| Tag object v6.2.6 | `984041831916fb0d2c5fd1e6a2c77cfb1ab9c94b` |
| Prior immutable | `v6.2.5` `de8f236…` / `v6.2.4` `26b8c169…` |

Do not retag or edit prediction, suggestion worthiness, ASK binding, or connector WRITE surface.

`PREPARED_WITHOUT_LLM` is a **conceptual outcome**, not a new `world_preparations.status`. The preparation stays `READY` / `ASKED`. Absence or `REJECTED`/`SUPERSEDED` draft is the signal.

---

## 3. Architecture Reference (locked)

- LLM is non-authoritative. Deterministic PREPARE/ASK remain source of authority.
- v1 input: allowlisted structured facts only. **No connector prose.**
- `PROACTIVE_LLM_DRAFT_UNTRUSTED_EXCERPTS`: **dormant commented config only**. Do not implement excerpt fetch or UNTRUSTED EVIDENCE population.
- Do not overload `world_preparations.status = 'DRAFT'`.
- Do not map `FUTURE_EMAIL_DRAFT`.
- Do not call `process_request`, `approve_task_action`, tools, SafeHttp, subprocess.
- Reuse `core.model_router.ModelRouter` — **no second router class**.

---

## 4. Current Repository Facts (must drive the plan)

| Fact | Implication |
|------|-------------|
| `proactive/` does not import `model_router` | Draft policy is the first allowed call site |
| `ModelRouter.generate(..., tools=)` | Wrapper **must** pass `tools=None` (F-V628-A01) |
| Success if `response.text or response.tool_calls` (`generate` L268) | Wrapper **must reject** non-empty `tool_calls` even if text exists |
| `capability_requirements["general"] = ["tool_calling"]` | Ollama excluded from `general`; add `bounded_draft` → `["reasoning"]` only |
| `provider_override` **still failovers** to the rest of the cascade (L231–232) | Privacy **cannot** use override alone. Add optional `allowed_providers` (F-V628-A02) |
| No privacy filter on router | Filter **before** `p.generate` via `allowed_providers` intersection |
| `generate` has no timeout | `draft_policy` wraps with 8s `ThreadPoolExecutor` timeout |
| Circuit breaker: 3 fails / 60s | Reuse as-is; one extra hop max inside `allowed_providers` |
| Prepare fingerprint `sha256(...).hexdigest()[:48]` | Draft fingerprint same truncation |
| Param hash `[:64]` | Store `param_hash_at_generation` CHAR/VARCHAR 64 |
| UNIQUE `(owner_id, fingerprint)` on preparations | Same uniqueness for drafts |
| Worker `process_once`: prepare then `expire_pending_asks` then `claim_batch` | Insert draft eval **between** prepare and ASK expire |
| ASK APIs already session-scoped | Draft GET uses same session; **read-only**; never generate |
| OTP allowlist | Add `draft_id`, `validation_status`, `reject_reason`, `deployment_mode` |
| `test_provider_routing_scenarios.py` | Extend for `bounded_draft` + `allowed_providers`; no live cloud |

---

## 5. Feature Flags

In `proactive/config.py` (hot-read booleans, default **false**):

| Function | Env | Default |
|----------|-----|---------|
| `is_llm_draft_enabled()` | `PROACTIVE_LLM_DRAFT_ENABLED` | false |
| `is_llm_draft_normal_enabled()` | `PROACTIVE_LLM_DRAFT_NORMAL_ENABLED` | false |
| `is_llm_draft_private_enabled()` | `PROACTIVE_LLM_DRAFT_PRIVATE_ENABLED` | false |

Master AND at call sites:

`PROACTIVE_ENABLED ∧ PREDICTION ∧ SUGGEST ∧ PREPARE ∧ LLM_DRAFT`

Plus NORMAL or PRIVATE sub-flag matching `privacy_class`.

Constants:

- `DRAFT_PROMPT_VERSION = "v628.1"`
- `LLM_DRAFT_CANDIDATE_CAP` env default **5**
- `LLM_DRAFT_TIMEOUT_SEC` default **8.0**
- `LLM_DRAFT_MAX_FAILOVER_HOPS = 2` (primary + at most 1 hop)

`PROACTIVE_LLM_DRAFT_UNTRUSTED_EXCERPTS`: **comment only** in `config_example.txt`. No `is_*` function that enables excerpts. No code path that reads connector text.

When master off: `evaluate_world_drafts` returns 0 immediately; no `model_router.generate`.

---

## 6. File Map

### 6.1 MUST CREATE

| File | Purpose |
|------|---------|
| `proactive/draft_contract.py` | Input DTO, output schema constants, JSON extract, fingerprint helper |
| `proactive/draft_prompts.py` | Immutable SYSTEM POLICY / TASK / FACTS / empty UNTRUSTED / OUTPUT CONTRACT builders |
| `proactive/draft_validate.py` | Pure validators; no I/O; no router |
| `proactive/draft_policy.py` | Privacy allowlist + timeout wrap + `model_router.generate(tools=None, task_type="bounded_draft", allowed_providers=...)` |
| `proactive/draft.py` | Worker entry `evaluate_world_drafts`; candidate loop; persist orchestration |
| `test_v628_llm_draft.py` | Dedicated suite + AST firewall |

### 6.2 MUST MODIFY

| File | Change | Why | Security |
|------|--------|-----|----------|
| `database/postgres_db.py` | Additive `world_preparation_drafts` (+ optional `world_draft_deliveries`) | Persistence | FK CASCADE to preparations; owner column |
| `proactive/config.py` | Flags + caps + prompt version | Gating | Default off |
| `config_example.txt` | Commented flags | Operator discoverability | All false; excerpts comment “not implemented” |
| `core/model_router.py` | `bounded_draft` maps + optional `allowed_providers` on `generate` | F-V628-A01/A02/A09 | Default `allowed_providers=None` preserves all existing routes |
| `proactive/store.py` | Insert/get/list/supersede drafts; delivery upsert | Same store pattern as prepare | Owner-scoped SQL |
| `proactive/worker.py` | Isolated `evaluate_world_drafts()` after prepare, before ASK expire | Isolation from INFORM | try/except `draft_eval` |
| `proactive/delivery.py` | `deliver_draft` NORMAL only | Distinct card | TTS false; persist-then-WS |
| `proactive/attention.py` | **No new counters in v1** | Candidate cap is sufficient | Avoid extra budget coupling |
| `dashboard/server.py` | GET draft by preparation_id (session, no CSRF) | Read path | Must not call generate; no execute routes |
| `observability/schemas.py` | Allowlist keys for draft metadata | OTP | Still forbid prompt/response/body |

### 6.3 MUST NOT MODIFY

`proactive/predict.py`, `evidence.py`, `temporal.py`, `significance.py`, `snapshot.py`, `suggest.py`, `suggest_templates.py`, `prepare.py` worthiness/`TYPE_MAP`/`build_safe_params`, `prepare_templates.py` allowlist, `ask.py` binding create logic, `ask_decisions.py` canonical binding string, `ask_session.py`, connectors, `http_safe.py`, `core/task_engine.py`, `core/orchestrator.py`, `core/cognition/*`, `core/tool_registry.py`, `tools/*`, `memory/*`, `README.md` (until release).

**`prepare.py` / `ask.py`:** do not gate ASK on drafts. No import of draft modules from `ask_decisions`.

**Exception (justified):** `core/model_router.py` **must** change (F-V628-A09). Smallest change: two dict entries + one optional `generate` argument. Do not change `route()`, `select_provider()`, capability maps for `coding`/`reasoning`/`general`/etc except adding the new key.

---

## 7. Model Router Change (exact)

**File:** `core/model_router.py`  
**Class:** `ModelRouter`

### 7.1 Maps (`__init__`)

Add **only**:

```text
capability_requirements["bounded_draft"] = ["reasoning"]
capability_priorities["bounded_draft"] = ["ollama", "groq", "nim", "openai", "gemini"]
```

Do **not** require `tool_calling` or `web_search`.  
Do **not** add `fallback` (not an LLM).  
Do **not** add `bedrock` unless already in other reasoning lists (today reasoning list has no bedrock — keep consistent).

Existing keys (`coding`, `reasoning`, `general`, …) **byte-identical behavior** when `task_type` is unchanged and `allowed_providers` is omitted.

### 7.2 `generate` signature

Today:

`generate(self, prompt, system_prompt=None, tools=None, task_type="general", provider_override=None)`

Add:

`allowed_providers: Optional[List[str]] = None`

After `capable_cascade` is built:

- If `allowed_providers` is `None`: current behavior.
- If a list: `capable_cascade = [n for n in capable_cascade if n in allowed_set]` (preserve cascade order).
- Empty intersection → `NoCapableProviderError` (same as today when none capable).

`provider_override` remains for tests; **draft_policy must not rely on it for privacy** because override still concatenates the rest of the cascade.

### 7.3 Tools

`draft_policy` always calls `generate(..., tools=None, task_type="bounded_draft", allowed_providers=allowed)`.

Do not change the default `tools` parameter (other callers may still pass tools).

### 7.4 Backward compatibility tests

Extend `test_provider_routing_scenarios.py` (fake providers, no live APIs):

- `task_type="general"` cascade unchanged vs pre-change snapshot of names for a given fake matrix.
- `bounded_draft` includes a provider that has `reasoning` but **not** `tool_calling`.
- `bounded_draft` **excludes** a provider that only has `web_search`.
- `allowed_providers=["ollama"]` never calls groq even if groq is first in priority.
- Omitting `allowed_providers` does not filter.

Do not weaken existing tests A–N.

---

## 8. Privacy Policy (`draft_policy.py`)

**Runs before any prompt is sent.**

| `privacy_class` | Flags | `allowed_providers` |
|-----------------|-------|---------------------|
| SENSITIVE | any | **[]** — no generate |
| PRIVATE | private flag off | **[]** |
| PRIVATE | private flag on | names where `deployment_mode == LOCAL` **and** provider is `ollama` (not `fallback`) |
| NORMAL | normal flag off | **[]** |
| NORMAL | normal flag on | intersection of `bounded_draft` capable set with `{ollama, groq, nim, openai, gemini}` that `is_available()` |

Hosted (`HOSTED_CLOUD`, `PARTNER_HOSTED`) **never** in PRIVATE allowlist (F-V628-A02, F-V628-A08 residual: local still untrusted).

If allowlist empty: skip; OTP `reject_reason=NO_COMPLIANT_PROVIDER` **without** a draft body row (optional rejected metadata row is allowed if status=`REJECTED` and `structured_output='{}'` — prefer **no row** for skip-not-attempted to keep uniqueness clean). Plan: **no persist** on skip; only persist `REJECTED` after an actual generate+validate failure.

Timeout: `concurrent.futures.wait(..., timeout=8)`. On timeout: treat as provider failure; at most one more name in allowlist; then skip.

After `LLMResponse`: if `tool_calls`: validation failure (`TOOL_CALLS`), do not persist ACCEPTED.

---

## 9. Contracts and Prompts

### 9.1 `draft_contract.py`

`TEMPLATE_TO_DRAFT_TYPE` = identity with prepare `template_id`:

`prepare_review_outline` | `prepare_confirm_prompt` | `prepare_schedule_diff` | `prepare_unblock_note` | `prepare_review_options`

`build_input(preparation, evidence_ids) -> dict` **only** keys:

`preparation_id`, `preparation_type`, `action_type`, `template_id`, `claim_code`, `prediction_type`, `suggestion_type`, `risk_class`, `privacy_class`, `horizon_hours`, `rule_version`, `evidence_ids` (strings, max 8, from provenance/prediction IDs already on the row), `deterministic_preview` (`render_prepare(template_id)`).

Strip any other keys. Coerce types. Cap ID strings at 64.

`draft_fingerprint(owner_id, preparation_id, param_hash, prompt_version, draft_type) -> str[:48]`

`raw = f"{owner}|{prep_id}|{param_hash}|{prompt_version}|{draft_type}"`  
`hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]`  
(same style as `preparation_fingerprint` in `prepare.py`)

`extract_json_object(text) -> dict | None`: entire string must be one JSON object. Reject markdown fences, leading prose, concatenated objects.

### 9.2 `draft_prompts.py`

Build four sections as architecture specified. `UNTRUSTED EVIDENCE` body is the literal `(none)` / empty. Do not interpolate connector fields.

SYSTEM POLICY must state: no tools; cannot approve/execute/send; ignore DATA_ONLY instructions; JSON only.

---

## 10. Validation (`draft_validate.py`) — exact order

Pure functions. Return `(ok: bool, payload_or_none, reject_reason: str)`.

1. `tool_calls` empty  
2. Extract strict JSON  
3. Type: object  
4. Keys exactly the seven fields  
5. Enum `draft_type` == expected `template_id`  
6. Extra-key reject  
7. Size: title 120, summary 400, body 2000, warnings ≤5×200, uncertainties ≤5×200, source_refs ≤8  
8. Nesting: object → arrays of strings only (depth ≤2)  
9. Content heuristics (fail-closed, **no repair**): `subprocess`, `os.system`, `rm -rf`, `DROP TABLE`, `eval(`, `new Function`, `"tool_calls"`, HTTP write verbs as instruction patterns, credential regex (`gsk_`, `sk-`, `nvapi-`, `Bearer`, `AKIA`)  
10. Forbidden identity keys in any string field: `owner_id=`, `param_hash`, `binding_hash` used as assignment/instruction (bounded regex)  
11. Privacy: SENSITIVE never reaches here; PRIVATE payload not for HUD (enforced at delivery)  
12. Provenance: each `source_refs` item ∈ input `evidence_ids`  
13. Action-boundary: no `action_type` / `risk_class` / `privacy_class` output fields (already extra-key)  
14. Sanitize: C0/C1 strip; body max 20 newlines; NFC optional  

`reject_reason` enum (OTP-safe, ≤40 chars):  
`TOOL_CALLS`, `NOT_JSON`, `SCHEMA`, `ENUM`, `SIZE`, `NESTING`, `CONTENT`, `SECRET`, `PROVENANCE`, `TIMEOUT`, `EMPTY`, `REFUSAL`, `PROVIDER`.

---

## 11. Persistence

### 11.1 `world_preparation_drafts`

Additive in `database/postgres_db.py` next to V6.2.6 tables:

```text
draft_id            VARCHAR(64) PK
owner_id            VARCHAR(64) NOT NULL
preparation_id      VARCHAR(64) NOT NULL
                    REFERENCES world_preparations(preparation_id) ON DELETE CASCADE
draft_type          VARCHAR(64) NOT NULL
                    CHECK (draft_type IN (the five template_id values))
structured_output   JSONB NOT NULL DEFAULT '{}'
provider            VARCHAR(32) NOT NULL DEFAULT ''
model               VARCHAR(80) NOT NULL DEFAULT ''
model_version       VARCHAR(40) NOT NULL DEFAULT ''
prompt_version      VARCHAR(16) NOT NULL DEFAULT 'v628.1'
rule_version        VARCHAR(16) NOT NULL DEFAULT 'v626.1'
param_hash_at_generation VARCHAR(64) NOT NULL
validation_status   VARCHAR(16) NOT NULL
                    CHECK (IN ('ACCEPTED','REJECTED','SUPERSEDED','EXPIRED'))
reject_reason       VARCHAR(40) NOT NULL DEFAULT ''
privacy_class       VARCHAR(16) NOT NULL
                    CHECK (IN ('NORMAL','PRIVATE','SENSITIVE'))
fingerprint         VARCHAR(64) NOT NULL
correlation_id      VARCHAR(64) NOT NULL DEFAULT ''
created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
UNIQUE (owner_id, fingerprint)
```

Indexes: `(owner_id, validation_status)`, `(preparation_id, validation_status)`.

Partial unique (idempotent current accepted):  
`UNIQUE (preparation_id) WHERE validation_status = 'ACCEPTED'`  
— one live ACCEPTED draft per preparation. ON CONFLICT: return existing id (replay).

On new generation with **different** fingerprint (param_hash changed): mark old ACCEPTED → `SUPERSEDED` in the **same transaction**, then insert.

REJECTED rows: allow multiple (no partial unique); or skip persist and OTP only. Plan: persist REJECTED **without** filling `structured_output` body (store `{}`) so audits exist; fingerprint still unique so duplicate ticks don’t flood.

### 11.2 `world_draft_deliveries`

Same pattern as `world_prepare_deliveries`: `(draft_id, channel)` UNIQUE, FK CASCADE, HUD only.

### 11.3 Store methods (`proactive/store.py`)

- `list_draft_candidates(owner_id, cap)` — READY/ASKED preparations with no ACCEPTED draft whose `param_hash_at_generation` would match live `param_hash`, privacy ≠ SENSITIVE, flags already applied in Python
- `insert_world_draft(row)` — allowlist JSON keys in `structured_output`
- `get_current_draft(preparation_id, owner_id)` — ACCEPTED and `param_hash_at_generation = preparations.param_hash` else None (stale)
- `supersede_stale_drafts(owner_id)` — ACCEPTED where hash mismatch → SUPERSEDED
- `persist_draft_delivery(draft_id, owner)`

Stale drafts **never** join ASK SQL.

---

## 12. Worker

**File:** `proactive/worker.py`  
**Function:** `process_once`

Insert after `evaluate_world_preparations()` try/except, **before** `expire_pending_asks()`:

```text
try:
    evaluate_world_drafts()
except Exception:
    emit_proactive(..., reason="draft_eval")
```

`evaluate_world_drafts` (`draft.py`):

1. If flags off: return 0  
2. `supersede_stale_drafts`  
3. List ≤5 candidates  
4. For each: build input → policy allowlist → generate wrap → validate → insert  
5. If ACCEPTED and NORMAL: `deliver_draft` (best-effort, after commit)  
6. Never raise  

Does **not** run inside `claim_batch`. INFORM unchanged.

Concurrency: worker is one thread → 1 generation at a time.

---

## 13. HUD / WS / API

**Card type:** `proactive_draft`  
Fields: `draft_id`, `preparation_id`, `title`/`summary` from validated JSON, disclaimer:

> This text is a draft. Approval authorizes the bounded preparation parameters, not execution.

`tts: false`. PRIVATE: persist only. SENSITIVE: none.

**API:** `GET /api/proactive/preparations/{preparation_id}/draft`  
- `require_ask_session(need_csrf=False)`  
- owner from session  
- returns current ACCEPTED draft or `{ok:false, current:false}`  
- **never** calls `draft_policy` / `generate`

No POST execute/run/apply. Nested draft on existing GET preparations is optional extra; prefer the dedicated GET to avoid mixing card types.

Do not write drafts onto `WorldSnapshot`.

---

## 14. Telemetry

`emit_proactive` names e.g. `proactive.draft.attempted` / `.accepted` / `.rejected` / `.skipped`.

Allowed attributes: `draft_id`, `preparation_id`, `provider`, `model`, `deployment_mode`, `latency_ms` (event field), `validation_status`, `reject_reason`, `privacy_class`.

Extend `ALLOWED_ATTR_KEYS` in `observability/schemas.py`. Do **not** add prompt/response.

---

## 15. Security / AST

`test_v628_llm_draft.py` parses AST of:

`draft.py`, `draft_contract.py`, `draft_validate.py`, `draft_prompts.py`, `draft_policy.py`

Forbidden import module names: `core.orchestrator`, `core.cognition`, `core.task_engine`, `core.tool_registry`, `tools`, `proactive.connectors.http_safe`, `subprocess`, connector write modules.

`draft_policy.py` **may** import `core.model_router`.

Forbidden Call names: `process_request`, `approve_task_action`, `os.system`.

Grep dashboard: no new `/execute` `/run` `/apply` under `/api/proactive/`.

`TYPE_MAP` in `prepare.py` still has no `FUTURE_EMAIL_DRAFT`.

---

## 16. Test Matrix (`test_v628_llm_draft.py`)

Fake `LLMResponse`; patch `draft_policy` or `ModelRouter.generate`. **No live Groq/OpenAI.**

| # | Case |
|---|------|
| 1 | Master flag off → 0 generates |
| 2 | Flags on → ACCEPTED with fake JSON |
| 3 | NORMAL + hosted name in allowlist |
| 4 | NORMAL + ollama in allowlist |
| 5 | PRIVATE + groq **blocked** (generate not called with groq) |
| 6 | PRIVATE + ollama allowed when private flag on |
| 7 | SENSITIVE → no generate, no row |
| 8 | `NoCapableProviderError` → prep still READY |
| 9 | Timeout → skip / REJECTED, prep READY |
| 10 | Circuit skip all → no generate success |
| 11 | Rate limit exception → hop then skip |
| 12 | Empty text → reject |
| 13 | Refusal string / empty JSON → reject |
| 14 | Malformed JSON |
| 15 | Markdown-wrapped JSON |
| 16 | Extra properties |
| 17 | Invalid `draft_type` |
| 18 | Oversized body |
| 19 | Nested object in body |
| 20 | `tool_calls` non-empty |
| 21 | `subprocess` in body |
| 22 | `gsk_` in body |
| 23 | Injection in `claim_code` still cannot escape schema (output still JSON-only) |
| 24 | `source_refs` not subset |
| 25 | Provenance IDs stored |
| 26–27 | `safe_params` / `param_hash` unchanged |
| 28 | ASK `binding_hash_for` same bytes with/without draft |
| 29 | Hash mismatch → not current / SUPERSEDED |
| 30 | Duplicate fingerprint idempotent |
| 31 | Other owner cannot GET |
| 32 | PRIVATE not delivered to HUD |
| 33 | OTP event has no `prompt` key |
| 34–35 | Worker: `evaluate_world_drafts` raises → `claim_batch` still invoked (mock) |
| 36–37 | Timeout 8s / max 2 hops |
| 38 | Restart second tick no second ACCEPTED |
| 39 | AST firewall |
| 40 | No execute routes |
| 41 | `FUTURE_EMAIL_DRAFT` unmapped |
| 42–48 | Documented regression commands (run in Phase 13, not all inside this file) |

Router-specific cases live in `test_provider_routing_scenarios.py` as above.

---

## 17. Regression Baseline (Phase 13)

| Suite | Expected |
|-------|----------|
| `test_v626_prepare_ask.py` | 44/44 |
| `test_v625_suggest.py` | 36/36 |
| `test_v623_email_commitments.py` | 11/11 |
| `test_v62_connectors.py` | 12/12 |
| `test_v62_emitters.py` | 13/13 |
| `test_v532_transaction_engine.py` | 30/30 |
| `test_v61_proactive_foundation.py` | 72 pass + **F-V622-07** TestClient errors |
| v6.2.4 predict | inherited **F-V625-F01** |

Do not treat inherited findings as V6.2.8 defects.

---

## 18. Architecture Findings — Implementation Handling

| ID | Plan handling |
|----|----------------|
| F-V628-A01 | `tools=None`; reject `tool_calls`; do not use CognitiveEngine |
| F-V628-A02 | `allowed_providers` + privacy table; never hosted for PRIVATE |
| F-V628-A03 | New table; do not set preparation status DRAFT |
| F-V628-A04 | No mapper; tests assert TYPE_MAP |
| F-V628-A05 | Inherited CORS; ASK origin middleware unchanged |
| F-V628-A06 | `deliver_draft` same WS import pattern; no TaskEngine |
| F-V628-A07 | Out of scope |
| F-V628-A08 | Document in code comment on PRIVATE local path; still fence output |
| F-V628-A09 | Additive maps + optional arg only; routing scenario tests |

---

## 19. Implementation Phases (do not execute now)

| Phase | Work |
|-------|------|
| 0 | Verify HEAD `7868bcd`; do not mix unrelated markdown |
| 1 | Additive DDL |
| 2 | `draft_contract.py` |
| 3 | `draft_prompts.py` (empty untrusted section) |
| 4 | `draft_policy.py` privacy + timeout |
| 5 | `model_router` `bounded_draft` + `allowed_providers` |
| 6 | `draft_validate.py` |
| 7 | `store` persist/idempotency/stale |
| 8 | `draft.py` + worker isolation |
| 9 | `deliver_draft` + GET API |
| 10 | OTP allowlist |
| 11 | AST tests |
| 12 | `test_v628_llm_draft.py` |
| 13 | Full regression |
| 14 | Forensic audit (separate) |
| 15 | Owner release authorization (separate) |

---

## 20. Release Boundary

Implementation, when authorized, stays **unreleased** until: tests + regressions + security/forensic audits + **explicit owner release authorization**.

No commit/tag/push in this planning phase.

---

## 21. Explicit Exclusions

V6.3 ACT; excerpt ingestion; second ModelRouter; `process_request`; TaskEngine from drafts; changing ASK binding; fixing inherited findings; README until release; enabling `FUTURE_EMAIL_DRAFT`.

---

## 22. Final Principle

More **intelligent** previews. Not more **powerful**.

```
DETERMINISTIC POLICY → PREPARATION → LLM DRAFT → VALIDATE → ASK → STOP
```

Never `LLM → TOOL → ACTION`. ACT is V6.3.

---

**IMPLEMENTATION PLAN READY**

Must handle in-slice: F-V628-A01, A02, A03, A09 (and A04–A08 as documented non-fixes / comments).

V6.2.8 IMPLEMENTATION PLAN COMPLETE — IMPLEMENTATION NOT AUTHORIZED BY THIS PLAN.
