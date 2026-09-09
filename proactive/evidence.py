"""V6.2.4 world evidence citations. Not memory_evidence. Ids/hashes only."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any, Dict, Optional

from proactive.config import OWNER_ID, RULE_VERSION


def independence_key(
    owner_id: str,
    source_kind: str,
    connector_type: str,
    source_record_id: str,
    content_sha256: str,
) -> str:
    rec = str(source_record_id or "").strip()
    raw = f"{owner_id}|{source_kind}|{connector_type}|{rec}|{content_sha256}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def source_id_fallback(value: str) -> str:
    return str(value or "")


def idempotency_key(owner_id: str, source_kind: str, source_id: str, content_sha: str) -> str:
    return f"{owner_id}|{source_kind}|{source_id}|{content_sha}"[:160]


def _rel_for_connector(connector_type: str) -> str:
    c = str(connector_type or "")
    if c == "gmail":
        return "gmail_extract"
    if c in ("calendar_google", "calendar"):
        return "calendar"
    if c == "github":
        return "github"
    if c == "task_engine":
        return "task_engine"
    return "gmail_extract"


def _strength(source_kind: str, connector_type: str, fact_kind: str = "") -> float:
    if source_kind == "TASK_CHECKPOINT":
        return 1.00
    if fact_kind in ("GH_REVIEW", "GH_PR"):
        return 1.00
    if fact_kind == "CAL_EVENT" or connector_type in ("calendar_google", "calendar"):
        return 0.85
    if connector_type == "gmail":
        return 0.55
    if connector_type == "github":
        return 0.70
    return 0.55


def cite_from_commitment(row: Dict[str, Any], owner_id: str = OWNER_ID) -> Optional[Dict[str, Any]]:
    cid = str(row.get("commitment_id") or "")
    if not cid:
        return None
    sha = str(row.get("fingerprint") or row.get("content_sha256") or cid)
    conn_t = str(row.get("source_connector") or "")
    rec = str(row.get("source_message_id") or cid)
    kind = "COMMITMENT"
    rel = _rel_for_connector(conn_t)
    return {
        "evidence_id": str(uuid.uuid4()),
        "owner_id": owner_id,
        "project_id": row.get("project_id") or None,
        "source_kind": kind,
        "source_id": cid[:128],
        "source_record_id": rec[:128],
        "connector_type": conn_t[:32],
        "reliability_class": rel,
        "strength": _strength(kind, conn_t),
        "privacy_class": str(row.get("privacy_class") or "PRIVATE"),
        "occurred_at": row.get("source_ts") or row.get("due_at"),
        "valid_until": row.get("valid_until"),
        "independence_key": independence_key(owner_id, kind, conn_t, rec, sha),
        "transform_id": "cite_v624",
        "transform_version": RULE_VERSION,
        "idempotency_key": idempotency_key(owner_id, kind, cid, sha),
        "content_sha256": sha[:64],
        "provenance": {"source_id": cid[:64]},
    }


def cite_from_fact(row: Dict[str, Any], owner_id: str = OWNER_ID) -> Optional[Dict[str, Any]]:
    fid = str(row.get("fact_id") or "")
    if not fid:
        return None
    sha = str(row.get("content_sha256") or fid)
    conn_t = str(row.get("connector_type") or "")
    rec = str(row.get("source_record_id") or fid)
    fk = str(row.get("fact_kind") or "")
    kind = "EXTERNAL_FACT"
    rel = _rel_for_connector(conn_t)
    if fk in ("GH_REVIEW", "GH_PR"):
        rel = "github"
    elif fk == "CAL_EVENT":
        rel = "calendar"
    return {
        "evidence_id": str(uuid.uuid4()),
        "owner_id": owner_id,
        "project_id": row.get("project_id") or None,
        "source_kind": kind,
        "source_id": fid[:128],
        "source_record_id": rec[:128],
        "connector_type": conn_t[:32],
        "reliability_class": rel,
        "strength": _strength(kind, conn_t, fk),
        "privacy_class": str(row.get("privacy_class") or "NORMAL"),
        "occurred_at": row.get("occurred_at") or row.get("start_ts"),
        "valid_until": row.get("valid_until") or row.get("end_ts"),
        "independence_key": independence_key(owner_id, kind, conn_t, rec, sha),
        "transform_id": "cite_v624",
        "transform_version": RULE_VERSION,
        "idempotency_key": idempotency_key(owner_id, kind, fid, sha),
        "content_sha256": sha[:64],
        "provenance": {"source_id": fid[:64], "fact_kind": fk[:32]},
    }


def cite_from_task(row: Dict[str, Any], owner_id: str = OWNER_ID) -> Optional[Dict[str, Any]]:
    tid = str(row.get("task_id") or "")
    if not tid:
        return None
    status = str(row.get("status") or "")
    sha = hashlib.sha256(f"{tid}|{status}".encode("utf-8")).hexdigest()
    kind = "TASK_CHECKPOINT"
    rec = tid
    return {
        "evidence_id": str(uuid.uuid4()),
        "owner_id": owner_id,
        "project_id": row.get("project_id") or None,
        "source_kind": kind,
        "source_id": tid[:128],
        "source_record_id": rec[:128],
        "connector_type": "task_engine",
        "reliability_class": "task_engine",
        "strength": 1.00,
        "privacy_class": "NORMAL",
        "occurred_at": row.get("updated_at") or row.get("occurred_at"),
        "valid_until": None,
        "independence_key": independence_key(owner_id, kind, "task_engine", rec, sha),
        "transform_id": "cite_v624",
        "transform_version": RULE_VERSION,
        "idempotency_key": idempotency_key(owner_id, kind, tid, sha),
        "content_sha256": sha[:64],
        "provenance": {"source_id": tid[:64], "status": status[:40]},
    }
