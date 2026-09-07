"""
DOOM V5.3.4 — Memory Relationship Domain Models & Schemas
Defines first-class relationship types, candidate structures, typed exceptions,
and serialization helpers for the DOOM knowledge graph and supersession DAG.
"""
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional
import uuid

from memory.lifecycle import MemoryLifecycleError


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_relationship_id() -> str:
    return f"rel_{uuid.uuid4().hex[:16]}"


def new_candidate_id() -> str:
    return f"cand_{uuid.uuid4().hex[:16]}"


def compute_relationship_idempotency_key(
    source_memory_id: str,
    target_memory_id: str,
    relationship_type: Any,
    context_tag: str = "",
) -> str:
    """Deterministic idempotency key for memory relationship creation."""
    s_id = str(source_memory_id).strip()
    t_id = str(target_memory_id).strip()
    r_type = relationship_type.value if hasattr(relationship_type, "value") else str(relationship_type).strip().upper()
    c_tag = str(context_tag).strip()
    raw = f"{s_id}::{t_id}::{r_type}::{c_tag}"
    return f"idem_rel_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}"


# ============================================================================
# 1. ENUMS
# ============================================================================

class RelationshipType(str, Enum):
    """Authoritative semantic relationship types."""
    SUPERSEDES      = "SUPERSEDES"       # Directed: Source supersedes/replaces Target (DAG enforced)
    DUPLICATE_OF    = "DUPLICATE_OF"     # Directed: Source is an alias/duplicate of canonical Target
    CONFLICTS_WITH  = "CONFLICTS_WITH"   # Symmetric: Source and Target express contradictory facts
    RELATED_TO      = "RELATED_TO"       # Undirected: Semantic topical association
    DERIVED_FROM    = "DERIVED_FROM"     # Directed: Source was derived/consolidated from Target


class RelationshipCandidateType(str, Enum):
    """Advisory candidate types generated via semantic/lexical discovery."""
    DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
    CONFLICT_CANDIDATE  = "CONFLICT_CANDIDATE"


class CandidateStatus(str, Enum):
    """Workflow state for advisory candidate relationships."""
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED            = "CONFIRMED"
    REJECTED             = "REJECTED"
    EXPIRED              = "EXPIRED"


# ============================================================================
# 2. TYPED EXCEPTIONS
# ============================================================================

class MemoryRelationshipError(MemoryLifecycleError):
    """Base exception for relationship and graph operations."""
    pass


class SelfReferenceError(MemoryRelationshipError):
    """Raised when a relationship attempts to connect a memory to itself."""
    def __init__(self, memory_id: str):
        super().__init__(f"Self-referential memory relationship rejected for memory '{memory_id}'.", memory_id=memory_id)


class CyclicSupersessionError(MemoryRelationshipError):
    """Raised when a SUPERSEDES edge would introduce a cycle into the supersession DAG."""
    def __init__(self, source_id: str, target_id: str, depth: int = 0):
        super().__init__(
            f"Cyclic supersession rejected: '{source_id}' -> '{target_id}' would violate DAG invariants (path length {depth}).",
            memory_id=source_id,
        )
        self.source_id = source_id
        self.target_id = target_id
        self.depth = depth


class InvalidRelationshipTypeError(MemoryRelationshipError):
    """Raised when an unparseable or unsupported relationship type is specified."""
    pass


class RelationshipValidationError(MemoryRelationshipError):
    """Raised when relationship parameters, confidence, or evidence fail validation."""
    pass


class IdempotencyConflictError(MemoryRelationshipError):
    """Raised when an existing idempotency key exists with different payload parameters."""
    pass


class SensitiveRelationshipError(MemoryRelationshipError):
    """Raised when attempting to form an unauthorized relationship with a SENSITIVE memory."""
    def __init__(self, memory_id: str, message: str = ""):
        msg = message or f"Memory '{memory_id}' is SENSITIVE and cannot participate in relationship graph."
        super().__init__(msg, memory_id=memory_id)


class RelationshipNotFoundError(MemoryRelationshipError):
    """Raised when a requested relationship edge does not exist."""
    pass


# ============================================================================
# 3. DOMAIN DATACLASSES
# ============================================================================

@dataclass
class MemoryRelationship:
    """Authoritative memory relationship edge in PostgreSQL."""
    relationship_id: str = field(default_factory=new_relationship_id)
    source_memory_id: str = ""
    target_memory_id: str = ""
    relationship_type: RelationshipType = RelationshipType.RELATED_TO
    confidence: float = 1.0
    reason: Optional[str] = None
    actor: str = "SYSTEM"
    idempotency_key: Optional[str] = None
    created_at: str = field(default_factory=_utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "source_memory_id": self.source_memory_id,
            "target_memory_id": self.target_memory_id,
            "relationship_type": self.relationship_type.value if hasattr(self.relationship_type, "value") else str(self.relationship_type),
            "confidence": self.confidence,
            "reason": self.reason,
            "actor": self.actor,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "metadata": self.metadata or {},
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryRelationship":
        raw_type = d.get("relationship_type", RelationshipType.RELATED_TO.value)
        try:
            rel_type = RelationshipType(raw_type)
        except Exception:
            rel_type = RelationshipType.RELATED_TO

        meta = d.get("metadata", {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        return cls(
            relationship_id=str(d.get("relationship_id", "")),
            source_memory_id=str(d.get("source_memory_id", "")),
            target_memory_id=str(d.get("target_memory_id", "")),
            relationship_type=rel_type,
            confidence=float(d.get("confidence", 1.0)),
            reason=d.get("reason"),
            actor=str(d.get("actor", "SYSTEM")),
            idempotency_key=d.get("idempotency_key"),
            created_at=str(d.get("created_at", _utcnow())),
            metadata=meta if isinstance(meta, dict) else {},
        )


@dataclass
class RelationshipCandidate:
    """Advisory duplicate or conflict candidate generated during discovery."""
    candidate_id: str = field(default_factory=new_candidate_id)
    candidate_type: RelationshipCandidateType = RelationshipCandidateType.DUPLICATE_CANDIDATE
    source_memory_id: str = ""
    candidate_memory_id: str = ""
    similarity: float = 0.0
    confidence: float = 0.5
    evidence: Dict[str, Any] = field(default_factory=dict)
    status: CandidateStatus = CandidateStatus.PENDING_CONFIRMATION
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_type": self.candidate_type.value,
            "source_memory_id": self.source_memory_id,
            "candidate_memory_id": self.candidate_memory_id,
            "similarity": self.similarity,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "status": self.status.value,
            "created_at": self.created_at,
        }


@dataclass
class RelationshipMutationResult:
    """Outcome of an authoritative relationship mutation."""
    success: bool
    relationship_id: Optional[str] = None
    relationship_type: Optional[str] = None
    source_memory_id: Optional[str] = None
    target_memory_id: Optional[str] = None
    is_idempotent_replay: bool = False
    error: Optional[str] = None
