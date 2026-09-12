# DOOM Computer Control Hardening Report

This is a **post-V8.10** hardening milestone. It is **not V8.11**. No new computer capabilities were added.

**Verdict: COMPUTER CONTROL HARDENING READY WITH DOCUMENTED LIMITATIONS**

**Manual validation: NOT PERFORMED**

Git commit: NOT PERFORMED  
Git push: NOT PERFORMED  
Git tag: NOT PERFORMED  
History rewrite: NOT PERFORMED

---

## 1. Objective

Harden the existing V8 CLICK/TYPE path so stale observations, stale plans, stale authorizations, session mismatch, duplicate/disappearing targets, TYPE payload abuse, replay, concurrency, verification failure, cancel, e-stop, partial execution, server restart, and malformed requests fail **closed** and **deterministically**.

The production path remains a single stack:

Trusted ASK session → ExecutionIdentity → V8 request → bounded V7 observation → exact target → GoalPlan → ASK explicit approval → plan_hash authorization → `execute_plan()` → V7 ApprovalState → V7 TOCTOU → V7 CLICK/TYPE → V7.6 verification.

## 2. Architecture inspected

Inspected before changing:

- `orchestration/authorization.py` — 120s in-memory pending store, consume-before-execute under `_LOCK`
- `orchestration/observation.py` — bounded structured targets, `MAX_AGE_MS`, dead session states
- `orchestration/executor.py` / `executor_errors.py` — `execute_plan`, default computer/verify adapters
- `orchestration/identity.py` — ASK → ExecutionIdentity, live computer session check
- `orchestration/production.py` — V8 prepare/handle, no CognitiveEngine fallback
- `orchestration/goal/planner.py` / plan validator hashing
- V7 session, observe, CLICK/TYPE kernel, ApprovalState, TOCTOU, V7.6 verify
- `dashboard/server.py`, ASK UI HTML/JS/CSS
- existing V8/V7/Cost Guard tests

No second computer stack, approval system, or identity system was added.

## 3. Authorization lifecycle

In-memory `_Pending` states:

| State | Meaning |
|---|---|
| PENDING | Stashed after propose; 120s TTL |
| CONSUMED | Claimed under lock; never reusable |
| EXPIRED | TTL elapsed; never auto-refreshed |
| REVOKED | Cancel/reject |
| INVALID | Owner / ASK session / computer session mismatch |

Claim requires:

- `hash_goal_plan(plan) == plan.plan_hash == rec.plan_hash`
- same owner, ASK session, computer session as stash
- live V7 computer session
- MEDIUM computer CLICK/TYPE only

A changed plan, plan_hash, owner, or `computer_session_id` cannot use the old authorization.

## 4. Consume-before-execute analysis (preserved)

The claim lock **consumes first**, then the dashboard calls `execute_plan()`. This is intentional fail-closed behavior. It was **not** reversed.

| Case | Behavior |
|---|---|
| A. Authorization consumed | Record is `CONSUMED`. Replay returns `AUTHORIZATION_CONSUMED`. |
| B. Executor fails before V7 mutation | Authorization already consumed. No silent re-authorize. |
| C. V7 action fails | Same. Operator must propose + approve again. |
| D. TOCTOU fails | `STALE_OBSERVATION_HASH` / `PRECONDITION_FAILED`. Auth consumed. No blind click. |
| E. Verification fails | `NOT_VERIFIED` or `VERIFICATION_FAILED` ≠ SUCCESS. Auth consumed. |
| F. E-stop | `EMERGENCY_STOPPED` at executor boundary before adapter. Auth already consumed if claim succeeded. |
| G. Process crash after consume | In-memory store is gone on restart anyway. Surviving process still has CONSUMED. No replay. |

Making consume-after-success would allow a crash after mutation to leave a reusable authorization. That is a replay risk. Consume-before-execute is preserved.

## 5. Plan immutability

Before claim and before `execute_plan()`, canonical `hash_goal_plan(plan)` must match `plan.plan_hash`. Tests cover mutations to action target id/type/name, TYPE payload, observation hash, timeout, retry (mutation retry remains forbidden), risk-bearing fields, and verification flags. Client-supplied hashes are not trusted. `/api/command` cannot authorize.

## 6. Computer-session binding

`ExecutionIdentity.owner_id`, `session_id`, and `computer_session_id` are taken from the trusted ASK cookie plus a live V7 session. The ASK session id is never used as `computer_session_id`.

Dead computer sessions: STOPPED, EXPIRED, REVOKED, **CANCELLED**.

Session A approve → Session B execute: `AUTHORIZATION_INVALID` / fail closed.

## 7. Observation / TOCTOU

`precondition_observation_hash` remains on CLICK/TYPE plans. V7 kernel still compares live observation hash. `_default_computer` now maps V7 `error_code=STALE_OBSERVATION_HASH` to executor status `STALE_OBSERVATION_HASH` (not SUCCESS). There is no observe → re-plan → click loop.

Expired observations (`MAX_AGE_MS`) fail at the observation bridge with `SESSION_UNAVAILABLE`.

## 8. Target resolution

CLICK: exact name + Button (`Button` / `50000`) + unique.  
TYPE: exact Edit name, else unique focused **Edit**, else exactly one Edit.

0 targets → `PLANNING_UNAVAILABLE`  
2 same-name Buttons/Edits → `AMBIGUOUS_TARGET`  
Similar names (`DOOM_TEST_BUTTON2`) → no fuzzy match.

No coordinate fallback, DOM order, or experience scoring.

## 9. TYPE security

Limit remains ≤ 256 characters. 257 quoted characters are rejected (not truncated). Forbidden patterns (eval, exec, subprocess, shell, powershell, cmd.exe, python, bash, `cmd /c`, `rm -rf`, `<script`, backticks, `$()`) remain blocked. Harmless text (`hello DOOM`, `Hello, world!`, `test@example.com`, `12345`) is accepted.

## 10. Approval UI

Operator sees the exact action:

- CLICK / Target / Type
- TYPE / Target / Type / Text: "…"

Wording: **Approve this action**. Note: this approval applies only to this exact action; it is not general computer access. Cancel is present. No “Allow computer control”.

## 11. Cancel

Propose → panel → Cancel → `revoke_pending` → `AUTHORIZATION_REVOKED`. No `execute_plan()`, no V7 mutation, no leftover executable authorization.

## 12. E-stop

Existing executor `emergency_stop_fn` / session STOPPED check is used at plan start and before each step. `_default_computer` maps V7 `EMERGENCY_STOP_ACTIVE` to `EMERGENCY_STOPPED`. No new e-stop mechanism. Tests do not bypass e-stop.

## 13. Verification

`NOT_VERIFIED ≠ SUCCESS`. `VERIFICATION_FAILED ≠ SUCCESS`. Computer steps use capability `computer` and `TARGET_STATE_MATCH`, not a filesystem verification spec. Adapter SUCCESS without VERIFIED is `NOT_VERIFIED`.

## 14. Concurrency

`claim_authorization` consumes under `_LOCK`. Eight concurrent claims: exactly one `OK`, seven `AUTHORIZATION_CONSUMED`.

## 15. Restart behavior

Authorization store is **in-memory**. `reset_authorization_store_for_tests()` models process restart: pending ids disappear (`PLAN_NOT_FOUND`). A leftover plan object without a live authorization cannot execute (`APPROVAL_REQUIRED`). This is fail-closed. No persistent authorization database was added.

## 16. V8 OFF

`PROACTIVE_V8_ENABLED=false` continues the legacy CognitiveEngine path. No V8 authorization, observation, or computer path overhead.

## 17. V8 ON

V8 failures return `[V8] …` and do not fall back to CognitiveEngine, `ALL_TOOLS`, pyautogui, or legacy ACT. Spies confirm cognition is not called.

## 18. Cost Guard

HARD $0. No new LLM, HTTP, cloud, vision, or telemetry. Computer path remains local UIA. Planner TYPE denylist is local regex only.

## 19. Automated tests

New suite: `test_v8_computer_hardening.py` — **75 tests**.

Regression (same V8 + V7 + Cost Guard files as prior computer-path baseline, plus this suite):

| | Count |
|---|---|
| TOTAL | 575 |
| PASSED | 572 |
| FAILED | 0 |
| ERRORS | 3 |

Delta vs prior 500 / 497 / 0 / 3: **+75 tests, all passing**. The three errors are unchanged baseline FastAPI `on_startup` import failures.

## 20. Manual tests

Live Windows UIA against `tools/doom_safe_test_window.py` was **not** run in this environment (no operator ASK click, no live OBSERVING session exercised end-to-end on a real desktop).

**Manual validation: NOT PERFORMED**

Negative live tests (cancel, duplicate target, UI change, expiry, replay on a real window) were therefore also not performed. Automated equivalents exist.

## 21. Static security audit (changed / path files)

| Check | Result |
|---|---|
| eval/exec/subprocess/os.system in new control path | Absent except planner **denylist** strings |
| PowerShell / cmd.exe / ctypes / importlib as execution | Absent |
| pyautogui / playwright / selenium | Absent |
| Arbitrary HTTP from this path | Absent |
| Secret logging of TYPE text for password-named fields | Display redacts |
| Public test hooks in production modules | Test helpers remain test-only (`reset_authorization_store_for_tests`, `test_v8_harness`) |
| Direct V7 from dashboard authorize/command | Absent; `execute_plan` only |
| Client identity / risk / approval authority | Cookie ASK + server stash; body owner/session ignored |
| Generic computer permission | Absent; one-plan approval |
| Automatic re-authorization | Absent |

## 22. Known baseline errors (unchanged)

1. `test_v71_computer_observe.test_api_auth_csrf_and_second_session`  
2. `test_cost_guard.test_dashboard_types_not_direct_groq`  
3. `test_cost_guard.test_ide_uses_router`  

Cause: FastAPI `Router.__init__() got an unexpected keyword argument 'on_startup'`. Unrelated tests were not altered to hide these.

## 23. Remaining limitations

- Live UIA CLICK/TYPE on the safe test window was not performed here.
- Authorization is process-local; restart drops pending approvals (acceptable fail-closed).
- Consume-before-execute means a failed TOCTOU/verify still spends the one-time approval (operator must re-propose).
- Dashboard propose still accepts `computer_session_id` in the JSON body, then **server-verifies** it with `get_session`; it is not client identity authority.
- `handle_v8_enabled_request` (in-process) still requires `authorized_plan_hash`; the ASK UI is the operator authorization surface.

## 24. Remaining security debt

- In-memory authorization is not multi-worker safe; two processes would not share consume locks.
- FastAPI `on_startup` baseline errors still block some dashboard import tests.
- No live confirmation that UIA `Button`/`Edit` names on the safe test window match planner exact-match rules under real COM timing.

## 25. Exact next dependency

Operator-supervised live validation on Windows using only `python tools/doom_safe_test_window.py`:

1. ASK session  
2. Explicit V7 computer session start  
3. `click DOOM_TEST_BUTTON` → Approve this action  
4. `Type hello DOOM into DOOM_TEST_INPUT` → Approve this action  
5. Cancel / duplicate / stale UI negatives if still safe  

Do not treat that as V8.11. Do not add browser/FS/world/sequences/OCR/LLM autonomy.

---

## Implementation notes (this milestone)

Smallest safe code changes:

- Planner: CLICK requires unique Button; TYPE 257+ rejected; focused TYPE requires Edit; `_TYPE_QUOTED` restored.
- Identity / observe / default OBSERVE: CANCELLED is a dead session.
- Claim: stash `plan_hash` compared on consume.
- Executor: live computer session before CLICK/TYPE; map stale hash and e-stop; `VERIFICATION_FAILED` already distinct from SUCCESS.
- ASK UI: exact Target/Type/Text copy; one-action-only wording.
