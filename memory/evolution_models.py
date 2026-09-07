"""
DOOM V5.3.5 — Memory Evolution Models, Enums & Schemas
Defines core data structures for semantic freshness, continuous confidence,
normalized evidence, and epistemic explainability profiles.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
import uuid
from typing import Any, Dict, List, Optional

from memory.types import ConfidenceLevel, MemorySource


class FreshnessClass(str, Enum):
    """Semantic decay classification determining half-life and freshness floors."""
    PERMANENT      = "PERMANENT"       # Universal/mathematical truths; half-life=inf, floor=1.00
    FOUNDATIONAL   = "FOUNDATIONAL"    # Core user preferences/architecture; half-life=365d, floor=0.85
    PROJECT_STABLE = "PROJECT_STABLE"  # Project architecture, config; half-life=90d, floor=0.50
    DYNAMIC_FACT   = "DYNAMIC_FACT"    # Environment state, versions, packages; half-life=14d, floor=0.15
    EPHEMERAL      = "EPHEMERAL"       # Session notes, active branch, weather; half-life=1d, floor=0.00


@dataclass(frozen=True)
class FreshnessParameters:
    """Immutable parameter configuration for a freshness class."""
    half_life_days: float
    floor: float


FRESHNESS_CONFIG: Dict[FreshnessClass, FreshnessParameters] = {
    FreshnessClass.PERMANENT: FreshnessParameters(half_life_days=float("inf"), floor=1.00),
    FreshnessClass.FOUNDATIONAL: FreshnessParameters(half_life_days=365.0, floor=0.85),
    FreshnessClass.PROJECT_STABLE: FreshnessParameters(half_life_days=90.0, floor=0.50),
    FreshnessClass.DYNAMIC_FACT: FreshnessParameters(half_life_days=14.0, floor=0.15),
    FreshnessClass.EPHEMERAL: FreshnessParameters(half_life_days=1.0, floor=0.00),
}


class EvidencePolarity(str, Enum):
    """Polarity of observation relative to target memory assertions."""
    SUPPORTING    = "SUPPORTING"
    CONTRADICTING = "CONTRADICTING"
    AMBIGUOUS     = "AMBIGUOUS"


class EvidenceType(str, Enum):
    """Taxonomy of evidence sources."""
    USER_EXPLICIT_CONFIRMATION = "USER_EXPLICIT_CONFIRMATION"
    USER_EXPLICIT_CORRECTION   = "USER_EXPLICIT_CORRECTION"
    VERIFIED_TASK_OUTCOME      = "VERIFIED_TASK_OUTCOME"
    AUTHORITATIVE_TOOL_OUTPUT  = "AUTHORITATIVE_TOOL_OUTPUT"
    SYSTEM_ENVIRONMENT_STATE   = "SYSTEM_ENVIRONMENT_STATE"
    CONVERSATION_INFERENCE     = "CONVERSATION_INFERENCE"
    GRAPH_RELATIONSHIP_LINK    = "GRAPH_RELATIONSHIP_LINK"
    EXTERNAL_CORROBORATION     = "EXTERNAL_CORROBORATION"


class EvolutionType(str, Enum):
    """Auditable evolution mutation operations."""
    EVIDENCE_UPDATE       = "EVIDENCE_UPDATE"
    CONFIDENCE_REFRESH    = "CONFIDENCE_REFRESH"
    IMPORTANCE_UPDATE     = "IMPORTANCE_UPDATE"
    CONTRADICTION_DEGRADE = "CONTRADICTION_DEGRADE"
    STALENESS_FLAG        = "STALENESS_FLAG"
    FOUNDATIONAL_LOCK     = "FOUNDATIONAL_LOCK"
    RECONCILIATION_REPAIR = "RECONCILIATION_REPAIR"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class MemoryEvolutionError(Exception):
    """Base exception for all V5.3.5 evolution subsystem failures."""
    pass


class InadmissibleEvidenceError(MemoryEvolutionError):
    """Raised when evidence fails policy validation or originates from untrusted sources."""
    pass


class InactiveMemoryEvolutionError(MemoryEvolutionError):
    """Raised when attempting to add evidence or evolve a non-ACTIVE memory."""
    pass


class EvolutionValidationError(MemoryEvolutionError):
    """Raised when numerical parameters or arguments violate safety constraints."""
    pass


class EvolutionConcurrencyError(MemoryEvolutionError):
    """Raised on concurrency race or deadlocks during evolution."""
    pass


class IdempotencyConflictError(MemoryEvolutionError):
    """Raised when an idempotency key is reused with differing payloads."""
    pass


class SensitiveEvidencePolicyError(MemoryEvolutionError):
    """Raised when sensitive memory evidence violates privacy or redaction policies."""
    pass


# ---------------------------------------------------------------------------
# Numerical Safety & Validation Helpers
# ---------------------------------------------------------------------------
def clamp_float(val: Any, min_val: float, max_val: float, default: float = 0.5) -> float:
    """Deterministically clamp a numeric value into [min_val, max_val], rejecting NaN and Inf."""
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return max(min_val, min(f, max_val))
    except (TypeError, ValueError):
        return default


def project_confidence_score_to_level(score: float) -> ConfidenceLevel:
    """Deterministic, standardized projection from continuous score to legacy ConfidenceLevel."""
    s = clamp_float(score, 0.0, 1.0, 0.5)
    if s >= 0.80:
        return ConfidenceLevel.HIGH
    elif s >= 0.40:
        return ConfidenceLevel.MEDIUM
    elif s >= 0.10:
        return ConfidenceLevel.LOW
    else:
        return ConfidenceLevel.UNKNOWN


def project_confidence_level_to_score(level: ConfidenceLevel) -> float:
    """Deterministic default continuous score corresponding to legacy ConfidenceLevel."""
    if level == ConfidenceLevel.HIGH:
        return 0.90
    elif level == ConfidenceLevel.MEDIUM:
        return 0.60
    elif level == ConfidenceLevel.LOW:
        return 0.30
    return 0.50


def compute_observation_hash(
    source: str,
    actor: str,
    raw_payload: Any,
) -> str:
    """
    Produce deterministic SHA-256 hash of normalized observation.
    Prevents duplicate evidence replay while avoiding sensitive leakage.
    """
    if isinstance(raw_payload, (dict, list)):
        payload_str = json.dumps(raw_payload, sort_keys=True, default=str)
    else:
        payload_str = str(raw_payload).strip()
    canonical = f"{source.strip().upper()}|{actor.strip().upper()}|{payload_str}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_evidence_idempotency_key(
    memory_id: str,
    source_task_id: Optional[str],
    observation_hash: str,
    polarity: str,
) -> str:
    """
    Deterministic idempotency key for evidence insertion.
    Guarantees replay safety and prevents duplicate confidence amplification.
    """
    task_component = (source_task_id or "NONE").strip()
    raw = f"ev_{memory_id}_{task_component}_{observation_hash[:24]}_{polarity}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


# ---------------------------------------------------------------------------
# Core Dataclasses
# ---------------------------------------------------------------------------
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MemoryEvidence:
    """
    Immutable relational record representing an empirical observation supporting,
    contradicting, or qualifying a specific MemoryRecord.
    """
    evidence_id: str
    memory_id: str
    evidence_type: EvidenceType
    polarity: EvidencePolarity
    strength: float
    source: MemorySource
    actor: str = "SYSTEM"
    source_task_id: Optional[str] = None
    observation_hash: str = ""
    idempotency_key: str = ""
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "memory_id": self.memory_id,
            "evidence_type": self.evidence_type.value if hasattr(self.evidence_type, "value") else str(self.evidence_type),
            "polarity": self.polarity.value if hasattr(self.polarity, "value") else str(self.polarity),
            "strength": self.strength,
            "source": self.source.value if hasattr(self.source, "value") else str(self.source),
            "actor": self.actor,
            "source_task_id": self.source_task_id,
            "observation_hash": self.observation_hash,
            "idempotency_key": self.idempotency_key,
            "summary": self.summary,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryEvidence":
        return cls(
            evidence_id=d["evidence_id"],
            memory_id=d["memory_id"],
            evidence_type=EvidenceType(d.get("evidence_type", EvidenceType.SYSTEM_ENVIRONMENT_STATE.value)),
            polarity=EvidencePolarity(d.get("polarity", EvidencePolarity.SUPPORTING.value)),
            strength=clamp_float(d.get("strength", 0.5), 0.0, 1.0, 0.5),
            source=MemorySource(d.get("source", MemorySource.DERIVED_CONTEXT.value)),
            actor=d.get("actor", "SYSTEM"),
            source_task_id=d.get("source_task_id"),
            observation_hash=d.get("observation_hash", ""),
            idempotency_key=d.get("idempotency_key", ""),
            summary=d.get("summary", ""),
            metadata=d.get("metadata", {}) or {},
            created_at=str(d.get("created_at", _utcnow_iso())),
        )


@dataclass
class MemoryEvolutionEvent:
    """
    Immutable audit trail row recording an automatic or supervised mutation
    of a memory's confidence, importance, or epistemic state.
    """
    event_id: str
    memory_id: str
    evolution_type: EvolutionType
    confidence_before: float
    confidence_after: float
    importance_before: float
    importance_after: float
    delta_confidence: float
    delta_importance: float
    reason: str
    actor: str = "SYSTEM"
    evidence_id: Optional[str] = None
    idempotency_key: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "memory_id": self.memory_id,
            "evidence_id": self.evidence_id,
            "evolution_type": self.evolution_type.value if hasattr(self.evolution_type, "value") else str(self.evolution_type),
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
            "importance_before": self.importance_before,
            "importance_after": self.importance_after,
            "delta_confidence": self.delta_confidence,
            "delta_importance": self.delta_importance,
            "reason": self.reason,
            "actor": self.actor,
            "idempotency_key": self.idempotency_key,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryEvolutionEvent":
        return cls(
            event_id=d["event_id"],
            memory_id=d["memory_id"],
            evidence_id=d.get("evidence_id"),
            evolution_type=EvolutionType(d.get("evolution_type", EvolutionType.EVIDENCE_UPDATE.value)),
            confidence_before=clamp_float(d.get("confidence_before", 0.5), 0.0, 1.0, 0.5),
            confidence_after=clamp_float(d.get("confidence_after", 0.5), 0.0, 1.0, 0.5),
            importance_before=clamp_float(d.get("importance_before", 0.5), 0.0, 1.0, 0.5),
            importance_after=clamp_float(d.get("importance_after", 0.5), 0.0, 1.0, 0.5),
            delta_confidence=float(d.get("delta_confidence", 0.0)),
            delta_importance=float(d.get("delta_importance", 0.0)),
            reason=d.get("reason", ""),
            actor=d.get("actor", "SYSTEM"),
            idempotency_key=d.get("idempotency_key", ""),
            metadata=d.get("metadata", {}) or {},
            created_at=str(d.get("created_at", _utcnow_iso())),
        )


@dataclass
class EvolutionResult:
    """Structured response from MemoryEvolutionEngine mutations."""
    success: bool
    memory_id: str
    confidence_before: float
    confidence_after: float
    importance_before: float
    importance_after: float
    evidence_id: Optional[str] = None
    event_id: Optional[str] = None
    is_idempotent_replay: bool = False
    error: Optional[str] = None


@dataclass
class MemoryEpistemicProfile:
    """
    Structured explainability profile answering 'Why do you believe/trust this memory?'
    without exposing raw LLM chain-of-thought.
    """
    memory_id: str
    content: str
    confidence_score: float
    confidence_level: str
    freshness_score: float
    freshness_class: str
    importance: float
    is_foundational: bool
    status: str
    supporting_evidence_count: int
    contradicting_evidence_count: int
    primary_source: str
    last_confirmed_at: str
    summary_reason: str
    recent_evidence: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "confidence_score": round(self.confidence_score, 4),
            "confidence_level": self.confidence_level,
            "freshness_score": round(self.freshness_score, 4),
            "freshness_class": self.freshness_class,
            "importance": round(self.importance, 4),
            "is_foundational": self.is_foundational,
            "status": self.status,
            "supporting_evidence_count": self.supporting_evidence_count,
            "contradicting_evidence_count": self.contradicting_evidence_count,
            "primary_source": self.primary_source,
            "last_confirmed_at": self.last_confirmed_at,
            "summary_reason": self.summary_reason,
            "recent_evidence": self.recent_evidence,
        }
