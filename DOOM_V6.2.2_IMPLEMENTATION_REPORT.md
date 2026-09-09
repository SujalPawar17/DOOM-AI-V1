# DOOM V6.2.2 — Implementation Report

**Mode:** Implementation complete (working tree only — no commit/tag/push)  
**Date:** 2026-09-09  
**Scope:** Vault + READ calendar/GitHub connectors only.  
**V6.2.3–V6.2.8:** **NOT STARTED**

**Final verdict:** **A. V6.2.2 IMPLEMENTATION COMPLETE — READY FOR FORENSIC REVIEW**

---

## 1. Release objective (this slice)

External **READ-only** connectors behind flags that default **off**, a Windows **DPAPI vault** for tokens (PostgreSQL stores `secret_ref` only), additive `connector_accounts` / `connector_sync_state` / `external_facts`, and poller ingest through the existing V6.1 outbox.

This is **not** V6.2.3 email, V6.2.4 prediction, V6.2.5 SUGGEST, V6.2.6 PREPARE, V6.2.7 ASK, V6.2.8 LLM, or V6.3 ACT.

---

## 2. Baseline

| Item | Value |
|------|--------|
| Branch | `DOOM-V5.2` |
| Prior release | `v6.2.1` / `753754ecce209e8712d9ff8f118aedf9fb0760c8` |
| INFORM / F-RA-01 / F-WRK-02 | Unchanged worker recovery and `_OWNER_PRED` |
| `PROACTIVE_ENABLED` default | **false** |
| Connector flags default | **false** |

---

## 3. Files created

| File | Role |
|------|------|
| `proactive/vault.py` | DPAPI JSON vault get/put/delete |
| `proactive/connectors/__init__.py` | Export readers only |
| `proactive/connectors/base.py` | `ReadConnector`, `FencedRecord` |
| `proactive/connectors/http_safe.py` | GET allowlist + token-URL POST only |
| `proactive/connectors/oauth_token.py` | Google refresh → `oauth2.googleapis.com/token` |
| `proactive/connectors/calendar_google.py` | `events.list` GET |
| `proactive/connectors/github.py` | notifications/issues GET |
| `proactive/connectors/registry.py` | Flag-gated `get_enabled_readers()` |
| `test_v62_connectors.py` | 12 tests (fixtures, no live network) |
| This report | Evidence |

**Not created:** `proactive/connectors/write.py`, `email_gmail.py`, `vault` in PG, prediction/prepare/ask/llm modules.

---

## 4. Files modified

| File | Change |
|------|--------|
| `proactive/config.py` | Calendar/GitHub flags, poll intervals, vault path |
| `proactive/schemas.py` | `CALENDAR_EVENT`, `GITHUB_*` types; `calendar_google`, `github` sources |
| `proactive/significance.py` | Conservative scores (review request 0.55, below INFORM floor) |
| `proactive/store.py` | Account/sync/fact CRUD; `git_remote` map; **no secret columns** |
| `proactive/poller.py` | `poll_connectors()` + `persist_fenced_record()` |
| `proactive/worker.py` | Call `poll_connectors()` after internal polls (same worker) |
| `database/postgres_db.py` | Additive CREATE TABLE/INDEX |
| `observability/schemas.py` | `connector_id`, `fact_id`, `capability` attr keys |
| `config_example.txt` | Flag names and vault path comments; **no real tokens** |

---

## 5. Vault

- Path: `DOOM_CONNECTOR_VAULT_PATH` or `%LOCALAPPDATA%\DOOM\connector_vault.dpapi`
- `CryptProtectData` / `CryptUnprotectData` (UI forbidden)
- Values: `{secret_ref: {type, token, refresh?, expiry, ...}}`
- Non-Windows: refuse (no plaintext production vault)
- `DOOM_GITHUB_PAT` may bootstrap a missing GitHub ref once; PAT is never written to PG
- Linux portable vault is **follow-up**, not this slice

---

## 6. HTTP enforcement

`SafeHttp` raises unless:

- `GET` to `www.googleapis.com`, `calendar.googleapis.com`, or `api.github.com`, or
- `POST` to `https://oauth2.googleapis.com/token` with form encoding

HTTPS only. No IP/localhost/metadata. No PUT/PATCH/DELETE. AST scan forbids `requests.post` / `httpx.post` outside `oauth_token.py`.

---

## 7. Polling

`process_once` → `poll_internal_sources` → `poll_connectors` → `claim_batch`.

`poll_connectors` is a no-op unless `PROACTIVE_ENABLED` **and** a connector flag is on. 403/429 → `connector_sync_state.backoff_until` and health `DEGRADED`. Auth miss → no HTTP body stored.

Fenced records upsert `external_facts` and `ingest_signal(..., extra_idem=source_record_id)`. Description/issue body not stored.

---

## 8. Tests

| Suite | Result |
|-------|--------|
| `test_v62_connectors.py` | **12/12** |
| `test_v62_emitters.py` | **13/13** |
| `test_v61_proactive_foundation.py` HUD 95–98 deselected | **72 passed**, 4 deselected |
| Isolated 110 / 111 / 119 after that suite | **3/3 PASS** |
| `test_v532_transaction_engine.py` | **30/30** |

HUD TestClient (`httpx` 0.28 vs Starlette 0.27) unchanged. Tests not weakened.

Connector tests use recorded fixtures only (no live Google/GitHub).

Known non-blocking: `claim_batch(limit=8)` can starve a new signal if many older `PENDING` rows exist (signal stays `PENDING`, not `PROCESSED` + zero delivery). Not fixed in this slice.

---

## 9. Flag-off

`PROACTIVE_CALENDAR_ENABLED` and `PROACTIVE_GITHUB_ENABLED` default **false**. Flags off → zero HTTP (`get_enabled_readers()` empty). No silent enable.

---

## 10. Scope verification

| Item | Status |
|------|--------|
| Gmail / email snippet | **not implemented** |
| WorldSnapshot commitments | **not this slice** (V6.2.3) |
| Prediction / SUGGEST / PREPARE / ASK / LLM | **not implemented** |
| ACT / SEND / MERGE / browser / shell | **not implemented** |
| Second worker / `DOOMAutomation` / worker `process_request` | **not implemented** |
| Connector WRITE | **absent** |

---

## 11. Security

- No tokens in PG columns or `external_facts.payload`
- Vault ciphertext does not contain the plaintext PAT in the DPAPI file bytes
- `config_example.txt` documents empty `DOOM_GITHUB_PAT=`
- OTP attributes use ids/reasons only

---

## 12. V6.2.3+ freeze

Do not start email READ, commitments snapshot, prediction, SUGGEST, PREPARE, ASK, or LLM until this slice is forensically reviewed and released.

V6.2.7 remains a **hard stop** unless session + CSRF + ownership + `param_hash` can be implemented without inventing a workaround.

---

## 13. Git

Working-tree implementation only. **No commit, tag, or push** in this task.
