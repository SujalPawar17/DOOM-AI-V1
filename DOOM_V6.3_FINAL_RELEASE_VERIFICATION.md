# DOOM V6.3 — Final Pre-Release Verification

**Mode:** verification only — no commit, tag, push, or release  
**Date:** 2026-09-10  
**This document does not authorize release.**

**Final recommendation:** **READY FOR OWNER RELEASE AUTHORIZATION**

---

## 1. Baseline

| Item | SHA |
|------|-----|
| HEAD / `DOOM-V6` | `d86ebde13de11448dcb0f5e8688043e06d913169` |
| `v6.2.8` peeled commit | `d86ebde13de11448dcb0f5e8688043e06d913169` |
| `v6.2.8` tag object | `96e6f8bd061be9a873ac2b3525a4f7bff3dc37f9` |
| Message | `release: DOOM v6.2.8` |

No `v6.3` tag. No V6.3 commit. Remote `v6.2.8` still `96e6f8bd061be9a873ac2b3525a4f7bff3dc37f9`.

---

## 2. Current branch

`DOOM-V6` (tracks `origin/DOOM-V6`). Working tree dirty with uncommitted V6.3 + unrelated historical markdown.

---

## 3. Branch structure

| Ref | SHA |
|-----|-----|
| `DOOM-V5.2` / `origin/DOOM-V5.2` | `d86ebde13de11448dcb0f5e8688043e06d913169` |
| `DOOM-V6` / `origin/DOOM-V6` | `d86ebde13de11448dcb0f5e8688043e06d913169` |
| `DOOM-V7` / `origin/DOOM-V7` | `d86ebde13de11448dcb0f5e8688043e06d913169` |

`DOOM-V5.2` was not reset, renamed, or force-pushed. V6.1–V6.2.8 history remains on `DOOM-V5.2`. `DOOM-V7` has no V6.3 commit (same v6.2.8 pointer only).

---

## 4. Git identity

```
GIT_AUTHOR_IDENT:     SujalPawar17 <pawarsujalab@gmail.com>
GIT_COMMITTER_IDENT:  SujalPawar17 <pawarsujalab@gmail.com>
```

Repo-local `user.name` / `user.email` match. No test commit was created.

---

## 5. Cursor attribution status

**Git:** no Cursor `user.*`, no commit template, no active hooks that inject trailers. `Co-authored-by` is **not** configured in `.git/config`.

**Historical:** `d86ebde` still contains `Co-authored-by: Cursor <cursoragent@cursor.com>` in the **message body**. That commit was **not** rewritten.

**IDE:** Agent Attribution is a Cursor UI setting (`Cursor Settings → Agent → Attribution`), not stored in this repo. It is **not present** in Git. The owner must keep the UI toggle off so a future V6.3 commit message does not grow a Cursor trailer.

---

## 6. Working-tree status

- Staged: **empty** (`git diff --cached` empty)
- Unstaged V6.3 + unrelated dirt: present
- No clean/reset of unrelated files

---

## 7. Exact V6.3 file set

### Include in a future V6.3 commit (24 paths)

**Modified vs v6.2.8 (10)**

- `config_example.txt`
- `dashboard/ask_session.py`
- `dashboard/server.py`
- `database/postgres_db.py`
- `observability/schemas.py`
- `proactive/ask.py`
- `proactive/ask_decisions.py`
- `proactive/config.py`
- `proactive/store.py`
- `proactive/worker.py`

**New (14, including this report)**

- `proactive/act.py`
- `proactive/act_engine.py`
- `proactive/act_policy.py`
- `proactive/act_spec.py`
- `proactive/act_verify.py`
- `proactive/writers/__init__.py`
- `proactive/writers/internal_ledger.py`
- `proactive/writers/calendar_hold.py`
- `test_v63_act.py`
- `DOOM_V6.3_ACT_ARCHITECTURE_AUDIT.md`
- `DOOM_V6.3_ACT_IMPLEMENTATION_REPORT.md`
- `DOOM_V6.3_ACT_FORENSIC_AUDIT_REPORT.md`
- `DOOM_V6.3_ACT_TEST_REMEDIATION_REPORT.md`
- `DOOM_V6.3_FINAL_RELEASE_VERIFICATION.md`

### Unrelated — must not enter the V6.3 commit (20)

**Modified (3):** `DOOM_V6.2.5_RELEASE_REPORT.md`, `DOOM_V6.2.6_RELEASE_REPORT.md`, `DOOM_V6.2.8_RELEASE_REPORT.md`

**Untracked (17):** provider-routing audits/reports; `DOOM_V5.3.7.3_RELEASE_REPORT.md`; `DOOM_V5.3.7.5_FINAL_ACCEPTANCE_AUDIT.md`; `DOOM_V6.1_RELEASE_REPORT.md`; `DOOM_V6.2.1`–`DOOM_V6.2.4` release reports; `DOOM_V6.2_ARCHITECTURE_AUDIT.md`; `DOOM_V6.2_IMPLEMENTATION_PLAN.md`

### Potentially unexpected

None outside the sets above. `postgres_db.py` lock/statement timeout on schema init is the F-V63-F04 remediation (in the V6.3 modified set).

**Protected / empty vs HEAD:** `proactive/connectors/http_safe.py`, `core/task_engine.py`, `core/orchestrator.py`, `proactive/predict.py`, `proactive/suggest.py`, `proactive/prepare.py`, draft modules.

---

## 8. V6.3 test results (this session)

```
python -m unittest test_v63_act -v
Ran 17 tests in 3.312s
OK
exit 0
```

F-V63-F04 remains **CLEARED**.

---

## 9. Regression results

| Suite | Result |
|-------|--------|
| `python -m unittest test_v628_llm_draft -q` | **36 tests, 1 skipped, 0 failed, exit 0** (2.193s) |
| `python test_v626_prepare_ask.py` | **44/44 OK, exit 0** (6.972s) |
| `python test_v625_suggest.py` | **36/36 OK, exit 0** (2.201s, `V625_PERF_MS=253.62`) |
| `python test_v624_evidence_prediction.py` | **40 tests: 5 fail, 1 error, exit 1** |

**v624 classification:** `proactive/predict.py` is **unmodified** vs v6.2.8. Failures (`DEADLINE_HORIZON` / conflict / blocked / poll / concurrent upsert / generation) match the inherited owner-cap / emitter-isolation class (**F-V625-F01**), not ACT writers. Tests were **not** weakened. Not treated as a V6.3 authority blocker.

Inherited also: F-V622-07 TestClient, F-V628-I01 `test_91` / `model_router` glob, CORS `*`.

---

## 10. ACT flag status

Code defaults (`_bool_env(..., False)`):

- `PROACTIVE_ACT_ENABLED` = **false**
- `PROACTIVE_ACT_INTERNAL_ENABLED` = **false**
- `PROACTIVE_ACT_CALENDAR_HOLD_ENABLED` = **false**

`config_example.txt` documents all three as `false`. `.env` has **no** `PROACTIVE_ACT_*` keys. Flags were **not** enabled for this verification. No Calendar/email/GitHub/shell ACT was executed.

---

## 11. ActionEngine authority

- Dedicated `act_engine.py`; no `process_request` / `task_engine` / `model_router` / `subprocess` in `act*.py` or writers.
- `_dispatch` allowlists ACT-0 `write_internal_ledger` and ACT-1 `write_calendar_hold` only.
- `decide_approval` sets `APPROVED` on the ask row and `APPROVED_NOT_RUN` on actions when `action_hash` is non-empty. **No writer call.**
- Worker: `expire_pending_asks` then `evaluate_world_actions()` in isolated `try/except` (`act_eval`).
- `claim_run` SQL: `status = 'RUN_REQUESTED'` only.

**Authority boundary: PASS**

---

## 12. ASK / RUN

- v63.1 ASK only when a materialized `action_hash` exists; else v626.1 worker binding.
- GET `/api/proactive/actions/{id}`: session, read-only.
- POST `.../run`: `need_csrf=True`, `request_run` enqueue only.
- POST `.../cancel`: CSRF, no writer.
- No `/api/proactive/execute` or `/approve-and-execute`.

**ASK/RUN: PASS**

---

## 13. Action hash

`compute_action_hash` binds owner, preparation, capability, action_type, target_ref, exec_params, param_hash, risk, privacy, policy_version. Tests: deterministic; param change changes hash. Live preconditions recompute and compare.

**Hash: PASS** (F-V63-F01: empty POST `action_hash` is replaced with stored hash; CSRF/session still required)

---

## 14. Legacy approval

Preconditions: `rule_version != v63.1` or empty/mismatched approval `action_hash` → `legacy_approval`. Approve bump to `APPROVED_NOT_RUN` requires non-empty stored approval `action_hash`. `binding_hash_for` / `canonical_binding_string` unchanged vs HEAD (v63 functions are additive).

**Legacy: PASS**

---

## 15. UNKNOWN_OUTCOME

Timeout / post-send uncertainty → `UNKNOWN_OUTCOME`. `request_run` refuses that status (`unknown_outcome`, no auto retry). Verify mismatch with a receipt is UNKNOWN, not COMPLETED. Covered by `test_unknown_not_retried` and ACT-1 mismatch test.

**UNKNOWN_OUTCOME: PASS**

---

## 16. Calendar writer

- Typed `calendar_hold.py`; URL template `https://www.googleapis.com/calendar/v3/calendars/{cal}/events` (quoted calendar id only).
- Not `SafeHttp.request` POST; `http_safe.py` unmodified (GET + OAuth token POST only).
- Summary from `CAL_SUMMARY_TEXT`, not LLM/draft.
- Materialize refuses without RFC3339 start/end.
- Verify: SafeHttp **GET** event; start/end/summary mismatch → not COMPLETED.
- Engine sets `_runtime_secret_ref` at run time (not in `action_hash`). Writer currently reads `target_ref.secret_ref`; missing ref **fail-closes** (`credential`) rather than posting. No live Calendar call in this verification.

**Calendar writer: PASS with remaining wiring note (fail-closed)**

---

## 17. Security

RUN: session + origin + CSRF + owner-scoped `get_action` + enqueue only if `APPROVED_NOT_RUN` + flags + hash check. Cross-owner GET uses session owner (404). Stale hash rejected. SENSITIVE: no materialize / insert refuse.

**Security: PASS**

---

## 18. Database

Additive `world_actions`, events, attempts, verifications, idempotency, receipts; `action_hash` on approvals default `''`. Preparation is not an execute state. Extra receipts table: F-V63-F03 informational.

**Database: PASS (additive)**

---

## 19. LLM firewall

Builders/hash do not import `proactive.draft` or `model_router`. AST test. Draft `body` is not `exec_params`.

**LLM firewall: PASS**

---

## 20. Git integrity

HEAD = v6.2.8. Tag object unchanged on origin. No force-push. No V6.3 commit/tag. `DOOM-V5.2` SHA unchanged.

**Git integrity: PASS**

---

## 21. Remaining findings

| ID | Severity | Note |
|----|----------|------|
| F-V63-F01 | Non-blocking | Empty client `action_hash` uses stored hash |
| F-V63-F02 | Non-blocking | ACT-1 not auto-materialized from current prepare `safe_params` |
| F-V63-F03 | Info | Extra `world_action_receipts` table |
| ACT-1 secret_ref | Non-blocking, fail-closed | Writer vs `_runtime_secret_ref` |
| F-V625-F01 | Inherited | v624 emit/upsert empties; predict.py unchanged |
| F-V628-I01 | Inherited | V6.1 `test_91` vs draft `model_router` |
| Cursor UI | Operator | Keep Agent Attribution off for the release commit |

---

## 22. Final release recommendation

**READY FOR OWNER RELEASE AUTHORIZATION**

Owner must still: (1) keep Cursor Attribution off, (2) commit **only** the V6.3 file set on `DOOM-V6`, (3) not mix unrelated dirt, (4) not rewrite `v6.2.8`, (5) leave ACT flags **false** until separately enabled.

This verification **did not** commit, tag, push, enable ACT, or execute real-world actions.
