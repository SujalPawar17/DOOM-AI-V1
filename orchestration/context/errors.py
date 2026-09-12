"""V8.8 context errors. Retrieval failures are not execution or authorization."""

from __future__ import annotations


class ContextError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class InvalidContextRequest(ContextError):
    def __init__(self, code: str = "INVALID_REQUEST") -> None:
        super().__init__(code)


class ContextUnavailable(ContextError):
    def __init__(self, code: str = "CONTEXT_UNAVAILABLE") -> None:
        super().__init__(code)
