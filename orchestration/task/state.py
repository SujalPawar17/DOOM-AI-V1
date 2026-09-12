"""Central V8.6 task transition table. Invalid transitions fail closed."""

from __future__ import annotations

from orchestration.task.errors import InvalidTaskTransition
from orchestration.task.types import TaskState

ALLOWED_TRANSITIONS = {
    TaskState.CREATED: frozenset({TaskState.READY, TaskState.BLOCKED, TaskState.CANCELLED}),
    TaskState.READY: frozenset({TaskState.RUNNING, TaskState.BLOCKED, TaskState.CANCELLED, TaskState.WAITING}),
    TaskState.RUNNING: frozenset({
        TaskState.WAITING,
        TaskState.RECOVERING,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
        TaskState.ABORTED,
    }),
    TaskState.WAITING: frozenset({
        TaskState.RUNNING,
        TaskState.RECOVERING,
        TaskState.CANCELLED,
        TaskState.ABORTED,
        TaskState.FAILED,
        TaskState.BLOCKED,
    }),
    TaskState.RECOVERING: frozenset({
        TaskState.RUNNING,
        TaskState.WAITING,
        TaskState.FAILED,
        TaskState.BLOCKED,
        TaskState.CANCELLED,
        TaskState.ABORTED,
        TaskState.COMPLETED,
    }),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.BLOCKED: frozenset(),
    TaskState.CANCELLED: frozenset(),
    TaskState.ABORTED: frozenset(),
}


def ensure_transition(current: TaskState, nxt: TaskState) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if nxt not in allowed:
        raise InvalidTaskTransition("INVALID_TRANSITION")
