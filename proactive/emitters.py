"""V6.2.1 internal observational emitters. Thin wrappers around ingest_signal.

Never persist memory, never call tools/LLM, never raise to callers.
Payloads are status/id metadata only.
"""

from __future__ import annotations

import re
from typing import Optional

from proactive.ingest import ingest_signal

_TOKEN = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")


def _token(value: str) -> str:
    s = str(value or "").strip().upper()
    if not _TOKEN.match(s):
        return ""
    return s


def emit_task_status(entity_id: str, status: str, **_ignored) -> str:
    """TASK_STATUS from TaskEngine. Payload is {status} only. extra_idem=status."""
    try:
        tid = str(entity_id or "").strip()[:120]
        st = _token(status)
        if not tid or not st:
            return ""
        return ingest_signal(
            signal_type="TASK_STATUS",
            source="task_engine",
            entity_type="task",
            entity_id=tid,
            payload={"status": st},
            privacy_class="NORMAL",
            extra_idem=st,
        )
    except Exception:
        return ""


def emit_lifecycle(
    memory_id: str,
    event_id: str,
    event_type: str,
    related_memory_id: Optional[str] = None,
    **_ignored,
) -> str:
    """MEMORY_LIFECYCLE after audit persist. IDs and event_type only."""
    try:
        mid = str(memory_id or "").strip()[:120]
        eid = str(event_id or "").strip()[:120]
        et = _token(event_type)
        if not mid or not eid or not et:
            return ""
        payload = {"event_type": et, "event_id": eid[:64]}
        rel = str(related_memory_id or "").strip()[:120]
        if rel:
            payload["related_memory_id"] = rel
        return ingest_signal(
            signal_type="MEMORY_LIFECYCLE",
            source="lifecycle",
            entity_type="memory",
            entity_id=mid,
            payload=payload,
            privacy_class="NORMAL",
            extra_idem=eid,
        )
    except Exception:
        return ""


def emit_experience(experience_id: str, outcome_status: str, **_ignored) -> str:
    """EXPERIENCE_CREATED API. No production hook in V6.2.1 (do not invent events)."""
    try:
        xid = str(experience_id or "").strip()[:120]
        st = _token(outcome_status)
        if not xid or not st:
            return ""
        return ingest_signal(
            signal_type="EXPERIENCE_CREATED",
            source="experiences",
            entity_type="experience",
            entity_id=xid,
            payload={"outcome_status": st},
            privacy_class="NORMAL",
            extra_idem=xid + "|" + st,
        )
    except Exception:
        return ""
