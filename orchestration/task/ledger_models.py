"""Immutable V8.7 ledger records. Metadata only. Not authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from orchestration.task.types import TaskSnapshot, TaskState, TaskTransition


@dataclass(frozen=True)
class LedgerTaskRecord:
    snapshot: TaskSnapshot
    version: int
    pending_recovery: bool
    persistence_ok: bool = True

    def as_public(self) -> Dict[str, Any]:
        out = self.snapshot.as_public()
        out["version"] = self.version
        out["pending_recovery"] = self.pending_recovery
        out["persistence_ok"] = self.persistence_ok
        out["durable"] = True
        out["authorizes_execution"] = False
        return out


@dataclass(frozen=True)
class LedgerHistory:
    task_id: str
    transitions: Tuple[TaskTransition, ...]

    def as_public(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "transitions": [t.as_public() for t in self.transitions],
        }


def require_state(name: str) -> TaskState:
    return TaskState(str(name))
