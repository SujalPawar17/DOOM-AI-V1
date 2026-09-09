# DOOM V6.2.2 — Independent Forensic Audit

**Mode:** AUDIT ONLY — no source/test/schema changes, no commit/tag/push, no V6.2.3  
**Date:** 2026-09-09  
**Subject:** Uncommitted V6.2.2 vault + READ calendar/GitHub vs frozen `v6.2.1`

**Final verdict:** **A. PASS — V6.2.2 ACCEPTABLE (NON-BLOCKING FINDINGS)**

This is not a release. It does **not** authorize commit. It does **not** start V6.2.3.

---

## 1. Baseline

| Check | Observed |
|--------|----------|
| Branch | `DOOM-V5.2` |
| HEAD | `753754ecce209e8712d9ff8f118aedf9fb0760c8` = annotated tag `v6.2.1` |
| Tracked mods | `config_example.txt`, `database/postgres_db.py`, `observability/schemas.py`, `proactive/config.py`, `proactive/poller.py`, `proactive/schemas.py`, `proactive/significance.py`, `proactive/store.py`, `proactive/worker.py` (+510/−2) |
| New | `proactive/vault.py`, `proactive/connectors/*`, `test_v62_connectors.py`, `DOOM_V6.2.2_IMPLEMENTATION_REPORT.md` |
| Absent | `proactive/connectors/write.py`, `email_gmail.py`, `prediction.py`, `prepare.py`, `ask.py`, `llm_draft.py` |

HEAD was **not** moved. Implementation remains working-tree only.

Worker diff vs `v6.2.1` is **two lines**: import `poll_connectors` and call it after `poll_internal_sources`. `_recover_undelivered_inform` body and `store._OWNER_PRED` are **unmodified**.

---

## 2. Audit focus (stricter than V6.2.1)

V6.2.2 is the first credential + outbound HTTP boundary. This review independently checked:

1. `SafeHttp` cannot be used as a generic POST/write client, and cannot be pointed at arbitrary hosts.
2. Tokens never land in PostgreSQL, OTP/telemetry, logs, or exception text as DOOM secrets.
3. GitHub PAT bootstrap does not print or persist the PAT into PG.

---

## 3. SafeHttp — GET-only + token POST exception

### 3.1 Enforcement (source)

`_validate_url` runs **before** any transport:

- Scheme must be `https`.
- Host denylist: `localhost`, `127.0.0.1`, `::1`, `metadata.google.internal`, dotted IPv4.
- `GET` host must be in `{www.googleapis.com, calendar.googleapis.com, api.github.com}`.
- `POST` canonical `scheme://host/path` (no query) must equal `https://oauth2.googleapis.com/token` **and** host `oauth2.googleapis.com`.
- Other methods raise `method not allowed`.
- POST also requires `Content-Type` containing `application/x-www-form-urlencoded`.

`post_token()` is not a second HTTP stack. It calls `request("POST", url, ...)`. The allowlist is the only POST gate. Production refresh (`oauth_token.py`) passes the `TOKEN_POST_URL` constant, not a caller-supplied GitHub/Calendar URL.

Redirects use `_SameHostRedirect`: **different hostname → `SafeHttpError`**. Default urllib redirect handler is replaced (subclass passed to `build_opener`).

Calendar/GitHub modules issue **GET only**. No `requests`/`httpx` in `proactive/connectors/`.

### 3.2 Independent probe (this audit)

A `Deny` transport (raises if invoked) was used so a bypass would be visible. **16/16** rejected before transport:

| Attempt | Result |
|---------|--------|
| POST `api.github.com/repos/.../issues` | BLOCK (POST host) |
| POST Google Calendar events | BLOCK (POST host) |
| PUT/PATCH/DELETE | BLOCK (method) |
| GET `http://api.github.com` | BLOCK (https required) |
| GET `evil.example`, `uploads.github.com` | BLOCK (GET host) |
| GET `127.0.0.1`, `localhost`, metadata, `169.254.169.254` | BLOCK |
| POST token URL + query | BLOCK |
| POST `.../token/extra`, `.../revoke` | BLOCK |
| POST token URL with `application/json` | BLOCK (form encoding) |

Allowed path: GET `api.github.com/notifications` and POST exact token URL **did** reach a recording transport (2 calls).

**Conclusion:** the OAuth exception is **not** a generic POST primitive. It cannot POST to GitHub, Calendar, Gmail, or other Google paths.

### 3.3 Residual (non-blocking) — redirects

After the first URL passes the allowlist, redirects re-check **hostname only**, not scheme or path. Same-host `Location: http://api.github.com/...` would not be re-run through `_validate_url`. Same-host path change on `www.googleapis.com` could follow a Google 302 to another path with the Bearer header.

This is **not** alternate-host SSRF (cross-host redirects fail closed). It is a same-allowlist confused-deputy residual. See **F-V622-01**.

---

## 4. Vault / token isolation

| Surface | Finding |
|---------|---------|
| PostgreSQL columns | `secret_ref` only. No `token` / `refresh` / `password` / `api_key` columns on connector tables (confirmed by `information_schema` test + schema read). |
| `external_facts.payload` | Built from fenced scalars. Calendar **omits `description`**. GitHub **omits `body`**. |
| OTP | Connector errors emit `reason` ∈ `{rate_limit, auth_expired, fetch_failed}` and `connector_id` (account uuid). `FORBIDDEN_ATTR_KEYS` includes `token`, `authorization`, `secret`, `headers`. |
| Logs | No `print` of secrets in vault/connectors. `SafeHttpError` on HTTP errors is `http {code}` only; response body is discarded. OAuth refresh catches `SafeHttpError` and returns `None` without logging the body. |
| Exceptions | `VaultError` messages are fixed strings (`CryptProtectData failed`, etc.). `get_secret` swallows `VaultError` → `None`. |
| DPAPI file | Whole JSON map is `CryptProtectData`’d. Unit test: plaintext PAT **absent** from file bytes after `put_secret`. Non-Windows: protect/unprotect **refuse** (no plaintext production vault). |
| Env bootstrap | `import_github_pat_from_env` copies `DOOM_GITHUB_PAT` into the vault if that `secret_ref` is missing. It does **not** log the PAT and does **not** write it to PG. The variable may remain in `.env` (plan-accepted bootstrap). See **F-V622-05**. |

`list_active_accounts` returns `secret_ref` (vault id), not ciphertext.

---

## 5. Scope

| Forbidden in V6.2.2 | Present? |
|---------------------|----------|
| Gmail / `email_gmail.py` | **No** |
| Snapshot commitments / prediction | **No** |
| SUGGEST / PREPARE / ASK / LLM / ACT | **No** |
| Connector WRITE / `write.py` | **No** |
| Second worker / `DOOMAutomation` / worker `process_request` | **No** |
| `gmail` / `EMAIL_META` in SQL CHECK | Schema placeholders only — **no client** |

Flags with env unset: `PROACTIVE_ENABLED`, `PROACTIVE_CALENDAR_ENABLED`, `PROACTIVE_GITHUB_ENABLED` all **false**. `poll_connectors` returns before `get_enabled_readers` if flags are off → **zero HTTP**.

---

## 6. V6.1 integrity

| Invariant | Result |
|-----------|--------|
| F-RA-01 recovery function | **UNCHANGED** |
| F-WRK-02 `_OWNER_PRED` | **UNCHANGED** |
| INFORM / ACK / lease | **UNCHANGED** (worker only gained `poll_connectors()`) |
| Flag-off ingest | **PASS** (`ingest_signal` still gated) |
| Connector scores | Calendar/issue/notification below INFORM floor; review request **0.55** (still below **0.65**) |

---

## 7. Findings (non-blocking)

**F-V622-01 — Redirects not re-validated for scheme/path**  
Cross-host redirects are blocked. Same-host HTTP downgrade or same-host path change is not re-checked against `_validate_url`. Not a generic POST hole. Residual for a later hardening pass (re-run `_validate_url` on each `Location`).

**F-V622-02 — Fence credential list does not include `ghp_` / `github_pat_`**  
If an **external** issue title or event summary contains a GitHub PAT-shaped string, fence may **not** drop the record. This is untrusted third-party text, not DOOM vault leakage. OTP redactor also does not list `ghp_`. Incomplete marker list, not a vault-to-PG pipe.

**F-V622-03 — `claim_batch(limit=8)` starvation (known)**  
Connector `PENDING` rows can delay F-RA-01 tests if the queue is dirty. Isolated 110/111/119 pass on a drained queue. Signal remains `PENDING`, not `PROCESSED ∧ zero delivery`. Do **not** rewrite the worker in this slice.

**F-V622-04 — SQL CHECK includes `gmail` / `EMAIL_META`**  
Forward-looking constraints only. No Gmail connector. Do not treat as V6.2.3 started.

**F-V622-05 — `DOOM_GITHUB_PAT` remains in the process environment after bootstrap**  
Matches the approved plan. Vault is the durable store; `.env` is still a stealable copy until the operator unsets it.

**F-V622-06 — HTTP 403 classified as `RATE_LIMIT`**  
GitHub 403 may be auth/scope. Health becomes `DEGRADED`/`rate_limit` rather than `auth_expired`. Operational, not a secret leak.

**F-V622-07 — HUD TestClient**  
`test_95`–`98` still httpx 0.28 vs Starlette 0.27. Pre-existing. Not V6.2.2.

---

## 8. Test quality (implementation suite; not re-authored here)

`test_v62_connectors.py`: AST GET-only, SafeHttp denials, vault round-trip without plaintext in file, fixture calendar/GitHub (no live network), PG no token columns, persist fact+signal, git_remote map, backoff skips second fetch, flags off → zero HTTP. No `assertTrue(x or True)`.

Implementation-reported counts (not re-run as a full gate in this audit): connectors 12/12, emitters 13/13, V6.1 HUD-deselected 72, transaction 30/30. This audit **did** independently execute the 16 SafeHttp denial cases and flag-default / file-absence checks.

---

## 9. Git

No commit, tag, or push performed by this audit.

---

## 10. Recommendation

V6.2.2 is **acceptable to keep as uncommitted work** and **acceptable to commit when the owner explicitly requests a V6.2.2 release**.

Do **not** start V6.2.3 from this document. Do **not** “fix” F-V622-01…07 as drive-bys before an independent PASS is recorded (this document **is** that PASS).

V6.2.7 ASK hard stop is unchanged: session + CSRF + ownership + `param_hash`, or stop.

---

## Final verdict

**A. PASS — V6.2.2 ACCEPTABLE (NON-BLOCKING FINDINGS)**
