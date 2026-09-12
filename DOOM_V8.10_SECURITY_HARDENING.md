# DOOM V8.10 Security Hardening

Audit follow-up. Not V8.11. V8 remains disconnected from `doom.py` and default-off.

**Final verdict: HARDENED WITH DOCUMENTED DEBT — READY FOR FUTURE INTEGRATION**

Zero CRITICAL. Zero HIGH. V8-A01 and V8-A02 are remediated in source and tests.

---

## 1. Original Audit Findings

From `DOOM_V8.10_SECURITY_AUDIT.md`:

| ID | Severity | Issue |
|----|----------|--------|
| V8-A01 | MEDIUM | `execute_plan(adapters=)` replaced the production adapter table |
| V8-A02 | MEDIUM | Empty `expected_owner_id` / `expected_session_id` skipped binding |
| V8-A03 | LOW | `MAX_TASKS` check-then-insert TOCTOU |
| V8-A04 | LOW | Nested recovery with a new execution id used a new attempt key |
| V8-A05 | LOW | Incomplete secret regex |
| V8-A06 | LOW | Shared 1024-event audit ring can evict other owners |

---

## 2. V8-A01 Remediation

**Original weakness:** Any caller of public `execute_plan()` could pass `adapters=` and replace V7/V6/V5 routing with arbitrary callables.

**Attack scenario:** Future HTTP/voice wiring forwards untrusted kwargs into `execute_plan(plan, adapters={...})` with a fake computer adapter, skipping the V7 kernel.

**Changed interface:**

```text
execute_plan(plan, *, identity=None, authorized_plan_hash="")
```

- No `adapters`, `emergency_stop_fn`, or `cancelled_fn` on `execute_plan`, `recover_execution`, or `start_task`.
- Extra kwargs raise `TypeError`.
- Default capability table remains `_DEFAULTS` inside `orchestration/executor.py`.

**Test-only mechanism:** `use_test_execution_hooks` in `executor.py` is process-local, not exported from `orchestration.__init__`, and cannot be passed through the public `execute_plan` signature. Isolated tests use `test_v8_harness.py` (not imported by `doom.py`).

**Why injection is blocked:** The public function has no parameter that accepts execution callables. Satisfying V8-on, a valid `GoalPlan`, matching identity, and matching `authorized_plan_hash` still cannot install a fake V7 adapter.

**Evidence:** `test_public_execute_plan_rejects_adapter_injection`, `test_public_api_ast_no_adapter_parameter`.

---

## 3. V8-A02 Remediation

**Original weakness:** Omitting `expected_owner_id` / `expected_session_id` skipped owner/session comparison.

**Identity model:** Frozen `ExecutionIdentity` with required `owner_id` and `session_id`, plus `computer_session_id` (required when the plan contains computer/browser/filesystem/sequence/verification steps).

Identity is a trusted-caller contract. It is not taken from user text, GoalSpec context, task_id, plan_hash, memory, audit, or planner output.

**Binding:** After V8-enabled and identity validation:

- `identity.owner_id == plan.owner_id`
- `identity.session_id == plan.session_id`
- computer-class steps: `identity.computer_session_id == plan.computer_session_id` (both non-empty)

Empty, whitespace, oversized (>64), or NUL IDs → `IDENTITY_REQUIRED`. Mismatch → `SESSION_UNAVAILABLE`.

**Failure behavior:** Fail closed before adapters. `plan.approved` and `authorized_plan_hash` still do not replace identity. Identity does not replace authorization.

**Evidence:** `test_identity_required_and_binding`, `test_computer_session_binding`, `test_start_task_requires_identity`.

No `trusted=True`, `bypass_auth`, env bypass, or magic `owner="system"`.

---

## 4. Recovery Hardening

`recover_execution(..., identity=..., authorized_plan_hash=...)`.

- Missing identity → `IDENTITY_REQUIRED` (no adapter call).
- Identity bound to original plan and candidate recovery plan.
- Recovery cannot change owner/session/computer_session.
- Original `authorized_plan_hash` still cannot authorize a new recovery hash.
- Attempt key is `owner_id|goal_id|plan_hash` (not execution_id). A second recovery of the same original plan is denied even with a new `execution_id`.
- `recovery_attempt=True` remains recursive denial.

---

## 5. Task Hardening

`start_task(task_id, *, identity=..., authorized_plan_hash="", allow_recovery=False)`.

- Missing identity → `InvalidTaskPlan("IDENTITY_REQUIRED")` without leaving READY.
- Snapshot owner/session must match identity. Task id / READY / RUNNING still do not authorize.
- Execution still goes only through `execute_plan`.

---

## 6. Context Hardening

No production change to V8.8. Confirmed: `retrieve_context` does not call `execute_plan`. Memory `owner_id=attacker` cannot bind `ExecutionIdentity`. Test: `test_context_and_audit_cannot_replace_identity`.

---

## 7. Audit Hardening

No production change to authorization. Audit metadata `approved=true` / `owner_id=attacker` remain descriptive. Test: `test_context_and_audit_cannot_replace_identity`.

---

## 8. LOW Finding Review

| ID | Result | Rationale |
|----|--------|-----------|
| V8-A03 | **FIXED** | `put_created` capacity check, duplicate check, persist, and insert run under one lock. In-process bound cannot race past 256. |
| V8-A04 | **FIXED** | Recovery attempts keyed by owner+goal+original plan hash. Max 1 attempt. |
| V8-A05 | **FIXED** (limited) | Added `token=`, `refresh_token`, `id_token`, `private_key=`, `set-cookie`. Not a DLP engine; unknown secret shapes can still miss. |
| V8-A06 | **DOCUMENTED DEBT** | Shared 1024 ring is availability/retention, not authorization. No per-owner store added. |

---

## 9. Regression Tests

| Suite | Tests | Passed | Failed | Errors |
|-------|-------|--------|--------|--------|
| V8.10 (`test_v810_security_audit`) | 21 | 21 | 0 | 0 |
| V8.1–V8.10 | 198 | 198 | 0 | 0 |
| V7 + Cost Guard + V8.1–V8.10 | 355 | 352 | 0 | 3 |

Three errors are the documented FastAPI/Starlette `on_startup` baseline. Unchanged. Not modified.

---

## 10. Static Analysis

Inspected `orchestration/**/*.py`:

- Public `execute_plan` / `recover_execution` / `start_task` have no `adapters` parameter (verified via `inspect.signature`).
- `adapters` remains only on `use_test_execution_hooks` / `_TEST_HOOKS` (test isolation).
- No `subprocess`, `eval`/`exec` calls, `importlib`, `ctypes`, `ALL_TOOLS`, `TaskEngine`, `playwright`, `selenium`, `pyautogui`.
- `proactive.computer.*` imports remain lazy inside `executor.py` default adapters only.
- Context MemoryAdapter/ExperienceAdapter are read-only data adapters, not execution adapters.

---

## 11. Default-Off Verification

`PROACTIVE_V8_ENABLED` still defaults false. `execute_plan` returns `V8_DISABLED` before identity when the flag is off. Ledger/context/audit flags still do not enable execution.

---

## 12. HARD $0 Verification

No HTTP, LLM, cloud, or provider imports added under `orchestration/`. Identity is an in-process caller struct.

---

## 13. V8 Integration Status

**NOT CONNECTED TO doom.py**

`doom.py` still has no `orchestration` import.

---

## 14. Remaining Security Debt

- **V8-A06 (LOW):** shared audit ring eviction across owners (availability).
- **V8-A05 residual:** regex redaction is not complete DLP.
- **INFO:** Dual V7 `ApprovalState.NONE` gate unchanged; V8 still not wired to production.
- Future integration must construct `ExecutionIdentity` at the trusted boundary and must never call `use_test_execution_hooks`.

---

## 15. Final Verdict

**HARDENED WITH DOCUMENTED DEBT — READY FOR FUTURE INTEGRATION**

### Hardening release gate

| Gate | Status |
|------|--------|
| V8-A01 no public adapter injection | PASS |
| V8-A02 explicit caller identity | PASS |
| Owner mismatch fail closed | PASS |
| Session mismatch fail closed | PASS |
| Computer session binding | PASS |
| Recovery cannot bypass identity | PASS |
| Task/ledger/context/audit cannot authorize | PASS |
| plan_hash / plan.approved / recovery hash not permission | PASS |
| V7 remains execution boundary | PASS |
| No new capabilities / network / LLM | PASS |
| HARD $0 | PASS |
| Default-off | PASS |
| Disconnected from doom.py | PASS |
| No new V8 test failures | PASS |
| Shared audit ring | DOCUMENTED LOW |
