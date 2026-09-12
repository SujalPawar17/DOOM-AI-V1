"""In-process bounded audit recorder. Observes only. Never executes or authorizes."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from orchestration.audit.codes import AuditEventCode, AuditPhase, AuditReasonCode
from orchestration.audit.errors import AuditValidationError
from orchestration.audit.formatter import explain
from orchestration.audit.redaction import redact_text
from orchestration.audit.types import (
    MAX_AUDIT_EVENTS,
    MAX_HASH_CHARS,
    MAX_ID_CHARS,
    MAX_METADATA_KEYS,
    MAX_METADATA_VALUE_CHARS,
    AuditEvent,
)
from proactive.config import is_v8_audit_enabled

_LOCK = threading.Lock()
_BUFFER: Deque[AuditEvent] = deque()
_INDEX: Dict[str, AuditEvent] = {}
_SEQ = 0

_PHASE_FOR_CODE = {
    AuditEventCode.GOAL_ACCEPTED: AuditPhase.GOAL,
    AuditEventCode.GOAL_REJECTED: AuditPhase.GOAL,
    AuditEventCode.CONTEXT_RETRIEVED: AuditPhase.CONTEXT,
    AuditEventCode.CONTEXT_EMPTY: AuditPhase.CONTEXT,
    AuditEventCode.CONTEXT_UNAVAILABLE: AuditPhase.CONTEXT,
    AuditEventCode.PLAN_CREATED: AuditPhase.PLANNING,
    AuditEventCode.PLAN_REJECTED: AuditPhase.PLANNING,
    AuditEventCode.PLAN_HASH_MISMATCH: AuditPhase.VALIDATION,
    AuditEventCode.VALIDATION_FAILED: AuditPhase.VALIDATION,
    AuditEventCode.APPROVAL_REQUIRED: AuditPhase.APPROVAL,
    AuditEventCode.APPROVAL_ACCEPTED: AuditPhase.APPROVAL,
    AuditEventCode.APPROVAL_REJECTED: AuditPhase.APPROVAL,
    AuditEventCode.TASK_CREATED: AuditPhase.TASK,
    AuditEventCode.TASK_STARTED: AuditPhase.TASK,
    AuditEventCode.TASK_WAITING: AuditPhase.TASK,
    AuditEventCode.STEP_STARTED: AuditPhase.EXECUTION,
    AuditEventCode.STEP_COMPLETED: AuditPhase.EXECUTION,
    AuditEventCode.STEP_FAILED: AuditPhase.EXECUTION,
    AuditEventCode.STEP_BLOCKED: AuditPhase.EXECUTION,
    AuditEventCode.VERIFICATION_PASSED: AuditPhase.VERIFICATION,
    AuditEventCode.VERIFICATION_FAILED: AuditPhase.VERIFICATION,
    AuditEventCode.NOT_VERIFIED: AuditPhase.VERIFICATION,
    AuditEventCode.RECOVERY_PROPOSED: AuditPhase.RECOVERY,
    AuditEventCode.RECOVERY_BLOCKED: AuditPhase.RECOVERY,
    AuditEventCode.RECOVERY_COMPLETED: AuditPhase.RECOVERY,
    AuditEventCode.TASK_COMPLETED: AuditPhase.RESULT,
    AuditEventCode.TASK_FAILED: AuditPhase.RESULT,
    AuditEventCode.TASK_CANCELLED: AuditPhase.RESULT,
    AuditEventCode.TASK_ABORTED: AuditPhase.RESULT,
    AuditEventCode.EMERGENCY_STOPPED: AuditPhase.RESULT,
}


def reset_audit_for_tests() -> None:
    global _SEQ
    with _LOCK:
        _BUFFER.clear()
        _INDEX.clear()
        _SEQ = 0


def record_event(
    *,
    owner_id: str,
    event_code: AuditEventCode,
    reason_code: AuditReasonCode,
    outcome: str,
    session_id: str = "",
    goal_id: str = "",
    task_id: str = "",
    plan_hash: str = "",
    recovery_plan_hash: str = "",
    step_id: str = "",
    phase: Optional[AuditPhase] = None,
    explanation: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    timestamp_unix_ms: int = 0,
) -> Optional[AuditEvent]:
    """Record a descriptive event for a decision that already occurred."""
    if not is_v8_audit_enabled():
        return None
    if callable(metadata) or callable(explanation) or callable(event_code):
        raise AuditValidationError("CALLABLE_REJECTED")
    code = event_code if isinstance(event_code, AuditEventCode) else AuditEventCode(str(event_code))
    reason = reason_code if isinstance(reason_code, AuditReasonCode) else AuditReasonCode(str(reason_code))
    owner = _bound_id(owner_id, "owner_id")
    if not owner:
        raise AuditValidationError("OWNER_REQUIRED")
    meta = _freeze_metadata(metadata)
    text = explain(code, reason, str(outcome or ""), explanation)
    now = int(timestamp_unix_ms) if int(timestamp_unix_ms or 0) > 0 else int(time.time() * 1000)
    with _LOCK:
        global _SEQ
        _SEQ += 1
        seq = _SEQ
        eid = "a%08d" % seq
        event = AuditEvent(
            event_id=eid,
            sequence=seq,
            timestamp_unix_ms=now,
            owner_id=owner,
            session_id=_bound_id(session_id, "session_id"),
            goal_id=_bound_id(goal_id, "goal_id"),
            task_id=_bound_id(task_id, "task_id"),
            plan_hash=_bound_hash(plan_hash),
            recovery_plan_hash=_bound_hash(recovery_plan_hash),
            step_id=_bound_id(step_id, "step_id"),
            phase=phase or _PHASE_FOR_CODE[code],
            event_code=code,
            outcome=str(outcome or "")[:64],
            reason_code=reason,
            explanation=text,
            metadata=meta,
        )
        _BUFFER.append(event)
        _INDEX[eid] = event
        while len(_BUFFER) > MAX_AUDIT_EVENTS:
            old = _BUFFER.popleft()
            _INDEX.pop(old.event_id, None)
        return event


def _bound_id(value: str, name: str) -> str:
    text = str(value or "").replace("\x00", "")
    if len(text) > MAX_ID_CHARS:
        raise AuditValidationError("IDENTIFIER_TOO_LONG")
    return text


def _bound_hash(value: str) -> str:
    text = str(value or "").replace("\x00", "")
    if len(text) > MAX_HASH_CHARS:
        raise AuditValidationError("HASH_TOO_LONG")
    return text


def _freeze_metadata(raw: Optional[Dict[str, Any]]) -> Tuple[Tuple[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise AuditValidationError("INVALID_METADATA")
    pairs = sorted(((str(k), raw[k]) for k in raw.keys()), key=lambda p: p[0])
    items: List[Tuple[str, str]] = []
    for key, val in pairs:
        if callable(val):
            raise AuditValidationError("CALLABLE_REJECTED")
        if isinstance(val, (dict, list, tuple, set)):
            raise AuditValidationError("INVALID_METADATA")
        redacted, _ = redact_text(str(val), MAX_METADATA_VALUE_CHARS)
        items.append((key[:64], redacted))
        if len(items) >= MAX_METADATA_KEYS:
            break
    return tuple(items)


def stored_events() -> Tuple[AuditEvent, ...]:
    with _LOCK:
        return tuple(_BUFFER)


def stored_index() -> Dict[str, AuditEvent]:
    with _LOCK:
        return dict(_INDEX)
