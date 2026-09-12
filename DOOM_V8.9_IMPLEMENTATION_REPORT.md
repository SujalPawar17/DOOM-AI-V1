# DOOM V8.9 — Explainability & Audit

Structured, deterministic, privacy-safe explanations of V8 lifecycle decisions. Audit is **descriptive only**. It does not authorize, execute, recover, or mutate policy.

**Verdict: V8.9 READY FOR REVIEW**

---

## 1. Summary

V8.9 adds an in-process ring buffer of immutable `AuditEvent`s. Callers explicitly `record_event(...)` after authoritative layers have already decided. Disabled by default (`PROACTIVE_V8_AUDIT_ENABLED=false`): recording is a no-op and does not change V8 execution.

## 2. Architecture

```
Authoritative V8.1–V8.8 / V7.6 results
        |
        v
record_event()  ->  bounded buffer (1024)
        |
        v
get_event / list_events  (owner-scoped, max 100)
```

No second task ledger, no cloud backend, no LLM.

## 3. Audit event model

Frozen `AuditEvent`: `event_id`, `sequence`, `timestamp_unix_ms`, `owner_id`, `session_id`, `goal_id`, `task_id`, `plan_hash`, `recovery_plan_hash`, `step_id`, `phase`, `event_code`, `outcome`, `reason_code`, `explanation`, `metadata` (tuple of string pairs).

`as_public()` always sets `authorizes_execution=false`, `approved=false`.

## 4. Event phases

`GOAL`, `CONTEXT`, `PLANNING`, `VALIDATION`, `APPROVAL`, `TASK`, `EXECUTION`, `VERIFICATION`, `RECOVERY`, `RESULT`.

## 5. Event codes

Stable codes matching actual V8 stages (goal, context, plan, approval, task, step, verification, recovery, terminal task states, emergency stop). Codes never invoke those stages.

## 6. Reason codes

Aligned with existing statuses: `V8_DISABLED`, `PLAN_HASH_MISMATCH`, `APPROVAL_REQUIRED`, `NOT_VERIFIED`, `STALE_IN_FLIGHT`, `EMERGENCY_STOPPED`, `CANCELLED`, ledger/context/capability codes, etc.

## 7. Explanation rules

Deterministic templates from reason codes. `NOT_VERIFIED` is never explained as SUCCESS. Cancellation states that effects were **not** rolled back. Stale in-flight states that execution could **not** be safely resumed (not replayed).

## 8. Redaction

Secret-like `password=` / `api_key=` / `Bearer` / `Authorization:` / cookie / private-key blocks → `[REDACTED]`. Ordinary phrases such as “authorization required” remain.

## 9. Privacy

No raw memory/experience dumps, files, screenshots, audio, or browser pages. Metadata is bounded primitives only.

## 10. Retention

`MAX_AUDIT_EVENTS = 1024` deque. Oldest evicted. Evicted IDs → `EVENT_NOT_FOUND` (not reconstructed).

## 11. Query API

`get_event(event_id, owner_id)`, `list_events(owner_id, goal_id="", task_id="", limit<=100)`. Structured filters only. Order: timestamp, sequence, event_id.

## 12–13. Isolation

Owner_A cannot read Owner_B. Task filter cannot leak other tasks.

## 14–15. Correlation

`plan_hash` and `recovery_plan_hash` stored separately. Identifiers are not permission.

## 16–23. Write/call boundaries

Audit package does **not** import or call: V5 memory writes, V7.7 experience writes, V8.7 ledger, V8.5 recovery, V8.4 `execute_plan`, `build_goal_plan`, `proactive.computer.*`, V6 ActionEngine, legacy tools.

## 24. Cost Guard

HARD $0. No HTTP, LLM, or paid telemetry.

## 25–27. Tests

Immutability, redaction, injection-as-text, owner/task isolation, retention/query/metadata/explanation bounds, determinism of stable fields, outcome consistency, recovery hashes, V8.6 state names, stale/cancel wording, execution/ledger/planner spies, catalog unchanged, audit validation failure, AST.

## 28. Test counts

| Suite | Tests | Passed | Failed | Errors |
|--------|-------|--------|--------|--------|
| V8.9 | 15 | 15 | 0 | 0 |
| V8.1–V8.9 | 177 | 177 | 0 | 0 |
| V7 + Cost Guard + V8.1–V8.9 | 334 | 331 | 0 | 3 baseline |

## 29. Baseline errors

Unchanged FastAPI `on_startup`:

- `test_v71_computer_observe.test_api_auth_csrf_and_second_session`
- `test_cost_guard.test_dashboard_types_not_direct_groq`
- `test_cost_guard.test_ide_uses_router`

## 30–37. Confirmations

No real side effects. No network. No LLM. No learning. No policy mutation. Voice/STT untouched. V8.10 untouched. No git commit/push/tag.
