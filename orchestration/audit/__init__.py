"""V8.9 explainability and audit. Descriptive only. Never executes or authorizes."""

from orchestration.audit.codes import AuditEventCode, AuditPhase, AuditReasonCode
from orchestration.audit.errors import AuditNotFound, AuditUnavailable, AuditValidationError
from orchestration.audit.query import get_event, list_events
from orchestration.audit.recorder import record_event, reset_audit_for_tests
from orchestration.audit.types import (
    MAX_AUDIT_EVENTS,
    MAX_QUERY_RESULTS,
    AuditEvent,
)

__all__ = [
    "MAX_AUDIT_EVENTS",
    "MAX_QUERY_RESULTS",
    "AuditEvent",
    "AuditEventCode",
    "AuditNotFound",
    "AuditPhase",
    "AuditReasonCode",
    "AuditUnavailable",
    "AuditValidationError",
    "get_event",
    "list_events",
    "record_event",
    "reset_audit_for_tests",
]
