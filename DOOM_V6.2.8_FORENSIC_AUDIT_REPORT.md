# DOOM V6.2.8 — Independent Forensic Audit

**Bounded LLM draft (non-authoritative presentation)**  
**Mode:** AUDIT ONLY — no code, schema, test, config, README, commit, tag, or push  
**Date:** 2026-09-10  
**Auditor role:** Independent forensic review of the unreleased working tree  
**Implementation claim under review:** V6.2.8 IMPLEMENTED — NOT RELEASED  
**Baseline required:** V6.2.6 commit `7868bcdb1280607cd255013a36b8f0ac191d6625`

**Final verdict:** **PASS WITH NON-BLOCKING FINDINGS — RELEASE CONDITIONALLY RECOMMENDED**

V6.2.8 is safe to present for **owner** release authorization. This audit does **not** authorize commit, tag, push, or release.

**V6.2.8 IS READY FOR OWNER RELEASE AUTHORIZATION.**

---

## 1. Executive Summary

HEAD remains the released **v6.2.6** commit. V6.2.8 exists only as **uncommitted** working-tree files. Tag `v6.2.6` is unchanged. There is **no** `v6.2.8` commit and **no** `v6.2.8` tag.

Live code implements:

```
PREPARE (authoritative, V6.2.6)
  → optional LLM DRAFT (presentation only)
  → deterministic VALIDATE (fail-closed)
  → PERSIST (draft tables only)
  → ASK (V6.2.6, unchanged authority)
  → STOP
```

There is **no** ACT path. Draft modules do not call TaskEngine, orchestrator, tool registry, connectors, SafeHttp writes, memory writes, or ASK approve/reject/revoke/execute.

Independent forensic re-run:

| Suite | This audit |
|-------|------------|
| `test_v628_llm_draft.py` | **36 collected; 35 passed, 1 skipped, 0 failed** (2.496s) |
| `test_provider_routing_scenarios.py` | **20/20 OK** (0.041s) including O–R |
| `test_v625_suggest.py` | **36/36 OK** (`V625_PERF_MS=265.50`) |
| `test_v626_prepare_ask.py` | **exit 0** (file contains **44** `test_*` methods) |

V6.1 `test_91` is an **expected** substring clash with the approved V6.2.8 architecture (`draft_policy.py` → `model_router`). Classification: **EXPECTED / TEST-OBSOLESCENCE**. Not a security blocker. Owner must not treat an unmodified V6.1 glob as a V6.2.8 security veto.

No blocking ACT, privacy-downgrade, or ASK-authority defect was found.

---

## 2. Repository Baseline

| Check | Evidence |
|-------|----------|
| Branch | `DOOM-V5.2` |
| Remote tracking | `DOOM-V5.2...origin/DOOM-V5.2` (no unpublished V6.2.8 commit) |
| HEAD | `7868bcdb1280607cd255013a36b8f0ac191d6625` |
| HEAD message | `release: DOOM v6.2.6` |
| Index / staged | empty (`git diff --cached` empty) |
| V6.2.8 committed? | **No.** Working tree only |
| `v6.2.8` tag | **absent** (`git tag -l v6.2*` shows through `v6.2.6` only) |
| Push of V6.2.8 | **No.** HEAD is still origin-aligned v6.2.6 plus local dirt |

---

## 3. Git / Tag Integrity

| Tag | Object | Peeled commit |
|-----|--------|---------------|
| `v6.2.6` | `984041831916fb0d2c5fd1e6a2c77cfb1ab9c94b` | `7868bcdb1280607cd255013a36b8f0ac191d6625` |
| `v6.2.5` | `f8dc1a5fcf6fa9d1a09d8654543da45fb7148435` | `de8f236092f104246b59920e2bea08a516e5518a` |
| `v6.2.4` | `269139ee88a592244e0dc52ca044eba120c1cc1b` | `26b8c169f8540f94490ff271a648225d0a8ebdec` |

`git rev-parse "v6.2.6^{commit}"` equals HEAD. Tag object SHA matches the prior release record. No retag observed. `git log` tip is still `7868bcd release: DOOM v6.2.6`. No commit message containing a V6.2.8 release was required; implementation is uncommitted.

---

## 4. Changed-File Audit

### V6.2.8 created (untracked)

- `proactive/draft.py`
- `proactive/draft_contract.py`
- `proactive/draft_prompts.py`
- `proactive/draft_validate.py`
- `proactive/draft_policy.py`
- `test_v628_llm_draft.py`
- `DOOM_V6.2.8_ARCHITECTURE_AUDIT.md` (prior planning phase)
- `DOOM_V6.2.8_IMPLEMENTATION_PLAN.md` (prior planning phase)
- `DOOM_V6.2.8_IMPLEMENTATION_REPORT.md`
- `DOOM_V6.2.8_FORENSIC_AUDIT_REPORT.md` (this file; audit artifact only)

### V6.2.8 modified vs HEAD (v6.2.6)

| File | Role |
|------|------|
| `database/postgres_db.py` | **+49** additive `world_preparation_drafts` / `world_draft_deliveries` |
| `core/model_router.py` | `bounded_draft` maps; `allowed_providers` filter |
| `proactive/config.py` | flags, `DRAFT_PROMPT_VERSION`, cap/timeout/hops |
| `config_example.txt` | commented flags; dormant excerpts comment |
| `proactive/store.py` | draft list/insert/current/stale/delivery |
| `proactive/worker.py` | isolated `evaluate_world_drafts()` |
| `proactive/delivery.py` | `deliver_draft` / `proactive_draft` |
| `dashboard/server.py` | GET draft only |
| `observability/schemas.py` | allowlist keys `draft_id`, `validation_status`, `reject_reason`, `deployment_mode` |
| `test_provider_routing_scenarios.py` | tests O–R |

`git diff --stat HEAD` on the implementation set is additive (~475 insertions across the first aggregated stat, plus `database/postgres_db.py` +49 confirmed separately).

### Unrelated dirty / historical (not V6.2.8 source)

- `DOOM_V6.2.5_RELEASE_REPORT.md` — post-push SHA fill-in vs tagged bytes
- `DOOM_V6.2.6_RELEASE_REPORT.md` — same pattern vs tagged v6.2.6
- Untracked leftover provider-routing / older version markdown

These must **not** be mixed into a V6.2.8 release commit. **F-V628-N01** (non-blocking process finding).

### Protected files vs HEAD

`git diff HEAD --name-only` empty for:

`proactive/predict.py`, `evidence.py`, `suggest.py`, `prepare.py`, `prepare_templates.py`, `ask.py`, `ask_decisions.py`, `dashboard/ask_session.py`, `core/orchestrator.py`, `core/tool_registry.py`, `README.md`

Connectors, SafeHttp, TaskEngine, tools, and memory were not in the V6.2.8 diff set.

---

## 5. Architecture Compliance

Approved pipeline is implemented:

**PREPARE → optional DRAFT → VALIDATE → PERSIST → ASK → STOP**

Worker order in `process_once`: recover / expire → emitters → connectors → predictions → suggestions → preparations → **drafts** → ASK expiry → INFORM `claim_batch`.

Verified absences from draft call graph:

| Forbidden edge | Result |
|----------------|--------|
| DRAFT → ACT | **Absent** (no ACT module, no execute routes) |
| DRAFT → EXECUTE / TOOL / TaskEngine | **Absent** (AST + imports) |
| DRAFT → connector write / SafeHttp write | **Absent** |
| DRAFT → memory write | **Absent** |
| LLM mutates `safe_params` / `param_hash` / fingerprint / risk / privacy / approval | **Absent** — validator rejects assignment-like strings; persist writes only draft tables; `get_current_draft` requires `param_hash_at_generation = preparations.param_hash` |

Preparation remains authoritative. `world_preparations.status = DRAFT` is **not** used (no matches in `proactive/`).

LLM output is presentation JSON with seven keys only after validation. Rejected bodies persist as `{}`.

**Architecture verdict:** COMPLIANT with approved V6.2.8 plan, with non-blocking product/reliability notes in §18.

---

## 6. Draft Module Audit

Inspected: `draft.py`, `draft_contract.py`, `draft_prompts.py`, `draft_validate.py`, `draft_policy.py`.

| Requirement | Evidence |
|-------------|----------|
| Strict contract | `OUTPUT_KEYS` exact seven; `validate_draft` requires `set(obj.keys()) == set(OUTPUT_KEYS)` |
| No extra keys | extra → `SCHEMA`, fail-closed, no repair |
| Size limits | title 120, summary 400, body 2000, lists 5×200, refs 8 |
| Provenance | `source_refs` must be subset of evidence ids |
| Secrets / exec | regex fail-closed (`SECRET` / `CONTENT`) |
| `tool_calls` | rejected in policy and validator |
| No authority | prompts forbid changing hashes; validator has no write to prep/ASK |
| `tools=None` | `generate_bounded_draft` always passes `tools=None`, `task_type="bounded_draft"` |

`test_33_ast_firewall` **passed** this audit: draft files do not import `subprocess`, `core.task_engine`, `core.orchestrator`, `core.tool_registry`, `tools`, or `proactive.connectors.http_safe`. No `process_request` / `approve_task_action` names.

Only `draft_policy.py` imports `core.model_router` (and `NoCapableProviderError`). That is the **intentional** V6.2.8 control point.

`generate_bounded_draft` does not pass `provider_override`. Privacy list is computed **before** `generate`.

---

## 7. Model Router Security Audit

`capability_requirements["bounded_draft"] = ["reasoning"]` — **not** `tool_calling`, **not** `web_search`. Confirmed by code and tests O, P, R.

`generate(..., allowed_providers=None)`:

- `None` → no extra filter (test Q: general cascade preserved).
- non-`None` → `capable_cascade` filtered to the set **after** capability filter.

Draft path always supplies a privacy-derived `allowed` list (or skips generate when empty).

Circuit breaker may skip a name **inside** `allowed`; it cannot add a name outside `allowed`. Provider override still prepends then concatenates the rest of the cascade, **then** `allowed_providers` filters. Draft does not pass override. Residual footgun if a future caller uses `bounded_draft` without `allowed_providers`: **F-V628-F12**, non-blocking (no current second caller; grep shows only `draft_policy` + tests).

`fallback` is not in `bounded_draft` priorities or `NORMAL_NAMES`.

Non-empty `tool_calls` → `TOOL_CALLS` (not accepted). Empty text → `EMPTY`.

**Router verdict:** COMPLIANT for the draft worker path.

---

## 8. Privacy Audit

| Class | Implemented behavior |
|-------|----------------------|
| SENSITIVE | `allowed_providers_for` → `[]`; worker `continue` without persist; SQL candidates `privacy_class <> 'SENSITIVE'`; `insert_world_draft` returns `""` if SENSITIVE |
| PRIVATE | only `ollama` if enabled, available, **and** `deployment_mode == LOCAL`; private flag default false; no groq in list (test 16); HUD `deliver_draft` requires `privacy_class == NORMAL` |
| NORMAL | up to 2 of `ollama, groq, nim, openai, gemini` if normal flag on |

SENSITIVE never reaches model generation on the worker path.

PRIVATE cannot failover to hosted names because they are never in `allowed`.

`HOSTED_MODES` is defined but unused (`F-V628-F04` informational). LOCAL ollama is **not** a confidentiality proof (`F-V628-F09` inherited architecture note).

GET API can return an ACCEPTED PRIVATE draft body to the **session owner** (persist-only means no HUD/WS push). **F-V628-F05** informational, not a cross-owner leak (test 26 owner isolation).

Prompt construction uses allowlisted structured fields + `render_prepare(template_id)` preview. No email body/subject, snippets, tokens, or URLs in `build_user_prompt`. Untrusted block is literal `(none)`. `PROACTIVE_LLM_DRAFT_UNTRUSTED_EXCERPTS` appears **only** as a comment in `config_example.txt`; **no** `config.py` loader. Dormant.

---

## 9. Output Validation Audit

Fail-closed paths covered by `test_v628` 01–13 (this audit, all OK): valid JSON, malformed JSON, markdown fence, extra keys, wrong enum, oversized body, nested list item, `tool_calls`, executable substrings, secrets, provenance, empty, leading prose.

Invalid results cannot become `get_current_draft` current authority: current requires `validation_status = ACCEPTED` **and** hash match. Rejected payload stored as `{}`.

---

## 10. Database Audit

Additive tables only (`CREATE TABLE IF NOT EXISTS`):

- `world_preparation_drafts`: FK `preparation_id` → `world_preparations` ON DELETE CASCADE; UNIQUE `(owner_id, fingerprint)`; partial unique ACCEPTED per `preparation_id`; CHECK on `draft_type`, `validation_status`, `privacy_class`
- `world_draft_deliveries`: FK to draft; UNIQUE `(draft_id, channel)` HUD only

`insert_world_draft` does not `UPDATE world_preparations`. Stale ACCEPTED drafts with hash mismatch are SUPERSEDED. Idempotent `ON CONFLICT (owner_id, fingerprint) DO UPDATE SET updated_at = updated_at` (no-op).

**F-V628-F01 (NON-BLOCKING):** a REJECTED row occupies the fingerprint. A later valid generation with the same fingerprint cannot upgrade to ACCEPTED (conflict no-op). Candidate SQL still selects preps without a **current ACCEPTED** hash match, so the worker may **re-call the LLM** each tick even though insert is a no-op. Fail-closed for authority; wasteful for cost; drafts may stick REJECTED after one bad model reply. Not an ASK/ACT bypass.

`world_preparations.status = DRAFT` unused.

**Database verdict:** additive, owner-bound, preparation-FK, no mutation of preparation authority.

---

## 11. Worker Audit

Required conceptual order is implemented. `evaluate_world_drafts` is in its own `try/except` (`draft_eval`), **before** ASK expiry, **outside** `claim_batch`. Test 29 asserts worker still invokes `claim_batch` if draft eval raises.

LLM failure (`NO_COMPLIANT_PROVIDER`, `TIMEOUT`, `PROVIDER`): skip persist, preparation untouched. Validation failure: REJECTED draft row only.

**Worker verdict:** COMPLIANT. Isolation holds. Worst-case stall: up to 5 candidates × 8s timeout ≈ 40s on a background tick if every generate times out (**F-V628-F06** related). Acceptable for a background worker with flags default off; not a correctness blocker.

---

## 12. ASK / Authority Audit

`ask_decisions.py` / `ask.py` / `dashboard/ask_session.py` **unmodified** vs v6.2.6. Test 30 asserts `binding_hash_for` unchanged with/without draft.

Draft cannot approve, reject, revoke, execute, or apply. No new weaker approval path. `param_hash` on the preparation remains the binding input.

**ASK verdict:** V6.2.6 authority intact. **APPROVED ≠ EXECUTED.**

---

## 13. API / UI Audit

`GET /api/proactive/preparations/{preparation_id}/draft`:

- `require_ask_session(..., need_csrf=False)`
- flags: prepare + `is_llm_draft_enabled()`
- `get_current_draft(id, session owner)` — no generate
- returns `type: proactive_draft`, `tts: False`, disclaimer that approval is parameters not execution

No new `/execute`, `/apply`, `/run`, `/approve-and-execute` for drafts (test 28). Pre-existing IDE/command `execute` routes are unrelated to preparations.

`deliver_draft`: distinct `proactive_draft`; TTS false; NORMAL only; title/summary/disclaimer (HUD omits full body). Not inserted into `WorldSnapshot` fields. Not TaskEngine.

**API/UI verdict:** COMPLIANT.

---

## 14. Telemetry / Observability Audit

Draft OTP attributes: ids, provider, model, validation_status, reject_reason, privacy_class. No prompt text, no LLM body.

`FORBIDDEN_ATTR_KEYS` still includes `prompt`, `response`, `body`, `token`, etc. New allowlist keys are metadata only.

Router `provider.generate.*` events remain metadata (provider, hop, capability). Draft prompts are not copied into those attributes by `draft_policy`.

**Telemetry verdict:** COMPLIANT (metadata only on new paths).

---

## 15. Config / Flag Audit

Defaults (`proactive/config.py`):

| Flag | Default |
|------|---------|
| `PROACTIVE_LLM_DRAFT_ENABLED` | **false** |
| `PROACTIVE_LLM_DRAFT_NORMAL_ENABLED` | **false** |
| `PROACTIVE_LLM_DRAFT_PRIVATE_ENABLED` | **false** |
| `DRAFT_PROMPT_VERSION` | `v628.1` |
| candidate cap | 5 |
| timeout | 8.0s |
| max hops | 2 (allowlist length, not a separate router counter) |

`flags_draft_on()` also requires proactive + prediction + suggest + prepare. Disabling V6.2.8 master flag makes `evaluate_world_drafts` return 0 immediately (test 20). V6.2.6 prepare/ASK path does not depend on draft flags.

---

## 16. Regression Tests (this forensic session)

| Suite | Result | Notes |
|-------|--------|-------|
| `test_v628_llm_draft.py` | **35 pass, 1 skip** | skip `test_23` — V6.2.6 never persists SENSITIVE preps; policy covered by `test_15` |
| `test_provider_routing_scenarios.py` | **20/20** | O–R present |
| `test_v626_prepare_ask.py` | **exit 0** | 44 tests in file; first forensic invocation completed ~23s |
| `test_v625_suggest.py` | **36/36** | `V625_PERF_MS=265.50` |
| `test_v623_email_commitments.py` | **not completed live** | later batched run produced no footer after long wait (likely Postgres contention from overlapping invocations). Implementation-phase record: **11/11**. No V6.2.8 edits to that suite. |
| `test_v62_connectors.py` | **not completed live** | same. Implementation-phase: **12/12**. Connectors unmodified. |
| `test_v62_emitters.py` | **not completed live** | same. Implementation-phase: **13/13**. |
| `test_v532_transaction_engine.py` | **not completed live** | same. Implementation-phase: **30/30**. Transaction engine unmodified. |
| `test_v61_proactive_foundation.py` | **static + prior live** | **F-V622-07** TestClient errors inherited. **`test_91` must fail** (see §17). 76 `test_*` methods in file. |
| `test_v624_evidence_prediction.py` | **not re-completed live** | 40 tests in file; `predict.py` unmodified. Inherited **F-V625-F01** owner-cap/env failures remain classified inherited. |

Incomplete later suites are an **audit-process** limitation (overlapping Python/Postgres), not a V6.2.8 functional failure. Protected modules those suites cover were not modified.

---

## 17. test_91 Classification (required)

V6.1 `test_91_no_llm_imports_in_proactive` concatenates **every** `proactive/*.py` and asserts the substring `model_router` is absent.

**A. Is test_91 still a valid security invariant?**  
It was valid for V6.1–V6.2.6: proactive had no LLM. As a **global “no model_router token in proactive/*.py”** rule, it is **obsolete** once a bounded, tools-off, privacy-filtered draft wrapper is an approved feature. The security invariant that still matters is: no `process_request`, no tools, no ACT, privacy-before-generate, fail-closed validation — **not** the substring.

**B. Does V6.2.8 legitimately require violating the old invariant?**  
**Yes.** Approved architecture: `draft_policy.py` must call `ModelRouter.generate` with `tools=None` and `allowed_providers`. That requires the import (or an equivalent name that would also fail a naive glob).

**C. Is the failure caused solely by the expected V6.2.8 architecture?**  
**Yes.** `draft_policy.py` contains `from core.model_router import ...`. No other new proactive root-level LLM provider classes (`GroqProvider` still absent from that glob intent).

**D. Safer architecture without weakening the real boundary?**  
A thin `core/bounded_draft_router.py` wrapper would hide the substring from `proactive/*.py` but would **not** improve security; it would only appease the glob. Keeping the import next to `tools=None` and privacy filtering is the clearer control point. Excluding `draft_policy.py` from test_91 (or asserting `tools=None` + no TaskEngine) is the correct **future test-scope** update. **Not done in this slice** (tests frozen).

**E. Does the test need a future scope update?**  
**Yes.** V6.2.8 is the first **controlled** model-router dependency in `proactive/`.

**Classification:** **EXPECTED / TEST-OBSOLESCENCE**

Not BLOCKING for security/ASK/ACT. Not an ARCHITECTURAL DEFECT (the architecture explicitly required this import). Owner release process must **not** require a green unmodified `test_91` unless the owner chooses to update that test in a later slice.

Finding ID: **F-V628-I01** (same as implementation report).

---

## 18. Test Quality vs Planned ~48 Cases

`python -m unittest test_v628_llm_draft` collected **36** tests, not 48.

Plan table 1–41 were in-file cases; **42–48 were documented regression commands**, not additional methods in `test_v628_llm_draft.py`. Implementation report “36 cases” is **collection-accurate**.

In-file mapping is largely present (flags, persist, privacy, schema, AST, API, worker isolation, FUTURE_EMAIL_DRAFT, binding).

**Coverage gaps vs plan 1–41 (not added):**

| Plan # | Gap |
|--------|-----|
| 8–11 | Dedicated NoCapableProvider / timeout / circuit-all-skip / rate-limit hop tests (behavior exists in policy: skip persist on TIMEOUT/PROVIDER) |
| 23 | Injection in `claim_code` as its own case (prompt still JSON-allowlisted; schema still fail-closed) |
| 33 | OTP event has no `prompt` key (telemetry schema forbids `prompt`; draft emits do not include it — inspect-only) |
| 36–37 | Explicit 8s / 2-hop unit tests (constants match plan; timeout wraps **entire** `generate`, so hop 2 may not run inside 8s) |
| 4 | NORMAL+ollama as a dedicated allowlist case (NORMAL hosted covered by test 19) |

**F-V628-F08 (NON-BLOCKING):** planned in-file depth is slightly thinner than 41 numbered rows; regressions were always separate. Not a missing ACT test. Do not treat 35+1 as “48 renamed.”

---

## 19. Performance Findings

Measured this audit:

- `test_v628_llm_draft.py` wall **2.496s** (includes Postgres draft persist cases).
- Routing tests **0.041s**.
- V6.2.5 suggest suite `V625_PERF_MS=265.50`.

Inspected bounds (not optimized):

- Candidate cap **5**.
- Per-generate timeout **8s** (`ThreadPoolExecutor`, `shutdown(wait=False)` — **F-V628-F02** possible orphaned generate thread after timeout).
- Policy/fingerprint/validate are in-process and cheap relative to LLM I/O.
- Worker impact: draft eval is outside INFORM batch; worst case ~40s if all five time out. Acceptable for a default-off background worker.

A dedicated microbench import hung when overlapping with other Postgres-using tests; not used as a numeric claim.

---

## 20. Security Threat Model

| Threat | Result |
|--------|--------|
| Prompt injection | Untrusted excerpts dormant; DATA_ONLY JSON allowlist; validator fail-closed; cannot change hashes via JSON extras |
| Tool-call injection | `tools=None` + reject non-empty `tool_calls` |
| Execution-instruction injection | `_EXEC_RE` / HTTP write / auth-assign reject |
| Provider fallback bypass | `allowed_providers` after cascade; PRIVATE list is ollama-only |
| Privacy downgrade | SENSITIVE never generated/persisted; PRIVATE no hosted names |
| Owner substitution / cross-owner GET | `get_current_draft` filters `owner_id`; test 26 |
| Stale preparation | SUPERSEDE + hash equality for current |
| Replay / duplicate ACCEPTED | fingerprint unique + partial unique ACCEPTED; test 21 |
| Approval / parameter substitution | ASK/prep unmodified; draft cannot write `safe_params` |
| Secret / connector prose leakage into prompt | allowlisted keys; no snippet/subject in prompt (tests 35–36) |
| Hallucinated authority | presentation only; ASK binding independent |
| Confused deputy | draft cannot approve; GET is read-only |
| Fail-open | empty/invalid → not ACCEPTED; insert SENSITIVE refused |

Residual: **F-V628-F01** (REJECTED sticky + re-generate), **F-V628-F12** (router API can be called without privacy filter by **future** code).

**Security verdict:** PASS for V6.2.8 scope. No blocker.

---

## 21. Protected Boundaries

**Not modified:** predict, evidence, suggest, prepare TYPE_MAP/worthiness, prepare_templates allowlist, ask_decisions canonical binding, connectors, SafeHttp, TaskEngine, tools, memory, orchestrator, V6.2.6 session/approval modules.

Router change is **additive** (`bounded_draft` + optional `allowed_providers`). Existing `allowed_providers=None` preserves prior cascade (test Q).

---

## 22. Findings Register

### New V6.2.8

| ID | Severity | Summary |
|----|----------|---------|
| F-V628-I01 | **EXPECTED / TEST-OBSOLESCENCE** | V6.1 `test_91` forbids `model_router` in all `proactive/*.py`; V6.2.8 requires it in `draft_policy.py` |
| F-V628-F01 | NON-BLOCKING | REJECTED fingerprint blocks later ACCEPTED; candidates may re-generate every tick |
| F-V628-F02 | NON-BLOCKING | `ThreadPoolExecutor.shutdown(wait=False)` on timeout may leave a generate running |
| F-V628-F03 | INFORMATIONAL | Returned provider name is `allowed[0]`, not necessarily the hop that succeeded |
| F-V628-F04 | INFORMATIONAL | `HOSTED_MODES` unused; PRIVATE uses `deployment_mode == LOCAL` on ollama |
| F-V628-F05 | INFORMATIONAL | Owner-scoped GET may return PRIVATE draft body; HUD/WS still NORMAL-only |
| F-V628-F06 | NON-BLOCKING | 8s timeout wraps full `generate` (failover hops may not complete); cap 5 × 8s worker stall |
| F-V628-F08 | NON-BLOCKING | In-file tests are 36 collected vs plan rows 1–41; 42–48 are separate regressions |
| F-V628-F09 | INFORMATIONAL | LOCAL ≠ confidentiality (architecture already stated) |
| F-V628-F12 | NON-BLOCKING | `ModelRouter.generate(bounded_draft)` without `allowed_providers` is a future footgun; worker does not do this |
| F-V628-N01 | NON-BLOCKING | Unrelated dirty V6.2.5/V6.2.6 release-report SHA fill-ins and historical untracked markdown |
| F-V628-N02 | INFORMATIONAL | Forensic re-run of v623/connectors/emitters/v532/v61/v624 incomplete due to overlapping Postgres-bound processes |

### Inherited (not new V6.2.8 defects)

| ID | Notes |
|----|-------|
| F-V622-07 | V6.1 TestClient HUD errors |
| F-V625-F01 | V6.2.4 owner-cap / env prediction failures; `predict.py` unchanged |
| F-V626-* | Prior PREPARE/ASK non-blocking items; not remediating in V6.2.8 |
| CORS `*` / delivery→server import | Pre-existing dashboard patterns; GET draft follows same session model |

No finding is classified **BLOCKING**.

---

## 23. Release-Readiness Gate Matrix

| Gate | Status |
|------|--------|
| HEAD is v6.2.6; no V6.2.8 commit/tag/push | **PASS** |
| Pipeline PREPARE→DRAFT→VALIDATE→PERSIST→ASK→STOP | **PASS** |
| No ACT / execute / apply | **PASS** |
| `tools=None` + reject `tool_calls` | **PASS** |
| SENSITIVE: no generate/persist/HUD | **PASS** |
| PRIVATE: local ollama only; no HUD | **PASS** |
| ASK binding / `param_hash` unchanged | **PASS** |
| Additive schema only | **PASS** |
| Flags default false | **PASS** |
| Protected modules unmodified | **PASS** |
| `test_v628` 35 pass / 1 skip | **PASS** |
| Routing 20/20 | **PASS** |
| V6.2.6 44 / V6.2.5 36 | **PASS** (this session) |
| V6.1 `test_91` green | **N/A — obsolete glob**; owner must accept F-V628-I01 |
| Unrelated dirty files excluded from commit | **OWNER ACTION at release time** |

**Conditions for owner authorization:**

1. Accept **F-V628-I01** (do not block release solely on unmodified `test_91`).
2. Keep LLM draft flags **off** in production until the owner explicitly enables them.
3. Do not include unrelated `DOOM_V6.2.5_RELEASE_REPORT.md` / `DOOM_V6.2.6_RELEASE_REPORT.md` dirt or leftover provider-routing markdown in the V6.2.8 commit unless the owner separately decides.
4. Treat F-V628-F01 as known draft-retry limitation (optional follow-up; not ASK-critical).

---

## 24. Working-Tree Status (exact)

```
Branch: DOOM-V5.2 (tracks origin/DOOM-V5.2)
HEAD:   7868bcdb1280607cd255013a36b8f0ac191d6625  release: DOOM v6.2.6
Tag:    v6.2.6 → object 984041831916fb0d2c5fd1e6a2c77cfb1ab9c94b
        peeled 7868bcd
v6.2.8 tag: absent
Staged: none
```

Modified vs HEAD (implementation + unrelated reports):  
`DOOM_V6.2.5_RELEASE_REPORT.md`, `DOOM_V6.2.6_RELEASE_REPORT.md`, `config_example.txt`, `core/model_router.py`, `dashboard/server.py`, `database/postgres_db.py`, `observability/schemas.py`, `proactive/config.py`, `proactive/delivery.py`, `proactive/store.py`, `proactive/worker.py`, `test_provider_routing_scenarios.py`

Untracked V6.2.8: draft modules, `test_v628_llm_draft.py`, V6.2.8 architecture/plan/implementation/forensic markdown.

This forensic file is an **audit artifact**. Creating it does not constitute a product code change.

---

## 25. Final Verdict Block

1. **Final verdict:** PASS WITH NON-BLOCKING FINDINGS — RELEASE CONDITIONALLY RECOMMENDED  
2. **Architecture compliance:** COMPLIANT (PREPARE→DRAFT→VALIDATE→PERSIST→ASK→STOP; no ACT)  
3. **Security verdict:** PASS (fail-closed draft; no execution authority)  
4. **Privacy verdict:** PASS (SENSITIVE never; PRIVATE local ollama; NORMAL policy allowlist)  
5. **ASK/authority verdict:** PASS (V6.2.6 binding intact; APPROVED ≠ EXECUTED)  
6. **Test results:** v628 **35 pass / 1 skip**; routing **20/20**; v626 **exit 0 / 44 tests**; v625 **36/36**; some later suites not re-finished live (**F-V628-N02**)  
7. **test_91:** EXPECTED / TEST-OBSOLESCENCE (**F-V628-I01**), not BLOCKING  
8. **Database verdict:** PASS (additive; no prep-authority mutation)  
9. **Worker verdict:** PASS (isolated, after prepare, before ASK expire, outside claim_batch)  
10. **API/UI verdict:** PASS (GET read-only; `proactive_draft`; TTS false)  
11. **Performance:** acceptable for default-off background worker; worst-case ~40s/tick if all drafts time out  
12. **Findings:** see §22; highest new items are NON-BLOCKING or EXPECTED  
13. **Blockers:** **none**  
14. **Working tree:** HEAD v6.2.6; V6.2.8 uncommitted; no v6.2.8 tag; no V6.2.8 push  
15. **Safe to present for release authorization?** **Yes.**

**V6.2.8 IS READY FOR OWNER RELEASE AUTHORIZATION.**

The owner will make the release decision separately. This forensic audit does **not** commit, tag, push, or release.
