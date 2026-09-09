"""HUD/OS delivery. No TTS. Idempotent. Retry-limited. Redacted cards only.

PostgreSQL proactive_deliveries is authoritative. The in-memory HUD ring is a
best-effort cache for the delivering process only and is not a second store.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List

from proactive.config import OWNER_ID, TTS_PROACTIVE_ALLOWED
from proactive.otp import emit_proactive
from proactive.store import proactive_store
from proactive.templates import render_inform

_hud: List[Dict[str, Any]] = []
_lock = threading.Lock()
MAX_HUD = 50


def deliver_inform(insight) -> bool:
    """True iff a delivery row exists after this call (newly created or already present).

    HUD/WS side effects run only when a *new* delivery row is created.
    Duplicate concurrent callers: unique (insight_id, channel) ⇒ one row.
    """
    if TTS_PROACTIVE_ALLOWED:
        return False
    did, created = proactive_store.upsert_delivery(insight.insight_id, "hud")
    if not did:
        emit_proactive(
            "proactive.delivery",
            status="error",
            attributes={"insight_id": (insight.insight_id or "")[:36], "reason": "persist_failed"},
        )
        return False
    if not created:
        emit_proactive(
            "proactive.delivery",
            status="skipped",
            attributes={"insight_id": insight.insight_id[:36], "reason": "duplicate"},
        )
        return True
    if created:
        try:
            from proactive.attention import record_inform
            record_inform(
                getattr(insight, "dedupe_key", "") or insight.insight_id,
                getattr(insight, "owner_id", None) or OWNER_ID,
            )
        except Exception:
            pass
        card = {
        "type": "proactive_event",
        "insight_id": insight.insight_id,
        "intervention": "INFORM",
        "template_id": insight.template_id,
        "message": render_inform(insight.template_id, {**insight.safe_params, "entity_id": insight.entity_id, "insight_type": insight.insight_type}),
        "urgency": insight.urgency,
        "privacy_class": insight.privacy_class,
        "proactive_cycle_id": insight.proactive_cycle_id,
        "tts": False,
    }
    with _lock:
        if not any(c.get("insight_id") == insight.insight_id for c in _hud):
            _hud.insert(0, card)
            del _hud[MAX_HUD:]
    emit_proactive(
        "proactive.delivery",
        attributes={
            "insight_id": insight.insight_id[:36],
            "intervention": "INFORM",
            "privacy_class": insight.privacy_class,
        },
    )
    try:
        from dashboard.server import dashboard_loop, connected_clients
        import asyncio
        import json
        if dashboard_loop:
            for client in list(connected_clients):
                try:
                    asyncio.run_coroutine_threadsafe(
                        client.send_text(json.dumps(card)),
                        dashboard_loop,
                    )
                except Exception:
                    pass
    except Exception:
        pass
    return True


def deliver_suggest(suggestion: Dict[str, Any]) -> bool:
    """NORMAL HUD/WS only. Persistence+DELIVERED commit before WS. Do not touch INFORM _hud."""
    if TTS_PROACTIVE_ALLOWED:
        return False
    if str(suggestion.get("privacy_class") or "") != "NORMAL":
        return False
    sid = str(suggestion.get("suggestion_id") or "")
    owner = str(suggestion.get("owner_id") or OWNER_ID)
    if not sid:
        return False
    did, created = proactive_store.persist_normal_suggestion_delivery(sid, owner)
    if not did:
        emit_proactive(
            "proactive.suggestion.delivered",
            status="error",
            attributes={"suggestion_id": sid[:36], "reason": "persist_failed"},
        )
        return False
    if not created:
        emit_proactive(
            "proactive.suggestion.delivered",
            status="skipped",
            attributes={"suggestion_id": sid[:36], "reason": "duplicate"},
        )
        return True
    try:
        from proactive.attention import record_suggest
        record_suggest(str(suggestion.get("fingerprint") or sid), owner)
    except Exception:
        pass
    from proactive.suggest_templates import render_suggest
    card = {
        "type": "proactive_suggestion",
        "suggestion_id": sid,
        "intervention": "SUGGEST",
        "template_id": suggestion.get("template_id"),
        "message": render_suggest(str(suggestion.get("template_id") or ""), suggestion.get("safe_params") or {}),
        "priority": suggestion.get("priority") or "MEDIUM",
        "privacy_class": "NORMAL",
        "tts": False,
    }
    emit_proactive(
        "proactive.suggestion.delivered",
        attributes={
            "suggestion_id": sid[:36],
            "suggestion_type": str(suggestion.get("suggestion_type") or "")[:40],
            "privacy_class": "NORMAL",
        },
    )
    try:
        from dashboard.server import dashboard_loop, connected_clients
        import asyncio
        import json
        if dashboard_loop:
            for client in list(connected_clients):
                try:
                    asyncio.run_coroutine_threadsafe(
                        client.send_text(json.dumps(card)),
                        dashboard_loop,
                    )
                except Exception:
                    pass
    except Exception:
        pass
    return True


def _ws_send(card: Dict[str, Any]) -> None:
    try:
        from dashboard.server import dashboard_loop, connected_clients
        import asyncio
        import json
        if dashboard_loop:
            for client in list(connected_clients):
                try:
                    asyncio.run_coroutine_threadsafe(
                        client.send_text(json.dumps(card)),
                        dashboard_loop,
                    )
                except Exception:
                    pass
    except Exception:
        pass


def deliver_prepare(preparation: Dict[str, Any]) -> bool:
    if TTS_PROACTIVE_ALLOWED:
        return False
    if str(preparation.get("privacy_class") or "") != "NORMAL":
        return False
    pid = str(preparation.get("preparation_id") or "")
    owner = str(preparation.get("owner_id") or OWNER_ID)
    if not pid:
        return False
    did, created = proactive_store.persist_prepare_delivery(pid, owner)
    if not did:
        return False
    if not created:
        return True
    try:
        from proactive.attention import record_prepare
        record_prepare(str(preparation.get("fingerprint") or pid), owner)
    except Exception:
        pass
    from proactive.prepare_templates import render_prepare
    card = {
        "type": "proactive_preparation",
        "preparation_id": pid,
        "suggestion_id": preparation.get("suggestion_id"),
        "action_type": preparation.get("action_type"),
        "template_id": preparation.get("template_id"),
        "message": render_prepare(str(preparation.get("template_id") or "")),
        "disclaimer": "PREPARED — NOT CARRIED OUT. Approval does not run any action.",
        "privacy_class": "NORMAL",
        "tts": False,
    }
    _ws_send(card)
    return True


def deliver_ask(approval: Dict[str, Any], preparation: Dict[str, Any] | None = None) -> bool:
    if TTS_PROACTIVE_ALLOWED:
        return False
    if str(approval.get("privacy_class") or "") != "NORMAL":
        return False
    aid = str(approval.get("approval_id") or "")
    owner = str(approval.get("owner_id") or OWNER_ID)
    if not aid:
        return False
    did, created = proactive_store.persist_ask_delivery(aid, owner)
    if not did:
        return False
    if not created:
        return True
    try:
        from proactive.attention import record_ask
        record_ask(str((preparation or {}).get("fingerprint") or aid), owner)
    except Exception:
        pass
    prep = preparation or {}
    card = {
        "type": "proactive_ask",
        "approval_id": aid,
        "preparation_id": approval.get("preparation_id"),
        "suggestion_id": prep.get("suggestion_id"),
        "action_type": approval.get("action_type"),
        "risk_class": approval.get("risk_class"),
        "privacy_class": "NORMAL",
        "valid_until": approval.get("valid_until"),
        "binding_hash": approval.get("binding_hash"),
        "message": "Prepared for later review. Approval does not run any action.",
        "tts": False,
    }
    _ws_send(card)
    return True


def deliver_authorization(approval: Dict[str, Any]) -> bool:
    if TTS_PROACTIVE_ALLOWED:
        return False
    if str(approval.get("privacy_class") or "") != "NORMAL":
        return False
    card = {
        "type": "proactive_authorization",
        "approval_id": approval.get("approval_id"),
        "preparation_id": approval.get("preparation_id"),
        "message": "Authorization recorded. Approval does not run any action. Nothing was sent or changed.",
        "tts": False,
    }
    _ws_send(card)
    return True


def hud_cards() -> List[Dict[str, Any]]:
    with _lock:
        return list(_hud)


def clear_hud() -> None:
    with _lock:
        _hud.clear()
