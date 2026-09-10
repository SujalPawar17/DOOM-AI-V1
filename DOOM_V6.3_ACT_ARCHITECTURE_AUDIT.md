# DOOM V6.3 — ACT Architecture Audit

**Controlled real-world execution (authorization-bound, non-LLM authority)**

**Mode:** ARCHITECTURE AUDIT ONLY — no source, schema, tests, config, README, commit, tag, push, or ACT execution  
**Date:** 2026-09-10  
**Auditor role:** Principal architect / safety and reliability engineer  
**Baseline release:** tag `v6.2.8` → commit `d86ebde13de11448dcb0f5e8688043e06d913169` (tag object `96e6f8bd061be9a873ac2b3525a4f7bff3dc37f9`)  
**Prior immutable:** `v6.2.6` → `7868bcdb1280607cd255013a36b8f0ac191d6625` (tag object `984041831916fb0d2c5fd1e6a2c77cfb1ab9c94b`)  
**Branch:** `DOOM-V5.2`

**Final verdict:** **PASS — IMPLEMENTATION READY**

This document does **not** authorize implementation, commit, tag, or push. Owner implementation authorization is a separate step.

If the owner authorizes implementation, it must follow this document exactly. Deviating (TaskEngine ownership, generic HTTP POST, executing legacy APPROVED rows, LLM payloads, worker auto-execute) would void this pass.

---

## 1. Executive Summary

Released V6.2.8 still **stops at authorization plus optional draft presentation**:

```
PREDICT → SUGGEST → PREPARE → optional LLM DRAFT → ASK → APPROVED → STOP
```

**APPROVED ≠ EXECUTED** is true in live code. `decide_approval` writes a row and an event. It does not call TaskEngine, tools, connectors, `process_request`, or `model_router`. Connectors remain READ-only. `SafeHttp` allows GET plus one OAuth token POST.

V6.3 must add **the only new authority**: controlled ACT.

```
PREDICT → SUGGEST → PREPARE → DRAFT (optional, still non-authoritative)
  → ASK (binding includes action_hash for ACT-eligible rows)
  → APPROVED
  → explicit RUN gate (user-initiated)
  → VERIFY PRECONDITIONS (TOCTOU)
  → LEASE
  → ACT (typed writer)
  → OBSERVE / READ-BACK
  → VERIFY OUTCOME
  → COMPLETE | PARTIAL_SUCCESS | FAILED | UNKNOWN_OUTCOME
```

Three facts from the live tree **force** the design:

1. **`param_hash` is not an execution binding.** It hashes preview `safe_params` (`claim_code`, `horizon_hours`, `prediction_type`, `suggestion_type`, `action_type`, `risk_class`). There is no recipient, calendar event, repository, branch, or message body. Approving a V6.2.6/V6.2.8 grant does **not** specify a real-world mutation.
2. **`FUTURE_*` action types are stubs.** TYPE_MAP has `FUTURE_CAL_RECONCILE`, `FUTURE_GH_REVIEW`, `FUTURE_TASK_NOTE`. `FUTURE_EMAIL_DRAFT` exists only in a schema CHECK. None have writers.
3. **TaskEngine is the wrong owner.** It is the V4 cognitive tool ledger (`ALL_TOOLS`, including shell). `POST /api/tasks/{id}/approve` is a different plane from ASK. Dashboard IDE/shell routes bypass even that.

Therefore V6.3 introduces a **dedicated ActionEngine**, an **immutable Action Specification** with **`action_hash`**, **typed write clients** (not generic `SafeHttp` POST), and an **explicit user RUN** after APPROVED. Legacy APPROVED rows **remain non-executable forever**.

LLM remains non-authoritative. V6.2.8 drafts must never become payloads. V7 desktop/OS control is deferred.

---

## 2. Current V6.2.8 Authority Audit

### 2.1 Preparation

| Item | Live behavior |
|------|----------------|
| Table | `world_preparations` |
| Fingerprint | `sha256(owner\|suggestion_fp\|type\|template\|param_hash\|rule_version)[:48]` — UNIQUE `(owner_id, fingerprint)` |
| `param_hash` | `sha256(canonical_json(safe_params))[:64]` |
| `safe_params` | Allowlisted preview keys only (`prepare_templates.ALLOWED_PARAM_KEYS`) |
| Status | `DRAFT`/`READY`/`ASKED`/`EXPIRED`/`CANCELLED`/`SUPERSEDED` — **`DRAFT` unused**; APPROVED does **not** change prep status (stays `ASKED`) |
| Risk | `NONE`/`LOW`/`MEDIUM`/`HIGH` — no CRITICAL on preparations |
| Privacy | `NORMAL`/`PRIVATE`/`SENSITIVE` — SENSITIVE never persisted |
| `future_act_class` | `NONE` \| `MUTATION` |
| `action_type` | `NONE`, `FUTURE_CAL_RECONCILE`, `FUTURE_GH_REVIEW`, `FUTURE_TASK_NOTE`, schema-only `FUTURE_EMAIL_DRAFT` |

### 2.2 ASK / approval

| Item | Live behavior |
|------|----------------|
| Table | `world_approval_requests` |
| Binding | `sha256(owner\|prep_id\|action_type\|param_hash\|risk\|privacy\|valid_until_epoch\|rule_version\|csrf_binding_id)` full hex |
| `csrf_binding_id` | Constant `"worker"` on create — **not** the browser CSRF cookie |
| States | `PENDING`/`APPROVED`/`REJECTED`/`EXPIRED`/`REVOKED`/`CANCELLED` |
| Partial unique | one PENDING per `preparation_id` |
| `approval_valid_until` | **Column exists, never written** |
| Decide | `ask_decisions.decide` → `store.decide_approval` only; replay of already-APPROVED returns `replay: true` |
| Session | `doom_ask_sid` HttpOnly SameSite=Strict; mutations require `X-DOOM-CSRF` |
| Owner | session `owner_id` must match row |

### 2.3 Draft (V6.2.8)

Presentation JSON after fail-closed validation. GET draft is read-only. `tools=None`. SENSITIVE never generated. PRIVATE local Ollama only.

### 2.4 Connectors / HTTP

`ReadConnector` only. `SafeHttp`: GET allowlisted hosts; POST only `https://oauth2.googleapis.com/token`. PUT/PATCH/DELETE rejected.

### 2.5 TaskEngine / cognition / dashboard bypass

- `process_request` → CognitiveEngine → `tool_obj.execute` after risk gate.
- `approve_task_action` clears `WAITING_FOR_APPROVAL`; it is **not** ASK and must not become ACT.
- Direct dashboard: `/api/agent/run_code_terminal`, `/api/ide/run`, `/api/command`, file write, modes — **out of V6.3**.

### 2.6 Worker

```
recover → emitters → READ connectors → predict → suggest → prepare
  → drafts → ASK expire → INFORM claim_batch
```

No ACT stage. Worker never consumes APPROVED.

### 2.7 Where V6.3 may attach without weakening ASK

| Attach | Allowed |
|--------|---------|
| New `world_actions` + events/attempts/idempotency | Yes, additive |
| New ActionEngine modules under `proactive/` | Yes |
| Typed write clients beside READ connectors | Yes, **not** by opening `SafeHttp` |
| ASK binding **v63.1** adding `action_hash` for **new** ACT-eligible requests | Yes, additive; old formula unchanged for non-ACT asks |
| Dashboard `POST .../actions/{id}/run` (CSRF, no inline writer) | Yes |
| Consume **legacy** APPROVED as execute grant | **No** |
| Call TaskEngine / `process_request` from ASK approve | **No** |
| Execute inside `decide_approval` | **No** |
| Auto-execute in `process_once` because status is APPROVED | **No** |

---

## 3. V6.3 Objective

Give DOOM a **single, centralized, lease-guarded executor** that can perform **one exact immutable Action Specification** after:

- explicit owner approval bound to `action_hash`, and
- explicit owner (or equivalently gated) **run**, and
- immediate precondition revalidation, and
- idempotent dispatch, and
- independent read-back verification.

Default **off**. First production capability must be **narrow, typed, verifiable**, and **not** V7.

---

## 4. Proposed ACT Architecture

```
                    ┌─────────────────────────────────────┐
                    │  Action Specification (immutable)   │
                    │  action_hash  (deterministic)       │
                    └─────────────────┬───────────────────┘
                                      │ included in ASK binding v63.1
                                      ▼
                    ┌─────────────────────────────────────┐
                    │  world_approval_requests APPROVED   │
                    │  (authorization only)               │
                    └─────────────────┬───────────────────┘
                                      │ does NOT execute
                                      ▼
                    ┌─────────────────────────────────────┐
                    │  POST /actions/{id}/run  (CSRF)     │
                    │  enqueue + lease request            │
                    └─────────────────┬───────────────────┘
                                      ▼
                    ┌─────────────────────────────────────┐
                    │  ActionEngine (sole execution owner)│
                    │  preconditions → lease → writer     │
                    │  → verify → terminal state          │
                    └─────────────────┬───────────────────┘
                                      ▼
                    ┌─────────────────────────────────────┐
                    │  TypedWriteClient (capability)      │
                    │  vault secret_ref at call time      │
                    │  allowlisted URL template only      │
                    └─────────────────────────────────────┘
```

**Hard separations**

| Plane | Owner | Must not |
|-------|--------|----------|
| Cognition / tools | Orchestrator / TaskEngine | External ACT |
| Presentation | V6.2.8 draft | Payload / hash |
| Authorization | ASK `decide_approval` | Writers |
| Execution | ActionEngine | LLM, UI inline HTTP to providers |
| Verification | ActionEngine + READ client | WorldSnapshot, memory, LLM |
| Credentials | Vault | PG, OTP, prompts |

---

## 5. Action Specification

Distinct from draft, suggestion, preparation prose, and approval HUD text.

**Minimal canonical fields (v1):**

| Field | Role |
|-------|------|
| `action_id` | PK |
| `owner_id` | Owner binding |
| `preparation_id` | FK, CASCADE |
| `approval_id` | FK to the grant that may authorize run; set when ASK created or when bound |
| `capability_id` | Registry key (not free string from LLM) |
| `action_type` | Stable enum matching capability |
| `target_ref` | Allowlisted structured pointer (e.g. `calendar_id`, `hold_slot`) — **no arbitrary URL** |
| `exec_params` | Allowlisted JSON for that capability only |
| `param_hash` | Copy of preparation `param_hash` at materialize (TOCTOU) |
| `action_hash` | Hash of canonical spec (below) |
| `risk_class` | Deterministic from registry + params; never LLM |
| `privacy_class` | Copied from preparation; never lowered |
| `reversibility` | `REVERSIBLE` / `PARTIALLY_REVERSIBLE` / `IRREVERSIBLE` |
| `idempotency_key` | Unique per owner+capability+canonical payload |
| `policy_version` | e.g. `v63.1` |
| `valid_until` | Execution window (may be tighter than ASK TTL) |
| `provenance` | Evidence ids only |
| `status` | Lifecycle (§10) |
| `created_at` / `updated_at` | |

**Not in the spec:** LLM `body`/`title`/`summary`, email snippets, tokens, raw HTTP.

**Materialize time:** **before ASK** for ACT-eligible preparations. The user approves the action that will run, not a preview slogan. If builders cannot fill required `exec_params`, **do not create ASK** (stay PREPARED_WITHOUT_ACT).

---

## 6. `action_hash` / Approval Binding

### 6.1 `action_hash`

```
canonical = json.dumps({
  "owner_id", "preparation_id", "capability_id", "action_type",
  "target_ref", "exec_params", "param_hash", "risk_class",
  "privacy_class", "policy_version"
}, sort_keys=True, separators=(",", ":"))

action_hash = sha256(canonical).hexdigest()   # full 64 hex
```

Computed **only** by the deterministic builder. The model must never supply or overwrite `action_hash`. Store rejects client-supplied hashes.

### 6.2 Why `param_hash` is insufficient

`param_hash` does not include target, account, or mutation fields. Two different calendar holds could share the same preview `param_hash`. Execution binding **must** be `action_hash`.

### 6.3 ASK binding v63.1 (ACT-eligible only)

Additive string (do not change v626.1 non-ACT asks):

```
owner_id|preparation_id|action_type|param_hash|action_hash|
risk_class|privacy_class|valid_until_epoch|rule_version|csrf_binding_id
```

`rule_version` for ACT-eligible asks: `v63.1`. Non-ACT (`action_type=NONE`) keeps `v626.1` and **no** `action_hash`.

### 6.4 Grandfathering

Any `world_approval_requests` row with `rule_version=v626.1` or empty `action_hash` is **not executable**. ActionEngine must refuse. No migration that “upgrades” old APPROVED into ACT.

Any change to target, recipient, times, repo, branch, message, permission, or `exec_params` changes `action_hash` → old approval invalid → new ASK.

---

## 7. Execution Authority

**Chosen owner: dedicated ActionEngine** (`proactive/act.py` + `proactive/act_engine.py` + `proactive/act_verify.py` + `proactive/writers/*`).

| Candidate | Decision |
|-----------|----------|
| TaskEngine | **Reject.** Cognitive ALL_TOOLS; wrong approval route; MEDIUM shell without ASK; incomplete resume. |
| Orchestrator / `process_request` | **Reject.** Model-influenced tool selection. |
| Connector self-approve | **Reject.** Writers are dumb; they do not interpret APPROVED. |
| Dashboard route calling Gmail | **Reject.** HTTP handler must only enqueue. |
| ActionEngine | **Accept.** Single lease, single verifier, single audit trail. |

Reuse **ideas** from V4 `core/reliability/idempotency.py` (`FAILED_BEFORE_SIDE_EFFECT` vs `UNKNOWN`) — **do not** share the cognitive idempotency store with ACT keys.

---

## 8. Capability Model

Registry (code constant, not LLM):

| Field | Meaning |
|-------|---------|
| `capability_id` | Stable ID |
| `read_or_write` | WRITE for ACT |
| `risk_class` | Floor; builder may raise, never lower |
| `privacy_floor` | Minimum privacy of resulting action |
| `reversibility` | See §36 |
| `requires_approval` | Always true in V6.3 |
| `requires_explicit_run` | Always true in V6.3 |
| `idempotency` | `EXTERNAL_KEY` / `INTERNAL_ONLY` |
| `writer` | Typed client class name |
| `verifier` | Read-back method |
| `allowed_accounts` | Vault `secret_ref` kinds |
| `deferred` | If true, builder refuses |

READ capabilities stay on existing connectors. WRITE never registers in `get_enabled_readers()`.

---

## 9. Initial ACT Capability Scope

**Do not implement “execute everything.”**

### 9.1 In V6.3 first implementation slice

| ID | Capability | Why | Mechanism | Approval | Rollback | Verify |
|----|------------|-----|-----------|----------|----------|--------|
| **ACT-0** | `INTERNAL_LEDGER_NOTE` | Prove gates without provider write | Insert immutable `world_action_receipts` note from allowlisted `exec_params.claim_code` only | ASK v63.1 + RUN | Compensating note (separate approved action) | PG row exists + hash match |
| **ACT-1** | `CALENDAR_CREATE_HOLD` | First **external** write; structured; GET read-back exists | Typed Calendar insert; fixed path template; title/times from builder **not** LLM | ASK v63.1 + RUN | Delete hold = **separate** capability (deferred in slice 1 or ACT-1b) | GET event by id; compare start/end/summary allowlist |

ACT-1 is **flagged separately** and may ship disabled while ACT-0 lands.

**Builder rule for ACT-1:** if required `exec_params` (`calendar_id` from vault metadata, `start_rfc3339`, `end_rfc3339`, `summary_template_id`) cannot be filled from **deterministic** sources (suggestion/prediction structured fields + config calendar id), **refuse to materialize**. Do not invent times from draft text.

### 9.2 Explicitly deferred (not V6.3.0)

| ID | Reason |
|----|--------|
| `EMAIL_SEND` | Irreversible; high confused-deputy; LLM draft body temptation |
| `EMAIL_CREATE_PROVIDER_DRAFT` using V6.2.8 `body` | Payload from model |
| `GITHUB_MERGE` / `GITHUB_CREATE_PR` | High/irreversible; target not in current params |
| `FUTURE_GH_REVIEW` as merge | Stub; no exec_params |
| `FILE_WRITE`, `COMMAND_EXECUTION`, `BROWSER_ACTION` | V7 / existing bypass tools |
| `CALENDAR_UPDATE` / `DELETE` of arbitrary events | TOCTOU on foreign events |
| CRITICAL risk actions | Stronger controls not designed |
| Autonomous worker ACT | §33 |
| “Always allow” / “don’t ask again” | §21 |
| Blanket tool approval | Forbidden |

`FUTURE_EMAIL_DRAFT` stays unmapped. V6.2.8 drafts stay presentation.

---

## 10. Execution State Machine

Do **not** reuse TaskEngine statuses as authority.

```
CREATED
  → READY                 # spec materialized; ASK not yet APPROVED
  → APPROVAL_REQUIRED     # PENDING ask exists
  → APPROVED_NOT_RUN      # ASK APPROVED; still no lease
  → RUN_REQUESTED         # explicit run accepted
  → PRECONDITION_CHECK
  → EXECUTING             # lease held
  → VERIFYING
  → COMPLETED
  → PARTIAL_SUCCESS       # reserved for multi-step batches (v1: single action → unused or = FAILED+COMPLETED mix at batch layer)
  → FAILED                # verified non-occurrence or definite rejection
  → UNKNOWN_OUTCOME       # side effect possible; not confirmed
  → CANCELLED | EXPIRED | REVOKED
```

**UNKNOWN_OUTCOME semantics**

- Timeout after request may have left the provider.
- Crash during EXECUTING after writer returned.
- Read-back inconclusive.

**Forbidden:** UNKNOWN_OUTCOME → FAILED because the timer fired. **Forbidden:** automatic retry of UNKNOWN_OUTCOME. Recovery is **reconcile** (read-back) or **human** new action.

Terminal: COMPLETED, FAILED (definite), CANCELLED, EXPIRED, REVOKED. UNKNOWN_OUTCOME is **quarantine**, not retry fuel.

---

## 11. Precondition Verification

Immediately before writer call, all must hold (fail closed):

1. `PROACTIVE_ACT_ENABLED` and capability flag on  
2. Session/owner match (for RUN) and `owner_id` on row  
3. Approval `APPROVED`, not EXPIRED/REVOKED/CANCELLED  
4. Approval `valid_until` (and action `valid_until`) in the future  
5. Recomputed `binding_hash` (v63.1) matches stored  
6. Recomputed `action_hash` matches stored  
7. Preparation exists, not SUPERSEDED/EXPIRED/CANCELLED  
8. Preparation `param_hash` equals spec `param_hash`  
9. Risk/privacy unchanged vs spec  
10. Capability still in registry and not deferred  
11. Writer allowlisted; connector health OK  
12. Vault `secret_ref` present; no token in spec  
13. Idempotency key not COMPLETED / EXECUTING / UNKNOWN_OUTCOME  
14. Privacy policy: SENSITIVE → refuse hosted side channels (v1: refuse SENSITIVE ACT entirely)  
15. Daily budget / concurrency / circuit not exhausted  

Any failure: **do not execute**; state `FAILED` (precondition) or remain `APPROVED_NOT_RUN` with reason code. Approval is **not** auto-revoked unless policy says so (v1: leave APPROVED; run refused).

---

## 12. TOCTOU Defense

Between APPROVED and RUN, and again between RUN and writer:

Re-read preparation, approval, action row **in one transaction** with `SELECT … FOR UPDATE` on the action and idempotency rows.

Recompute hashes from stored canonical fields, not from the client body (client may send `action_hash` for compare-only).

External target drift (calendar slot taken): verifier after insert; if conflict, FAILED with no retry storm.

Credential rotation: `get_secret` at call time, never cached in the action row.

Policy/flag flip: re-read flags inside the lease.

Stale approval: `valid_until` check at lease, not only at approve.

---

## 13. Idempotency

| Key | `sha256(owner_id\|capability_id\|action_hash)` |
|-----|-----------------------------------------------|
| Unique | `(owner_id, idempotency_key)` |
| Persist | `world_action_idempotency` |

| Situation | Behavior |
|-----------|----------|
| Retry before writer invoked | Allowed if state `FAILED_BEFORE_SIDE_EFFECT` |
| Duplicate RUN / double-click / two tabs | Second call sees lease or COMPLETED; no second write |
| Worker restart mid-EXECUTING | Resume as UNKNOWN_OUTCOME unless receipt exists; **reconcile** |
| Network timeout | UNKNOWN_OUTCOME; reconcile via GET |
| Replay API | Same as duplicate RUN |
| UNKNOWN_OUTCOME | Reconcile only; never second insert |

---

## 14. Connector Write Boundary

**Do not** add unrestricted POST/PUT/PATCH/DELETE to `SafeHttp`.

```
SafeHttp          — GET + OAuth token POST (unchanged)
TypedWriteClient  — one class per capability
  - compile-time URL template
  - compile-time method
  - JSON schema for body from exec_params
  - timeout, max bytes
  - maps response → receipt ids only
```

No caller-supplied URL. No SSRF. Same host/path allowlisting spirit as GET, **stricter**.

Writers must not import `model_router`, drafts, or TaskEngine.

---

## 15. Credential Security

| Store | Content |
|-------|---------|
| PostgreSQL | `secret_ref` only |
| Vault (DPAPI) | tokens/refresh |
| Action spec / OTP / LLM | **never** tokens |
| Writer | `get_secret` → Authorization header → drop |

LLM / draft_policy already must not import vault. ActionEngine may import vault. Telemetry keeps `FORBIDDEN_ATTR_KEYS` (`token`, `authorization`, `body`, …).

---

## 16. Payload Construction

Per-capability **pure builder**:

```
build_exec_params(preparation, prediction, vault_metadata) -> exec_params | Refuse
```

Inputs: allowlisted structured fields + config/vault metadata (calendar_id).  

**Forbidden inputs:** draft `body`, suggestion HUD text, email snippet, WorldSnapshot, memory, prompt, connector prose, arbitrary URL.

ACT-0 payload: `{ "claim_code": <from safe_params>, "note_template_id": "act0_ledger_v1" }`.

---

## 17. Approval UX

UI must show **authoritative exec_params**, not “Approve this?”.

Show: capability, WHAT, WHICH ACCOUNT (redacted), TARGET, WHEN, WHAT CHANGES, RISK, REVERSIBILITY, `action_hash` (short), valid_until.

SENSITIVE/PRIVATE: redact as today (PRIVATE persist-only HUD; no TTS).

Approve still calls existing decide path (authorization). **Run** is a second control with copy: “This will perform the action now.”

---

## 18. Approval Replay

| Case | Result |
|------|--------|
| Approve twice | Existing replay: still APPROVED, no second grant, **no execute** |
| RUN twice | Idempotency |
| Prep/action_hash changed | Binding mismatch; cannot approve or run |
| EXPIRED / REVOKED / CANCELLED | Run refused |
| Wrong owner / session | 404/403 |
| CSRF mismatch | 403 |
| Legacy v626.1 APPROVED | Run refused (`NOT_ACT_BOUND`) |

---

## 19. Concurrency

- One EXECUTING lease per `action_id` (`FOR UPDATE SKIP LOCKED` or `lease_owner` + TTL).
- Unique idempotency key globally per owner.
- Two workers: only one claim.
- Generation/fencing: `attempt_n` monotonic; stale worker cannot complete a superseded attempt.

---

## 20. Crash Recovery

| When | Recovery |
|------|----------|
| Before writer | FAILED_BEFORE_SIDE_EFFECT; RUN may retry |
| After HTTP sent, no response | UNKNOWN_OUTCOME; reconcile |
| Response received, crash before persist | Reconcile using provider id if in flight log; else UNKNOWN |
| After persist COMPLETED | Restart is no-op |
| During VERIFYING | Re-run verifier only (GET) |

Never “retry insert” on UNKNOWN.

---

## 21. Outcome Verification

| Capability | Authoritative outcome | Non-authoritative |
|------------|----------------------|-------------------|
| ACT-0 | PG row + content hash | HTTP 200 of dashboard |
| ACT-1 | GET event id; start/end/summary match | Insert JSON alone |
| (deferred send) | Provider message id + GET | SMTP 250 rumor |

WorldSnapshot, memory, LLM, and predictions are **never** outcome authority.

---

## 22. Partial Success

V6.3.0 is **one action per spec**. Batch “3 holds” is out of scope.

If a future batch exists: persist per-action terminals; HUD must not say COMPLETED unless all COMPLETED. Mixed → `PARTIAL_SUCCESS` at batch wrapper only.

---

## 23. Retry Policy

| Class | Retry? |
|-------|--------|
| Flag off / precondition | No |
| HTTP 400 / 403 / 404 | No |
| Binding mismatch | No |
| Connection error **before** send | Yes, bounded, same idempotency key |
| Timeout **after** send | No auto retry; UNKNOWN + reconcile |
| 429 | Bounded backoff **only if** idempotent PUT/same key guaranteed; else UNKNOWN |
| 5xx after possible accept | UNKNOWN |

Max retries: `PROACTIVE_ACT_MAX_RETRIES` default 2, and **only** `FAILED_BEFORE_SIDE_EFFECT`.

---

## 24. Risk Model

Deterministic registry floor. Builder may **raise**, never lower. LLM cannot set risk.

| Level | V6.3 ACT |
|-------|----------|
| NONE / LOW | ACT-0 after approval+run |
| MEDIUM | ACT-1 after approval+run |
| HIGH | Deferred |
| CRITICAL | Deferred; not on preparation CHECK today |

No auto-ACT without RUN in v1, including LOW.

---

## 25. Privacy Model

| Class | ACT v1 |
|-------|--------|
| NORMAL | ACT-0/ACT-1 allowed if flags on; HUD allowed |
| PRIVATE | ACT-0 allowed persist-only; **no hosted writer**; ACT-1 (Google) treated as **not PRIVATE-safe** unless calendar is the owner’s vaulted account **and** no hosted LLM in the path (builder still no LLM). **v1 recommendation: PRIVATE ACT-1 deferred.** |
| SENSITIVE | **No ACT.** No persist of action spec. |

Never send SENSITIVE/PRIVATE payloads to hosted LLM (already V6.2.8).

---

## 26. Audit Events

Append-only `world_action_events`:

`created`, `prepared`, `approval_requested`, `approved`, `run_requested`, `precondition_checked`, `precondition_failed`, `lease_acquired`, `execution_started`, `execution_result`, `verification_started`, `verification_result`, `completed`, `failed`, `unknown_outcome`, `cancelled`, `revoked`, `expired`, `reconciled`

No secrets, no full PRIVATE payloads, no LLM text. Store receipt ids, status codes, capability, hashes.

Events are immutable (INSERT only).

---

## 27. Observability

OTP allowlist add: `action_id`, `capability_id`, `attempt_n`, `outcome_code`, `idempotency_hit`, `lease_id` (not tokens).

Never: credentials, private `exec_params` bodies, prompt/response.

---

## 28. API Security

**Do not** add `POST /execute` with arbitrary JSON.

| Method | Path | Role |
|--------|------|------|
| GET | `/api/proactive/actions/{action_id}` | Owner read |
| POST | `/api/proactive/actions/{action_id}/run` | CSRF; enqueue only |
| POST | `/api/proactive/actions/{action_id}/cancel` | CSRF; if not EXECUTING |

Reject: unknown id, wrong owner, stale `action_hash`, expired/revoked, duplicate, capability flag off, legacy approval.

Approve remains `/approvals/{id}/approve` — **still does not run**.

---

## 29. LLM Boundary

Unchanged V6.2.8 plus: ActionEngine **must not** import `draft.py` payload into `exec_params`. Draft GET remains read-only. `bounded_draft` must not gain tools. No `provider_override` for ACT.

---

## 30. TaskEngine Integration

**Option C: dedicated execution layer.**  
TaskEngine may later *observe* ACT outcomes as `TASK_STATUS` signals (optional, INFORM-only). It must not dispatch writers.

Do not extend `approve_task_action` to Gmail/Calendar.

---

## 31. V7 Boundary

V6.3 is **typed provider actions**. Deferred: arbitrary desktop, mouse/keyboard, unrestricted shell, unrestricted browser, visual computer-use, unrestricted filesystem.

Existing `terminal_execute` / IDE run stay **outside** ActionEngine.

---

## 32. Threat Model

| Threat | Control |
|--------|---------|
| Prompt / tool injection | No tools on draft; builders ignore prose |
| Approval / parameter / owner substitution | v63.1 binding + owner + CSRF |
| Session theft / CSRF | Existing ASK session; RUN needs CSRF |
| Replay | Idempotency + replay-safe approve |
| TOCTOU / race | FOR UPDATE + rehash |
| Duplicate workers | Lease |
| UNKNOWN_OUTCOME retry | Forbidden |
| Credential theft | Vault; forbidden OTP keys |
| SSRF / arbitrary URL | Typed templates |
| Arbitrary command / path / symlink | Not in scope |
| Policy/risk/privacy downgrade | Re-read registry; no LLM |
| Confused deputy | Capability+account allowlist |
| LLM hallucination | Non-authoritative |
| Dashboard shell bypass | Not used by ACT |
| Webhook spoofing | No inbound execute webhooks in v1 |

---

## 33. Database Design (additive)

```
world_actions
  action_id PK
  owner_id
  preparation_id FK CASCADE
  approval_id FK SET NULL
  capability_id, action_type
  target_ref JSONB
  exec_params JSONB
  param_hash, action_hash
  risk_class, privacy_class, reversibility
  policy_version
  idempotency_key
  status CHECK (states §10)
  valid_until
  lease_owner, lease_until
  provenance JSONB
  UNIQUE (owner_id, action_hash)
  UNIQUE (owner_id, idempotency_key)

world_action_events
  event_id PK, action_id FK, from_status, to_status, reason, created_at
  INSERT-only

world_action_attempts
  attempt_id PK, action_id FK, attempt_n, state,
  started_at, ended_at, http_status, receipt_ref, error_class

world_action_verifications
  verification_id PK, action_id FK, attempt_id FK,
  method, verdict, observed_ref, created_at

world_action_idempotency
  owner_id, idempotency_key PK,
  action_id, state, receipt_ref, updated_at
```

Do not change `decide_approval` semantics. Do not set `world_preparations.status` to an execute state. Do not use `status=DRAFT` as ACT.

Optional: write `approval_valid_until` for ACT-eligible asks (currently unused column) — **additive use**, same table.

---

## 34. Worker Architecture

```
… prepare → drafts → ASK expire
  → act_recover_leases / reconcile UNKNOWN
  → INFORM claim_batch
```

**Never:** `APPROVED → writer`.

RUN API inserts `RUN_REQUESTED`. Worker/ActionEngine claims only `RUN_REQUESTED` with valid lease. If ACT flag off, claim is no-op (rows stay RUN_REQUESTED or revert — prefer stay + skip).

Isolate `try/except` (`act_eval`) so INFORM cannot be blocked forever; cap claims per tick (e.g. 1–2).

---

## 35. Manual vs Autonomous ACT

| Mode | V6.3.0 |
|------|--------|
| User-initiated RUN | **Required** |
| Worker auto-run after APPROVED | **Forbidden** |
| Proactive auto-ACT | Deferred; would need separate architecture |

---

## 36. Reversibility

| Capability | Class | Rollback |
|------------|-------|----------|
| ACT-0 | REVERSIBLE | Compensating ledger note (new action) |
| ACT-1 | PARTIALLY_REVERSIBLE | Event remains until a future approved delete |
| EMAIL_SEND | IRREVERSIBLE | Deferred |
| MERGE | IRREVERSIBLE | Deferred |

Rollback is always a **new** Action Specification, never a silent inverse.

---

## 37. Rate Limits / Budget

Defaults (config, default conservative):

| Limit | Default |
|-------|---------|
| `PROACTIVE_ACT_ENABLED` | false |
| `PROACTIVE_ACT_INTERNAL_ENABLED` | false |
| `PROACTIVE_ACT_CALENDAR_HOLD_ENABLED` | false |
| Daily ACT-0 | 8 |
| Daily ACT-1 | 2 |
| Concurrent leases / owner | 1 |
| Max attempts / action | 3 (including first) |
| Writer timeout | 8s |
| Circuit | skip writer if connector health FAIL |

---

## 38. Loop Protection

No ACT→observe→ACT chains in v1 (single spec).

| Cap | Default |
|-----|---------|
| `max_actions_per_preparation` | 1 |
| `max_external_calls` per attempt | 1 write + N GET verify |
| `max_execution_time` | 15s including verify |

Exceed → stop; UNKNOWN if write may have occurred.

---

## 39. Verification Authority

External GET / PG for ACT-0. Never LLM, memory, snapshot, cached prediction.

---

## 40. Feature Flags / Rollout

```
ACT disabled (default)     → V6.2.8 unchanged
→ internal ACT-0 tests
→ ACT-0 owner opt-in
→ ACT-1 sandbox / test calendar
→ ACT-1 production calendar hold
→ later capabilities (separate audits)
```

Disable ACT: RUN refuses; worker skip; ASK v626.1 path unchanged.

---

## 41. Acceptance Gate Matrix

| ID | Requirement | Verify | Blocking |
|----|-------------|--------|----------|
| G-ARCH | ActionEngine sole writer dispatcher | AST/import | Yes |
| G-AUTH | APPROVED ≠ EXECUTED | approve does not call writer | Yes |
| G-BIND | v63.1 includes action_hash; v626.1 cannot run | Tests | Yes |
| G-LEGACY | Pre-v63 approvals refuse RUN | Tests | Yes |
| G-LLM | No draft/payload/hash from model | AST + builders | Yes |
| G-HTTP | SafeHttp write verbs unchanged | Diff + tests | Yes |
| G-CRED | No tokens in spec/OTP | Schema/OTP | Yes |
| G-IDEM | Unique key; no double write | Concurrent tests | Yes |
| G-UNK | UNKNOWN not auto-FAILED/retried | Tests | Yes |
| G-TOCTOU | Hash recheck under lock | Tests | Yes |
| G-PRIV | SENSITIVE no ACT; PRIVATE no ACT-1 v1 | Tests | Yes |
| G-API | No arbitrary /execute JSON | Route tests | Yes |
| G-WK | No auto-exec on APPROVED | Worker tests | Yes |
| G-TE | No TaskEngine / process_request | AST | Yes |
| G-V7 | No shell/browser/file writers | Registry | Yes |
| G-FLAG | Defaults false; v628 behavior | Tests | Yes |
| G-UX | RUN distinct from approve | API | Yes |
| G-VER | Read-back required for ACT-1 | Tests | Yes |
| G-BUDGET | Daily/concurrency caps | Tests | No |
| G-CORS | Inherited `*` | Document | No (inherited) |

---

## 42. Implementation File Map

### MUST CREATE

- `proactive/act.py` — flags, evaluate/recover entry
- `proactive/act_spec.py` — canonical hash, builders
- `proactive/act_engine.py` — lease, preconditions, dispatch
- `proactive/act_verify.py` — read-back
- `proactive/act_policy.py` — registry, budgets, retry class
- `proactive/writers/__init__.py` — no generic HTTP
- `proactive/writers/internal_ledger.py` — ACT-0
- `proactive/writers/calendar_hold.py` — ACT-1 (flag-gated)
- `test_v63_act.py`

### MUST MODIFY

- `database/postgres_db.py` — additive tables
- `proactive/store.py` — persist/lease/idempotency
- `proactive/config.py` / `config_example.txt` — flags default false
- `proactive/ask.py` / `ask_decisions.py` — **additive** v63.1 binding for ACT-eligible only; v626.1 path byte-stable for NONE
- `proactive/worker.py` — recover/reconcile only, not auto-run
- `dashboard/server.py` — GET action + POST run/cancel
- `observability/schemas.py` — allowlist keys
- `proactive/delivery.py` — optional status cards; **no execute**

### MUST NOT MODIFY (authority)

- `ask_decisions` v626.1 formula for existing NONE asks (keep function; add parallel v63 helper)
- V6.2.8 draft modules’ non-authority
- `prepare.py` TYPE_MAP worthiness except **additive** materialize hook that **refuses** if builder fails (do not map EMAIL_SEND)
- `predict.py`, `evidence.py`, `suggest.py` worthiness
- `connectors/http_safe.py` method policy
- READ connectors’ fetch methods
- TaskEngine, orchestrator, tool_registry, tools, memory, `core/automation.py`
- Using LLM draft body as `exec_params`

README: only at **release**, not this audit.

---

## 43. Known Findings

| ID | Severity | Note |
|----|----------|------|
| F-V63-A01 | Design constraint | `param_hash` cannot authorize ACT; `action_hash` required |
| F-V63-A02 | Design constraint | Legacy APPROVED is non-executable |
| F-V63-A03 | Design constraint | TaskEngine / dashboard shell must stay off the ACT path |
| F-V63-A04 | Design constraint | `FUTURE_*` stubs are not writers; `FUTURE_EMAIL_DRAFT` unmapped |
| F-V63-A05 | Design constraint | `approval_valid_until` unused; ACT should populate execution window |
| F-V63-A06 | Inherited | CORS `*`; ASK origin middleware remains |
| F-V63-A07 | Inherited | Delivery imports `dashboard.server` |
| F-V63-A08 | Inherited | F-V622-07 TestClient; F-V625-F01 predict cap; F-V628-I01 test_91 |
| F-V63-A09 | Non-blocking | ACT-1 PRIVATE deferred; only NORMAL calendar hold in first external |
| F-V63-A10 | Non-blocking | Calendar delete is a later approved compensating action |

No finding requires redesign of the chosen ActionEngine + action_hash + explicit RUN model.

---

## 44. Blockers

**None for architecture**, provided implementation follows §7, §6.4, §9, §14, §28, §30, §34, §35.

Would become blockers if implementation:

- executed from draft text or `process_request`
- opened generic SafeHttp POST
- auto-ran APPROVED in the worker
- treated v626.1 grants as ACT grants
- used TaskEngine as owner

---

## 45. Final Verdict

**PASS — IMPLEMENTATION READY**

Authority boundary is demonstrable:

- ASK remains authorization.
- Execution is a separate ActionEngine behind explicit RUN, hashes, leases, and typed writers.
- LLM, drafts, snapshot, memory, and TaskEngine are not execution authorities.
- V6.2.8 behavior is preserved when flags are off.
- V7 is not smuggled in.

**V6.3 ARCHITECTURE IS READY FOR IMPLEMENTATION AUTHORIZATION.**

This audit does **not** authorize implementation, commit, tag, push, or any real-world action.
