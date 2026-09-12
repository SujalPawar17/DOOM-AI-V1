"""Immutable V8.8 context records. Historical DATA only. Not authorization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Tuple


class ContextSource(str, Enum):
    MEMORY = "MEMORY"
    EXPERIENCE = "EXPERIENCE"


class ContextStatus(str, Enum):
    OK = "OK"
    EMPTY = "EMPTY"
    PARTIAL = "PARTIAL"
    CONTEXT_UNAVAILABLE = "CONTEXT_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"
    DISABLED = "DISABLED"


@dataclass(frozen=True)
class ContextRequest:
    goal_id: str
    owner_id: str
    session_id: str
    normalized_intent: str
    capability_class: str
    query: str
    max_items: int = 8
    max_memory_items: int = 4
    max_experience_items: int = 4

    def as_public(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "owner_id": self.owner_id,
            "session_id": self.session_id,
            "normalized_intent": self.normalized_intent,
            "capability_class": self.capability_class,
            "query": self.query,
            "max_items": self.max_items,
            "max_memory_items": self.max_memory_items,
            "max_experience_items": self.max_experience_items,
            "authorizes_execution": False,
        }


@dataclass(frozen=True)
class ContextItem:
    source: ContextSource
    reference_id: str
    content: str
    relevance: float
    outcome: str
    provenance: str
    timestamp_unix_ms: int
    truncated: bool = False

    def as_public(self) -> Dict[str, Any]:
        return {
            "source": self.source.value,
            "reference_id": self.reference_id,
            "content": self.content,
            "relevance": self.relevance,
            "outcome": self.outcome,
            "provenance": self.provenance,
            "timestamp_unix_ms": self.timestamp_unix_ms,
            "truncated": self.truncated,
            "authorizes_execution": False,
            "approved": False,
            "verification_override": False,
            "risk_override": False,
        }


@dataclass(frozen=True)
class ContextResult:
    items: Tuple[ContextItem, ...]
    memory_count: int
    experience_count: int
    truncated: bool
    status: ContextStatus
    source_errors: Tuple[str, ...]
    context_hash: str

    def as_public(self) -> Dict[str, Any]:
        return {
            "items": [item.as_public() for item in self.items],
            "memory_count": self.memory_count,
            "experience_count": self.experience_count,
            "truncated": self.truncated,
            "status": self.status.value,
            "source_errors": list(self.source_errors),
            "context_hash": self.context_hash,
            "authorizes_execution": False,
            "execution_permitted": False,
            "approved": False,
            "plan_hash": "",
        }


@dataclass(frozen=True)
class PlannerContext:
    """Data-only seam for V8.3. Never a MemoryManager, store, or callback."""

    result: ContextResult

    def as_public(self) -> Dict[str, Any]:
        return {
            "result": self.result.as_public(),
            "authorizes_execution": False,
        }


def hash_context_items(items: Tuple[ContextItem, ...]) -> str:
    """Diagnostic identity only. Not a plan_hash and not authorization."""
    parts = []
    for item in items:
        parts.append(
            "|".join((
                item.source.value,
                item.reference_id,
                item.content,
                str(round(float(item.relevance), 6)),
                item.outcome,
                item.provenance,
                str(int(item.timestamp_unix_ms)),
                "1" if item.truncated else "0",
            ))
        )
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest
