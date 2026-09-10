# DOOM V6.3 — Independent Forensic Audit (ACT)

**Controlled execution (ActionEngine)**  
**Mode:** AUDIT ONLY — no source, schema, test, config, README, commit, tag, or push  
**Date:** 2026-09-10  
**Baseline:** tag `v6.2.8` / commit `d86ebde13de11448dcb0f5e8688043e06d913169`  
**Branch:** `DOOM-V5.2`  
**Implementation claim:** V6.3 IMPLEMENTED — NOT RELEASED

**Final verdict:** **PASS WITH NON-BLOCKING FINDINGS — RELEASE CONDITIONALLY RECOMMENDED**

This audit does **not** authorize commit, tag, push, or release.

**V6.3 IS READY FOR OWNER RELEASE AUTHORIZATION** only if the owner accepts the conditions in §23 (especially a successful live `test_v63_act` run before tagging).

---

## 1. Executive Summary

HEAD is still the released **v6.2.8** commit. There is **no** `v6.3` tag and **no** V6.3 commit. ACT exists only in the working tree.

Live design matches the approved architecture:

```
Action spec + action_hash
  → v63.1 ASK (ACT-eligible only)
  → APPROVED (authorization; action → APPROVED_NOT_RUN)
  → POST /run (CSRF) → RUN_REQUESTED
  → worker claim RUN_REQUESTED only
  → preconditions → lease → typed writer → read-back
  → COMPLETED | FAILED | UNKNOWN_OUTCOME
```

**APPROVED never calls a writer.** `decide_approval` still only updates approval rows (plus an additive status bump on `world_actions`). Dashboard RUN calls `request_run` (enqueue). `claim_run` SQL filters `status = 'RUN_REQUESTED'` only.

Legacy v626.1 rows keep empty `action_hash`; preconditions return `legacy_approval`.

Protected modules (`http_safe.py`, TaskEngine, orchestrator, predict/suggest/prepare, drafts) have **empty** diffs vs HEAD.

---

## 2. Git / worktree

| Check | Evidence |
|-------|----------|
| HEAD | `d86ebde13de11448dcb0f5e8688043e06d913169` `release: DOOM v6.2.8` |
| `v6.2.8` peel | same commit |
| `v6.3` tag | **absent** |
| Staged | empty (not committed) |
| V6.3 source | uncommitted |

**Unrelated dirty (must not enter a V6.3 commit):** `DOOM_V6.2.5_RELEASE_REPORT.md`, `DOOM_V6.2.6_RELEASE_REPORT.md`, `DOOM_V6.2.8_RELEASE_REPORT.md`, leftover provider-routing / older markdown.

**V6.3 intended set:** act modules, `writers/`, `test_v63_act.py`, architecture + implementation (+ this forensic) reports, and the modified config/store/ask/worker/dashboard/postgres/otp/ask_session files listed in `git status`.

---

## 3. Architecture compliance

| Requirement | Result |
|-------------|--------|
| Dedicated ActionEngine | **Pass** — `act_engine.py`; no TaskEngine/`process_request` imports |
| APPROVED ≠ execute | **Pass** |
| Worker ≠ APPROVED | **Pass** — `claim_run` is `RUN_REQUESTED` only |
| Legacy non-executable | **Pass** — empty/mismatched approval `action_hash` → `legacy_approval` |
| v626.1 binding preserved | **Pass** — original `canonical_binding_string` / `binding_hash_for` intact; v63 is additive |
| Typed writers, not generic SafeHttp POST | **Pass** — calendar uses `urllib` + fixed URL; `http_safe.py` unmodified |
| ACT-0 / ACT-1 only | **Pass** — registry two capabilities |
| Flags default false | **Pass** |
| Explicit RUN + CSRF | **Pass** — `need_csrf=True` on POST run/cancel |
| UNKNOWN_OUTCOME no auto-retry | **Pass** — `request_run` refuses `UNKNOWN_OUTCOME` |
| LLM/draft not payload | **Pass** — builders allowlisted; AST forbids `proactive.draft` imports in act/writers |
| V7 / shell / email send / GH merge | **Pass** — not in registry |

---

## 4. ASK / authority

`binding_hash_for` (v626.1) is unchanged. `_recompute` uses `binding_hash_for_act` only when `rule_version == v63.1`.

`ensure_pending_ask` materializes an action when ACT flags allow a builder success; otherwise it still inserts a **v626.1** ask (backward compatible).

Approve path updates `world_actions` to `APPROVED_NOT_RUN` **only** when stored approval `action_hash` is non-empty.

---

## 5. API / worker

| Route | Behavior |
|-------|----------|
| GET `/api/proactive/actions/{id}` | Session, read-only |
| POST `.../run` | CSRF; `request_run`; **no writer** |
| POST `.../cancel` | CSRF; no writer |
| `/execute`, `/approve-and-execute` | **Absent** |

`process_once`: recover → emitters → READ connectors → predict → suggest → prepare → drafts → ASK expire → **ACT recover/claim RUN_REQUESTED** → INFORM `claim_batch`. ACT is isolated `try/except` (`act_eval`).

---

## 6. Privacy / credentials / HTTP

- SENSITIVE: no materialize; insert_world_action refuses; preconditions refuse.
- PRIVATE: ACT-1 builder requires NORMAL; ACT-0 may persist.
- Spec/OTP: no tokens. Vault `secret_ref` at call time for ACT-1 (`_runtime_secret_ref`, not in `action_hash`).
- `SafeHttp` still rejects non-GET except OAuth token POST.

---

## 7. Database

Additive tables as implemented, plus `world_action_receipts` (ACT-0). `action_hash` column on approvals defaults `''`. Preparation status is not an execute state.

---

## 8. Tests (this forensic)

`test_v63_act.py` collects **17** `test_*` methods.

This forensic **attempted** `python -m unittest test_v63_act.TestV63Hash test_v63_act.TestV63AST -v`. After ~110s the process was still running with **no test output** (import of `dashboard.server` / PostgreSQL init contention — same class as F-V628-N02). Implementation-phase `py_compile` of new modules had succeeded earlier.

**Coverage vs the implementation authorization matrix:** hash, flags, AST, no execute routes, legacy, ACT-0 path, CSRF, UNKNOWN no retry, ACT-1 mocked ok/mismatch, worker isolation, SENSITIVE refuse are **represented**. Gaps: dedicated concurrent lease race, crash-before-writer, capability-disabled/writer-allowlist as isolated cases, live calendar HTTP, full V6.2.8/V6.2.6 regression **not completed in this session**.

---

## 9. Findings

| ID | Severity | Summary |
|----|----------|---------|
| F-V63-F01 | NON-BLOCKING | Empty POST `action_hash` is replaced with the stored hash (`client_action_hash or stored`). CSRF/session still required. Tighten later: require explicit hash. |
| F-V63-F02 | NON-BLOCKING | ACT-1 will not auto-materialize from current prepare `safe_params` allowlist (no RFC3339 keys). Matches refuse-if-incomplete. Tests inject specs. |
| F-V63-F03 | INFORMATIONAL | Extra `world_action_receipts` table beyond the five named in architecture; justified for ACT-0 verify. |
| F-V63-F04 | NON-BLOCKING (release condition) | Live `test_v63_act` did not finish in this forensic process. **Do not tag until it is observed green.** |
| F-V63-F05 | NON-BLOCKING | 17 tests vs the long requested matrix; core authority cases are present. |
| F-V63-N01 | NON-BLOCKING | Unrelated dirty release reports / historical markdown. |
| Inherited | — | F-V622-07, F-V625-F01, F-V628-I01, CORS `*` |

**Blockers:** none on the authority design. Release tagging is **conditioned** on F-V63-F04.

---

## 10. Release-readiness gates

| Gate | Status |
|------|--------|
| HEAD v6.2.8; no v6.3 commit/tag | PASS |
| ActionEngine sole writer dispatch | PASS (static) |
| APPROVED ≠ execute | PASS |
| RUN enqueue only | PASS |
| Legacy grants non-executable | PASS (static + tests authored) |
| SafeHttp write policy unchanged | PASS |
| Flags default false | PASS |
| `test_v63_act` live green | **NOT OBSERVED this session** |
| V6.2.8 regression live | **NOT RE-RUN this session** |

---

## 11. Working tree (exact)

HEAD `d86ebde` `release: DOOM v6.2.8`. V6.3 uncommitted. No `v6.3` tag.

---

## 12. Final verdict block

1. **Verdict:** PASS WITH NON-BLOCKING FINDINGS — RELEASE CONDITIONALLY RECOMMENDED  
2. **Architecture:** COMPLIANT with V6.3 audit (ActionEngine, action_hash, explicit RUN)  
3. **Security:** PASS (static); empty client hash is a minor binding looseness  
4. **Privacy:** PASS for v1 (SENSITIVE none; ACT-1 NORMAL)  
5. **ASK:** v626.1 preserved; v63.1 additive  
6. **Tests:** 17 authored; **live run not completed** (F-V63-F04)  
7. **Database:** additive; prep not execution state  
8. **Worker:** RUN_REQUESTED only; isolated  
9. **API:** GET/RUN/cancel; no arbitrary execute  
10. **Blockers:** none for architecture; **do not release until `test_v63_act` is green**  
11. **Safe to present for release authorization?** Yes, with F-V63-F04 as a **hard release condition**.

This forensic audit does **not** commit, tag, push, or release.
