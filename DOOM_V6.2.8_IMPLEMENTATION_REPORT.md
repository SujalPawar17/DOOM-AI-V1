# DOOM V6.2.8 — Implementation Report

**LLM-assisted preparation / drafting (non-authoritative)**

**Date:** 2026-09-10  
**Baseline:** `v6.2.6` `7868bcdb1280607cd255013a36b8f0ac191d6625` on `DOOM-V5.2`  
**Status:** **V6.2.8 IMPLEMENTED — NOT RELEASED**

No commit, tag, or push. ACT / V6.3 not implemented. **APPROVED does not execute.**

---

## 1. Implementation status

Optional LLM drafts run after deterministic PREPARE, are validated, may persist as `world_preparation_drafts`, then existing ASK runs. Failures leave preparations `READY`/`ASKED` (`PREPARED_WITHOUT_LLM` is conceptual only).

Flags default **off**. `tools=None`. Privacy enforced before generate. PRIVATE → ollama LOCAL only. SENSITIVE never sent or persisted.

---

## 2. Files created

- `proactive/draft.py`
- `proactive/draft_contract.py`
- `proactive/draft_prompts.py`
- `proactive/draft_validate.py`
- `proactive/draft_policy.py`
- `test_v628_llm_draft.py`
- `DOOM_V6.2.8_IMPLEMENTATION_REPORT.md` (this file)

(Architecture audit and implementation plan were already untracked from the planning phase.)

---

## 3. Files modified

- `database/postgres_db.py` — additive draft tables
- `proactive/config.py` — flags, `DRAFT_PROMPT_VERSION`, cap/timeout/hops
- `config_example.txt` — commented flags; excerpts marked dormant
- `core/model_router.py` — `bounded_draft` maps; `allowed_providers` on `generate`
- `proactive/store.py` — draft persist/list/stale/delivery
- `proactive/worker.py` — `evaluate_world_drafts` after prepare, before ASK expire
- `proactive/delivery.py` — `deliver_draft` / `proactive_draft`
- `dashboard/server.py` — GET `/api/proactive/preparations/{id}/draft` only
- `observability/schemas.py` — `draft_id`, `validation_status`, `reject_reason`, `deployment_mode`
- `test_provider_routing_scenarios.py` — tests O–R

**Not modified:** `predict.py`, `evidence.py`, `suggest.py`, `prepare.py` worthiness/`TYPE_MAP`, `ask_decisions.py` binding, connectors, TaskEngine, tools, memory, README.

---

## 4. Schema

Additive:

- `world_preparation_drafts` — FK CASCADE to `world_preparations`; UNIQUE `(owner_id, fingerprint)`; partial unique ACCEPTED per `preparation_id`
- `world_draft_deliveries` — HUD channel unique per draft

No change to preparation/ASK authority columns. `world_preparations.status = DRAFT` unused.

---

## 5. Model-router changes

- `capability_requirements["bounded_draft"] = ["reasoning"]` (not tool_calling / web_search)
- `capability_priorities["bounded_draft"] = ["ollama", "groq", "nim", "openai", "gemini"]`
- `generate(..., allowed_providers=None)` — optional filter; omitted = previous cascade

Existing task types unchanged when `allowed_providers` is omitted.

---

## 6. Privacy / security

| Class | Behavior |
|-------|----------|
| SENSITIVE | no generate, no persist |
| PRIVATE | ollama `deployment_mode=LOCAL` only, if private flag on |
| NORMAL | bounded_draft allowlist if normal flag on |

`draft_policy.generate_bounded_draft` always `tools=None`, `task_type="bounded_draft"`, 8s timeout, max 2 hops. Non-empty `tool_calls` → `TOOL_CALLS` reject.

ASK binding unchanged. No execute/apply/run routes. No `process_request` / TaskEngine from draft modules.

---

## 7. Worker

`process_once`: predict → suggest → prepare → **drafts** → ASK expire → INFORM `claim_batch`. Draft isolated `try/except` (`draft_eval`).

---

## 8. API / UI

`GET /api/proactive/preparations/{preparation_id}/draft` — session, no CSRF, owner-scoped, read-only, never generates. Card type `proactive_draft`. TTS false. Disclaimer: approval authorizes parameters, not execution. PRIVATE persist-only.

---

## 9. Dedicated tests

`test_v628_llm_draft.py`: **35 passed, 1 skipped, 0 failed**.

Skip: `test_23_sensitive_no_row` — V6.2.6 prepare worthiness never persists SENSITIVE preparations (`PRIVACY_BLOCK`). Policy still blocks SENSITIVE (`test_15`).

`test_provider_routing_scenarios.py`: **20/20** including O–R.

---

## 10. Regression results

| Suite | Result |
|-------|--------|
| `test_v626_prepare_ask.py` | **44/44** |
| `test_v625_suggest.py` | **36/36** (`V625_PERF_MS=242.75`) |
| `test_v623_email_commitments.py` | **11/11** |
| `test_v62_connectors.py` | **12/12** |
| `test_v62_emitters.py` | **13/13** |
| `test_v532_transaction_engine.py` | **30/30** |
| `test_v61_proactive_foundation.py` | 4 TestClient errors **F-V622-07**; **plus `test_91` FAIL** (see findings) |
| `test_v624_evidence_prediction.py` | inherited configured-owner / env failures (**F-V625-F01** and related); `predict.py` not modified |

---

## 11. AST / security

`test_33_ast_firewall` walks draft modules: no `subprocess` / TaskEngine / orchestrator / tool_registry imports. `draft_policy.py` is the only draft file that imports `model_router`; `tools=None` required.

Dashboard has no `/approve-and-execute` / `/execute-approved`. `FUTURE_EMAIL_DRAFT` still unmapped.

---

## 12. Known findings

| ID | Notes |
|----|--------|
| F-V628-A01–A09 | Handled in-slice (tools=None, allowed_providers, new table, no excerpts, no ACT) |
| F-V628-I01 | V6.1 `test_91_no_llm_imports_in_proactive` concatenates **all** `proactive/*.py` and forbids the substring `model_router`. V6.2.8 **requires** that import in `draft_policy.py`. Test not rewritten. Expected until a later test-scope update. |
| F-V622-07 | Inherited TestClient HUD errors |
| F-V625-F01 | Inherited v6.2.4 owner-cap; predict unchanged |
| F-V626-* | Inherited; not remediated |
| F-V628-I02 | SENSITIVE preparations are not created by V6.2.6; draft skip covered at policy layer |

---

## 13. Deviations from plan

- Dedicated suite is 36 cases (35+1 skip), not 48 numbered items; regressions are separate commands as planned.
- No `proactive_attention.draft_count` (plan: none in v1).
- Untrusted excerpts: comment-only in `config_example.txt`; no `is_*` function.
- V6.1 test_91 not altered (see F-V628-I01).

---

## 14. Git status

HEAD remains `7868bcd` `release: DOOM v6.2.6`. V6.2.8 sources uncommitted. Unrelated dirty `DOOM_V6.2.5_RELEASE_REPORT.md` / `DOOM_V6.2.6_RELEASE_REPORT.md` and leftover historical markdown untouched.

---

## 15. Confirmations

- **No commit**
- **No tag**
- **No push**
- **V6.3 ACT NOT IMPLEMENTED**
- **APPROVED ≠ EXECUTED**

V6.2.8 IMPLEMENTATION COMPLETE — RELEASE NOT AUTHORIZED.
