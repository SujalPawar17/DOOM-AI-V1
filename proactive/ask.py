"""V6.2.6 ASK create/expire. Worker-safe. Does not approve or run actions."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Dict

from proactive.config import (
    OWNER_ID,
    PREPARE_RULE_VERSION,
    WORKER_CSRF_BINDING_ID,
    ask_ttl_seconds,
    is_ask_enabled,
    is_prepare_enabled,
    is_prediction_enabled,
    is_proactive_enabled,
    is_suggest_enabled,
)
from proactive.otp import emit_proactive
from proactive.store import proactive_store


def flags_ask_on() -> bool:
    return bool(
        is_proactive_enabled()
        and is_prediction_enabled()
        and is_suggest_enabled()
        and is_prepare_enabled()
        and is_ask_enabled()
    )


def ask_deadline(preparation: Dict[str, Any], now: float) -> float:
    try:
        pvu = float(preparation.get("valid_until") or (now + 3600))
    except (TypeError, ValueError):
        pvu = now + 3600
    return min(pvu, now + float(ask_ttl_seconds()))


def _worker_binding_hash(preparation: Dict[str, Any], owner_id: str, vu: float) -> str:
    raw = (
        f"{owner_id}|{preparation.get('preparation_id')}|{preparation.get('action_type')}|"
        f"{preparation.get('param_hash')}|{preparation.get('risk_class')}|"
        f"{preparation.get('privacy_class')}|{int(vu)}|"
        f"{preparation.get('rule_version') or PREPARE_RULE_VERSION}|{WORKER_CSRF_BINDING_ID}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def flags_ask_on() -> bool:
    return bool(
        is_proactive_enabled()
        and is_prediction_enabled()
        and is_suggest_enabled()
        and is_prepare_enabled()
        and is_ask_enabled()
    )


def ask_deadline(preparation: Dict[str, Any], now: float) -> float:
    try:
        pvu = float(preparation.get("valid_until") or (now + 3600))
    except (TypeError, ValueError):
        pvu = now + 3600
    return min(pvu, now + float(ask_ttl_seconds()))


def ensure_pending_ask(preparation: Dict[str, Any], owner_id: str = OWNER_ID) -> str:
    if not flags_ask_on():
        return ""
    if str(preparation.get("future_act_class") or preparation.get("action_type") or "") in ("NONE", ""):
        if str(preparation.get("action_type") or "NONE") == "NONE":
            return ""
    if str(preparation.get("action_type") or "NONE") == "NONE":
        return ""
    if str(preparation.get("future_act_class") or "") != "MUTATION":
        return ""
    now = time.time()
    vu = ask_deadline(preparation, now)
    csrf_binding_id = WORKER_CSRF_BINDING_ID
    bhash = _worker_binding_hash(preparation, owner_id, vu)
    aid = proactive_store.insert_approval_request(
        {
            "owner_id": owner_id,
            "preparation_id": preparation.get("preparation_id"),
            "action_type": preparation.get("action_type"),
            "param_hash": preparation.get("param_hash"),
            "binding_hash": bhash,
            "csrf_binding_id": csrf_binding_id,
            "risk_class": preparation.get("risk_class"),
            "privacy_class": preparation.get("privacy_class"),
            "valid_until": vu,
            "rule_version": preparation.get("rule_version") or PREPARE_RULE_VERSION,
        }
    )
    if not aid:
        return ""
    emit_proactive(
        "proactive.ask.requested",
        attributes={
            "approval_id": aid[:36],
            "preparation_id": str(preparation.get("preparation_id") or "")[:36],
            "action_type": str(preparation.get("action_type") or "")[:40],
        },
    )
    live = proactive_store.get_approval(aid, owner_id) or {}
    priv = str(live.get("privacy_class") or "NORMAL")
    if priv == "NORMAL":
        from proactive.attention import may_ask
        from proactive.delivery import deliver_ask
        fp = str(preparation.get("fingerprint") or aid)
        ok, _why = may_ask(fp, owner_id)
        if ok:
            deliver_ask(live, preparation)
    return aid


def expire_pending_asks(owner_id: str = OWNER_ID) -> int:
    try:
        n = proactive_store.expire_pending_asks(owner_id)
        if n:
            emit_proactive(
                "proactive.ask.expired",
                attributes={"reason": "ttl"},
            )
        return n
    except Exception:
        emit_proactive(
            "proactive.ask.expired",
            status="error",
            attributes={"reason": "ask_eval"},
        )
        return 0
