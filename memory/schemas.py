"""
DOOM V5.1 — Memory Schemas
Canonical dataclasses for memory records and memory context.
These are the authoritative data structures for all V5.1 memory operations.
"""
import uuid
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from memory.types import (
    MemoryType, MemoryStatus, MemorySource,
    ConfidenceLevel, VerificationStatus, PrivacyClass,
)


def _utcnow() -> str:
    """Return current UTC timestamp as ISO string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def new_memory_id() -> str:
    """Generate a unique memory record ID."""
    return f"mem_{uuid.uuid4().hex[:16]}"


@dataclass
class MemoryRecord:
    """
    Canonical V5.1 memory record.
    Represents a single durable unit of structured memory.
    Never stores raw chain-of-thought, secrets, or credentials.
    """
    # Core identity
    memory_id: str = field(default_factory=new_memory_id)
    memory_type: MemoryType = MemoryType.SEMANTIC

    # Content
    content: str = ""                              # The actual memory text/value (never secrets)

    # Provenance
    source: MemorySource = MemorySource.DERIVED_CONTEXT
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED

    # Importance & lifecycle
    importance: float = 0.5                        # 0.0 (lowest) to 1.0 (highest)
    status: MemoryStatus = MemoryStatus.ACTIVE

    # Temporal fields
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    last_accessed_at: Optional[str] = None

    # Association
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    entity_ids: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    # Supersession chain
    supersedes_memory_id: Optional[str] = None    # ID of the older memory this replaces

    # Event linkage
    source_event_id: Optional[str] = None         # task_id / episode_id that produced this

    # Privacy
    privacy_class: PrivacyClass = PrivacyClass.NORMAL

    # Extensible metadata (never logs raw content)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        """Update last_accessed_at timestamp."""
        self.last_accessed_at = _utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Safe serialization. Excludes sensitive content from metadata."""
        return {
            "memory_id": self.memory_id,
            "memory_type": self.memory_type.value,
            "content": self.content,
            "source": self.source.value,
            "confidence": self.confidence.value,
            "verification_status": self.verification_status.value,
            "importance": self.importance,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed_at": self.last_accessed_at,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "entity_ids": self.entity_ids,
            "tags": self.tags,
            "supersedes_memory_id": self.supersedes_memory_id,
            "source_event_id": self.source_event_id,
            "privacy_class": self.privacy_class.value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryRecord":
        """Deserialize from a dictionary (e.g. database row)."""
        return cls(
            memory_id=data.get("memory_id", new_memory_id()),
            memory_type=MemoryType(data.get("memory_type", MemoryType.SEMANTIC.value)),
            content=data.get("content", ""),
            source=MemorySource(data.get("source", MemorySource.DERIVED_CONTEXT.value)),
            confidence=ConfidenceLevel(data.get("confidence", ConfidenceLevel.MEDIUM.value)),
            verification_status=VerificationStatus(data.get("verification_status", VerificationStatus.UNVERIFIED.value)),
            importance=float(data.get("importance", 0.5)),
            status=MemoryStatus(data.get("status", MemoryStatus.ACTIVE.value)),
            created_at=data.get("created_at", _utcnow()),
            updated_at=data.get("updated_at", _utcnow()),
            last_accessed_at=data.get("last_accessed_at"),
            project_id=data.get("project_id"),
            task_id=data.get("task_id"),
            entity_ids=data.get("entity_ids") or [],
            tags=data.get("tags") or [],
            supersedes_memory_id=data.get("supersedes_memory_id"),
            source_event_id=data.get("source_event_id"),
            privacy_class=PrivacyClass(data.get("privacy_class", PrivacyClass.NORMAL.value)),
            metadata=data.get("metadata") or {},
        )


@dataclass
class ScoredMemory:
    """A memory record paired with its retrieval relevance score."""
    record: MemoryRecord
    score: float = 0.0


@dataclass
class SemanticMemoryMatch:
    """A semantic vector search match for a memory record."""
    record: MemoryRecord
    similarity: float = 0.0
    distance: float = 0.0
    model: str = ""
    model_version: str = ""


@dataclass
class MemoryContext:
    """
    Structured memory context passed into the cognitive pipeline.
    Contains only controlled, filtered, relevant memories.
    Never exposes private memory content to unrelated contexts.
    """
    query: str = ""
    retrieved_memories: List[MemoryRecord] = field(default_factory=list)
    relevance_scores: Dict[str, float] = field(default_factory=dict)  # memory_id → score
    sources: List[str] = field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    context_summary: str = ""                      # Safe summary, never contains private content
    retrieval_latency_ms: float = 0.0
    memory_hit: bool = False                       # True if relevant memories were found
    memory_count: int = 0
    semantic_matches: List[SemanticMemoryMatch] = field(default_factory=list)
    semantic_scores: Dict[str, float] = field(default_factory=dict)
    retrieval_mode: str = "LEXICAL"

    def has_memories(self) -> bool:
        return len(self.retrieved_memories) > 0

    def get_summary_for_cognition(self) -> str:
        """
        Returns a safe, controlled summary for injection into cognitive reasoning.
        Does NOT dump raw content. Excludes SENSITIVE privacy class records.
        """
        if not self.retrieved_memories:
            return ""
        lines = []
        for mem in self.retrieved_memories:
            # Never inject sensitive memory into general cognition
            if mem.privacy_class == PrivacyClass.SENSITIVE:
                continue
            label = f"[{mem.memory_type.value}|{mem.source.value}|{mem.confidence.value}]"
            lines.append(f"  {label} {mem.content}")
        if not lines:
            return ""
        return "Relevant Memory Context:\n" + "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Safe serialization. Omits private content from telemetry."""
        return {
            "query": self.query,
            "memory_count": self.memory_count,
            "memory_hit": self.memory_hit,
            "retrieval_latency_ms": self.retrieval_latency_ms,
            "confidence": self.confidence.value,
            "sources": self.sources,
            "context_summary": self.context_summary,
            # Note: does NOT include raw memory content for telemetry safety
        }
