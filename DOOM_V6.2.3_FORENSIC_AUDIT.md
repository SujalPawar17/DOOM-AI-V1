# DOOM V6.2.3 — Independent Forensic Audit

**Mode:** AUDIT ONLY — no source/test/schema/README changes except this report file  
**Auditor role:** Independent forensic auditor (not the implementation agent)  
**Date:** 2026-09-09  
**Subject:** Uncommitted V6.2.3 Gmail READ + commitment intelligence vs frozen `v6.2.2`

**Final verdict:** **B — PASS WITH NON-BLOCKING FINDINGS**

**V6.2.3 RELEASE AUTHORIZED: YES**

This document is not a release. It does **not** perform commit, annotated tag, or push. It does **not** start V6.2.4.

The implementation report (`DOOM_V6.2.3_IMPLEMENTATION_REPORT.md`) was treated as **claims to verify**, not as proof.

---

## 1. Audit date / role / baseline

| Field | Observed |
|--------|----------|
| Audit date | 2026-09-09 |
| Role | Independent forensic auditor |
| Branch | `DOOM-V5.2` (tracks `origin/DOOM-V5.2`) |
| HEAD | `5b670def55d7f6680c09829f0455c8e75ca512a7` |
| Exact tag on HEAD | annotated **`v6.2.2`** |
| Tag `v6.2.3` | **absent** |
| V6.2.3 commit | **none** |
| V6.2.3 push | **none** (branch up to date with origin at `v6.2.2`) |

HEAD was **not** moved by this audit.

---

## 2. Scope examined

Intended slice: **EMAIL READ → DERIVE → WORLD MODEL**.

In scope: Gmail READ connector, bounded metadata/snippet fetch, deterministic commitment extraction, `proactive_commitments`, WorldSnapshot fields, flags, tests, fencing, SafeHttp path allowlist.

Out of scope / must remain absent: SUGGEST, PREPARE, ASK, LLM proactive inference, ACT, Gmail/Calendar/GitHub writes, canonical memory writes, email embeddings.

---

## 3. Files examined

**Working-tree application changes vs `v6.2.2` (11 tracked files, +706/−137 including README):**

| Path | Role |
|------|------|
| `proactive/connectors/http_safe.py` | `gmail.googleapis.com` + Gmail GET path allowlist |
| `proactive/connectors/base.py` | in-memory `ephemeral` on `FencedRecord` |
| `proactive/connectors/registry.py` | flag-gated `gmail` reader |
| `proactive/config.py` | `is_email_enabled()`, `EMAIL_POLL_SEC` |
| `proactive/poller.py` | email interval; `persist_email_commitment` |
| `proactive/store.py` | **+150 lines only** (commitment CRUD, unread count, gmail health) |
| `proactive/snapshot.py` | `commitments`, `email_unread_count`, `email_health` |
| `database/postgres_db.py` | additive `proactive_commitments` |
| `config_example.txt` | commented email flags |
| `test_v62_connectors.py` | dropped freeze assert that `email_gmail.py` must not exist |
| `README.md` | documentation redesign (behavior-neutral) |

**New (untracked) V6.2.3 implementation:**

| Path | Role |
|------|------|
| `proactive/connectors/email_gmail.py` | list + `format=metadata` get |
| `proactive/commitments.py` | deterministic extract + fingerprint |
| `test_v623_email_commitments.py` | 11 tests, fixture HTTP |

**Unchanged vs `v6.2.2` (verified `git diff` empty):** `proactive/worker.py` (still the single worker; already called `poll_connectors()` in V6.2.2). No diffs under `core/`, `memory/`, `models/`, `tools/`, `observability/`, `dashboard/`.

**Deleted files:** none.

**Unrelated leftover markdown** (provider-routing / prior release notes) remains untracked and was **not** treated as V6.2.3 product code.

---

## 4. Database objects examined

Live PostgreSQL `Doom` on `localhost:5432`.

`proactive_commitments` (created additively `IF NOT EXISTS`):

- PK `commitment_id`
- UNIQUE `(account_id, fingerprint)`
- FK `account_id → connector_accounts(account_id) ON DELETE CASCADE`
- indexes `(owner_id, status, due_at)` and `(account_id, source_message_id)`
- CHECK on `commitment_type`, `status`, `privacy_class`
- columns: owner/account, source message/thread/ts, type, due, timezone, confidence, provenance JSONB, evidence_ref, fingerprint, validity window
- **No** token / password / refresh / api_key columns on this table

Migration is **CREATE TABLE / CREATE INDEX IF NOT EXISTS** only. No DROP. No rewrite of V5 `memory_records` beyond pre-existing later statements already in `ensure_schema`.

Sample persisted `EMAIL_META` payload observed: `{unread, thread_id, message_id, from_domain}` with `privacy_class=PRIVATE`. Sample commitment `provenance`: `{fact_id, message_id}` only.

---

## 5. Security results

Independent `SafeHttp` probe used a **Deny** transport (raises if invoked) plus a recording transport.

| Attempt | Result |
|---------|--------|
| GET drafts / labels / attachments / modify / batch | **BLOCK** before transport |
| POST send / POST modify | **BLOCK** |
| PUT / PATCH / DELETE | **BLOCK** |
| `http://`, evil host, localhost, metadata IP, encoded `%2Fmodify`, `../drafts` | **BLOCK** |
| Token POST with query or JSON content-type | **BLOCK** |
| GET list + GET `messages/{id}` + exact OAuth token POST | **ALLOW** (recording transport) |
| GET `.../messages/send` | **TRANSPORT REACHED** — path treated as a message id (`send` matches `[A-Za-z0-9._-]+`) |

Write verbs to Gmail remain impossible through `SafeHttp`. The OAuth POST exception is still **only** `https://oauth2.googleapis.com/token` with form encoding.

Inherited residual **F-V622-01**: same-host redirects still re-check hostname only, not scheme/path.

Gmail GET **query strings are not validated**. Production `email_gmail.py` hardcodes `format=metadata` and metadata header names. `SafeHttp` is not imported by `core/` or tools.

---

## 6. Privacy results

- Connector forces `privacy_class="PRIVATE"` and `fact_kind="EMAIL_META"`.
- `persist_fenced_record` **does not** `ingest_signal` for non-NORMAL records. Fixture test: **zero** `proactive_signals` rows for the Gmail message id.
- `upsert_commitment` SQL hard-codes `'PRIVATE'`.
- `list_open_commitments(owner_id)` filters `owner_id` + `privacy_class='PRIVATE'`. Cross-owner test: alice row not visible to `sujal`.
- Snapshot commitment dicts: ids, type, due, confidence, source, status, evidence_ref — **no subject/snippet**.
- Unread count is a **count** over PRIVATE `EMAIL_META` facts, not message text.
- Dashboard has **no** commitments/email_unread API (grep empty). Worker INFORM `safe_params` do not include snapshot commitments. Significance does not score email PRIVATE signals (hard IGNORE).

---

## 7. Credential isolation results

- Gmail uses existing `refresh_google_access_token` / vault type `oauth_google`. PostgreSQL stores `secret_ref` on accounts.
- Connector HTTP errors emit OTP `reason` ∈ `{rate_limit, auth_expired, fetch_failed}` and `connector_id` (account uuid). `FORBIDDEN_ATTR_KEYS` includes `token`, `authorization`, `secret`, `headers`, `body`.
- `SafeHttpError` on HTTP errors is `http {code}` — response body discarded in urllib error path.
- Tests redact Authorization in fixture transport logs.
- Live fact/commitment JSON inspected: no token fields.
- **G3 (OAuth readonly):** refresh request does **not** send `scope=`. Effective Google scopes are those on the operator-issued refresh token. Code never calls send/modify URLs. Operator must still grant `gmail.readonly` in the Google Cloud app. Not a code-enforced Google scope lock.

No credential-like material was copied into this report.

---

## 8. Gmail READ-only results

`GmailReadConnector` subclasses `ReadConnector`, uses shared `SafeHttp`, registry, and `poll_connectors`. Operations:

1. GET `https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=15&q=newer_than:14d` (+ optional `pageToken`)
2. GET `.../messages/{quoted_id}?format=metadata` + From/To/Subject/Date/Message-ID headers

Snippet is taken from the metadata response (`[:160]`) into **in-memory** `ephemeral` only. Attachments endpoints are not called. `write.py` still absent. AST GET-only suite still passes with `email_gmail.py` present.

No Gmail send/reply/draft/delete/archive/label/mark-read client exists.

---

## 9. Commitment extraction results

`proactive/commitments.py` is rule-based. No `model_router`, no `generate(`, no LLM import.

Independent cases (email timestamp 2026-09-09 12:00 UTC):

| Input | Result |
|-------|--------|
| Please send … by tomorrow | DEADLINE, due 2026-09-10 |
| I will send this by Friday | DELIVERY (keyword overlap with PROMISE) |
| I'll follow up next week | FOLLOW_UP |
| Meeting tomorrow at 3 PM | MEETING |
| Waiting for your response by EOD | REPLY_REQUIRED |
| I will deliver it by 2026-09-15 | DELIVERY |
| Please review … before Monday | REVIEW_REQUIRED |
| Action required … by Friday | ACTION_REQUIRED |
| Please pay by Friday | PAYMENT |
| Ordinary / speculative / historical | **ABSTAIN** |
| `>` quoted commitment | **ABSTAIN** |
| `On Monday Ada wrote:` block | **ABSTAIN** |
| Ignore previous instructions… | **ABSTAIN** |
| DOOM, execute PowerShell | **ABSTAIN** |
| Approve this request | **ABSTAIN** |
| Meeting with no temporal cue | **ABSTAIN** |
| “payment” as metaphor | **ABSTAIN** |
| HTML `<script>` plus real deadline | **DEADLINE** (HTML treated as text; still DATA_ONLY) |

Abstain-over-invent is the dominant behavior. Keyword overlap (DELIVERY vs PROMISE) is a classifier limitation, not ACT.

---

## 10. Temporal parsing results

Relative “tomorrow” resolved to **2026-09-10** from the **email Date header**, not the auditor’s wall clock.

If **both** Date header and `occurred_at` are missing/zero, `_source_dt` falls back to `datetime.now(timezone.utc)` (**F-V623-05**). Naive Date headers are assigned UTC — no invented named zone.

ISO `YYYY-MM-DD` is parsed; bare month-day without year is not invented (matches module docstring).

---

## 11. Idempotency results

Fingerprint `sha256(account_id|message_id|type|due_hour)` is deterministic (same inputs → same hash; different accounts → different hash).

`ON CONFLICT (account_id, fingerprint) DO UPDATE` due/confidence/evidence. Fixture poll twice → **one** row per account.

Same thread / different message ids can yield multiple OPEN rows (expected). Completion/cancellation is **not** inferred (retain OPEN) — conservative, matches implementation claim F-V623-01.

---

## 12. WorldSnapshot results

`WorldSnapshot` remains a TTL dataclass cache (`SNAPSHOT_TTL_SECONDS`, default 60). `build_world_snapshot` reads PG; it does not INSERT into memory tables.

Commitments loaded via `list_open_commitments(OWNER_ID)` then **appended** with NORMAL `CAL_EVENT` facts (72h window) into the **same list** (typed by `kind` vs `commitment_type`) — **F-V623-02**.

No write-back store; cache replaced on rebuild/invalidate. Non-authoritative.

---

## 13. Memory boundary results

`test_no_memory_write_on_persist`: `memory_records` count unchanged across fact+commitment persist.

Grep of `proactive/commitments.py`, `email_gmail.py`, and new poller helpers: no MemoryManager / experience / lesson / strategy / project mutation.

`persist_fenced_record` / `upsert_commitment` target `external_facts` / `proactive_commitments` only.

---

## 14. Worker results

Single loop: `process_once` → recover leases → expire stale → `poll_internal_sources` → **`poll_connectors()`** → `claim_batch`. No second email daemon.

Per-account `SafeHttpError` / generic Exception → backoff + health, then continue other accounts. Connector failure does not abort the worker process (outer `cycle_error` only if the whole cycle raises).

`poll_connectors` returns immediately if `PROACTIVE_ENABLED` is false, or if calendar+github+email flags are all false.

401 → `auth_expired` + skip second fetch while backoff set. 403/429 → `rate_limit` (inherited GitHub classification **F-V622-06**). Other HTTP → `fetch_failed`. Malformed list JSON → empty records, no crash.

Per-message GET non-401/403/429 failure returns `None` (skip that id) and can still mark sync **success** if the list call succeeded (**F-V623-07**).

---

## 15. Observability results

Email failure OTP: `{reason, connector_id}` only. Allowed OTP keys do not include subject/snippet. `url` is forbidden. Dashboard WebSocket does not serialize commitments.

No production path puts email bodies into insights (`privacy_class` PRIVATE never INFORM).

---

## 16. Production path results

```
process_once (existing v61-worker)
  → poll_connectors()
    → get_enabled_readers()['gmail']  (if flags)
    → GmailReadConnector.fetch_updates
    → SafeHttp GET (allowlisted)
    → fence_payload
    → persist_fenced_record (PRIVATE fact, no signal)
    → extract_commitment(ephemeral) → upsert_commitment
  → later: build_world_snapshot() reads commitments (derived)
```

`email_gmail.py` is not a dead module: registry imports it when email is enabled; poller invokes it. Worker does not import Gmail directly (correct).

---

## 17. README audit

Working-tree README (not released):

- Positions DOOM as a Personal AI Operating System
- Marks V6.2.3 **IN DEVELOPMENT / not released**
- ACT, prediction, SUGGEST/PREPARE/ASK, Gmail write, V7: **PLANNED**
- No decorative emojis in the file
- No `USER_NAME=Sujal`; placeholders only
- Badges: Python 3.8+, PostgreSQL, release **v6.2.2**, branch — not fake CI/coverage
- No “500 tokens/sec” claims

Documentation-only; does not alter runtime.

---

## 18. Regression results

| Suite | Result | Classification |
|-------|--------|----------------|
| `test_v623_email_commitments` | **11/11** | V6.2.3 |
| `test_v62_connectors` | **12/12** | V6.2.2 intact (freeze assert on `email_gmail.py` removed **by design**) |
| `test_v62_emitters` | **13/13** | V6.2.1 intact |
| V6.1 HUD 95–98 deselected | **72/72** including 110/111/119 | V6.1 intact |
| `test_v532_transaction_engine` | **30/30** | memory engine intact |
| Isolated 110+111 (dirty queue) | 2 FAIL then later 72-suite PASS | **Pre-existing** `claim_batch` / stuck `CLAIMED` (**F-V622-03**). At probe time: 9 `CLAIMED`, 478 `PROCESSED`. Not a V6.2.3 worker rewrite (`worker.py` diff vs `v6.2.2` is empty). |
| HUD tests 95–98 | not re-run as gate | **Pre-existing** TestClient/httpx vs Starlette (**F-V622-07**) |

No V6.2.3 regression hidden as “flaky” without queue evidence.

---

## 19. G1–G35 acceptance matrix

| Gate | Requirement | Evidence | Result |
|------|-------------|----------|--------|
| G1 | Email connector architecture | `ReadConnector` + registry + poller; no second HTTP stack | **PASS** |
| G2 | Gmail read-only | Connector GET list/get only; writes blocked at SafeHttp | **PASS** |
| G3 | OAuth read-only | No send/modify in code; scope is operator grant, not refresh-body enforced | **PASS** (informational residual) |
| G4 | SafeHttp security boundary | Host allowlist + Gmail path check + token POST exception | **PASS** |
| G5 | No arbitrary Gmail URLs | Host locked; path limited to list + `messages/{segment}`; `send` aliases as id | **PASS** with residual F-V623-04 |
| G6 | No Gmail writes | POST/PUT/PATCH/DELETE blocked in independent probe | **PASS** |
| G7 | Credential isolation | Vault + redacted errors; no token in PG facts | **PASS** |
| G8 | PostgreSQL secret_ref only | Commitments/facts columns; V6.2.2 schema test still green | **PASS** |
| G9 | PRIVATE email | Forced PRIVATE fact + commitment | **PASS** |
| G10 | Cross-owner isolation | `list_open_commitments` owner predicate; alice hidden | **PASS** |
| G11 | No full bodies persisted | metadata + ephemeral snippet; PG payload ids/domain/unread | **PASS** |
| G12 | No email in telemetry | OTP reason/connector_id; forbidden body/token keys | **PASS** |
| G13 | No email in WebSocket | No dashboard commitments serializer; PRIVATE not INFORM | **PASS** |
| G14 | Prompt injection inert | Fence drop / abstain; no tool dispatch from email | **PASS** |
| G15 | Deterministic extraction | Pure functions; no LLM | **PASS** |
| G16 | Ambiguous content abstains | Ordinary/speculative/no-time meeting abstain | **PASS** |
| G17 | Source timestamp dates | Tomorrow → 2026-09-10 from header | **PASS** with F-V623-05 if timestamps missing |
| G18 | Idempotent polling | UNIQUE fingerprint; second persist count=1 | **PASS** |
| G19 | Deterministic identity | Stable sha256 inputs | **PASS** |
| G20 | Snapshot derived/TTL | Cache + TTL; no second canonical DB | **PASS** |
| G21 | No canonical memory writes | Count invariant + no manager calls | **PASS** |
| G22 | No prediction | No `prediction.py`; significance unchanged for email | **PASS** |
| G23 | No SUGGEST/PREPARE/ASK/ACT | Absent modules; INFORM-only worker | **PASS** |
| G24 | Existing worker reused | `worker.py` unmodified vs v6.2.2 | **PASS** |
| G25 | Flags default OFF | `_bool_env(..., False)` for email | **PASS** |
| G26 | Global proactive OFF blocks network | Deny transport, zero calls | **PASS** |
| G27 | Failure/rate-limit handling | 401 backoff; 429 raise; malformed JSON empty list | **PASS** with F-V623-07 / F-V622-06 |
| G28 | V6.2.1 intact | emitters 13/13 | **PASS** |
| G29 | V6.2.2 intact | connectors 12/12 | **PASS** |
| G30 | No credentials staged | No `.env` / vault in git status | **PASS** |
| G31 | Production request path stable | `core/` diff empty | **PASS** |
| G32 | No unrelated architecture changes | Store +150 additive; ephemeral field; README docs | **PASS** |
| G33 | Actual persistence verified | Live PG rows PRIVATE, no body | **PASS** |
| G34 | No hidden background writes | Single worker poll path | **PASS** |
| G35 | No hidden tool authority | Email never reaches CognitiveBridge/tools | **PASS** |

---

## 20. Findings (severity)

**BLOCKER:** none  
**HIGH:** none affecting the V6.2.3 release boundary (no write client, no token-in-PG, no memory mutation, no ACT)

**F-V623-04 — MEDIUM — Gmail GET path treats `send` as a message id**  
`GET https://gmail.googleapis.com/gmail/v1/users/me/messages/send` reached a recording transport. Gmail **send is POST**; this is not a write primitive. Defense-in-depth: deny reserved collection names (`send`, `batchDelete`, …) rather than any `[A-Za-z0-9._-]+` segment.

**F-V623-05 — LOW — `_source_dt` falls back to `now` if email time is missing**  
Does not override a present Date/`internalDate`. Residual vs “never use wall clock when the message should be authoritative.”

**F-V623-06 — LOW — HTML/script text not stripped before classify**  
A subject containing `<script>` plus a real deadline still extracts DEADLINE. Content remains DATA_ONLY (no tools). Fence still drops credential-shaped / injection patterns.

**F-V623-07 — LOW — metadata GET 5xx skipped as success**  
Non-auth list errors back off; per-id GET 500 returns `None` and the poller may still `last_success=True`.

**F-V623-08 — LOW — `persist_email_commitment` runs even if `fact_id` is empty**  
If fact upsert fails, a commitment could still insert with empty `evidence_ref`. Privacy still PRIVATE; provenance weaker.

**F-V623-01 — INFORMATIONAL — OPEN status never auto-completed** (implementation-honest)

**F-V623-02 — INFORMATIONAL — snapshot list mixes calendar NORMAL facts with PRIVATE commitments**

**F-V623-03 / F-V622-01 — MEDIUM inherited — same-host redirect scheme/path not revalidated**

**F-V622-03 — INFORMATIONAL — `claim_batch` starvation / stuck CLAIMED** (isolated 110/111; suite 72/72)

**F-V622-06 — LOW inherited — HTTP 403 classified as `rate_limit`**

**F-V622-07 — INFORMATIONAL — HUD TestClient 95–98 pre-existing**

Quoted-reply handling is line-prefix based; inline quotes without `>` / `On … wrote:` are not stripped (documented limitation).

---

## 21. Nonblocking risks

- English keyword extractor miss/over-match (DELIVERY vs PROMISE).
- Operator must issue **gmail.readonly** (not send) in Google Cloud; refresh path does not re-assert scope.
- SafeHttp Gmail GET ignores `format=` query (production connector sets metadata).
- Registry import of `GmailReadConnector` is unconditional; a broken Gmail module would skip **all** connector polling in that `try`.
- `OWNER_ID` default `sujal` remains a V6.1 identity default (not a secret).

---

## 22. Test counts (this audit executed)

| Suite | Count |
|-------|--------|
| V6.2.3 | 11/11 |
| V6.2.2 connectors | 12/12 |
| V6.2.1 emitters | 13/13 |
| V6.1 minus HUD 95–98 | 72/72 |
| Transaction engine | 30/30 |
| Independent SafeHttp cases | 20/21 blocked; 1 residual GET `.../messages/send` |

---

## 23. Performance observations

≤15 list + ≤15 metadata GETs per account per `EMAIL_POLL_SEC` (default 900). Snippet cap 160. No embeddings. Indexed commitment lookups. Bounded `MAX_BODY` 256 KiB on SafeHttp.

---

## 24. Git status (end of audit)

HEAD **unchanged** `5b670def55d7f6680c09829f0455c8e75ca512a7` (`v6.2.2`).

Tracked dirty: README + V6.2.3 application files listed in §3.  
Untracked: V6.2.3 implementation files, this audit, leftover markdown.

**This audit added only** `DOOM_V6.2.3_FORENSIC_AUDIT.md`.

---

## 25. Current HEAD

`5b670def55d7f6680c09829f0455c8e75ca512a7`

---

## 26. Release actions

**No commit, annotated tag, or push** was performed.

---

## 27. Final verdict

**B — PASS WITH NON-BLOCKING FINDINGS**

V6.2.3 as implemented is **EMAIL READ + PRIVATE commitment derivation + derived WorldSnapshot**. It does not add SUGGEST/PREPARE/ASK/LLM/ACT, Gmail writes, or automatic canonical memory writes.

**V6.2.3 RELEASE AUTHORIZED: YES**

Authorization means the owner **may** later request commit + annotated tag + push. This audit did not perform those steps.

Do **not** treat F-V623-04…08 as drive-by must-fix before an owner-requested release unless the owner wants them closed first. Smallest fix if closing F-V623-04: deny Gmail GET paths whose final segment is a reserved collection name (`send`, `batchDelete`, `import`, …) or require Gmail message ids to match the actual Gmail id alphabet more tightly than “any token.”

V6.2.4 was **not** started.
