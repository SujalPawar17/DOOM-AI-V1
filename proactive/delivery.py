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


def hud_cards() -> List[Dict[str, Any]]:
    with _lock:
        return list(_hud)


def clear_hud() -> None:
    with _lock:
        _hud.clear()
