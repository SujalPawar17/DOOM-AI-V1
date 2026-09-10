"""Computer session start/list/get. Observe-only. No EXECUTING."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from proactive.computer.policy import PRIVACY_DEFAULT, observation_allowed, session_ttl_sec
from proactive.config import OWNER_ID
from proactive.store import proactive_store


def start_session(owner_id: str = OWNER_ID, privacy_class: str = PRIVACY_DEFAULT) -> Dict[str, Any]:
    if not observation_allowed():
        return {"ok": False, "enabled": False, "http": 403}
    owner = str(owner_id or "")[:64]
    if not owner:
        return {"ok": False, "error": "unauthenticated", "http": 401}
    privacy = str(privacy_class or PRIVACY_DEFAULT).upper()
    if privacy not in ("NORMAL", "PRIVATE", "SENSITIVE"):
        privacy = PRIVACY_DEFAULT
    sid = str(uuid.uuid4())
    row = proactive_store.insert_computer_session(
        session_id=sid,
        owner_id=owner,
        privacy_class=privacy,
        ttl_sec=session_ttl_sec(),
    )
    if not row:
        existing = proactive_store.get_observing_computer_session(owner)
        if existing:
            return {"ok": False, "error": "already_observing", "http": 409, "session_id": existing.get("session_id")}
        return {"ok": False, "error": "persist_failed", "http": 503}
    return {"ok": True, "session": row, "http": 200}


def list_sessions(owner_id: str = OWNER_ID, limit: int = 20) -> List[Dict[str, Any]]:
    return proactive_store.list_computer_sessions(str(owner_id)[:64], limit)


def get_session(session_id: str, owner_id: str = OWNER_ID) -> Optional[Dict[str, Any]]:
    return proactive_store.get_computer_session(str(session_id)[:64], str(owner_id)[:64])
