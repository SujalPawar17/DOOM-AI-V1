# DOOM V8 Observation / Target Resolution Bridge Report

Not V8.11. Observation is DATA only. V7 execution remains behind `execute_plan()`.

## Verdict

**OBSERVATION BRIDGE READY WITH DOCUMENTED LIMITATIONS**

A single bounded V7.1 capture can now supply `structured_targets` to the existing planner. CLICK/TYPE still require exact unique matches, identity, computer session, and `authorized_plan_hash`. The planner still does not call V7 actions.

---

## 1. Actual V7 observation API discovered

Public V7.1:

- `get_session(session_id, owner_id)` — owner-bound computer session
- `capture_observation(owner_id)` — one structured `ComputerObservation` (no screenshots)
- `evaluate_computer_observation(owner_id)` — session tick + persist (not used by this bridge)
- `observation_hash` over authoritative identity fields (hwnd/pid/exe/UIA root ids/tree digest)

`ComputerObservation` previously kept only the **root** UIA ids, not named children. `UiaWalkNode` had no `name`.

This milestone extends the **same** observe walk:

- `UiaWalkNode.name` / `focused` (read-only `CurrentName` / `CurrentHasKeyboardFocus`)
- `capture_structured_observation()` — one capture + bounded named targets
- Names are **not** in `as_authoritative()` / observation_hash

No `execute_computer_action` / browser / fs / sequence from the bridge.

## 2. Observation session binding

`get_session(identity.computer_session_id, identity.owner_id)`.

Rejected: missing row, id mismatch, `STOPPED` / `EXPIRED` / `REVOKED` / `CANCELLED`.

Allowed live statuses include `OBSERVING` (and other non-dead rows returned by the store).

## 3. Owner binding

Only `ExecutionIdentity.owner_id`. Not body, headers, query, context, or target metadata.

## 4. Computer-session binding

Empty `computer_session_id` → no capture → `SESSION_UNAVAILABLE` / `COMPUTER_SESSION_INTEGRATION_BLOCKED`. ASK `session_id` is not copied.

## 5. Structured target model

Existing planner keys only:

- `automation_id`
- `runtime_id`
- `control_type` (UIA id mapped to `Button`/`Edit`/… when known)
- `name`
- optional `selected` from keyboard focus

Dropped: password nodes, missing id/type/name, coordinates, XPath, CSS, scripts.

## 6. Target matching rules

CLICK: regex `{name} button`; case-insensitive **exact** name equality; need id+type+name.

TYPE: `into the {name} field` exact Edit name, else exactly one focused target, else exactly one Edit.

No fuzzy / DOM-order / experience selection.

## 7. Ambiguity behavior

Two+ matches → `PlannerStatus.AMBIGUOUS_TARGET`. Zero → `PLANNING_UNAVAILABLE`. Never first-match.

## 8. Observation freshness

Live capture timestamp. Rejected if age `> 5000ms`. No observation cache. `observation_hash` is copied onto CLICK/TYPE as `precondition_observation_hash` for V7 TOCTOU, **not** as authorization.

## 9. Context bounds

Max 32 targets. Name ≤ 80, type ≤ 64, ids truncated. No screenshots. No owner/session fields in planner context. `authorizes_execution=False`.

Fetched only when V8 is ON, identity is valid, intent is COMPUTER, and text looks like click/press/type. One capture per such request.

## 10. CLICK planning

Requires trusted identity, computer session, observe+click flags, unique Button-name match, V8.2 validation. Risk MEDIUM. `approved=False`. Verification `TARGET_STATE_MATCH`.

## 11. TYPE planning

Same session/observation rules. Edit/focused unique target. Payload ≤ 256 chars.

## 12. TYPE payload security

Rejects eval/exec/subprocess/shell/powershell/cmd.exe/python/bash/`cmd /c`/`rm -rf`/`<script` / backticks / `$(`.

## 13. Authorization

Unchanged: `authorized_plan_hash` must equal `plan.plan_hash`. Observation hash, `plan.approved`, memory, audit, task_id, experience cannot authorize.

## 14. Verification

CLICK/TYPE still require verification. `NOT_VERIFIED ≠ SUCCESS`.

## 15. TOCTOU

Plan stores `precondition_observation_hash`. V7.2 compares a fresh capture at execute time (`STALE_OBSERVATION_HASH`). No blind retries. No observe→click loops.

## 16. Legacy fallback protection

V8 ON: observation/session/ambiguity/authz failures do not call CognitiveEngine / ALL_TOOLS / pyautogui.

## 17. Test-hook isolation

Bridge, planner, production do not import `use_test_execution_hooks`. No public `adapters`.

## 18. Cost Guard

HARD $0. Local UIA only. No LLM/HTTP/cloud vision.

## 19. Voice/STT status

Untouched. Frozen files not modified.

## 20. Static security audit

`orchestration/observation.py`, planner, production: no eval/exec/subprocess/ctypes/importlib/pyautogui/playwright/selenium; no `execute_*` action kernels. V7 action imports remain executor-only (plus identity lookup and this observe bridge).

## 21. Manual validation

Not performed. Tests used spies/mocks. No real destructive UI.

## 22. Test counts

| Suite | Total | Passed | Failed | Errors |
|-------|------:|-------:|-------:|-------:|
| `test_v8_observation_target_bridge.py` | 13 | 13 | 0 | 0 |
| V8.1–V8.10 + identity + production + expansion + bridge | 274 | 274 | 0 | 0 |
| V7 + Cost Guard + V8 + bridge | 431 | 428 | 0 | 3 |

No **NEW REGRESSION**.

## 23. Known baseline errors

Unchanged FastAPI `on_startup`:

- `test_v71_computer_observe.test_api_auth_csrf_and_second_session`
- `test_cost_guard.test_dashboard_types_not_direct_groq`
- `test_cost_guard.test_ide_uses_router`

**KNOWN BASELINE ERROR.**

## 24. Remaining limitations

- Named targets require Windows UIA `CurrentName`; empty names are dropped.
- Password fields are omitted (no TYPE into password).
- Production CLICK still needs a live computer session **and** a unique on-screen name.
- Browser NAVIGATE still does not open a V7 browser session.
- 5s freshness is local-clock based.
- No screenshot/OCR fallback (intentional).

## 25. Remaining security debt

Prior V8.10 / ASK shared-secret / FastAPI baseline items unchanged.

## 26. Exact next dependency

An explicit **authorized** path for MEDIUM computer plans from the ASK UI (user approval → `authorized_plan_hash`), plus a real V7 computer session started by the operator. Optional: bind an existing V7.3 in-process browser session if navigation should execute, not only plan.

---

Git commit: NOT PERFORMED  
Git push: NOT PERFORMED  
Git tag: NOT PERFORMED  
History rewrite: NOT PERFORMED  
