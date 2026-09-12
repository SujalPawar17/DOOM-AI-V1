"""Immutable V8.6 task snapshots. Process-local. Not authorization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from orchestration.executor import ExecutionResult
from orchestration.recovery.types import RecoveryExecutionResult


class TaskState(str, Enum):
    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    ABORTED = "ABORTED"


TERMINAL_STATES = frozenset({
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.BLOCKED,
    TaskState.CANCELLED,
    TaskState.ABORTED,
})

ACTIVE_STATES = frozenset({
    TaskState.CREATED,
    TaskState.READY,
    TaskState.RUNNING,
    TaskState.WAITING,
    TaskState.RECOVERING,
})


@dataclass(frozen=True)
class TaskTransition:
    from_state: str
    to_state: str
    timestamp_unix_ms: int
    reason_code: str
    plan_hash: str = ""

    def as_public(self) -> Dict[str, Any]:
        return {
            "from_state": self.from_state,
            "to_state": self.to_state,
            "timestamp_unix_ms": self.timestamp_unix_ms,
            "reason_code": self.reason_code,
            "plan_hash": self.plan_hash,
        }


@dataclass(frozen=True)
class TaskSnapshot:
    task_id: str
    goal_id: str
    owner_id: str
    session_id: str
    computer_session_id: str
    plan_hash: str
    recovery_plan_hash: str
    state: TaskState
    created_unix_ms: int
    started_unix_ms: int
    completed_unix_ms: int
    attempts_used: int
    transition_count: int
    failure_reason: str
    execution_started: bool
    transitions: Tuple[TaskTransition, ...]
    version: int = 1

    def as_public(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "owner_id": self.owner_id,
            "session_id": self.session_id,
            "computer_session_id": self.computer_session_id,
            "plan_hash": self.plan_hash,
            "recovery_plan_hash": self.recovery_plan_hash,
            "state": self.state.value,
            "created_unix_ms": self.created_unix_ms,
            "started_unix_ms": self.started_unix_ms,
            "completed_unix_ms": self.completed_unix_ms,
            "attempts_used": self.attempts_used,
            "transition_count": self.transition_count,
            "failure_reason": self.failure_reason,
            "execution_started": self.execution_started,
            "version": self.version,
            "durable": False,
        }


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    goal_id: str
    final_state: TaskState
    plan_hash: str
    recovery_plan_hash: str
    execution_result: Optional[ExecutionResult]
    recovery_result: Optional[RecoveryExecutionResult]
    attempts_used: int
    started_unix_ms: int
    completed_unix_ms: int
    reason_code: str

    def as_public(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal_id": self.goal_id,
            "final_state": self.final_state.value,
            "plan_hash": self.plan_hash,
            "recovery_plan_hash": self.recovery_plan_hash,
            "attempts_used": self.attempts_used,
            "started_unix_ms": self.started_unix_ms,
            "completed_unix_ms": self.completed_unix_ms,
            "reason_code": self.reason_code,
            "durable": False,
            "execution_result": None if self.execution_result is None else self.execution_result.as_public(),
            "recovery_result": None if self.recovery_result is None else self.recovery_result.as_public(),
        }
