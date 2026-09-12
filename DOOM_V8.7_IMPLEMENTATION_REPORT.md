# DOOM V8.7 — Persistent Task Ledger

Implementation report. Persistence records state and history only. It is not an execution queue, scheduler, or authorization source.

**Verdict: READY FOR REVIEW**

---

## 1. Files created

- `orchestration/task/ledger.py`
- `orchestration/task/ledger_models.py`
- `orchestration/task/ledger_errors.py`
- `orchestration/task/ledger_store.py`
- `orchestration/task/ledger_schema.py`
- `orchestration/task/migrations/v87_ledger.sql`
- `test_v87_task_ledger.py`
- `DOOM_V8.7_IMPLEMENTATION_REPORT.md`

## 2. Files modified

- `orchestration/task/types.py` — `TaskSnapshot.version`
- `orchestration/task/registry.py` — persist-before-mutate; inspection-only rehydrate insert (`plan=None`)
- `orchestration/task/engine.py` — `start_task` refuses unbound plans (`PLAN_NOT_BOUND`)
- `orchestration/task/__init__.py` — ledger inspection exports
- `proactive/config.py` — `is_v8_ledger_enabled()` / `PROACTIVE_V8_LEDGER_ENABLED` default **false**
- `config_example.txt` — documented the flag
- `database/postgres_db.py` — `_create_tables` applies `LEDGER_DDL` (`CREATE IF NOT EXISTS` only)

Voice/STT, V7 kernels, V8.1–V8.5 planners/executors, and Cost Guard provider policy were not redesigned.

## 3. Database technology

Existing local PostgreSQL via `database/postgres_db.py` (`PostgresManager` / parameterized `psycopg2`). No Redis, SQLite-only engine, cloud DB, or task broker.

Tests use an explicit in-memory `MemoryLedgerStore` (not a silent production fallback). Production writes occur only when `PROACTIVE_V8_LEDGER_ENABLED=true` and Postgres can apply schema.

## 4. Migration created

Repository has no separate migrator. V8.7 follows the existing `CREATE TABLE IF NOT EXISTS` pattern:

- SQL source: `orchestration/task/migrations/v87_ledger.sql`
- Applied by `LEDGER_DDL` in `PostgresManager._create_tables` and `PostgresLedgerStore.ensure_schema()`
- Reversible only by dropping the new tables (framework has no automatic DOWN). Existing V5 tables are not altered or truncated.

## 5. Tables / schema

- `doom_v8_tasks` — one row per `task_id` (PRIMARY KEY)
- `doom_v8_task_transitions` — UNIQUE `(task_id, sequence_number)` with FK CASCADE
- Indexes: `owner_id`, `goal_id`, `state`, `plan_hash`, `created_unix_ms`
- CHECKs: `transition_count <= 64`, `version >= 1`, `sequence_number` 1–64

## 6. Task record fields

`task_id`, `goal_id`, `owner_id`, `session_id`, `computer_session_id`, `plan_hash`, `recovery_plan_hash`, `state`, `created_unix_ms`, `started_unix_ms`, `completed_unix_ms`, `attempts_used`, `transition_count`, `failure_reason`, `execution_started`, `pending_recovery`, `version`, `updated_unix_ms`

Bounded strings. No JSON blobs, secrets, prompts, screenshots, or tool dumps.

## 7. Transition record fields

`task_id`, `sequence_number`, `from_state`, `to_state`, `timestamp_unix_ms`, `reason_code`, `plan_hash`, `version`

## 8. Version / concurrency model

Optimistic concurrency: `UPDATE ... WHERE task_id = %s AND version = expected_version`. Zero rows → `LedgerConcurrencyError`. Memory store compares version under a lock. Concurrent `RUNNING→COMPLETED` vs `RUNNING→CANCELLED` commits exactly one; the other fails closed.

## 9. Transaction semantics

Postgres: `FOR UPDATE`, insert transition, update task, commit. Any failure rolls back. Memory store mutates task + events under one lock. Callers never get a successful transition in-process if persist failed (`apply_transition` persists first).

External V8.4 mutations cannot be rolled back by the database. Ledger failure after world mutation does **not** replay. Uncertain execution is aborted on restart, not continued.

## 10. Capacity limits

`MAX_TASKS = 256`. Additional creates → `LedgerCapacityError`. No silent deletion of active tasks. No background cleanup worker.

## 11. Transition limits

`MAX_TRANSITIONS_PER_TASK = 64` (app + CHECK). Overflow → `LedgerValidationError`.

## 12. Retention behavior

No automatic purge. Fail closed at 256 rows. Terminal cleanup is not implemented in V8.7.

## 13. Restart behavior

`rehydrate(execute=False)` loads bounded snapshots into the in-process registry **without** `GoalPlan`. `rehydrate(execute=True)` is rejected. No `resume_task` / replay API.

## 14. Startup reconciliation

In-flight `RUNNING` and `RECOVERING` → `ABORTED` with reason `STALE_IN_FLIGHT`. Bookkeeping only: no `execute_plan`, V7, V6, or V8.5 recovery.

`READY` / `WAITING` remain inspectable and still do not execute (no bound plan).

## 15. No automatic replay

Confirmed. Tests patch `execute_plan` / `recover_execution` and assert zero calls after rehydrate, including stored `RUNNING` + original `plan_hash`.

## 16. No automatic resume

Confirmed. No resume API. Unbound registry rows raise `InvalidTaskPlan("PLAN_NOT_BOUND")` on `start_task`.

## 17. V8.6 integration

Preferred order: validate → persist → update registry. `create_task` / `apply_transition` call `try_persist_*` only when a store is attached or the ledger flag is on. Default flag off keeps V8.6 tests unchanged (no writes).

## 18. V8.5 recovery integration

Ledger does not call recovery. Engine still runs V8.5; persistence records `RECOVERING` and a separate `recovery_plan_hash` without overwriting original `plan_hash`.

## 19. Authorization behavior

`LedgerTaskRecord.as_public()["authorizes_execution"] = False`. Stored `READY`/`RUNNING`/`plan_hash`/`task_id` are not permission. V8.4 hash + session checks remain authoritative at execution time. Rehydrated session IDs do not create sessions.

## 20. Plan-hash behavior

Original `plan_hash` is identity metadata. Recovery hash is a separate column. Neither authorizes execution.

## 21. V7 boundary

Ledger modules do not import `proactive.computer.*`, pyautogui, Playwright, Selenium, ctypes, or subprocess. No computer actions.

## 22. Legacy isolation

No `TaskEngine` class, `ALL_TOOLS`, `computer_tools`, or CognitiveEngine ACT imports in V8.7 files.

## 23. Cost Guard

HARD $0. No LLM, HTTP, paid/unknown providers, or telemetry in the ledger. Database persistence is not an AI provider.

## 24. Privacy / serialization

Parameterized SQL only. No pickle / eval / exec / dynamic import. Oversized IDs rejected (not truncated). Reason codes bounded. Exceptions do not include connection strings.

## 25. Security tests

Covered: SQL injection as a task_id, oversized IDs/hashes, malformed snapshots, pickle bytes, fake authorization flags, stale version, illegal transitions, terminal restart, replay/recovery after crash, persist failure vs COMPLETED, flag-off unavailable, no arbitrary SQL.

## 26. AST tests

`test_42_ast_isolation` / `test_43_no_learning_or_explainability_symbols` scan `ledger*.py`.

## 27. V8.7 test count

**49** tests in `test_v87_task_ledger.py`.

## 28. V8.7 pass / fail / error

**49 pass / 0 fail / 0 error.**

## 29. Combined V8.1–V8.7

**145 pass / 0 fail / 0 error.**

(V8.1–V8.6 baseline 96 + V8.7 49.)

## 30. V7 + Cost Guard + V8 regression

**302 tests, 3 errors, 0 new failures.**

## 31. Existing baseline failures / errors

Unchanged FastAPI `Router.__init__() got an unexpected keyword argument 'on_startup'`:

- `test_v71_computer_observe.test_api_auth_csrf_and_second_session`
- `test_cost_guard.test_dashboard_types_not_direct_groq`
- `test_cost_guard.test_ide_uses_router`

## 32. Side effects

Unit tests use the in-memory ledger. Postgres probe is `SELECT 1` / `CREATE IF NOT EXISTS` on new `doom_v8_*` tables only. No V5 memory writes, no real OS/browser/FS actions, no HTTP.

## 33. Voice / STT

Untouched (`core/listen.py`, `core/stt/`, `core/cinematic_voice.py`, `core/commands.py`).

## 34. V8.8+

Not implemented (no memory/experience context, explainability UI, or V8.10 audit).

## 35. No learning

Confirmed. Ledger does not ingest MemoryManager or embeddings.

## 36. No worker / queue

Confirmed. No Celery/Redis/Kafka/background scanner of READY rows.

## 37. No automatic replay

Confirmed (see §15–16). Uncertain crash state = **do not replay**.

## 38. Git

No commit, push, tag, or history rewrite.

---

### Architecture after V8.7

```
V8.6 TaskEngine (in-process)
  → ledger.create / persist_transition   (state only)
  → V8.4 execute_plan                    (exclusive execution)
  → V8.5 recover_execution               (exclusive recovery)
  → ledger.persist_transition

Restart → rehydrate + ABORT in-flight → inspection only
```

Persistence is never permission. A stored `RUNNING` row does not mean run it again.
