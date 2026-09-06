"""
DOOM V5.1 — Memory Types
Canonical enums for the V5.1 Memory Foundation.
All memory domain objects use these types exclusively.
"""
from enum import Enum


class MemoryType(str, Enum):
    """Canonical memory type classifications."""
    SHORT_TERM   = "SHORT_TERM"    # Ephemeral conversation context (not durably stored)
    SEMANTIC     = "SEMANTIC"      # Facts, knowledge, definitions
    EPISODIC     = "EPISODIC"      # Recorded verified experiences from completed tasks
    PREFERENCE   = "PREFERENCE"    # User-stated or inferred behavioral preferences
    PROJECT      = "PROJECT"       # Project-specific context and decisions
    EXPERIENCE   = "EXPERIENCE"    # Distilled lessons from completed task executions


class MemoryStatus(str, Enum):
    """Lifecycle status of a memory record."""
    ACTIVE               = "ACTIVE"               # Live, retrievable
    SUPERSEDED           = "SUPERSEDED"           # Replaced by a newer record; kept for history
    ARCHIVED             = "ARCHIVED"             # No longer active but retained
    DELETED              = "DELETED"              # Logically deleted; not returned in retrieval
    PENDING_VERIFICATION = "PENDING_VERIFICATION" # Awaiting evidence before becoming ACTIVE


class MemorySource(str, Enum):
    """Provenance: where this memory came from."""
    USER_EXPLICIT      = "USER_EXPLICIT"      # User directly said "remember X"
    USER_CONVERSATION  = "USER_CONVERSATION"  # Inferred from natural conversation (weaker provenance)
    VERIFIED_TASK      = "VERIFIED_TASK"      # Result of a ground-truth-verified completed task
    SYSTEM_OBSERVATION = "SYSTEM_OBSERVATION" # Observed from system telemetry / environment
    TOOL_RESULT        = "TOOL_RESULT"        # Direct output of a tool execution
    IMPORTED_DATA      = "IMPORTED_DATA"      # Loaded from external data source
    DERIVED_CONTEXT    = "DERIVED_CONTEXT"    # Derived or inferred (lowest provenance weight)


class ConfidenceLevel(str, Enum):
    """Confidence in the accuracy/validity of the memory."""
    HIGH    = "HIGH"    # Directly verified, user-confirmed, or empirically evidenced
    MEDIUM  = "MEDIUM"  # Reasonably reliable but not directly verified
    LOW     = "LOW"     # Inferred, assumed, or lightly corroborated
    UNKNOWN = "UNKNOWN" # Provenance insufficient to determine confidence


class VerificationStatus(str, Enum):
    """Verification state of the memory content."""
    VERIFIED     = "VERIFIED"     # Empirically confirmed (e.g. file on disk, tool output)
    UNVERIFIED   = "UNVERIFIED"   # Not yet confirmed
    CONTRADICTED = "CONTRADICTED" # Contradicted by newer evidence
    SUPERSEDED   = "SUPERSEDED"   # Replaced by a newer verified memory


class PrivacyClass(str, Enum):
    """Privacy classification governing memory access and logging."""
    NORMAL    = "NORMAL"    # Standard memory — retrievable in relevant contexts
    PRIVATE   = "PRIVATE"   # User-personal — restricted to identity/profile contexts only
    SENSITIVE = "SENSITIVE" # High sensitivity — never exposed to telemetry or broad context


# Importance range: 0.0 (lowest) to 1.0 (highest)
IMPORTANCE_MIN: float = 0.0
IMPORTANCE_MAX: float = 1.0
IMPORTANCE_DEFAULT: float = 0.5

# Maximum records returned from a single retrieval
MAX_RETRIEVAL_RECORDS: int = 10

# Minimum relevance score threshold for retrieval inclusion
RELEVANCE_THRESHOLD: float = 0.25

# V5.2.3 Semantic retrieval thresholds & limits (calibrated to 0.40 via benchmark evidence)
SEMANTIC_SIMILARITY_THRESHOLD: float = 0.40
MAX_SEMANTIC_CANDIDATES: int = 25

# V5.2.4 Hybrid ranking limits & constants
MAX_LEXICAL_CANDIDATES: int = 25
MAX_MERGED_CANDIDATES: int = 50
RECENCY_HALFLIFE_DAYS: float = 30.0


class HybridRankingWeights:
    """
    Canonical, validated, configurable weights for the V5.2.4 six-factor hybrid ranking formula.
    Weights must be non-negative, finite numbers and sum to exactly 1.00 within 1e-6 tolerance.
    """
    __slots__ = (
        "weight_lexical",
        "weight_semantic",
        "weight_importance",
        "weight_recency",
        "weight_confidence",
        "weight_project",
    )

    def __init__(
        self,
        weight_lexical: float = 0.25,
        weight_semantic: float = 0.35,
        weight_importance: float = 0.15,
        weight_recency: float = 0.10,
        weight_confidence: float = 0.05,
        weight_project: float = 0.10,
    ):
        self.weight_lexical = float(weight_lexical)
        self.weight_semantic = float(weight_semantic)
        self.weight_importance = float(weight_importance)
        self.weight_recency = float(weight_recency)
        self.weight_confidence = float(weight_confidence)
        self.weight_project = float(weight_project)
        self.validate()

    def validate(self) -> None:
        import math
        weights = (
            self.weight_lexical,
            self.weight_semantic,
            self.weight_importance,
            self.weight_recency,
            self.weight_confidence,
            self.weight_project,
        )
        for w in weights:
            if math.isnan(w) or math.isinf(w) or w < 0.0:
                raise ValueError(f"HybridRankingWeights: all weights must be non-negative finite numbers, got {w}")
        total = sum(weights)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"HybridRankingWeights must sum to 1.00 within 1e-6 tolerance, got {total:.6f}")

    def to_dict(self) -> dict:
        return {
            "weight_lexical": self.weight_lexical,
            "weight_semantic": self.weight_semantic,
            "weight_importance": self.weight_importance,
            "weight_recency": self.weight_recency,
            "weight_confidence": self.weight_confidence,
            "weight_project": self.weight_project,
        }


DEFAULT_HYBRID_WEIGHTS = HybridRankingWeights()

# Memory sources that must NEVER be stored in durable canonical memory
BLOCKED_SOURCES: set = set()  # Policy enforces content-level blocking instead

# Secret patterns — content containing these strings must be rejected
SECRET_PATTERNS: tuple = (
    "password", "passwd", "secret", "api_key", "apikey",
    "token", "access_key", "private_key", "auth_key",
    "bearer ", "sk-", "-----begin", "credential",
)
