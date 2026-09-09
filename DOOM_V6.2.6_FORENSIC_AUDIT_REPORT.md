# DOOM V6.2.6 — Independent Forensic Audit

**PREPARE + ASK (authorization only)**  
**Mode:** AUDIT ONLY — no code, schema, test, config, README, or prior-report changes  
**Date:** 2026-09-10  
**Auditor role:** Independent forensic review of the unreleased working tree  
**Implementation claim under review:** V6.2.6 IMPLEMENTED — NOT RELEASED

**Final verdict:** **PASS WITH NON-BLOCKING FINDINGS**

V6.2.6 is safe to proceed to release authorization, subject to owner approval.

This audit does **not** authorize commit, tag, push, or release.

---

## 1. Executive Summary

HEAD is still the released **v6.2.5** commit. V6.2.6 exists only as **uncommitted** working-tree files. Tags `v6.2.5` and `v6.2.4` peel to the expected commits and are unchanged. There is **no** `v6.2.6` tag.

Live code implements:

```
ACTIVE prediction (v6.2.4, unchanged)
  → suggestion (v6.2.5, unchanged)
  → deterministic PREPARE (preview)
  → optional ASK PENDING
  → APPROVED | REJECTED | EXPIRED | REVOKED | CANCELLED
  → STOP
```

ASK decision APIs (`decide_approval`) only update `world_approval_requests` / events and optionally emit a HUD/WS card. They do **not** call TaskEngine, tools, connectors, shell, or `model_router`.

Independent re-run: `test_v626_prepare_ask.py` **44/44 OK**. V6.2.5 **36/36**. Inherited V6.2.4 owner-cap failures and V6.1 TestClient errors remain as previously accepted findings.

No blocking ACT path was found.

---

## 2. Repository Baseline

| Check | Evidence |
|-------|----------|
| Branch | `DOOM-V5.2` (`git rev-parse --abbrev-ref HEAD`) |
| HEAD | `de8f236092f104246b59920e2bea08a516e5518a` |
| HEAD message | `release: DOOM v6.2.5` |
| Staged files | none (`git diff --cached` empty) |
| V6.2.6 committed? | **No.** HEAD is v6.2.5; implementation is working-tree only |
| `v6.2.6` tag | **absent** (`git tag -l v6.2.6` empty) |

---

## 3. Git / Tag Integrity

| Tag | Object | Peeled commit | Type |
|-----|--------|---------------|------|
| `v6.2.5` | `f8dc1a5fcf6fa9d1a09d8654543da45fb7148435` | `de8f236092f104246b59920e2bea08a516e5518a` | annotated tag |
| `v6.2.4` | `269139ee88a592244e0dc52ca044eba120c1cc1b` | `26b8c169f8540f94490ff271a648225d0a8ebdec` | annotated tag |

Matches the required baseline. No retag observed.

**Unexpected working-tree item (not V6.2.6 source):** `DOOM_V6.2.5_RELEASE_REPORT.md` is modified vs tagged content (post-push SHA fill-in). Does not change v6.2.5 tagged bytes. See F-V626-F10.

Unrelated historical markdown remains untracked (provider-routing audits, older release reports). Not V6.2.6 code.

---

## 4. Changed-File Audit

### Created (untracked), matches expected V6.2.6 set

- `dashboard/ask_session.py`
- `proactive/prepare.py`
- `proactive/prepare_templates.py`
- `proactive/ask.py`
- `proactive/ask_decisions.py`
- `test_v626_prepare_ask.py`
- `DOOM_V6.2.6_IMPLEMENTATION_REPORT.md` (plus architecture/plan docs from prior phases)

### Modified vs HEAD (v6.2.5)

| File | Role |
|------|------|
| `database/postgres_db.py` | Additive V6.2.6 DDL |
| `proactive/config.py` | Prepare/ASK flags and session helpers |
| `config_example.txt` | Commented flags |
| `proactive/store.py` | Session/prepare/ask CRUD |
| `proactive/attention.py` | `may_prepare` / `may_ask` |
| `proactive/delivery.py` | Prepare/ASK/authorization cards |
| `proactive/worker.py` | Isolated prepare eval + ASK expire |
| `dashboard/server.py` | Session/ASK routes + origin middleware |
| `observability/schemas.py` | OTP allowlist keys |

`git diff --stat de8f236…` also includes `DOOM_V6.2.5_RELEASE_REPORT.md` (**unexpected for this slice**; documentation only).

### Protected files vs HEAD

`git diff HEAD --` empty for:

`proactive/predict.py`, `evidence.py`, `temporal.py`, `significance.py`, `snapshot.py`, `suggest.py`, `proactive/connectors/http_safe.py`, `core/task_engine.py`, `core/model_router.py`

Connectors and `memory/` were not in the V6.2.6 diffstat.

---

## 5. Architecture Compliance

Intended pipeline is implemented in `evaluate_world_preparations` + `ensure_pending_ask` + API `decide()`. Worker never imports `ask_decisions`.

**PREPARE ≠ EXECUTE. ASK ≠ EXECUTE. APPROVED ≠ EXECUTED.** Confirmed by store updates only + delivery WS.

Existing `POST /api/tasks/{task_id}/approve` remains on `dashboard/server.py` (~786) and still calls `task_engine.approve_task_action`. ASK routes call `proactive.ask_decisions.decide` only. No ASK→TaskEngine call.

---

## 6. PREPARE Audit

**Schema:** `world_preparations` in `postgres_db.py` with UNIQUE `(owner_id, fingerprint)`, FKs to suggestions/predictions, status CHECK DRAFT/READY/ASKED/EXPIRED/CANCELLED/SUPERSEDED. Worker inserts `'READY'`.

**TYPE_MAP** (`prepare.py` 27–33):

| Suggestion | Preparation | action_type | ASK |
|------------|-------------|-------------|-----|
| CONSIDER_REVIEW_WORK | PREPARE_REVIEW_OUTLINE | NONE | no |
| CONSIDER_CONFIRM_OPEN | PREPARE_CONFIRM_PROMPT | NONE | no |
| CONSIDER_RECONCILE_TIME | PREPARE_SCHEDULE_DIFF | FUTURE_CAL_RECONCILE | yes |
| CONSIDER_UNBLOCK | PREPARE_UNBLOCK_NOTE | FUTURE_TASK_NOTE | yes |
| CONSIDER_COMPLETE_REVIEW | PREPARE_REVIEW_OPTIONS | FUTURE_GH_REVIEW | yes |

**FUTURE_EMAIL_DRAFT:** present only in SQL CHECK. No `TYPE_MAP` entry. `evaluate_world_preparations` only uses `TYPE_MAP`. App cannot persist it. Raw SQL could; that is DBA-level, not a worker path.

**safe_params:** allowlisted keys only (`prepare_templates.ALLOWED_PARAM_KEYS`). Extra keys rejected at upsert. Values are enums/ints from suggestion/prediction columns, not email bodies.

**param_hash:** `sha256(json.dumps(..., sort_keys=True, separators=(",", ":")))[:64]`.

**fingerprint:** `sha256(owner\|suggestion_fp\|type\|template\|param_hash\|v626.1)[:48]`.

**provenance:** `suggestion_id`, `prediction_id`, `suggestion_fingerprint` only.

**Templates:** fixed strings; `render_prepare` ignores params.

**Lifecycle:** terminal CANCELLED/EXPIRED/SUPERSEDED skipped on re-eval; no resurrection in application SQL (no status reverse UPDATE). `sync_preparation_lifecycle` expires/supersedes from suggestion/TTL.

**Duplicate:** UNIQUE + ON CONFLICT touch `evaluated_at` only for READY/ASKED.

---

## 7. ASK Audit

`world_approval_requests` PENDING unique per preparation. Worker `ensure_pending_ask` gated by `flags_ask_on()` and `future_act_class==MUTATION`.

Decisions: `ask_decisions.decide` → `store.decide_approval` with `SELECT … FOR UPDATE` owner-scoped. Transitions: PENDING→APPROVED/REJECTED; APPROVED→REVOKED; PENDING→EXPIRED (worker); PENDING→CANCELLED (cancel prep).

No execute/run/apply routes in `dashboard/server.py`.

---

## 8. Session / Identity Security

| Control | Evidence |
|---------|----------|
| Server sessions | `ask_sessions`; hash of `secrets.token_urlsafe(32)` stored |
| Cookie | `doom_ask_sid`, HttpOnly, SameSite=strict, Path=`/api/proactive/`, max_age 12h |
| Secure flag | `DOOM_ASK_COOKIE_SECURE` default **false** (F-V626-F07) |
| Unlock | `DOOM_ASK_UNLOCK`; missing → 503; mismatch → 401 |
| Owner | `compare_unlock` returns config `OWNER_ID` after secret match; APIs use `sess["owner_id"]` |
| Rotation | `revoke_ask_sessions_for_owner` before insert |
| Logout | CSRF required; `revoked_at`; cookie deleted |

Request `owner_id` JSON is not used for decisions.

Unlock comparison: equal-length `hmac.compare_digest`; unequal length compares one byte then 401 (F-V626-F06).

---

## 9. CSRF / CORS / Origin Security

**CSRF:** synchronizer token stored on session row; header `X-DOOM-CSRF`; compared constant-time when lengths match. Distinct from cookie. Mutating ASK/logout require `need_csrf=True`. Empty header → 403. New session gets new CSRF (old token fails length/value).

**CORS:** app still `allow_origins=["*"]` + credentials (pre-existing). ASK prefix uses `AskOriginMiddleware` (pure ASGI, not BaseHTTPMiddleware).

**Origin rules** (`origin_allowed`): listed Origin must be in `DOOM_ASK_ALLOWED_ORIGINS` (default localhost:8000). Missing Origin: GET/HEAD/OPTIONS allowed; POST requires `Sec-Fetch-Site` `same-origin` or `none`. Foreign Origin POST/OPTIONS rejected **before** route.

**Middleware classification:** **NON-BLOCKING / equivalent.** BaseHTTPMiddleware caused ASGI `EndOfStream` with httpx; ASGI wrapper still enforces the same `origin_allowed` predicate on ASK paths. Last-added middleware runs first (Starlette), so origin deny happens outside CORS reflection for those paths.

**SameSite=Strict** additionally suppresses cross-site cookie sends.

---

## 10. Approval Binding

Canonical string in `ask_decisions.canonical_binding_string`:

`owner_id|preparation_id|action_type|param_hash|risk_class|privacy_class|{int valid_until}|rule_version|csrf_binding_id`

Worker create uses `csrf_binding_id="worker"` (`WORKER_CSRF_BINDING_ID`). Server recomputes hash; client echo compared. Mismatch → 409, no transition.

| Case | Result in code |
|------|----------------|
| A same APPROVED twice | replay=true, same first APPROVED `event_id` |
| B param_hash changed on row | stored hash ≠ recompute → 409 |
| C other owner | SQL `approval_id AND owner_id` → 404 |
| D expired status EXPIRED | not PENDING → 409 |
| D′ clock past `valid_until` but still PENDING | **can still APPROVE** (F-V626-F01) |
| E wrong preparation | hash includes preparation_id → 409 |
| F other session, same owner | **allowed** (worker binding id; decision CSRF is live session) |
| G approve+reject concurrent | FOR UPDATE; one 200, one 409 (`test_32`) |

---

## 11. Replay / TOCTOU / Substitution

- Stale fingerprint/status: terminal rows not reopened.
- Parameter substitution: binding + param_hash.
- Owner substitution: session owner + SQL predicate.
- Session cookie theft: attacker needs CSRF (XSS or other). Same-origin dashboard XSS could approve (F-V626-F11, typical synchronizer).
- TOCTOU: no `NOW() < valid_until` in `decide_approval` (F-V626-F01). Expire vs approve serialize on row lock.
- Transfer across preparations: unique approval_id + preparation_id in binding.

No path turns APPROVED into tool execution.

---

## 12. Database / Transaction Audit

Constraints: UNIQUE fingerprint; partial UNIQUE PENDING ASK; FKs CASCADE from suggestion/preparation; attention columns additive IF NOT EXISTS. No DROP of prediction/suggestion tables. `proactive_insights` CHECK not altered in this diff.

`decide_approval`: lock → validate hashes → UPDATE with status predicate → event INSERT → commit. Failed UPDATE rowcount → 409. Exception → rollback.

`expire_pending_asks`: `FOR UPDATE` then PENDING→EXPIRED.

Events append-only (no UPDATE of event rows in inspected SQL).

---

## 13. Worker Audit

`process_once` (only if `is_proactive_enabled()`):

recover/expire_stale → internal emitters/poll → connectors → predictions try → suggestions try → **preparations try** → **expire_pending_asks try** → INFORM `claim_batch`.

Prepare/ASK exceptions OTP `prepare_eval` / `ask_eval`; INFORM still reached.

**ASK TTL with flag off:** `expire_pending_asks()` does **not** check `is_ask_enabled()`. If `PROACTIVE_ENABLED` is true, overdue PENDING rows become EXPIRED even when `PROACTIVE_ASK_ENABLED=false`. Side effects: DB status + OTP metadata only. No tools, no HTTP WRITE, no approve. **F-V626-F02 NON-BLOCKING.** Violates a strict reading of “disabled freezes ASK state” but matches lifecycle hygiene; does not execute.

Worker does not import `ask_decisions`.

---

## 14. Privacy Audit

SENSITIVE suggestions excluded from `list_prepare_candidates`. SENSITIVE predictions already blocked upstream. PRIVATE preparations persist; `list_hud_preparations` and `deliver_*` require NORMAL. `ASK_PRIVATE_IN_APP` default false. TTS `False` on cards; `TTS_PROACTIVE_ALLOWED` remains false. Delivery does not interpolate source bodies.

---

## 15. WorldSnapshot / Memory Boundary

`WorldSnapshot` has no `preparations` / `approvals` / `suggestions` fields (`snapshot.py` unchanged). HUD reads PostgreSQL.

Prepare/ask/ask_decisions/ask_session: no `memory_records` references. INFORM `insert_insight` remains in worker `_process_item` (V6.1 path), after ASK expire, not driven by approval.

---

## 16. Connector Boundary

Prepare/ASK modules do not import connectors or `SafeHttp`. Worker still polls READ connectors as before. `http_safe.py` unmodified vs v6.2.5 (GET + OAuth token POST only).

---

## 17. LLM Boundary

No `model_router` / `process_request` in V6.2.6 modules. Templates deterministic. No `PROACTIVE_PREPARE_LLM` in `config.py`.

---

## 18. TaskEngine / Tool Boundary

Direct imports in prepare/ask/ask_decisions/prepare_templates/ask_session: **none** of TaskEngine, tool_registry, subprocess.

**Indirect:** `deliver_ask` / `deliver_prepare` / `deliver_authorization` import `dashboard.server` for WebSocket (`dashboard_loop`, `connected_clients`). Loading `dashboard.server` imports `task_engine` at module level (pre-existing for TaskEngine UI). **No call** from ASK handlers to `approve_task_action`. Same coupling pattern as V6.2.5 `deliver_suggest`.

ASK route `_ask_decide` only: `require_ask_session` → `decide` → optional `deliver_authorization`.

---

## 19. UI / WS / Delivery Audit

Distinct types: `proactive_preparation`, `proactive_ask`, `proactive_authorization`. Not inserted into `/api/proactive/insights` (INFORM-only loop unchanged). TTS false. WS after persist; exceptions swallowed (no rollback). Copy: “PREPARED — NOT EXECUTED.” / “APPROVAL DOES NOT EXECUTE.” / “does not run any action.”

`binding_hash` is included on ASK HUD cards for the authenticated owner (needed for client echo). Not a credential.

---

## 20. AST / Import / Call-Graph Audit

Parsed with `ast.parse`. String hits for forbidden tokens in the five V6.2.6 modules: **NONE**.

Lazy imports: `prepare.py` → `ensure_pending_ask`, `deliver_prepare`; `ask.py` → `may_ask`, `deliver_ask`. `ask_decisions.py` → store + otp only.

`dashboard/server.py` ASK functions do not contain `task_engine.approve_task_action`. That call remains only on `approve_task_action` TaskEngine route.

---

## 21. Security Threat Model

| Threat | Outcome |
|--------|---------|
| 1 Replay exact APPROVED | Idempotent success; no second event |
| 2 Parameter substitution | 409 binding mismatch |
| 3 Owner substitution | 404 |
| 4 Session substitution (other owner) | cookie/hash miss → 401 |
| 4b Other session same owner | Can decide (F-V626-F08) |
| 5 CSRF | 403 without header/token |
| 6 XSS on dashboard | Could steal CSRF JSON and approve (F-V626-F11) |
| 7 Stale PENDING after TTL | Possible until worker expire (F-V626-F01) |
| 8 TOCTOU approve vs expire | Row lock; one winner |
| 9 Connector compromise | Not used by PREPARE/ASK |
| 10 Credential leakage | Unlock not logged; CSRF in DB/JSON not in OTP allowlist as token |
| 11 Confused deputy TaskEngine | ASK does not call it |
| 12 Accidental ACT | No execute routes; APPROVED unused by worker |
| 13 API bypass | Session+CSRF+origin on mutating ASK |
| 14 Worker/API race | FOR UPDATE |
| 15 Flag bypass create | Worker create requires flags; expire may still run (F-V626-F02) |

---

## 22. Gate Matrix G-A–G-AD

| Gate | Requirement | Evidence | Result | Finding |
|------|-------------|----------|--------|---------|
| G-A | v6.2.5 baseline | HEAD=`de8f236…`; suggest 36/36 | PASS | |
| G-B | suggestion semantics | `suggest.py` diff empty | PASS | |
| G-C | predict unchanged | `predict.py` diff empty | PASS | |
| G-D | state separation | four WS types; tables distinct | PASS | |
| G-E | PREPARE no execution | AST + no tool imports | PASS | |
| G-F | ASK no TaskEngine call | `_ask_decide` → `decide` only | PASS | F-V626-F09 import coupling |
| G-G | binding_hash | recompute + tests 30/31 | PASS | |
| G-H | owner SQL | `AND owner_id`; test_21 | PASS | |
| G-I | CSRF | header + tests | PASS | |
| G-J | replay | test_29 | PASS | |
| G-K | immutable param_hash | upsert does not rewrite on conflict; test_31 | PASS | |
| G-L | TTL | worker expire; **API lacks now-check** | PASS with note | F-V626-F01 |
| G-M | cancel | test_22/28 | PASS | |
| G-N | revoke | test_27 | PASS | |
| G-O | fingerprint UNIQUE | schema + test_12 | PASS | |
| G-P | concurrency | test_32 | PASS | |
| G-Q | crash leftover READY | test_33 | PASS | |
| G-R | privacy | tests 13/14 | PASS | |
| G-S | no tokens in params | allowlist + test_15 | PASS | |
| G-T | injection | test_15 | PASS | |
| G-U | no LLM | AST | PASS | |
| G-V | SafeHttp unchanged | git diff empty | PASS | |
| G-W | no TaskEngine from prepare | AST | PASS | |
| G-X | memory count | test_34 | PASS | |
| G-Y | snapshot | no new fields | PASS | |
| G-Z | OTP keys | schemas + test_44 | PASS | |
| G-AA | unauthenticated 401 | test_18 | PASS | |
| G-AB | distinct WS types | delivery.py | PASS | |
| G-AC | no ACT endpoint | grep server.py | PASS | |
| G-AD | no V628/V627 modules | no such files | PASS | |

---

## 23. Test Results (independent re-run)

`python test_v626_prepare_ask.py`  
**Ran 44 tests in 5.651s — OK**

Matches implementation claim of 44/44.

---

## 24. Regression Results (independent re-run)

| Suite | Result | Classification |
|-------|--------|----------------|
| `test_v625_suggest.py` | 36/36 OK; V625_PERF_MS=248.20 | V6.2.6 OK |
| `test_v623_email_commitments.py` | 11/11 OK | OK |
| `test_v62_connectors.py` | 12/12 OK | OK |
| `test_v62_emitters.py` | 13/13 OK | OK |
| `test_v532_transaction_engine.py` | 30/30 OK | OK |
| `test_v61_proactive_foundation.py` | 76 tests; **4 errors** TestClient(`app=`) | **F-V622-07 inherited** (72 logic tests pass) |
| `test_v624_evidence_prediction.py` | 40 tests; **5 fail + 1 error** on configured owner candidate list | **F-V625-F01 inherited**; `predict.py` unmodified |

V6.2.6 tests also insert predictions for `OWNER_ID=sujal`, which can worsen local v6.2.4 cap starvation. That is environment/data interaction with the inherited cap, not a `predict.py` change.

---

## 25. Findings

### F-V626-F01 — PENDING past `valid_until` can still be APPROVED  
- **Severity:** Medium (authorization freshness)  
- **Classification:** NON-BLOCKING  
- **Evidence:** `decide_approval` has no `valid_until > NOW()` check; only status==PENDING + binding.  
- **Reproduction:** Set `valid_until` in the past without running `expire_pending_asks`; POST approve with correct hash → 200 APPROVED.  
- **File:** `proactive/store.py` `decide_approval`  
- **Impact:** Overdue ASK can still become APPROVED until worker expiry. **Does not execute.**  
- **Origin:** V6.2.6-specific gap  

### F-V626-F02 — ASK TTL expiry runs when ASK flag is off  
- **Severity:** Low  
- **Classification:** NON-BLOCKING  
- **Evidence:** `ask.expire_pending_asks` has no flag guard; `worker.process_once` calls it whenever `PROACTIVE_ENABLED`.  
- **Impact:** PENDING→EXPIRED mutation without ASK create. No external I/O.  
- **Origin:** V6.2.6-specific  

### F-V626-F03 — Global CORS still `*`  
- **Severity:** Low  
- **Classification:** NON-BLOCKING  
- **Evidence:** `dashboard/server.py` CORSMiddleware unchanged; ASK origin middleware + SameSite=Strict.  
- **Impact:** Unrelated APIs still open CORS; ASK credentialed cross-origin blocked by origin check and cookie policy.  
- **Origin:** Pre-existing + in-slice ASK prefix tighten  

### F-V626-F04 — `FUTURE_EMAIL_DRAFT` CHECK without mapper  
- **Severity:** Info  
- **Classification:** INFORMATIONAL  
- **Evidence:** DDL CHECK vs `TYPE_MAP`  
- **Impact:** Cannot be created by worker  

### F-V626-F05 — Duplicate function definitions in `ask.py`  
- **Severity:** Info  
- **Classification:** INFORMATIONAL  
- **Evidence:** `flags_ask_on` and `ask_deadline` defined twice (lines 24–39 and 52–67). Python uses the second pair.  
- **Impact:** Dead code / maintainability only  

### F-V626-F06 — Unlock/CSRF length mismatch skips full compare_digest  
- **Severity:** Info  
- **Classification:** INFORMATIONAL  
- **File:** `dashboard/ask_session.py` `compare_unlock`, `csrf_ok`  
- **Impact:** Minor timing oracle on token length  

### F-V626-F07 — Cookie `Secure` default false  
- **Severity:** Info  
- **Classification:** INFORMATIONAL  
- **Impact:** Appropriate for localhost HTTP; operators must set `DOOM_ASK_COOKIE_SECURE` on HTTPS  

### F-V626-F08 — Binding `csrf_binding_id=worker`; any live owner session may decide  
- **Severity:** Info  
- **Classification:** INFORMATIONAL  
- **Impact:** Matches single-owner OS plan; does not reject “other session” (audit Case F)  

### F-V626-F09 — Delivery imports `dashboard.server` (loads TaskEngine module)  
- **Severity:** Info  
- **Classification:** INFORMATIONAL  
- **Impact:** Process import only; no ASK→`approve_task_action` call. Same pattern as SUGGEST.  

### F-V626-F10 — Dirty `DOOM_V6.2.5_RELEASE_REPORT.md`  
- **Severity:** Info  
- **Classification:** INFORMATIONAL  
- **Impact:** Working-tree doc vs tagged placeholders. Do not mix into a V6.2.6 source commit without owner intent.  

### F-V626-F11 — CSRF returned in JSON (`GET /session`)  
- **Severity:** Info  
- **Classification:** INFORMATIONAL  
- **Impact:** Required for synchronizer pattern; dashboard XSS could approve ASK. Cookie remains HttpOnly.  

### Inherited (not V6.2.6 defects)

- **F-V625-F01** — prediction eval cap / configured-owner starvation (`predict.py` unchanged)  
- **F-V625-F02** — accepted V6.2.5 latency band (this run 248.20 ms / 50)  
- **F-V622-07** — Starlette `TestClient(app=)` vs httpx (`test_95`–`test_98`)  

---

## 26. Deviations (reported items)

| Item | Evidence | Impact | Repro | Severity | Class |
|------|----------|--------|-------|----------|-------|
| F-V625-F01 | v624 suite fail; predict.py no diff | Eval skip under cap | Run `test_v624` on busy `sujal` | Known | Inherited NON-BLOCKING |
| F-V625-F02 | V625_PERF_MS=248.20 | Latency accepted | test_v625 | Known | Inherited |
| F-V622-07 | TypeError TestClient | HUD tests error | test_v61 95–98 | Known | Inherited |
| FUTURE_EMAIL_DRAFT CHECK | DDL vs TYPE_MAP | None in app | Inspect schema | Info | INFORMATIONAL |
| ASK TTL flag off | `expire_pending_asks` | State expire only | Enable proactive, disable ASK, expire rows | Low | NON-BLOCKING |
| ASGI origin middleware | `AskOriginMiddleware` | Equivalent origin deny | Foreign Origin → 403 (`test_20`) | — | NON-BLOCKING (safe) |
| 44 vs ~42 tests | 44 collected/passed | Extra coverage | `test_v626` | Info | INFORMATIONAL |

---

## 27. Release Recommendation

**PASS WITH NON-BLOCKING FINDINGS**

V6.2.6 is safe to proceed to release authorization, subject to owner approval.

Recommended owner notes before a later release commit:

- Keep v6.2.4 / v6.2.5 tags immutable.  
- Stage **only** V6.2.6 source files; do not blindly add unrelated markdown or the dirty V6.2.5 release report unless intended.  
- Accept F-V626-F01–F03 as known non-blocking (or remediate in a later slice).  
- Set `DOOM_ASK_UNLOCK` and origin/Secure cookie for any non-localhost deploy.  
- Do not treat APPROVED as executable.

This audit authorizes **neither** git commit **nor** `v6.2.6` tag.

---

**NO COMMIT / NO TAG / NO PUSH / NO RELEASE**

V6.2.6 FORENSIC AUDIT COMPLETE — RELEASE NOT AUTHORIZED BY THIS AUDIT.
