"""Read-only bounded audit queries. No SQL. No caller predicates."""

from __future__ import annotations

from typing import Tuple

from orchestration.audit.errors import AuditNotFound
from orchestration.audit.recorder import stored_events, stored_index
from orchestration.audit.types import MAX_ID_CHARS, MAX_QUERY_RESULTS, AuditEvent
from proactive.config import is_v8_audit_enabled


def get_event(event_id: str, owner_id: str) -> AuditEvent:
    if not is_v8_audit_enabled():
        raise AuditNotFound()
    eid = str(event_id or "")
    owner = str(owner_id or "")
    if not owner or len(owner) > MAX_ID_CHARS or len(eid) > MAX_ID_CHARS:
        raise AuditNotFound()
    event = stored_index().get(eid)
    if event is None or event.owner_id != owner:
        raise AuditNotFound()
    return event


def list_events(
    owner_id: str,
    goal_id: str = "",
    task_id: str = "",
    limit: int = MAX_QUERY_RESULTS,
) -> Tuple[AuditEvent, ...]:
    if not is_v8_audit_enabled():
        return ()
    owner = str(owner_id or "")
    if not owner or len(owner) > MAX_ID_CHARS:
        return ()
    try:
        cap = int(limit)
    except (TypeError, ValueError):
        cap = MAX_QUERY_RESULTS
    if cap < 0:
        cap = 0
    if cap > MAX_QUERY_RESULTS:
        cap = MAX_QUERY_RESULTS
    goal = str(goal_id or "")
    task = str(task_id or "")
    rows = []
    for event in stored_events():
        if event.owner_id != owner:
            continue
        if goal and event.goal_id != goal:
            continue
        if task and event.task_id != task:
            continue
        rows.append(event)
    rows.sort(key=lambda e: (e.timestamp_unix_ms, e.sequence, e.event_id))
    return tuple(rows[:cap])
