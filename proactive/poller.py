"""Internal source polling. Never reads command_logs. Flag-gated via ingest."""

from __future__ import annotations

import hashlib
import time
import uuid

from proactive.config import (
    CALENDAR_POLL_SEC,
    CONNECTOR_BACKOFF_SEC,
    EMAIL_POLL_SEC,
    GITHUB_POLL_SEC,
    OWNER_ID,
    is_calendar_enabled,
    is_email_enabled,
    is_github_enabled,
    is_proactive_enabled,
)
from proactive.ingest import ingest_signal
from proactive.otp import emit_proactive
from proactive.schemas import dump_bounded_payload
from proactive.store import proactive_store


def poll_internal_sources() -> None:
    try:
        from database.postgres_db import postgres_manager
        if not postgres_manager.is_connected():
            return
        rows = postgres_manager.execute_query(
            """
            SELECT cpu_percent, ram_percent, disk_percent
            FROM system_telemetry ORDER BY recorded_at DESC LIMIT 1
            """
        )
        if isinstance(rows, list) and rows and "error" not in rows[0]:
            disk = float(rows[0].get("disk_percent") or 0)
            if disk >= 80:
                ingest_signal(
                    signal_type="HOST_TELEMETRY",
                    source="system_telemetry",
                    entity_type="host",
                    entity_id="workstation",
                    payload={"disk_percent": int(disk), "cpu_percent": int(rows[0].get("cpu_percent") or 0)},
                    privacy_class="NORMAL",
                )
    except Exception:
        pass
    try:
        from core.reliability.circuit_breaker import CircuitState, provider_circuit_breaker
        state = getattr(provider_circuit_breaker, "_providers", {}) or {}
        for name, entry in list(state.items()) if isinstance(state, dict) else []:
            st = ""
            if isinstance(entry, dict):
                st = str(entry.get("state") or "")
            else:
                st = str(getattr(entry, "state", "") or "")
            if st == CircuitState.OPEN or "OPEN" in str(st).upper():
                ingest_signal(
                    signal_type="PROVIDER_CIRCUIT",
                    source="circuit_breaker",
                    entity_type="provider",
                    entity_id=str(name)[:40],
                    payload={"state": "OPEN"},
                    privacy_class="NORMAL",
                )
    except Exception:
        pass
    try:
        from core.state_machine import state_machine
        last = getattr(state_machine, "_last_changed", None)
        if last and (time.time() - float(last)) > 7 * 86400:
            ingest_signal(
                signal_type="INACTIVITY",
                source="clock",
                entity_type="user",
                entity_id="session",
                payload={"idle_days": 7},
                privacy_class="NORMAL",
            )
    except Exception:
        pass


def persist_fenced_record(account: dict, rec) -> str:
    """Upsert external_facts and ingest a typed signal. No secrets. Never raises."""
    try:
        if rec.privacy_class == "SENSITIVE":
            return ""
        payload = rec.payload if isinstance(rec.payload, dict) else {}
        sha = hashlib.sha256(dump_bounded_payload(payload).encode("utf-8")).hexdigest()
        aid = str(account.get("account_id") or "")
        kind = str(rec.fact_kind or "")
        src = str(rec.source_record_id or "")
        idem = f"{aid}|{kind}|{src}"[:160]
        fid = str(uuid.uuid4())
        occurred = float(rec.occurred_at or 0) or None
        proactive_store.upsert_external_fact({
            "fact_id": fid,
            "owner_id": account.get("owner_id") or OWNER_ID,
            "account_id": aid,
            "connector_type": rec.connector_type,
            "source_record_id": src,
            "fact_kind": kind,
            "project_id": rec.project_id or account.get("project_id"),
            "privacy_class": rec.privacy_class,
            "occurred_at": occurred,
            "valid_until": (occurred + 7 * 86400) if occurred else None,
            "confidence": 0.85,
            "payload": payload,
            "content_sha256": sha,
            "idempotency_key": idem,
        })
        if rec.privacy_class != "NORMAL":
            return fid
        ingest_signal(
            signal_type=rec.signal_type,
            source=rec.connector_type,
            entity_type="external",
            entity_id=src[:120],
            payload=payload,
            privacy_class="NORMAL",
            occurred_at=float(rec.occurred_at or time.time()),
            extra_idem=src,
        )
        return fid
    except Exception:
        return ""


def persist_email_commitment(account: dict, rec, fact_id: str) -> str:
    """PRIVATE commitment from ephemeral fenced text. Never stores snippet/subject."""
    try:
        if rec.connector_type != "gmail" or rec.privacy_class != "PRIVATE":
            return ""
        eph = rec.ephemeral if isinstance(rec.ephemeral, dict) else {}
        from proactive.commitments import extract_commitment, fingerprint
        extracted = extract_commitment(
            str(eph.get("subject") or ""),
            str(eph.get("snippet") or ""),
            float(rec.occurred_at or 0),
            str(eph.get("date_hdr") or ""),
        )
        if not extracted:
            return ""
        aid = str(account.get("account_id") or "")
        mid = str(rec.source_record_id or "")
        fp = fingerprint(aid, mid, extracted["commitment_type"], extracted.get("due_at"))
        due = extracted.get("due_at")
        valid_until = (due + 7 * 86400) if due else (float(rec.occurred_at or 0) + 30 * 86400)
        payload = rec.payload if isinstance(rec.payload, dict) else {}
        return proactive_store.upsert_commitment({
            "commitment_id": str(uuid.uuid4()),
            "owner_id": account.get("owner_id") or OWNER_ID,
            "account_id": aid,
            "source_connector": "gmail",
            "source_message_id": mid,
            "source_thread_id": str(payload.get("thread_id") or ""),
            "source_ts": rec.occurred_at,
            "commitment_type": extracted["commitment_type"],
            "normalized_code": extracted.get("normalized_code") or extracted["commitment_type"],
            "due_at": due,
            "timezone": extracted.get("timezone") or "",
            "confidence": extracted.get("confidence") or 0,
            "provenance": {"fact_id": fact_id[:64], "message_id": mid[:64]},
            "evidence_ref": fact_id[:64],
            "fingerprint": fp,
            "valid_until": valid_until if valid_until else None,
        })
    except Exception:
        return ""


def poll_connectors() -> None:
    """READ connectors only. Zero HTTP when flags are off."""
    if not is_proactive_enabled():
        return
    if not is_calendar_enabled() and not is_github_enabled() and not is_email_enabled():
        return
    try:
        from proactive.connectors.http_safe import SafeHttpError
        from proactive.connectors.registry import get_enabled_readers
    except Exception:
        return
    readers = get_enabled_readers()
    if not readers:
        return
    now = time.time()
    for ctype, reader in readers.items():
        interval = CALENDAR_POLL_SEC
        if ctype == "github":
            interval = GITHUB_POLL_SEC
        elif ctype == "gmail":
            interval = EMAIL_POLL_SEC
        try:
            accounts = proactive_store.list_active_accounts(ctype)
        except Exception:
            continue
        for account in accounts:
            aid = account.get("account_id") or ""
            sync = proactive_store.get_sync_state(aid)
            backoff = float(sync.get("backoff_until") or 0)
            if backoff > now:
                continue
            last_ok = float(sync.get("last_success_at") or 0)
            if last_ok and (now - last_ok) < interval:
                continue
            from core.cost_guard import ResourceRequest, ResourceType, cost_guard
            cmap = {
                "gmail": "gmail",
                "calendar_google": "google_calendar",
                "github": "github",
            }
            if not cost_guard.authorize(ResourceRequest(
                resource_type=ResourceType.CONNECTOR,
                provider=cmap.get(ctype, "arbitrary"),
                capability="connector_poll",
            )).is_allow:
                continue
            try:
                records, cursor = reader.fetch_updates(account, sync.get("cursor") or "")
                for rec in records or []:
                    fid = persist_fenced_record(account, rec)
                    persist_email_commitment(account, rec, fid)
                proactive_store.upsert_sync_state(
                    aid,
                    cursor=cursor or None,
                    last_success=True,
                    last_error_type="",
                    backoff_until=None,
                )
                proactive_store.set_account_health(aid, "OK", "")
            except SafeHttpError as exc:
                status = int(getattr(exc, "status", 0) or 0)
                if status in (403, 429):
                    err, health, detail = "RATE_LIMIT", "DEGRADED", "rate_limit"
                elif status == 401:
                    err, health, detail = "AUTH", "DEGRADED", "auth_expired"
                else:
                    err, health, detail = "NETWORK", "DOWN", "fetch_failed"
                proactive_store.upsert_sync_state(
                    aid,
                    last_success=False,
                    last_error_type=err,
                    backoff_until=now + CONNECTOR_BACKOFF_SEC,
                )
                proactive_store.set_account_health(aid, health, detail)
                emit_proactive(
                    "proactive.outcome",
                    status="error",
                    attributes={"reason": detail, "connector_id": str(aid)[:36]},
                )
            except Exception:
                proactive_store.upsert_sync_state(
                    aid,
                    last_success=False,
                    last_error_type="NETWORK",
                    backoff_until=now + CONNECTOR_BACKOFF_SEC,
                )
                proactive_store.set_account_health(aid, "DOWN", "fetch_failed")
