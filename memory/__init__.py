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
]

