# DOOM V8 Authorization Path Report

## Objective

Add an explicit, trusted ASK-UI authorization path for **MEDIUM-risk computer plans** (CLICK / TYPE only). Authorization is plan-hash-bound, identity-bound, computer-session-bound, short-lived, single-use, and fail-closed. Identity is not authorization.

This is not V8.11. V8 execution still goes only through `execute_plan()`.

## Existing architecture inspected

Reused, not replaced:

- ASK cookie session (`dashboard/ask_session.py`): `doom_ask_sid` hash, CSRF `X-DOOM-CSRF`, origin checks, `/api/proactive/v8` prefix
- `orchestration/identity.py` → `ExecutionIdentity` from ASK row
- V8 planner / `GoalPlan` / `hash_goal_plan()`
- V8 executor `authorized_plan_hash == plan.plan_hash`
- V7 computer session `get_session(session_id, owner_id)`
- Observation bridge `precondition_observation_hash` (TOCTOU, not authorization)
- V6 world ASK approvals (`/api/proactive/approvals/...`) — **not** used for GoalPlans

`POST /api/proactive/v8/command` previously accepted client `authorized_plan_hash`. That hole is closed.

## Authorization design

Process-local pending records (`orchestration/authorization.py`):

- `pending_id` (opaque)
- server `GoalPlan`
- `owner_id` + ASK `session_id_hash` + `computer_session_id`
- `plan_hash`
- `created_at` / `expires_at` (TTL **120s**)
- `PENDING` | `CONSUMED` | `REVOKED` | `EXPIRED`

ASK displays `safe_plan_display()` only (action, control type, name, bounded TYPE text). Password-like fields redact TYPE text. No cookies, CSRF secrets, or raw ASK tokens.

Stash normalizes `approved=False` and `execution_permitted=False`.

## Identity binding

WHO comes only from the validated ASK cookie session (`identity_from_ask_session_row`).

Authorize does **not** treat body `owner_id`, `session_id`, or `computer_session_id` as authority. A supplied `computer_session_id` must match the **stored** plan session or the claim fails `AUTHORIZATION_INVALID`.

ASK session ID is never copied onto `computer_session_id`.

## Plan-hash binding

Server stashes the canonical `GoalPlan`. Claim recomputes `hash_goal_plan(plan)` and requires equality with `plan.plan_hash`. Client-submitted GoalPlan JSON is not an authority. A changed TYPE payload, CLICK target, observation hash, or risk produces a different hash and needs a new explicit approval.

## Computer-session binding

Pending records store the plan’s V7 `computer_session_id`. Claim re-verifies `verified_computer_session_id(owner, session)` (live, owner-matched, not STOPPED/EXPIRED/REVOKED). Live id must equal the stored id. No V7 session is started during approval. Session A cannot authorize session B.

## Expiration

TTL is 120 seconds (ASK sessions themselves remain 12h). Expired pending → `AUTHORIZATION_EXPIRED`. No refresh. No automatic re-approval.

## Single-use / replay protection

`claim_authorization` consumes atomically under `threading.Lock`. A second claim returns `AUTHORIZATION_CONSUMED`. Concurrent claims: one winner. Consume happens **before** `execute_plan()` (fail-closed: a later TOCTOU/step failure does not restore permission). Revoke → `AUTHORIZATION_REVOKED`.

## ASK UI behavior

Existing dashboard (no second login). When V8 is on and an ASK session exists, the command bar uses `POST /api/proactive/v8/command`. MEDIUM CLICK/TYPE returns `APPROVAL_REQUIRED` plus safe display. Buttons:

- **Approve this action** → `POST /api/proactive/v8/authorize` `{plan_id, decision: approve}`
- **Cancel** → same endpoint `{decision: cancel}`

Natural-language “yes” is not authorization. Unauthenticated `/api/command` cannot claim.

## Endpoint behavior

| Route | Role |
|---|---|
| `POST /api/proactive/v8/command` | Propose. CSRF + origin + ASK session. Stash MEDIUM computer CLICK/TYPE. LOW executes without hash. HIGH / browser / FS mutation → `RISK_NOT_APPROVABLE`. Does not read body `authorized_plan_hash`. |
| `GET /api/proactive/v8/plans/{plan_id}` | Safe display for the stashed plan |
| `POST /api/proactive/v8/authorize` | Explicit approve/cancel. CSRF + origin + ASK session. Claim then `execute_plan(plan, identity, authorized_plan_hash=plan.plan_hash)` |

V8 ASK errors remap `csrf` → `CSRF_FAILURE`, `origin` → `ORIGIN_FAILURE`, `unauthenticated` → `IDENTITY_REQUIRED`.

## Executor integration

Authorize never calls V7 action kernels. Path:

ASK approve → `claim_authorization` → `execute_plan(..., authorized_plan_hash=plan.plan_hash)` → existing V7 boundary → observation TOCTOU → action → V7.6 verification.

## Failure modes

`IDENTITY_REQUIRED`, `SESSION_UNAVAILABLE`, `APPROVAL_REQUIRED`, `AUTHORIZATION_INVALID`, `AUTHORIZATION_EXPIRED`, `AUTHORIZATION_REVOKED`, `AUTHORIZATION_CONSUMED`, `PLAN_HASH_MISMATCH`, `PLAN_NOT_FOUND`, `RISK_NOT_APPROVABLE`, `COMPUTER_SESSION_REQUIRED`, `CSRF_FAILURE`, `ORIGIN_FAILURE`.

API bodies do not return cookies or ASK secrets.

## V8 OFF behavior

`PROACTIVE_V8_ENABLED=false`: dashboard stays on `/api/command` (session GET exposes `v8_enabled`). `v8/command` falls through to `process_request` without stash/authorize. No planner/authorization requirement added to CognitiveEngine.

## V8 ON legacy protection

Authorization / identity / planner failures stay on the V8 seam (`[V8] ...`). No CognitiveEngine, ALL_TOOLS, or pyautogui fallback.

## Cost Guard

HARD $0. Local store, existing ASK session, existing V8/V7 structures. No LLM, cloud auth, telemetry, or new HTTP dependency.

## Tests

`test_v8_authorization_path.py`: **53** tests (valid approval, identity/hash/replay/risk/session/TOCTOU/payload/static UI-API, CSRF/origin unit checks).

Regression (this run): **484** tests, **481** pass, **3** errors (known FastAPI baseline).

## Static audit

`orchestration/authorization.py`: no eval/exec/subprocess/ctypes/importlib/pyautogui/playwright/selenium; no `proactive.computer` import; no `execute_plan` / `execute_computer_action`. Computer liveness goes through `identity.verified_computer_session_id`. Authorize route calls only `execute_plan`.

## Manual validation

**NOT PERFORMED.** No harmless local test window / live V7 session / ASK click-through was run in this environment. Automated spies cover the executor accept/reject path only.

## Known baseline errors

Unchanged FastAPI/Starlette mismatch:

- `test_v71_computer_observe.test_api_auth_csrf_and_second_session`
- `test_cost_guard.test_dashboard_types_not_direct_groq`
- `test_cost_guard.test_ide_uses_router`

`Router.__init__() got an unexpected keyword argument 'on_startup'`

Because of that incompatibility, live ASGI approve-and-click was not used in new tests. CSRF/origin are covered via `ask_session` helpers plus source assertions.

## Remaining limitations

- Pending authorizations are **in-process** (lost on restart; not shared across workers).
- Consume-on-claim: failed execution after approve cannot reuse the same authorization (operator must propose again).
- Voice / `process_request` still returns `APPROVAL_REQUIRED` with no ASK UI.
- This path authorizes **computer CLICK/TYPE** only. Browser NAVIGATE and filesystem mutation remain non-approvable here.
- Dashboard binds the first live `OBSERVING` computer session from the list; it does not start one.
- Full browser click-through of ASK approve was not performed.

## Remaining security debt

- In-memory pending store is not durable or multi-process safe.
- Live HTTP authorize is not exercised under the current FastAPI baseline.
- TYPE display still shows bounded non-password text; operators must treat the panel as sensitive.
- Observation hash remains in the plan (integrity), not in the UI.

## Exact next dependency

A durable, owner-scoped pending-plan store (or single-process deployment contract) plus a harmless local ASK click-through once FastAPI `on_startup` is aligned — still without broadening risk classes.

## Final verdict

**AUTHORIZED ASK PATH READY WITH DOCUMENTED LIMITATIONS**
