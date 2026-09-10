"""Emergency stop for computer observe sessions. No input hooks. No LLM."""

from __future__ import annotations

from typing import Any, Dict

from proactive.config import OWNER_ID
from proactive.store import proactive_store


def stop_session(session_id: str, owner_id: str = OWNER_ID) -> Dict[str, Any]:
    owner = str(owner_id or "")[:64]
    sid = str(session_id or "")[:64]
    if not owner or not sid:
        return {"ok": False, "error": "not_found", "http": 404}
    row = proactive_store.stop_computer_session(sid, owner)
    if not row:
        return {"ok": False, "error": "not_found", "http": 404}
    return {"ok": True, "session": row, "http": 200}
