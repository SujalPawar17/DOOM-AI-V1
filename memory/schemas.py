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
    generation: int = 1                            # V5.3.3 monotonic generation counter

    # V5.3.5 Freshness, Temporal validity & Continuous Confidence
    confidence_score: Optional[float] = None       # Continuous score in [0.0, 1.0] (syncs from confidence if None)
    freshness_class: str = "PROJECT_STABLE"        # FreshnessClass enum value
    is_foundational: bool = False                  # Guaranteed high freshness floor / importance protection
    valid_from: Optional[str] = field(default_factory=_utcnow)
    valid_until: Optional[str] = None
    last_confirmed_at: Optional[str] = field(default_factory=_utcnow)

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

    def __post_init__(self) -> None:
        if self.confidence_score is None:
            _CONF_MAP = {
                ConfidenceLevel.HIGH: 1.0,
                ConfidenceLevel.MEDIUM: 0.6,
                ConfidenceLevel.LOW: 0.3,
                ConfidenceLevel.UNKNOWN: 0.1,
            }
            self.confidence_score = _CONF_MAP.get(self.confidence, 0.50)

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
            "confidence_score": self.confidence_score,
            "freshness_class": self.freshness_class,
            "is_foundational": self.is_foundational,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "last_confirmed_at": self.last_confirmed_at,
            "verification_status": self.verification_status.value,
            "importance": self.importance,
            "status": self.status.value,
            "generation": self.generation,
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
        c_score = data.get("confidence_score")
        if c_score is None:
            c_val = str(data.get("confidence", "MEDIUM")).upper()
            if c_val == "HIGH":
                c_score = 0.90
            elif c_val == "LOW":
                c_score = 0.30
            elif c_val == "UNKNOWN":
                c_score = 0.10
            else:
                c_score = 0.60
        else:
            c_score = float(c_score)

        return cls(
            memory_id=data.get("memory_id", new_memory_id()),
            memory_type=MemoryType(data.get("memory_type", MemoryType.SEMANTIC.value)),
            content=data.get("content", ""),
            source=MemorySource(data.get("source", MemorySource.DERIVED_CONTEXT.value)),
            confidence=ConfidenceLevel(data.get("confidence", ConfidenceLevel.MEDIUM.value)),
            confidence_score=c_score,
            freshness_class=str(data.get("freshness_class", "PROJECT_STABLE")),
            is_foundational=bool(data.get("is_foundational", False)),
            valid_from=data.get("valid_from"),
            valid_until=data.get("valid_until"),
            last_confirmed_at=data.get("last_confirmed_at", data.get("created_at", _utcnow())),
            verification_status=VerificationStatus(data.get("verification_status", VerificationStatus.UNVERIFIED.value)),
            importance=float(data.get("importance", 0.5)),
            status=MemoryStatus(data.get("status", MemoryStatus.ACTIVE.value)),
            generation=int(data.get("generation") or 1),
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
class HybridScoreBreakdown:
    """Detailed score breakdown across all six V5.2.4/V5.3.5 hybrid ranking factors."""
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    importance_score: float = 0.0
    recency_score: float = 0.0
    confidence_score: float = 0.0
    project_score: float = 0.0
    final_score: float = 0.0
    freshness_score: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return {
            "lexical_score": self.lexical_score,
            "semantic_score": self.semantic_score,
            "importance_score": self.importance_score,
            "recency_score": self.recency_score,
            "confidence_score": self.confidence_score,
            "project_score": self.project_score,
            "final_score": self.final_score,
            "freshness_score": self.freshness_score,
        }


@dataclass
class HybridRankedMemory:
    """A memory record paired with its 6-factor composite hybrid score and breakdown."""
    record: MemoryRecord
    score: float = 0.0
    breakdown: Optional[HybridScoreBreakdown] = None


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
    Enforces V5.2.5 [DATA_ONLY] structural fencing and deterministic budgeting.
    """
    query: str = ""
    retrieved_memories: List[MemoryRecord] = field(default_factory=list)
    relevance_scores: Dict[str, float] = field(default_factory=dict)  # memory_id → score
    sources: List[str] = field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    context_summary: str = ""                      # Safe summary, never contains private content
    fenced_context: str = ""                       # V5.2.5: Canonical [DATA_ONLY] structural fenced context
    retrieval_latency_ms: float = 0.0
    memory_hit: bool = False                       # True if relevant memories were found
    memory_count: int = 0
    semantic_matches: List[SemanticMemoryMatch] = field(default_factory=list)
    semantic_scores: Dict[str, float] = field(default_factory=dict)
    hybrid_breakdowns: Dict[str, HybridScoreBreakdown] = field(default_factory=dict)
    retrieval_mode: str = "LEXICAL"
    fencing_applied: bool = True
    context_char_count: int = 0
    budget_exceeded: bool = False
    omitted_count: int = 0
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    relationship_annotations: Dict[str, Any] = field(default_factory=dict)

    def has_memories(self) -> bool:
        return len(self.retrieved_memories) > 0

    def get_summary_for_cognition(self) -> str:
        """
        V5.2.5: Returns the safe, [DATA_ONLY] fenced context for injection into cognitive reasoning.
        Does NOT dump raw content. Excludes SENSITIVE privacy class records.
        Delegates to canonical fenced_context to maintain ONE single context safety path.
        """
        if self.fenced_context:
            return self.fenced_context
        if self.context_summary:
            return self.context_summary
        if not self.retrieved_memories:
            return ""

        # On-the-fly fencing fallback if constructed without builder
        try:
            from memory.fencing import memory_context_fencer
            scored = [
                ScoredMemory(record=r, score=self.relevance_scores.get(r.memory_id, 0.5))
                for r in self.retrieved_memories
            ]
            res = memory_context_fencer.fence_memories(self.query, scored)
            return res.fenced_context
        except Exception:
            return ""

    def to_dict(self) -> Dict[str, Any]:
        """Safe serialization for API/WebSocket. Omits raw memory records and embeddings."""
        return {
            "query": self.query,
            "memory_count": self.memory_count,
            "memory_hit": self.memory_hit,
            "retrieval_latency_ms": self.retrieval_latency_ms,
            "confidence": self.confidence.value if hasattr(self.confidence, "value") else str(self.confidence),
            "sources": self.sources,
            "context_summary": self.context_summary,
            "fenced_context": self.fenced_context,
            "retrieval_mode": self.retrieval_mode,
            "hybrid_breakdowns": {
                mid: bd.to_dict() for mid, bd in self.hybrid_breakdowns.items()
            },
            "fencing_applied": self.fencing_applied,
            "context_char_count": self.context_char_count or len(self.fenced_context),
            "budget_exceeded": self.budget_exceeded,
            "omitted_count": self.omitted_count,
            "conflicts": self.conflicts,
            "relationship_annotations": self.relationship_annotations,
        }

    def to_telemetry_dict(self) -> Dict[str, Any]:
        """
        V5.2.5: Sanitized telemetry serialization.
        STRICTLY NEVER leaks raw user query text, raw memory content, secrets, or embeddings.
        """
        import hashlib
        q_hash = hashlib.sha256(self.query.encode("utf-8")).hexdigest()[:16] if self.query else ""
        return {
            "query_present": bool(self.query),
            "query_length": len(self.query),
            "query_hash": q_hash,
            "memory_count": self.memory_count,
            "memory_hit": self.memory_hit,
            "retrieval_latency_ms": self.retrieval_latency_ms,
            "confidence": self.confidence.value if hasattr(self.confidence, "value") else str(self.confidence),
            "sources": self.sources,
            "retrieval_mode": self.retrieval_mode,
            "fencing_applied": self.fencing_applied,
            "context_char_count": self.context_char_count or len(self.fenced_context),
            "budget_exceeded": self.budget_exceeded,
            "omitted_count": self.omitted_count,
        }

