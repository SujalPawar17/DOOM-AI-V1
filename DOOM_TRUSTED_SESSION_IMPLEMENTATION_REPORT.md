# DOOM Trusted Session Implementation Report

Not V8.11. Does not invent identity. Does not weaken V8 authorization. Voice/STT were not modified.

## Verdict

**IDENTITY READY WITH DOCUMENTED LIMITATIONS**

A trusted identity boundary exists for ASK-authenticated dashboard callers. Other callers still cannot supply `ExecutionIdentity` without inventing one, so they remain fail-closed under V8 ON.

---

## 1. Existing authentication/session architecture discovered

ASK-plane only (`dashboard/ask_session.py`):

- Unlock secret `DOOM_ASK_UNLOCK` compared with HMAC (`compare_unlock`)
- HttpOnly cookie `doom_ask_sid` (raw token); server stores SHA-256 `session_id_hash`
- CSRF `X-DOOM-CSRF`; origin allowlist; TTL 12h; revocation
- Cookie path `/api/proactive/` only

`POST /api/command` has no session, no CSRF, CORS `allow_origins=["*"]`. CSRF on ASK does not authenticate that route.

Localhost is not treated as identity.

## 2. Existing trusted identity source

After a successful unlock, the session row owner is `OWNER_ID` (`DOOM_OWNER_ID`). The unlock secret is the authentication event. Configuration `OWNER_ID` alone is not authentication.

No general multi-user login, OAuth, or password store exists.

## 3. Whether a new session boundary was required

**No second auth system.** ASK sessions were reused. Added:

- `orchestration/identity.py` — map a **server-loaded** ASK row → `ExecutionIdentity`
- `POST /api/proactive/v8/command` — cookie + CSRF; ignore body/header owner/session
- `ASK_PREFIXES` includes `/api/proactive/v8` for origin middleware

## 4. Exact session lifecycle

Existing ASK row states, classified by `session_lifecycle()`:

| State | Meaning |
|-------|---------|
| CREATE | `create_session` issues `secrets.token_urlsafe(32)`, stores hash, CSRF, expiry |
| ACTIVE | not revoked, `expires_at` in the future, owner + hash present |
| EXPIRED | `expires_at` ≤ now |
| REVOKED | `revoked_at` set (logout or new session for same owner) |
| MISSING | no row / empty owner or hash |

No infinite sessions. No background refresh worker.

## 5. Exact `owner_id` source

The `owner_id` column on the ASK session row loaded from the hashed cookie. Not `DOOM_OWNER_ID` at request time. Not body, query, or `X-Owner-ID`.

## 6. Exact `session_id` source

`session_id_hash` (SHA-256 of cookie). 64 hex characters. Not the raw cookie. Not a generated UUID at request time. Raw token is the secret; hash is the durable id used in `ExecutionIdentity`.

## 7. Exact `computer_session_id` source

Only if the request supplies `computer_session_id` **and** `proactive.computer.session.get_session(id, owner)` returns a live row for that owner (not STOPPED/EXPIRED/REVOKED).

Otherwise `""`. ASK `session_id` is never copied onto `computer_session_id`. Observing sessions are not auto-selected.

## 8. How request identity is bound

```
ASK cookie → load_session / require_ask_session (CSRF on POST)
    → identity_from_ask_session_row(sess, computer_session_id=optional verified)
    → ExecutionIdentity
    → DOOMCore.process_request(..., identity=..., authorized_plan_hash=...)
    → handle_v8_enabled_request → execute_plan
```

## 9. How forged request identity is rejected

`/api/proactive/v8/command` does not read `owner_id` / `session_id` from JSON, query, or `X-Owner-ID` / `X-Session-ID`.

`process_request` still ignores `context["owner_id"]`. GoalSpec, plan hash, task id, ledger, memory, and audit cannot supply identity.

A mismatched `ExecutionIdentity` vs plan owner/session still returns `SESSION_UNAVAILABLE`.

## 10. V8 OFF

Unchanged: `CognitiveEngine` path. Session layer is unused. Historical DOOM behavior preserved.

## 11. V8 ON

Valid ASK-derived `ExecutionIdentity` may enter the production seam.

Missing/invalid identity → `IDENTITY_REQUIRED` (or `SESSION_UNAVAILABLE` for expired/revoked/bad computer bind).

No fallback to CognitiveEngine, ALL_TOOLS, TaskEngine, or legacy computer tools.

## 12. Authorization remains separate

`ExecutionIdentity` ≠ `authorized_plan_hash`. Identity does not set `approved` or skip ASK. MEDIUM/HIGH still require the correct hash. Conversation may succeed without a hash; computer CLICK still `APPROVAL_REQUIRED`.

## 13. Computer-session limitations

**COMPUTER_SESSION_INTEGRATION_BLOCKED** unless the caller already has a real V7 session id and passes it on the V8 command body, where it is re-verified.

Empty `computer_session_id` is normal. Computer capabilities then fail closed (`CAPABILITY_UNAVAILABLE` / `PLANNING_UNAVAILABLE` / `SESSION_UNAVAILABLE`). No fabricated id.

## 14. Dashboard limitations

- Trusted V8 path: `POST /api/proactive/v8/command` only
- `POST /api/command` remains unauthenticated → V8 ON → `IDENTITY_REQUIRED`
- Unlock is a shared secret, not per-user passwords
- Cookie does not apply outside `/api/proactive/`

## 15. Voice limitations

`doom.py` / `core/commands.py` still call `process_request` without identity. Voice input is not authentication. Frozen Voice/STT files were not changed. Voice under V8 ON remains `IDENTITY_REQUIRED`.

## 16. V5 interaction

Unchanged V8.8 owner/session bounding once identity exists. No automatic V5 writes. Storage architecture unchanged.

## 17. V6 interaction

ASK, approval, action hash, risk, idempotency unchanged. Identity does not bypass V6.

## 18. V7 interaction

Lookup-only `get_session` from `orchestration/identity.py`. No direct computer execution. V8 still goes `execute_plan` → V7 public kernel when a plan is authorized.

## 19. Legacy fallback protection

V8 ON + missing identity + computer text: no CognitiveEngine / ALL_TOOLS / computer tools.

V8 ON + valid identity + unsupported capability: planner/capability fail-closed, not legacy execution.

## 20. Test-hook protection

`use_test_execution_hooks` is not in `orchestration.__all__`, identity.py, or `production.py`. Public `execute_plan` still has no `adapters=`.

## 21. Cost Guard status

HARD $0. No LLM, HTTP IdP, cloud auth, or paid identity provider. Sessions remain in existing local Postgres ASK tables.

## 22. Voice/STT status

Untouched in this milestone:

- `core/listen.py`
- `core/cinematic_voice.py`
- `core/commands.py`
- `core/stt/`

Pre-existing working-tree diffs in those files were left as-is.

## 23. Static security audit

`identity.py`: no eval/exec/subprocess/ctypes/importlib; no `OWNER_ID` / env owner import.

Dashboard V8 handler: no body/query/header owner or session as identity.

V8.10 AST allowlist: `proactive.computer` import permitted only in `executor.py` (execution) and `identity.py` (session lookup).

Insecure patterns `request.json["owner_id"]` as `ExecutionIdentity` are not used.

## 24. Test counts

| Suite | Total | Passed | Failed | Errors |
|-------|------:|-------:|-------:|-------:|
| `test_v8_trusted_session.py` | 19 | 19 | 0 | 0 |
| `test_v8_production_integration.py` | 29 | 29 | 0 | 0 |
| V8.1–V8.10 + trusted + production | 246 | 246 | 0 | 0 |
| V7 + Cost Guard + V8 + trusted + production | 403 | 400 | 0 | 3 |

No **NEW REGRESSION**.

## 25. Known baseline errors

Unchanged FastAPI `Router.__init__() got an unexpected keyword argument 'on_startup'`:

- `test_v71_computer_observe.test_api_auth_csrf_and_second_session`
- `test_cost_guard.test_dashboard_types_not_direct_groq`
- `test_cost_guard.test_ide_uses_router`

**KNOWN BASELINE ERROR.** Same as prior V8 production integration.

## 26. Remaining security debt

- Shared ASK unlock secret (single local operator), not multi-account IAM
- `/api/command` still unauthenticated
- Voice has no trusted session attachment
- Computer session must be explicitly bound; not auto-linked to ASK session
- V8.10 documented items (shared audit ring, etc.) unchanged
- Dashboard FastAPI `on_startup` incompatibility with current Starlette (baseline)

## 27. Exact next dependency for enabling V8 in normal DOOM usage

For **dashboard ASK callers**: set `PROACTIVE_V8_ENABLED=true`, unlock ASK session, call `POST /api/proactive/v8/command` with CSRF. Conversation can run. Computer/world still need V8.3 capabilities plus a verified V7 `computer_session_id` plus `authorized_plan_hash` when required.

For **voice / `/api/command`**: a non-invented session attachment that does not require frozen Voice/STT edits (or an explicit product decision to keep those paths fail-closed).

Do not treat `DOOM_OWNER_ID` or localhost as that attachment.

---

## Files

| File | Role |
|------|------|
| `DOOM_TRUSTED_SESSION_DESIGN.md` | Phase 0 architecture |
| `orchestration/identity.py` | ASK row → `ExecutionIdentity` |
| `dashboard/server.py` | `POST /api/proactive/v8/command` |
| `dashboard/ask_session.py` | `/api/proactive/v8` origin prefix |
| `test_v8_trusted_session.py` | Session/identity tests |
| `test_v8_production_integration.py` | ASK → `process_request` tests |
| `test_v810_security_audit.py` | Allow lookup import in `identity.py` |

`ExecutionIdentity` was not duplicated.
