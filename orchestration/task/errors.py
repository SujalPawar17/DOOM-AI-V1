"""V8.6 in-process task bounds and errors. Non-durable. Not an executor."""

from __future__ import annotations


MAX_TASKS = 256
MAX_TRANSITIONS_PER_TASK = 64
MAX_TASK_LIFETIME_MS = 300000
MAX_REASON_CHARS = 64
MAX_ID_CHARS = 64


class TaskError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class InvalidTaskTransition(TaskError):
    def __init__(self, code: str = "INVALID_TRANSITION") -> None:
        super().__init__(code)


class TaskNotFound(TaskError):
    def __init__(self) -> None:
        super().__init__("TASK_NOT_FOUND")


class TaskCapacityExceeded(TaskError):
    def __init__(self) -> None:
        super().__init__("TASK_CAPACITY_EXCEEDED")


class TaskAlreadyStarted(TaskError):
    def __init__(self) -> None:
        super().__init__("TASK_ALREADY_STARTED")


class TaskAlreadyTerminal(TaskError):
    def __init__(self) -> None:
        super().__init__("TASK_ALREADY_TERMINAL")


class InvalidTaskPlan(TaskError):
    def __init__(self, code: str = "INVALID_TASK_PLAN") -> None:
        super().__init__(code)


class TaskTimeout(TaskError):
    def __init__(self) -> None:
        super().__init__("TASK_TIMEOUT")


class TaskCancellationError(TaskError):
    def __init__(self) -> None:
        super().__init__("TASK_CANCELLED")


class TaskTransitionLimitExceeded(TaskError):
    def __init__(self) -> None:
        super().__init__("TRANSITION_LIMIT_EXCEEDED")
