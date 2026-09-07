# Legacy V2-V4.2 memory modules (preserved for backward compatibility)
from memory.user_profile import user_profile, UserProfile
from memory.short_term import short_term_memory, ShortTermMemory
from memory.episodic import episodic_memory, EpisodicMemory
from memory.semantic import semantic_memory, SemanticMemory

# V5.1 Memory Foundation — canonical memory system
from memory.types import (
    MemoryType, MemoryStatus, MemorySource,
    ConfidenceLevel, VerificationStatus, PrivacyClass,
)
from memory.schemas import MemoryRecord, MemoryContext, ScoredMemory
from memory.manager import memory_manager, MemoryManager

# V5.3.1 & V5.3.2 Memory Lifecycle & Transaction Engine
from memory.lifecycle import (
    memory_lifecycle,
    MemoryLifecycleManager,
    lifecycle_engine,
    MemoryLifecycleEngine,
    LifecycleTransitionResult,
    MemoryLifecycleError,
    InvalidLifecycleTransitionError,
    InvalidLifecycleStateError,
    MemoryAlreadyDeletedError,
    LifecycleValidationError,
    LifecycleAuditError,
    MemoryNotFoundError,
    ProvenanceValidationError,
    ConcurrentModificationError,
    LockTimeoutError,
    DeadlockDetectedError,
    DatabaseConnectionError,
    is_retryable_lifecycle_error,
    LifecycleActor,
    LifecycleTransition,
    MemoryLifecycleEvent,
    validate_transition,
    validate_provenance,
    is_valid_transition,
    get_transition,
    LIFECYCLE_TRANSITIONS,
)

# V5.3.3 Vector Synchronization & Reconciliation Engine
from memory.sync import (
    SyncOperation,
    VectorSyncStatus,
    VectorSyncWorkItem,
    VectorSyncResult,
    ReconciliationReport,
    compute_sync_idempotency_key,
    validate_sync_transition,
)
from memory.sync_engine import (
    vector_sync_engine,
    VectorSyncEngine,
)
from memory.reconciliation import (
    vector_reconciliation_engine,
    VectorReconciliationEngine,
)

# V5.3.4 Memory Relationships, Supersession DAG & Reconciliation
from memory.relationships import (
    RelationshipType,
    RelationshipCandidateType,
    CandidateStatus,
    MemoryRelationship,
    RelationshipCandidate,
    RelationshipMutationResult,
    MemoryRelationshipError,
    SelfReferenceError,
    CyclicSupersessionError,
    InvalidRelationshipTypeError,
    RelationshipValidationError,
    IdempotencyConflictError,
    SensitiveRelationshipError,
    RelationshipNotFoundError,
    compute_relationship_idempotency_key,
)
from memory.relationship_engine import (
    relationship_engine,
    MemoryRelationshipEngine,
)
from memory.relationship_reconciliation import (
    relationship_reconciliation_engine,
    RelationshipReconciliationEngine,
    RelationshipReconciliationReport,
)
from memory.relationship_migration import (
    run_relationship_migration,
)

# V5.3.5 Memory Freshness, Confidence & Importance Evolution
from memory.evolution_models import (
    FreshnessClass,
    FreshnessParameters,
    FRESHNESS_CONFIG,
    EvidencePolarity,
    EvidenceType,
    EvolutionType,
    MemoryEvidence,
    MemoryEvolutionEvent,
    EvolutionResult,
    MemoryEpistemicProfile,
    MemoryEvolutionError,
    InadmissibleEvidenceError,
    InactiveMemoryEvolutionError,
    EvolutionValidationError,
    EvolutionConcurrencyError,
    IdempotencyConflictError as EvolutionIdempotencyConflictError,
    SensitiveEvidencePolicyError,
    clamp_float,
    project_confidence_score_to_level,
    project_confidence_level_to_score,
    compute_observation_hash,
    compute_evidence_idempotency_key,
)
from memory.evolution_engine import (
    evolution_engine,
    MemoryEvolutionEngine,
)
from memory.evolution_migration import (
    run_evolution_migration,
)
from memory.evolution_reconciliation import (
    evolution_reconciliation_engine,
    MemoryEvolutionReconciliationEngine,
    EvolutionReconciliationReport,
)

__all__ = [
    # Legacy (backward compat)
    "user_profile", "UserProfile",
    "short_term_memory", "ShortTermMemory",
    "episodic_memory", "EpisodicMemory",
    "semantic_memory", "SemanticMemory",
    # V5.1 canonical
    "MemoryType", "MemoryStatus", "MemorySource",
    "ConfidenceLevel", "VerificationStatus", "PrivacyClass",
    "MemoryRecord", "MemoryContext", "ScoredMemory",
    "memory_manager", "MemoryManager",
    # V5.3.1 & V5.3.2 Lifecycle & Transaction Engine
    "memory_lifecycle", "MemoryLifecycleManager",
    "lifecycle_engine", "MemoryLifecycleEngine",
    "LifecycleTransitionResult",
    "MemoryLifecycleError", "InvalidLifecycleTransitionError",
    "InvalidLifecycleStateError", "MemoryAlreadyDeletedError",
    "LifecycleValidationError", "LifecycleAuditError",
    "MemoryNotFoundError", "ProvenanceValidationError",
    "ConcurrentModificationError", "LockTimeoutError",
    "DeadlockDetectedError", "DatabaseConnectionError",
    "is_retryable_lifecycle_error",
    "LifecycleActor", "LifecycleTransition", "MemoryLifecycleEvent",
    "validate_transition", "validate_provenance", "is_valid_transition", "get_transition",
    "LIFECYCLE_TRANSITIONS",
    # V5.3.3 Vector Synchronization & Reconciliation
    "SyncOperation", "VectorSyncStatus", "VectorSyncWorkItem", "VectorSyncResult",
    "ReconciliationReport", "compute_sync_idempotency_key", "validate_sync_transition",
    "vector_sync_engine", "VectorSyncEngine",
    "vector_reconciliation_engine", "VectorReconciliationEngine",
    # V5.3.4 Memory Relationships & DAG
    "RelationshipType", "RelationshipCandidateType", "CandidateStatus",
    "MemoryRelationship", "RelationshipCandidate", "RelationshipMutationResult",
    "MemoryRelationshipError", "SelfReferenceError", "CyclicSupersessionError",
    "InvalidRelationshipTypeError", "RelationshipValidationError",
    "IdempotencyConflictError", "SensitiveRelationshipError", "RelationshipNotFoundError",
    "compute_relationship_idempotency_key",
    "relationship_engine", "MemoryRelationshipEngine",
    "relationship_reconciliation_engine", "RelationshipReconciliationEngine",
    "RelationshipReconciliationReport", "run_relationship_migration",
    # V5.3.5 Freshness, Confidence & Importance Evolution
    "FreshnessClass", "FreshnessParameters", "FRESHNESS_CONFIG",
    "EvidencePolarity", "EvidenceType", "EvolutionType",
    "MemoryEvidence", "MemoryEvolutionEvent", "EvolutionResult",
    "MemoryEpistemicProfile", "MemoryEvolutionError", "InadmissibleEvidenceError",
    "InactiveMemoryEvolutionError", "EvolutionValidationError",
    "EvolutionConcurrencyError", "EvolutionIdempotencyConflictError",
    "SensitiveEvidencePolicyError", "clamp_float",
    "project_confidence_score_to_level", "project_confidence_level_to_score",
    "compute_observation_hash", "compute_evidence_idempotency_key",
    "evolution_engine", "MemoryEvolutionEngine",
    "run_evolution_migration", "evolution_reconciliation_engine",
    "MemoryEvolutionReconciliationEngine", "EvolutionReconciliationReport",
    # V5.3.6 Project & Experience Intelligence
    "ProjectLifecycleStatus", "TaskOutcomeStatus", "LessonScope",
    "TransferStatus", "TransferDecision",
    "ProjectRecord", "ExperienceRecord", "LessonRecord", "StrategyRecord",
    "TransferMatrixRecord", "TransferEvaluationResult", "StrategyExplainabilityProfile",
    "ProjectExperienceError", "ProjectHierarchyCycleError", "InvalidExperienceError",
    "InadmissibleLessonError", "CrossProjectTransferDeniedError", "StrategyReliabilityError",
    "compute_experience_idempotency_hash", "normalize_error_signature",
    "calculate_bayesian_strategy_reliability", "calculate_transfer_confidence",
    "project_experience_engine", "ProjectExperienceEngine",
    "project_migration_engine", "ProjectExperienceMigrationEngine", "run_project_experience_migration",
    "project_reconciliation_engine", "ProjectReconciliationEngine",
    "ProjectReconciliationReport", "run_project_reconciliation",
]

# V5.3.6 Project & Experience Intelligence Imports
from memory.project_models import (
    ProjectLifecycleStatus,
    TaskOutcomeStatus,
    LessonScope,
    TransferStatus,
    TransferDecision,
    ProjectRecord,
    ExperienceRecord,
    LessonRecord,
    StrategyRecord,
    TransferMatrixRecord,
    TransferEvaluationResult,
    StrategyExplainabilityProfile,
    ProjectExperienceError,
    ProjectHierarchyCycleError,
    InvalidExperienceError,
    InadmissibleLessonError,
    CrossProjectTransferDeniedError,
    StrategyReliabilityError,
    compute_experience_idempotency_hash,
    normalize_error_signature,
    calculate_bayesian_strategy_reliability,
    calculate_transfer_confidence,
)
from memory.project_engine import (
    project_experience_engine,
    ProjectExperienceEngine,
)
from memory.project_migration import (
    project_migration_engine,
    ProjectExperienceMigrationEngine,
    run_project_experience_migration,
)
from memory.project_reconciliation import (
    project_reconciliation_engine,
    ProjectReconciliationEngine,
    ProjectReconciliationReport,
    run_project_reconciliation,
)


