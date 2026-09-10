# DOOM V6.3 — F-V63-F04 Test Remediation

**Mode:** remediation of live `test_v63_act` (not a release)  
**Date:** 2026-09-10  
**HEAD unchanged:** `d86ebde13de11448dcb0f5e8688043e06d913169` (`release: DOOM v6.2.8`)  
**No commit, tag, or push.**

**Result:** `python -m unittest test_v63_act -v` → **Ran 17 tests in 2.075s — OK**

This report does **not** authorize release.

---

## 1. What F-V63-F04 actually was

The forensic hang was **not** an ActionEngine deadlock. Several leftover CPython test processes (v6.2.6 / v6.2.8 / v6.3 / foundation) were still running and holding PostgreSQL DDL locks. `PostgresManager._create_tables` (including `ALTER TABLE world_approval_requests ADD COLUMN IF NOT EXISTS action_hash`) waits with **no lock timeout**, so the next `from dashboard.server import app` never printed a first test line.

After those processes were killed, import completed in ~1–2s.

---

## 2. Fixes (working tree only)

| Area | Change |
|------|--------|
| Process | Stopped hung `python.exe` unittest / suite processes. **0** python processes remained before the green run. |
| `test_v63_act.py` | Lazy-import `dashboard.server` (`_app()`). Hash/AST no longer import FastAPI/Postgres at module load. `PGOPTIONS` lock/statement timeouts. Seed prediction + suggestion before preparation (FK). Session CSRF key is `csrf` (API), not `csrf_token`. Builder test no longer matches the word “draft” in a refusal docstring. |
| `database/postgres_db.py` | `SET lock_timeout = 15s` / `statement_timeout = 60s` on schema init so a future pile-up **fails** instead of hanging forever. |
| `proactive/act_engine.py` | `process_claimed` returns `""` if the claim row is missing (no `None.get`). |

Unrelated v6.2.5 / v6.2.6 / v6.2.8 release-report dirt was not touched.

---

## 3. Green output (this session)

Command: `python -m unittest test_v63_act -v`  
Working directory: `c:\Users\dell\Desktop\DOOM`  
Exit code: **0**

```
test_firewall ... ok
test_no_execute_routes ... ok
test_flags_default_false ... ok
test_hash_changes_on_params ... ok
test_hash_deterministic ... ok
test_llm_payload_not_in_builder ... ok
test_materialize_refuses_without_rfc3339 ... ok
test_v626_binding_unchanged ... ok
test_act0_verify_and_idempotent_run ... ok
test_act1_ok ... ok
test_act1_readback_mismatch ... ok
test_legacy_cannot_run ... ok
test_owner_csrf_run ... ok
test_sensitive_no_materialize ... ok
test_stale_hash_rejected ... ok
test_unknown_not_retried ... ok
test_worker_isolation_and_no_auto_approved ... ok

----------------------------------------------------------------------
Ran 17 tests in 2.075s

OK
```

F-V63-F04 is **cleared for the release gate** (suite observed green). Architecture findings F-V63-F01 / F-V63-F02 / F-V63-F03 are unchanged.

---

## 4. Still not a release

- HEAD is still v6.2.8 `d86ebde`.
- No `v6.3` tag.
- ACT flags still default **false**.
- Do not mix unrelated release-report dirt into a V6.3 commit.

Waiting for a **separate** owner release authorization before commit / tag / push.
