# DOOM Trusted Session Design

Written before implementation. Not V8.11. Does not invent identity.

## Phase 0 findings

### 1. Dashboard/API authentication

**Partial, ASK-plane only.**

`dashboard/ask_session.py` implements:

- Unlock via `DOOM_ASK_UNLOCK` compared with HMAC (`compare_unlock`)
- HttpOnly cookie `doom_ask_sid` (raw token; server stores SHA-256 hash)
- CSRF header `X-DOOM-CSRF`
- Origin allowlist
- TTL `ASK_SESSION_TTL_SECONDS` (12h)
- Revocation (`revoked_at`)
- Cookie path **`/api/proactive/` only**

This is a real session after a shared unlock secret. It is **not** general dashboard login.

`POST /api/command` has **no** session, CSRF, or cookie. CORS is `allow_origins=["*"]`.

CSRF on the ASK plane does **not** authenticate `/api/command`.

### 2. Trusted authenticated user identity

After successful unlock, `compare_unlock` returns `OWNER_ID` (`DOOM_OWNER_ID`, default `sujal`) as the session owner.

That is **not** “config as authentication.” The unlock secret is the authentication event; `OWNER_ID` is the account the secret maps to (single-user local app).

Config `OWNER_ID` **alone** is not authentication.

### 3–4. Server-side session identifier

Yes, on the ASK plane: `ask_sessions.session_id_hash` (hash of cookie). Raw cookie is the secret. Hash is the durable session id.

Expired and revoked rows are rejected by `load_session`.

### 5. Computer-control session

Yes, separate: `proactive.computer.session.start_session` inserts a V7 computer session UUID, owner-bound, TTL. Not the same as ASK `session_id_hash`.

### 6. Can this provide ExecutionIdentity?

| Caller | Can bind identity? |
|--------|--------------------|
| ASK-authenticated HTTP under `/api/proactive/` | **Yes**: owner from stored session row; `session_id` = `session_id_hash`; computer session only if a real V7 row exists for that owner |
| `POST /api/command` | **No** (no cookie on that path) |
| `doom.py` / `core/commands.py` | **No** without frozen Voice/STT changes and a local session that does not exist |

### 7. Smallest secure mechanism

**Reuse ASK sessions.** Do not add a second auth system, OAuth, or a process UUID.

Add:

- `orchestration/identity.py` — map a **validated ASK session row** → `ExecutionIdentity`
- `POST /api/proactive/v8/command` — CSRF + ASK cookie; ignore body/header owner/session

Do **not** auto-create computer sessions. Empty `computer_session_id` if none is verified.

## Identity model

Reuse `ExecutionIdentity`. Do not duplicate.

```
AUTHENTICATED ASK SESSION (cookie hash lookup)
        ↓
owner_id from stored row (not request body)
session_id = session_id_hash (not raw cookie)
computer_session_id = verified V7 session for that owner, or ""
        ↓
process_request(..., identity=...)
        ↓
V8 execute_plan (authorization still authorized_plan_hash)
```

## Non-goals

- Do not treat localhost as identity
- Do not trust `X-Owner-ID`, query, or body `owner_id`
- Do not modify Voice/STT
- Do not change `/api/command` to invent identity
- Do not expand V8.3 capabilities
- Identity ≠ authorization
