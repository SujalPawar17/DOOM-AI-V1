# DOOM V8 End-to-End Computer Validation Report

## 1. Objective

Prove the existing V8 computer chain can be used safely against a harmless local Windows UI:

Trusted ASK session → ExecutionIdentity → one bounded V7 observation → exact target → GoalPlan → ASK approve → plan-hash authorization → `execute_plan()` → V7 TOCTOU → V7 CLICK/TYPE → V7.6 verification.

This is validation, not a new capability class. Not V8.11.

## 2. Actual V7 APIs used

Reused as implemented:

- `proactive.computer.session.start_session` / `get_session` / `list_sessions`
- `POST /api/proactive/computer/sessions` (explicit operator start; not a command side effect)
- `capture_structured_observation` / `capture_observation`
- `execute_computer_action`
- `execute_verification`
- ASK `require_ask_session` + CSRF + origin
- `POST /api/proactive/v8/command` and `POST /api/proactive/v8/authorize`
- `execute_plan(plan, identity, authorized_plan_hash)`

No second session, observation, or authorization stack.

## 3. Test UI

`tools/doom_safe_test_window.py` — stdlib tkinter only.

- Title: `DOOM Safe Test Window`
- Status label: `READY` → `CLICKED` on button press
- Button name: `DOOM_TEST_BUTTON`
- Entry name: `DOOM_TEST_INPUT`
- `--duplicate-buttons` for `AMBIGUOUS_TARGET`

No browser, files, shell, credentials, or network.

## 4. V7 computer session lifecycle

Existing store session: insert OBSERVING, owner-bound, TTL default 3600s, stop/expire/revoke remain fail-closed. Dashboard now exposes **Start computer session** / **Stop** on the existing ASK-protected routes. Commands do not auto-create a session.

ASK session ID is never copied onto `computer_session_id`.

## 5. ASK identity

Unchanged: cookie session → `identity_from_ask_session_row`. Body owner/session are not authority. Computer session is looked up and verified separately.

## 6. Observation

Still one bounded `capture_structured_observation` per V8 computer click/type propose via `computer_planner_context`. Max 32 named targets. Observation hash is not authorization.

## 7. Target resolution

Planner now also accepts unique identifiers:

- `click DOOM_TEST_BUTTON`
- `Type hello DOOM into DOOM_TEST_INPUT`

Existing `Click the OK button` / `into the Search field` still work. Duplicate names still yield `AMBIGUOUS_TARGET`.

UIA numeric types (`50000`) and friendly names (`Button`) compare equal so observation → plan → V7 find cannot disagree on type spelling.

## 8. Plan creation

CLICK/TYPE plans still copy `precondition_observation_hash`, require a computer session, and set `verification_required` + `TARGET_STATE_MATCH`.

## 9. ASK approval

Unchanged explicit buttons. Cancel revokes without `execute_plan`. Natural-language “yes” is not authorization.

## 10. Authorization

Unchanged 120s single-use pending store bound to owner + ASK session + computer session + exact `plan_hash`.

## 11. execute_plan()

Authorize still calls only `execute_plan`. Dashboard does not call V7 kernels.

Production seam fix: after V8 `authorized_plan_hash == plan.plan_hash`, `_default_computer` now passes V7 `ApprovalState.APPROVED`. Unauthorized plans never reach that adapter (`APPROVAL_REQUIRED` first). This is mapping, not a skip flag.

## 12. V7 TOCTOU

`execute_computer_action` still captures a fresh observation and rejects `STALE_OBSERVATION_HASH`. No observe-click-retry loop.

## 13. V7 action

CLICK uses Invoke/select/toggle patterns only. TYPE uses Value pattern only. No pyautogui, coordinates, or shell.

## 14. Verification

`_default_verify` for computer steps now uses capability `computer` and `TARGET_STATE_MATCH` (not filesystem/`CLICK`). SUCCESS still requires adapter status `VERIFIED`. `NOT_VERIFIED` is not SUCCESS.

Named TARGET_STATE_MATCH also checks the unique structured target still exists.

## 15. CLICK result

**Manual validation: NOT PERFORMED.** This environment did not run a focused live UIA session plus ASK Approve on the real dashboard.

Automated: unique `DOOM_TEST_BUTTON` plans, hash-bound authorize, spy `execute_plan` → SUCCESS only with `VERIFIED`.

## 16. TYPE result

**Manual validation: NOT PERFORMED.** Same reason.

Automated: `Type hello DOOM into DOOM_TEST_INPUT` plans exact Edit + bounded text `hello DOOM`.

Tkinter Edit controls may not expose a UIA Name/Value pattern on every Windows/Tk build; that remains an operator risk for live TYPE.

## 17. Negative tests

Automated (deterministic):

- Cancel / expired / consumed: existing authorization tests
- Changed plan hash: authorization + executor
- Stale observation: executor `PRECONDITION_FAILED`
- Duplicate buttons: `AMBIGUOUS_TARGET`
- Wrong computer session: `SESSION_UNAVAILABLE`
- V8 ON failure does not call CognitiveEngine

Live UI Cancel / live UI mutation-before-click: not exercised manually.

## 18. Automated test counts

This run:

- **TOTAL:** 500
- **PASSED:** 497
- **FAILED:** 0
- **ERRORS:** 3

New file `test_v8_end_to_end_computer.py`: 16 passed. Authorization tests: 53 passed.

## 19. Baseline errors

Unchanged FastAPI `Router.on_startup`:

- `test_v71_computer_observe.test_api_auth_csrf_and_second_session`
- `test_cost_guard.test_dashboard_types_not_direct_groq`
- `test_cost_guard.test_ide_uses_router`

No new failures.

## 20. Security audit

Changed production code: no eval/exec/subprocess/os.system/pyautogui/playwright/selenium. Dashboard authorize still only `execute_plan`. `use_test_execution_hooks` not imported by dashboard, production, planner, authorization, or observation. No client-controlled identity/risk/authorization switch. No TESTING skip.

## 21. Cost Guard

HARD $0. Local UIA / tkinter fixture only. No cloud vision or LLM.

## 22. V8 OFF behavior

Unchanged: CognitiveEngine path, no observation bridge, no ASK medium-authorization requirement.

## 23. Remaining limitations

- Live ASK + UIA click/type was not performed here.
- Pending authorizations remain in-process.
- Computer TARGET_STATE_MATCH confirms window/target presence, not Edit value contents.
- Tkinter UIA naming/Value pattern quality varies.
- Operator must enable V7 computer/observe/click/type/verification flags and start a V7 session explicitly.
- FastAPI TestClient still blocked by known `on_startup` mismatch.

## 24. Manual validation status

**Manual validation: NOT PERFORMED**

CLICK live: not run  
TYPE live: not run  
ASK Approve on a real desktop: not run  

## Final verdict

**END-TO-END COMPUTER PATH READY WITH DOCUMENTED LIMITATIONS**
