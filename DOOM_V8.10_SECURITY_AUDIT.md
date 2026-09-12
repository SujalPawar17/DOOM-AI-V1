# DOOM V8.10 Security Audit

Audit-only. No V8.11. No production behavior change. V8 remains disconnected from `doom.py` and default-off.

**Final verdict: RELEASE READY WITH DOCUMENTED DEBT**

Zero CRITICAL. Zero HIGH. MEDIUM/LOW/INFO items below do not create an untrusted-data → authority bypass in the inspected V8 graph.

---

## 1. Executive Summary

V8.1–V8.9 was inspected against source, not prior reports. The trust chain holds:

- User text, planner output, context, audit, task IDs, ledger rows, and `plan.approved` **do not authorize execution**.
- The only V8 execution gateway is `execute_plan()` (`orchestration/executor.py`), gated by V8 flag, exact `GoalPlan` type, content hash, policy-derived approval (`authorized_plan_hash == plan.plan_hash`), optional owner/session checks, emergency stop, then V7/V6/V5 **public** adapters.
- Computer mutations additionally fail at V7 when `ApprovalState.NONE` is passed (defense in depth).
- Ledger rehydrate aborts in-flight tasks and does not bind a `GoalPlan` (`PLAN_NOT_BOUND`).
- `doom.py` does not import V8. Enabling context/audit/ledger flags does not enable `execute_plan`.

Documented debt is caller-contract and defense-in-depth (optional identity checks, injectable adapters, concurrency on capacity, regex redaction).

---

## 2. Scope

In scope: `orchestration/goal`, `executor.py`, `recovery`, `task` (+ ledger), `context`, `audit`, `proactive/config.py` V8 flags, `database/postgres_db.py` ledger DDL hook, public V7/V6/V5 call sites from `executor.py`.

Out of scope for *fixing*: V7 internals, Voice/STT, FastAPI/Starlette baseline, Cost Guard dashboard/IDE apps, connecting V8 to the voice loop.

---

## 3. Version Inventory

| Phase | Role | Execution? |
|-------|------|------------|
| V8.1 | GoalSpec / intent / catalog | No |
| V8.2 | GoalPlan / hash / validator | No |
| V8.3 | Bounded planner (conversation + memory_read only) | No |
| V8.4 | `execute_plan` — exclusive V8 execution gateway | Yes, after gates |
| V8.5 | Recovery → new plan + new hash + V8.4 | Via V8.4 only |
| V8.6 | In-process task lifecycle | `start_task` → V8.4 |
| V8.7 | Persistent ledger | No |
| V8.8 | Read-only context | No |
| V8.9 | In-process audit ring | No |

---

## 4. Architecture / Trust Boundaries

```
USER TEXT ──► V8.1 process_goal ──► GoalSpec (DATA)
                    │
                    ▼
              V8.3 plan_goal ──► untrusted proposal
                    │
                    ▼
              V8.2 build_goal_plan / hash ──► integrity
                    │
         V8.8 context (DATA) ──┘  (optional seam; unused by planner)
                    │
                    ▼
              V8.4 execute_plan ──► V7 kernels / V6 request_run / V5 retrieve
                    │
                    ▼
              V8.5 recover_execution ──► new GoalPlan ──► V8.4
                    │
              V8.6 start_task / poll / cancel
                    │
              V8.7 ledger (durable observation)
                    │
              V8.9 record_event (descriptive)
```

`proactive.computer.*` is imported **only** inside `orchestration/executor.py` default adapters (lazy, inside functions). Context, audit, ledger, goal, recovery planner do not import computer kernels.

---

## 5. Authority Hierarchy

| Artifact | Trust | Becomes permission? |
|----------|-------|---------------------|
| User text | Untrusted | **No** (verified) |
| Planner output | Untrusted | **No** — must pass V8.2; `approved` rejected |
| GoalSpec | Structured intent | **No** |
| plan_hash | Integrity | **No** alone; V8.4 requires matching `authorized_plan_hash` for MEDIUM+ |
| plan.approved / execution_permitted | Ignored | **No** (verified) |
| Context / memory / experience | Untrusted data | **No** (verified) |
| Audit event | Descriptive | **No** (verified) |
| task_id / READY / RUNNING | Lifecycle | **No** — rehydrate unbound; poll does not execute |
| Ledger row | Observation | **No** (verified) |
| Recovery plan | New plan | Needs **new** hash authorization when required |
| Flags / `authorized_plan_hash` / V7 approval | Control | Trusted **caller** of `execute_plan` |

---

## 6. V8.1 Findings

**VERIFIED:** `process_goal` never executes; `execution_permitted=False` on all results. Forbidden context keys and callables rejected. Injection-like tokens forced UNKNOWN. Goal hash is identity, not auth.

**INFO:** `context["owner_id"]` can override default owner if the *caller* supplies context — not parsed from utterance tokens as executable fields.

---

## 7. V8.2 Findings

**VERIFIED:** Frozen `GoalPlan`/`PlanStep`; tuples for steps/params/deps. Unknown capabilities/params fail. Cycles/self-deps/duplicates fail. `MAX_STEPS=16`, `MAX_DEPENDENCY_DEPTH=4`. Risk strongest-wins vs policy; understated risk → `INVALID_PLAN`. `approved`/`execution_permitted` always false at build. Hash mismatch fail-closed in V8.4. Tamper after hash → `PLAN_HASH_MISMATCH`.

---

## 8. V8.3 Findings

**VERIFIED:** `MAX_PLANNING_ATTEMPTS=1`. Computer/browser/filesystem/world intents → `PLANNING_UNAVAILABLE`. Only conversation RESPOND and memory RETRIEVE drafts. Planner cannot execute. `execution_permitted`/`approved` claims rejected.

---

## 9. V8.4 Findings

**VERIFIED:** `type(plan) is GoalPlan` (not isinstance). Hash recompute. `_policy_needs_approval` independent of plan flags. MEDIUM+ CLICK with `approved=True` still `APPROVAL_REQUIRED` without matching `authorized_plan_hash` (V8.10 test). E-stop before loop and each step. Mutations `retry_count` must be 0; runtime max_try=1. Conversation/memory adapters non-mutating. Default FS adapter `content=b""`.

**MEDIUM (V8-A01):** `adapters=` replaces the entire adapter table. Untrusted exposure of this kwarg would be code execution. **Not reachable from `doom.py`.** Trusted in-process / tests only.

**MEDIUM (V8-A02):** Empty `expected_owner_id` / `expected_session_id` skip binding. Future wiring must always pass caller identity.

**INFO (V8-A07):** Default V7 adapters pass `ApprovalState.NONE`, so OS mutations still need V7 APPROVED. Dual gate, not a V8 unlock.

---

## 10. V8.5 Findings

**VERIFIED:** `MAX_RECOVERY_ATTEMPTS=1`. Recoverable set is TIMEOUT / PRECONDITION_FAILED / NOT_VERIFIED / VERIFICATION_FAILED only. Mutations not retried on timeout. Same hash denied. Original hash ≠ recovery hash when a candidate exists. `recovery_attempt=True` → `RECURSIVE_RECOVERY`. Emergency stop / cancel terminal. V8.10: mutation TIMEOUT + original hash → no adapter calls.

**LOW (V8-A04):** Nested `recover_execution` on a *new* failed recovery plan without `recovery_attempt=True` uses a new attempt key. Task engine does not auto-chain.

---

## 11. V8.6 Findings

**VERIFIED:** Central `ALLOWED_TRANSITIONS`; terminals have no outbound edges. `poll_task` never calls `execute_plan`. Double start blocked. `PLAN_NOT_BOUND` after inspection load. Caps 256 / 64 / 300000 ms.

---

## 12. V8.7 Findings

**VERIFIED:** Parameterized `%s` SQL. No pickle. `try_persist_*` no-op when V8 off. `rehydrate(execute=True)` forbidden. RUNNING/RECOVERING → ABORTED. Ledger does not call `execute_plan`. Public `authorizes_execution=False`. Crash after mutation / before ledger write cannot be rolled back by DB — **by design, do not replay** (documented).

**LOW (V8-A03):** `COUNT` then `INSERT` / registry check-then-insert TOCTOU can exceed 256 under concurrency (soft bound).

**INFO (V8-A09):** WAITING survives restart without plan; `start_task` fails `PLAN_NOT_BOUND` (not resume).

---

## 13. V8.8 Findings

**VERIFIED:** Adapters expose `read` only. Missing/wrong owner dropped. Hard ceilings clamp down. No `proactive.computer` import. `authorizes_execution=False`. Prompt-injection strings remain data. No execute_plan during retrieve (V8.10 spy).

**LOW (V8-A05):** Regex redaction is incomplete for tokens that do not match listed patterns.

---

## 14. V8.9 Findings

**VERIFIED:** `authorizes_execution=false` / `approved=false` on public events. No execute_plan. Ring 1024. Query max 100. Owner isolation. Immutable events.

**LOW (V8-A06):** Global ring — one owner can evict another’s history (audit availability, not execution).

---

## 15. Legacy Bypass Audit

**VERIFIED:** No V8 import of `ALL_TOOLS`, `TaskEngine` class, `computer_tools`, `CognitiveEngine`, `subprocess`, `eval`/`exec` calls, `importlib`, `ctypes`, `pyautogui`, `playwright`, `selenium`. `eval`/`subprocess` appear as **forbidden string tokens** in normalizer/plan_registry.

`doom.py` uses `core.listen` / `submit_user_input` only — **no V8**.

V7 is reached only from `executor.py` default adapters after V8.4 gates.

---

## 16. V7 Boundary Audit

**VERIFIED:** V8 uses `execute_computer_action`, `execute_browser_action`, `execute_fs_action`, `execute_sequence`, `execute_verification`. V7 not modified. Extra V7 approval still applies on default path.

---

## 17. V6 Boundary Audit

**VERIFIED:** World path is `proactive.act_engine.request_run` looking up an existing action id — not a second writer. V8.3 does not plan WORLD_ACTION.

---

## 18. V5 Boundary Audit

**VERIFIED:** Default memory adapter is `memory_manager.retrieve` (`include_private=False`). V8.8 adapters have no store/update/delete.

---

## 19. Cost Guard / HARD $0 Audit

**VERIFIED in V8 tree:** No `openai`, `groq`, `httpx`, `requests`, Bedrock, ElevenLabs, or HTTP clients under `orchestration/`. No remote DB beyond existing Postgres used by V8.7.

**BASELINE / UNRELATED:** Cost Guard tests still hit FastAPI `on_startup` when importing dashboard/IDE. Historical provider routing debt outside V8 is not a V8 execution path.

---

## 20. Default-Off Flag Audit

**VERIFIED:** Absent env → False for `PROACTIVE_V8_ENABLED`, `_LEDGER_ENABLED`, `_CONTEXT_ENABLED`, `_AUDIT_ENABLED`. Malformed values not in `{1,true,yes,on}` → False. Context/audit/ledger true with V8 false → `execute_plan` returns `V8_DISABLED` (V8.10 test).

---

## 21. Cross-Phase Attack Matrix

| ID | Attack | Result | Stopped where | Why |
|----|--------|--------|---------------|-----|
| A | Context → execute | **BLOCKED** | V8.8 retrieve; no execute_plan | Read-only |
| B | Audit APPROVAL_ACCEPTED → auth | **BLOCKED** | V8.9 flags false | Descriptive |
| C | Ledger RUNNING → resume | **BLOCKED** | rehydrate ABORT + PLAN_NOT_BOUND | Persistence ≠ execution |
| D | Forged task_id start | **BLOCKED** | TaskNotFound / PLAN_NOT_BOUND | No plan bound |
| E | Hash without authorized_plan_hash on CLICK | **BLOCKED** | V8.4 APPROVAL_REQUIRED | Policy re-check |
| F | Original hash authorizes recovery | **BLOCKED** | V8.5 hash inequality + mutation policy | New plan / no mutation retry |
| G | NOT_VERIFIED as SUCCESS | **BLOCKED** | V8.4 status mapping; V8.9 templates | Distinct statuses |
| H | Claim LOW risk on CLICK | **BLOCKED** | V8.2/V8.4 policy risk | Strongest-wins |
| I | expected_owner mismatch | **BLOCKED** | SESSION_UNAVAILABLE | When expected_* set |
| J | expected_session mismatch | **BLOCKED** | SESSION_UNAVAILABLE | When expected_* set |
| K | Idempotency key owner+goal+hash+step | **VERIFIED** in executor ledger | In-process; INFO if 256 eviction | Not auth |
| L | E-stop before adapters | **BLOCKED** | execute_plan pre-loop | Spy n=0 |
| M | Restart replay | **BLOCKED** | V8.7 STALE_IN_FLIGHT | No auto execute |
| N | Legacy ALL_TOOLS | **BLOCKED** | AST + no imports | — |
| O | Planner approved=true | **BLOCKED** | EXECUTION_CLAIM_REJECTED | — |
| P | Audit metadata injection | **BLOCKED** | Unused by executor | — |
| Q | context_hash as plan auth | **BLOCKED** | Not read by execute_plan | Diagnostic |
| R | READY as permission | **BLOCKED** | start still needs bound plan + V8.4 | — |
| S | recovery_attempt=True | **BLOCKED** | RECURSIVE_RECOVERY | — |
| T | Dynamic extra steps | **BLOCKED** | Frozen tuple + hash | — |

I/J: if caller omits `expected_*`, skip — **MEDIUM V8-A02**, not a user-text bypass.

---

## 22. Resource Exhaustion Audit

Constants verified in source and V8.10: steps 16, depth 4, planner attempts 1, timeout 8000 ms (planner), step timeout ≤ 30000, recovery 1, tasks 256, transitions 64, lifetime 300000, context 8/4/4/2000/12000, audit 1024/100. V7.5 caps unchanged (not modified).

---

## 23. Immutability / TOCTOU Audit

**VERIFIED:** Frozen GoalPlan/PlanStep/AuditEvent/ContextResult. `type is GoalPlan` rejects subclasses/dicts. E-stop re-checked per step (not a proven bypass). Adapter TOCTOU after check is V7’s problem; V8 re-checks stop before each adapter call.

---

## 24. Database / Persistence Audit

**VERIFIED:** Bound parameters; no f-string SQL of user ids; version `WHERE version = expected`; unique (task_id, sequence_number). Injection strings treated as ids (`TaskNotFound`). External mutation vs ledger not atomic — **do not replay**.

---

## 25. Privacy / Secret Audit

**VERIFIED:** Ledger stores ids, hashes, reason codes — not prompts/files/screenshots. Context/audit redact password/api_key/Bearer/cookie/private-key patterns. **LOW:** pattern coverage incomplete.

---

## 26. Static Analysis Results

AST walk of `orchestration/**/*.py` in V8.10: forbidden modules absent except **intentional** lazy `proactive.computer.*` in `executor.py` only. No pickle/eval/exec **calls**. No HTTP clients in V8 package.

---

## 27. Dynamic Test Results

`test_v810_security_audit.py`: **14 passed / 0 failed / 0 errors.**

Prior phase suites remain green (see §28).

---

## 28. Regression Test Results

| Suite | Tests | Passed | Failed | Errors |
|--------|-------|--------|--------|--------|
| V8.10 | 14 | 14 | 0 | 0 |
| V8.1–V8.10 | 191 | 191 | 0 | 0 |
| V7 + Cost Guard + V8.1–V8.10 | 348 | 345 | 0 | **3 baseline** |

---

## 29. Findings by Severity

| ID | Severity | Phase | File | Issue | Exploit Path | Evidence | Status |
|----|----------|-------|------|-------|--------------|----------|--------|
| V8-A01 | MEDIUM | V8.4 | executor.py | `adapters=` replaces kernel table | Only if a future API forwards untrusted adapters | Source L398; V8 not in doom.py | Open / non-blocking |
| V8-A02 | MEDIUM | V8.4 | executor.py | Empty expected owner/session skips check | Miswired caller | Source L382–387; V8.10 owner test when set | Open / non-blocking |
| V8-A03 | LOW | V8.7 | registry.py / ledger_store.py | MAX_TASKS TOCTOU | Concurrent creates | Check-then-insert | Open |
| V8-A04 | LOW | V8.5 | recovery/engine.py | Nested recover without `recovery_attempt` | Direct API abuse | Attempt key by plan_hash | Open |
| V8-A05 | LOW | V8.8/V8.9 | sanitizer/redaction | Incomplete secret regex | Odd token shapes | Pattern lists | Open |
| V8-A06 | LOW | V8.9 | audit/recorder.py | Shared 1024 ring eviction | Cross-owner history loss | Global deque | Open |
| V8-A07 | INFO | V8.4 | executor.py | V8 hash auth ≠ V7 APPROVED | Dual gate | ApprovalState.NONE | Documented |
| V8-A08 | INFO | Runtime | doom.py | V8 not connected | Intentional | No V8 imports | Documented |
| V8-A09 | INFO | V8.7 | ledger.py | WAITING not aborted | PLAN_NOT_BOUND | start_task | Documented |

No CRITICAL. No HIGH.

---

## 30. Known Baseline Debt

**Not V8 defects:**

- FastAPI `Router.__init__(on_startup)` in `test_v71_computer_observe.test_api_auth_csrf_and_second_session`, `test_cost_guard.test_dashboard_types_not_direct_groq`, `test_cost_guard.test_ide_uses_router`
- Unrelated historical Cost Guard / provider-routing / dashboard code
- Voice/STT files may be dirty in the working tree; **not modified by this audit**

---

## 31. Release Gate

| Gate | Status |
|------|--------|
| No CRITICAL / HIGH | **PASS** |
| No unauthorized execution from untrusted data | **PASS** |
| No V7 bypass except executor adapters after gates | **PASS** |
| No V6 second auth system | **PASS** |
| No V5 writes via V8.8 | **PASS** |
| Planner / context / audit / ledger / task_id / plan_hash alone ≠ permission | **PASS** |
| No approval transfer to recovery (mutation path tested) | **PASS** |
| No verification/risk downgrade | **PASS** |
| Owner/session when expected_* provided | **PASS** |
| E-stop before adapters | **PASS** |
| Recovery attempts = 1; no auto replay | **PASS** |
| Resource constants | **PASS** |
| No dynamic plan expansion | **PASS** |
| No legacy tools | **PASS** |
| HARD $0 in V8 tree | **PASS** |
| Default-off execution | **PASS** |
| Secrets not stored raw in ledger | **PASS** |
| No new V8 test regressions | **PASS** |

Wiring `execute_plan` into a public HTTP/voice API without `expected_*` and without forbidding `adapters` would **re-open V8-A01/A02 as HIGH**. That wiring does not exist today.

---

## 32. Final Verdict

**RELEASE READY WITH DOCUMENTED DEBT**

V8.1–V8.9 is a controlled orchestration layer **above** V7/V6/V5: untrusted data does not become authority; computer execution stays behind V8.4 → V7; HARD $0 and default-off hold; persistence/context/audit/task state/hashes/recovery do not auto-authorize.

Before connecting V8 to `doom.py` or any network surface, remediate or explicitly gate **V8-A01** (no caller-supplied adapters) and **V8-A02** (mandatory owner/session expected_*).

---

## 33. Git

No commit, push, tag, or history rewrite.

`git status --short` at audit time included pre-existing dirty Voice/STT/report files plus V8 packages and `test_v810_security_audit.py`. This audit added `test_v810_security_audit.py` and this report only as working-tree artifacts.
