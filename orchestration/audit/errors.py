"""V8.9 audit errors. Descriptive only. Not authorization."""

from __future__ import annotations


class AuditError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AuditValidationError(AuditError):
    def __init__(self, code: str = "INVALID_EVENT") -> None:
        super().__init__(code)


class AuditNotFound(AuditError):
    def __init__(self) -> None:
        super().__init__("EVENT_NOT_FOUND")


class AuditUnavailable(AuditError):
    def __init__(self, code: str = "AUDIT_DISABLED") -> None:
        super().__init__(code)
