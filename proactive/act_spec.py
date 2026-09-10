"""V6.3 Action Specification. Deterministic hash. No LLM payloads."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from proactive.act_policy import (
    CAL_SUMMARY_TEMPLATE_ID,
    CAP_CAL_HOLD,
    CAP_INTERNAL,
    NOTE_TEMPLATE_ID,
    capability,
    capability_enabled,
    raise_risk,
)
from proactive.config import ACT_POLICY_VERSION, OWNER_ID, ask_ttl_seconds

_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})$"
)
_CAL_ID = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")
_CLAIM = re.compile(r"^[A-Za-z0-9._:-]{1,40}$")


def canonical_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def compute_action_hash(
    owner_id: str,
    preparation_id: str,
    capability_id: str,
    action_type: str,
    target_ref: Dict[str, Any],
    exec_params: Dict[str, Any],
    param_hash: str,
    risk_class: str,
    privacy_class: str,
    policy_version: str = ACT_POLICY_VERSION,
) -> str:
    blob = {
        "owner_id": str(owner_id or ""),
        "preparation_id": str(preparation_id or ""),
        "capability_id": str(capability_id or ""),
        "action_type": str(action_type or ""),
        "target_ref": target_ref if isinstance(target_ref, dict) else {},
        "exec_params": exec_params if isinstance(exec_params, dict) else {},
        "param_hash": str(param_hash or ""),
        "risk_class": str(risk_class or ""),
        "privacy_class": str(privacy_class or ""),
        "policy_version": str(policy_version or ACT_POLICY_VERSION),
    }
    return hashlib.sha256(canonical_json(blob).encode("utf-8")).hexdigest()


def idempotency_key(owner_id: str, capability_id: str, action_hash: str) -> str:
    raw = f"{owner_id}|{capability_id}|{action_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _claim_code(preparation: Dict[str, Any]) -> str:
    params = preparation.get("safe_params") if isinstance(preparation.get("safe_params"), dict) else {}
    c = str(params.get("claim_code") or "").strip()[:40]
    if not _CLAIM.match(c):
        return ""
    return c


def build_internal_note(preparation: Dict[str, Any], owner_id: str = OWNER_ID) -> Optional[Dict[str, Any]]:
    if not capability_enabled(CAP_INTERNAL):
        return None
    if str(preparation.get("privacy_class") or "") == "SENSITIVE":
        return None
    if str(preparation.get("action_type") or "") != "FUTURE_TASK_NOTE":
        return None
    cap = capability(CAP_INTERNAL) or {}
    claim = _claim_code(preparation)
    if not claim:
        return None
    exec_params = {"claim_code": claim, "note_template_id": NOTE_TEMPLATE_ID}
    target_ref = {"kind": "internal_ledger"}
    ph = str(preparation.get("param_hash") or "")[:64]
    risk = raise_risk(str(cap.get("risk_floor") or "LOW"), str(preparation.get("risk_class") or "NONE"))
    priv = str(preparation.get("privacy_class") or "NORMAL")
    if priv == "SENSITIVE":
        return None
    atype = str(cap.get("action_type") or "FUTURE_TASK_NOTE")
    pid = str(preparation.get("preparation_id") or "")
    ah = compute_action_hash(owner_id, pid, CAP_INTERNAL, atype, target_ref, exec_params, ph, risk, priv)
    now = time.time()
    vu = min(float(preparation.get("valid_until") or (now + 3600)), now + float(ask_ttl_seconds()))
    return {
        "action_id": str(uuid.uuid4())[:64],
        "owner_id": owner_id,
        "preparation_id": pid,
        "approval_id": "",
        "capability_id": CAP_INTERNAL,
        "action_type": atype,
        "target_ref": target_ref,
        "exec_params": exec_params,
        "param_hash": ph,
        "action_hash": ah,
        "risk_class": risk,
        "privacy_class": priv,
        "reversibility": cap.get("reversibility") or "REVERSIBLE",
        "idempotency_key": idempotency_key(owner_id, CAP_INTERNAL, ah),
        "policy_version": ACT_POLICY_VERSION,
        "valid_until": vu,
        "provenance": {"evidence_ids": []},
        "status": "READY",
    }


def build_calendar_hold(preparation: Dict[str, Any], owner_id: str = OWNER_ID) -> Optional[Dict[str, Any]]:
    if not capability_enabled(CAP_CAL_HOLD):
        return None
    if str(preparation.get("privacy_class") or "NORMAL") != "NORMAL":
        return None
    if str(preparation.get("action_type") or "") != "FUTURE_CAL_RECONCILE":
        return None
    params = preparation.get("safe_params") if isinstance(preparation.get("safe_params"), dict) else {}
    start = str(params.get("start_rfc3339") or "").strip()
    end = str(params.get("end_rfc3339") or "").strip()
    cal = str(params.get("calendar_id") or "primary").strip() or "primary"
    if not _RFC3339.match(start) or not _RFC3339.match(end):
        return None
    if not _CAL_ID.match(cal):
        return None
    if str(params.get("summary_template_id") or CAL_SUMMARY_TEMPLATE_ID) != CAL_SUMMARY_TEMPLATE_ID:
        return None
    cap = capability(CAP_CAL_HOLD) or {}
    exec_params = {
        "calendar_id": cal,
        "start_rfc3339": start,
        "end_rfc3339": end,
        "summary_template_id": CAL_SUMMARY_TEMPLATE_ID,
    }
    target_ref = {"kind": "google_calendar", "calendar_id": cal}
    ph = str(preparation.get("param_hash") or "")[:64]
    risk = raise_risk(str(cap.get("risk_floor") or "MEDIUM"), str(preparation.get("risk_class") or "NONE"))
    priv = "NORMAL"
    atype = str(cap.get("action_type") or "FUTURE_CAL_RECONCILE")
    pid = str(preparation.get("preparation_id") or "")
    ah = compute_action_hash(owner_id, pid, CAP_CAL_HOLD, atype, target_ref, exec_params, ph, risk, priv)
    now = time.time()
    vu = min(float(preparation.get("valid_until") or (now + 3600)), now + float(ask_ttl_seconds()))
    return {
        "action_id": str(uuid.uuid4())[:64],
        "owner_id": owner_id,
        "preparation_id": pid,
        "approval_id": "",
        "capability_id": CAP_CAL_HOLD,
        "action_type": atype,
        "target_ref": target_ref,
        "exec_params": exec_params,
        "param_hash": ph,
        "action_hash": ah,
        "risk_class": risk,
        "privacy_class": priv,
        "reversibility": cap.get("reversibility") or "PARTIALLY_REVERSIBLE",
        "idempotency_key": idempotency_key(owner_id, CAP_CAL_HOLD, ah),
        "policy_version": ACT_POLICY_VERSION,
        "valid_until": vu,
        "provenance": {"evidence_ids": []},
        "status": "READY",
    }


def materialize_for_preparation(preparation: Dict[str, Any], owner_id: str = OWNER_ID) -> Optional[Dict[str, Any]]:
    """Refuse if required fields missing. Never use draft prose."""
    at = str(preparation.get("action_type") or "")
    if at == "FUTURE_TASK_NOTE":
        return build_internal_note(preparation, owner_id)
    if at == "FUTURE_CAL_RECONCILE":
        return build_calendar_hold(preparation, owner_id)
    return None


def reject_client_hash(row: Dict[str, Any], client_hash: str) -> Tuple[bool, str]:
    expected = compute_action_hash(
        str(row.get("owner_id") or ""),
        str(row.get("preparation_id") or ""),
        str(row.get("capability_id") or ""),
        str(row.get("action_type") or ""),
        row.get("target_ref") if isinstance(row.get("target_ref"), dict) else {},
        row.get("exec_params") if isinstance(row.get("exec_params"), dict) else {},
        str(row.get("param_hash") or ""),
        str(row.get("risk_class") or ""),
        str(row.get("privacy_class") or ""),
        str(row.get("policy_version") or ACT_POLICY_VERSION),
    )
    got = str(client_hash or "")
    if got and got != expected:
        return False, "action_hash_mismatch"
    stored = str(row.get("action_hash") or "")
    if stored != expected:
        return False, "action_hash_mismatch"
    return True, expected
