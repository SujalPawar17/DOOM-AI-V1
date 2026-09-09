"""V6.2.6 ASK decisions. Authorization only. Never runs actions."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Dict, Optional

from proactive.otp import emit_proactive
from proactive.store import proactive_store


def canonical_binding_string(
    owner_id: str,
    preparation_id: str,
    action_type: str,
    param_hash: str,
    risk_class: str,
    privacy_class: str,
    valid_until_epoch: int,
    rule_version: str,
    csrf_binding_id: str,
) -> str:
    return (
        f"{owner_id}|{preparation_id}|{action_type}|{param_hash}|"
        f"{risk_class}|{privacy_class}|{int(valid_until_epoch)}|{rule_version}|{csrf_binding_id}"
    )


def binding_hash_for(**kwargs) -> str:
    raw = canonical_binding_string(
        kwargs["owner_id"],
        kwargs["preparation_id"],
        kwargs["action_type"],
        kwargs["param_hash"],
        kwargs["risk_class"],
        kwargs["privacy_class"],
        int(kwargs["valid_until_epoch"]),
        kwargs["rule_version"],
        kwargs["csrf_binding_id"],
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hashes_match(a: str, b: str) -> bool:
    x = (a or "").encode("utf-8")
    y = (b or "").encode("utf-8")
    if len(x) != len(y):
        return False
    return hmac.compare_digest(x, y)


def _recompute(row: Dict[str, Any]) -> str:
    return binding_hash_for(
        owner_id=str(row.get("owner_id") or ""),
        preparation_id=str(row.get("preparation_id") or ""),
        action_type=str(row.get("action_type") or ""),
        param_hash=str(row.get("param_hash") or ""),
        risk_class=str(row.get("risk_class") or ""),
        privacy_class=str(row.get("privacy_class") or ""),
        valid_until_epoch=int(float(row.get("valid_until") or 0)),
        rule_version=str(row.get("rule_version") or "v626.1"),
        csrf_binding_id=str(row.get("csrf_binding_id") or ""),
    )


def decide(
    kind: str,
    approval_id: str,
    owner_id: str,
    session_id_hash: str,
    client_binding_hash: str,
) -> Dict[str, Any]:
    """kind: approve | reject | revoke. No tools."""
    return proactive_store.decide_approval(
        kind,
        approval_id,
        owner_id,
        session_id_hash,
        client_binding_hash,
        recompute=_recompute,
        hashes_match=hashes_match,
        emit=emit_proactive,
    )


def cancel_preparation(preparation_id: str, owner_id: str) -> Dict[str, Any]:
    ok = proactive_store.cancel_preparation(preparation_id, owner_id)
    if ok:
        emit_proactive(
            "proactive.prepare.cancelled",
            attributes={"preparation_id": preparation_id[:36]},
        )
        return {"ok": True}
    return {"ok": False, "error": "not_found"}
