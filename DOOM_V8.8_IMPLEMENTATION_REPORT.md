# DOOM V8.8 — Safe Memory & Experience Context

Read-only, bounded, privacy-safe historical DATA for future planning. Not learning, authorization, execution, or policy.

**Verdict: V8.8 READY FOR REVIEW**

---

## 1. Summary

V8.8 adds `retrieve_context()` above V5 Memory and V7.7 Experience. Callers inject narrow read adapters. Results are immutable `ContextItem`s. Nothing in this layer writes memory, writes experience, mutates the task ledger, calls `execute_plan`, recovers, or changes V8.2/V8.4/V7.6 authority.

`context_hash` is diagnostic identity only. It is **not** `plan_hash` and **not** authorization.

## 2. Files created

- `orchestration/context/__init__.py`
- `orchestration/context/types.py`
- `orchestration/context/policy.py`
- `orchestration/context/errors.py`
- `orchestration/context/sanitizer.py`
- `orchestration/context/memory_adapter.py`
- `orchestration/context/experience_adapter.py`
- `orchestration/context/retriever.py`
- `test_v88_safe_context.py`
- `DOOM_V8.8_IMPLEMENTATION_REPORT.md`

## 3. Files modified

- `proactive/config.py` — `is_v8_context_enabled()` / `PROACTIVE_V8_CONTEXT_ENABLED` default **false**
- `config_example.txt` — documented the flag
- `orchestration/goal/planner.py` — unused `planner_context` data-only seam (planning unchanged)
- `orchestration/__init__.py` — export `retrieve_context`, `ContextRequest`, `ContextResult`

Voice/STT, V7 kernels, Cost Guard, V8.7 ledger SQL, and V8.4/V8.5 behavior were not redesigned.

## 4. V5 read interface used

`MemoryManager.retrieve(query, task_id=...)` via `bind_v5_retrieve(retrieve_fn)`. Tests use fake `read_fn` dicts. Adapter never exposes `store` / `update` / `delete`. Records without `metadata.owner_id` (or matching `owner_id`) are dropped (fail closed).

## 5. V7.7 read interface used

Public list/get shape: `experience_id`, `outcome`, `capability`, `action`, `verification_status`, `provenance`, `timestamp_unix_ms`, plus test `owner_id`. **No** `proactive.computer.*` import in V8.8 (AST). Production callers inject a read function over `list_experiences` results. Owner missing → record omitted.

## 6. Context types

`ContextRequest`, `ContextItem`, `ContextResult`, `PlannerContext`, `ContextSource` (`MEMORY` | `EXPERIENCE`), `ContextStatus` (`OK`, `EMPTY`, `PARTIAL`, `CONTEXT_UNAVAILABLE`, `INVALID_REQUEST`, `DISABLED`).

## 7. Context policy

Frozen `ContextPolicy`. Requested limits are clamped to hard ceilings.

## 8. Hard limits

`MAX_CONTEXT_ITEMS=8`, `MAX_MEMORY_ITEMS=4`, `MAX_EXPERIENCE_ITEMS=4`, `MAX_ITEM_CHARS=2000`, `MAX_TOTAL_CONTEXT_CHARS=12000`.

## 9. Sanitization

Null bytes stripped. Secret-like `password` / `api_key` / `Bearer` / `Authorization` / cookie / private-key blocks → `[REDACTED]`. Instruction-like text remains **inert DATA**.

## 10. Privacy

No filesystem follow, no URL fetch, no full history dump. Sensitive experience → `SENSITIVE_OMITTED`. Bounded items and characters only.

## 11. Owner / session isolation

Owner must match canonical adapter owner field. Cross-owner records never returned. `session_id` is request metadata, not authorization.

## 12. Deterministic ordering

`(-relevance, source, reference_id, timestamp_unix_ms)`.

## 13. Deduplication

Stable key `(source, reference_id)`.

## 14. Read-only guarantees

Adapters expose `read` only. Spies: memory/experience write/update/delete = 0; `execute_plan` = 0; `recover_execution` = 0; ledger `try_persist_create` = 0.

## 15. Planner context seam

`plan_goal(goal, planner_context=None)` accepts `PlannerContext` and **does not** change proposals. V8.2 `build_goal_plan` remains mandatory.

## 16–19. Authority isolation

Public flags `authorizes_execution=false`, `approved=false`, `plan_hash=""`. Historical `approved=true` / `risk=LOW` / `verification=unnecessary` / `secret_admin` do not mutate catalog, plans, risk, or verification.

## 20. V7 isolation

No `proactive.computer.*`, pyautogui, playwright, selenium, ctypes, subprocess.

## 21. Legacy isolation

No `TaskEngine`, `ALL_TOOLS`, `computer_tools`.

## 22. V8.7 ledger isolation

Context package does not import `orchestration.task`.

## 23. Cost Guard

HARD $0. No LLM, HTTP, or new embedding provider.

## 24–26. Tests

Immutability, bounds, truncation, total chars, injection, secrets, owner isolation, empty, partial/both failure, readonly spies, execution spies, planner seam, catalog unchanged, AST, V5 bind retrieve-only, sensitive omit, invalid request, disabled flag.

## 27. Test counts

| Suite | Tests | Passed | Failed | Errors |
|--------|-------|--------|--------|--------|
| V8.8 | 17 | 17 | 0 | 0 |
| V8.1–V8.8 | 162 | 162 | 0 | 0 |
| V7 + Cost Guard + V8.1–V8.8 | 319 | 316 | 0 | 3 baseline |

## 28. Existing baseline errors

Unchanged FastAPI `Router.__init__() got an unexpected keyword argument 'on_startup'`:

- `test_v71_computer_observe.test_api_auth_csrf_and_second_session`
- `test_cost_guard.test_dashboard_types_not_direct_groq`
- `test_cost_guard.test_ide_uses_router`

## 29–36. Confirmations

No real side effects (fakes/spies). No learning. No context DB/files/queue. No network. No LLM. Voice/STT untouched. V8.9/V8.10 untouched. No git commit/push/tag.

Flag: `PROACTIVE_V8_CONTEXT_ENABLED=false` → `DISABLED`, no execution.
