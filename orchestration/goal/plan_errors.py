"""V8.2 plan validation errors. Fail closed. Not execution."""

from __future__ import annotations


class PlanValidationError(ValueError):
    def __init__(self, code: str, message: str = ""):
        self.code = str(code)
        super().__init__(message or code)
