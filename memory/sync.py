"""
DOOM V5.3.3 — Vector Synchronization Domain Model
Defines sync operations, queue status state machine, outbox work items,
result models, and idempotency key generators.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional
import uuid


def _utcnow() -> str:
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def new_sync_id() -> str:
    """Generate a unique sync work item ID."""
    return f"sync_{uuid.uuid4().hex[:16]}"


# ============================================================================
# 1. ENUMS
# ============================================================================

class SyncOperation(str, Enum):
    """Authoritative vector store operation."""
    UPSERT = "UPSERT"
    DELETE = "DELETE"


class VectorSyncStatus(str, Enum):
    """
    Dedicated state machine for vector synchronization work items.
    Strictly decoupled from MemoryStatus lifecycle states.
    """
    PENDING                 = "PENDING"
    PROCESSING              = "PROCESSING"
    SYNCED                  = "SYNCED"
    RETRY_REQUIRED          = "RETRY_REQUIRED"
    FAILED                  = "FAILED"
    DEAD_LETTER             = "DEAD_LETTER"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


# Canonical transitions for vector sync queue states
VALID_SYNC_TRANSITIONS = {
    VectorSyncStatus.PENDING: {
        VectorSyncStatus.PROCESSING,
        VectorSyncStatus.FAILED,
    },
    VectorSyncStatus.PROCESSING: {
        VectorSyncStatus.SYNCED,
        VectorSyncStatus.RETRY_REQUIRED,
        VectorSyncStatus.FAILED,
        VectorSyncStatus.DEAD_LETTER,
    },
    VectorSyncStatus.RETRY_REQUIRED: {
        VectorSyncStatus.PROCESSING,
        VectorSyncStatus.DEAD_LETTER,
        VectorSyncStatus.FAILED,
    },
    VectorSyncStatus.SYNCED: {
        # Terminal success; re-evaluated only via reconciliation
        VectorSyncStatus.RECONCILIATION_REQUIRED,
    },
    VectorSyncStatus.FAILED: {
        VectorSyncStatus.RECONCILIATION_REQUIRED,
        VectorSyncStatus.PENDING,
    },
    VectorSyncStatus.DEAD_LETTER: {
        VectorSyncStatus.RECONCILIATION_REQUIRED,
        VectorSyncStatus.PENDING,
    },
    VectorSyncStatus.RECONCILIATION_REQUIRED: {
        VectorSyncStatus.PENDING,
        VectorSyncStatus.PROCESSING,
    },
}


def validate_sync_transition(current_status: VectorSyncStatus, target_status: VectorSyncStatus) -> bool:
    """Validate whether transition between queue states is permitted."""
    if current_status == target_status:
        return True
    allowed = VALID_SYNC_TRANSITIONS.get(current_status, set())
    return target_status in allowed


# ============================================================================
# 2. WORK ITEM & RESULT MODELS
# ============================================================================

@dataclass
class VectorSyncWorkItem:
    """
    Persistent transactional work item representing an intent to update VectorStore.
    Stored in vector_sync_queue table.
    Never stores raw memory content or raw vector embeddings.
    """
    sync_id: str
    memory_id: str
    operation: str                         # "UPSERT" or "DELETE"
    target_generation: int                 # Monotonic generation snapshot
    target_status: str                     # MemoryStatus string snapshot
    idempotency_key: Optional[str] = None
    sync_status: str = VectorSyncStatus.PENDING.value
    attempt_count: int = 0
    max_attempts: int = 5
    available_at: str = field(default_factory=_utcnow)
    locked_until: Optional[str] = None
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    last_error_class: Optional[str] = None
    last_error_message_safe: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sync_id": self.sync_id,
            "memory_id": self.memory_id,
            "operation": self.operation,
            "target_generation": self.target_generation,
            "target_status": self.target_status,
            "idempotency_key": self.idempotency_key,
            "sync_status": self.sync_status,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "available_at": str(self.available_at),
            "locked_until": str(self.locked_until) if self.locked_until else None,
            "created_at": str(self.created_at),
            "updated_at": str(self.updated_at),
            "last_error_class": self.last_error_class,
            "last_error_message_safe": self.last_error_message_safe,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VectorSyncWorkItem":
        return cls(
            sync_id=data["sync_id"],
            memory_id=data["memory_id"],
            operation=data["operation"],
            target_generation=int(data.get("target_generation", 1)),
            target_status=data.get("target_status", "ACTIVE"),
            idempotency_key=data.get("idempotency_key"),
            sync_status=data.get("sync_status", VectorSyncStatus.PENDING.value),
            attempt_count=int(data.get("attempt_count", 0)),
            max_attempts=int(data.get("max_attempts", 5)),
            available_at=str(data.get("available_at", _utcnow())),
            locked_until=str(data["locked_until"]) if data.get("locked_until") else None,
            created_at=str(data.get("created_at", _utcnow())),
            updated_at=str(data.get("updated_at", _utcnow())),
            last_error_class=data.get("last_error_class"),
            last_error_message_safe=data.get("last_error_message_safe"),
        )

@dataclass
class VectorSyncResult:
    """Execution result returned by VectorSyncEngine."""
    success: bool
    sync_id: str
    memory_id: str
    operation: str
    generation: int
    status: str
    error: Optional[str] = None
    is_stale: bool = False
    is_skipped: bool = False
    duration_ms: float = 0.0


@dataclass
class ReconciliationReport:
    """Diagnostic audit report generated by VectorReconciliationEngine."""
    scanned_records: int = 0
    missing_vectors_detected: int = 0
    missing_vectors_repaired: int = 0
    zombie_vectors_detected: int = 0
    zombie_vectors_purged: int = 0
    orphan_vectors_detected: int = 0
    orphan_vectors_purged: int = 0
    sensitive_vectors_detected: int = 0
    sensitive_vectors_purged: int = 0
    stale_generations_detected: int = 0
    stale_generations_repaired: int = 0
    duration_ms: float = 0.0
    errors: List[str] = field(default_factory=list)
    missing_vectors: List[str] = field(default_factory=list)
    zombie_vectors: List[str] = field(default_factory=list)
    orphan_vectors: List[str] = field(default_factory=list)
    sensitive_vectors: List[str] = field(default_factory=list)
    stale_generations: List[str] = field(default_factory=list)

    @property
    def fixed_count(self) -> int:
        return (
            self.missing_vectors_repaired
            + self.zombie_vectors_purged
            + self.orphan_vectors_purged
            + self.sensitive_vectors_purged
            + self.stale_generations_repaired
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scanned_records": self.scanned_records,
            "missing_vectors_detected": self.missing_vectors_detected,
            "missing_vectors_repaired": self.missing_vectors_repaired,
            "zombie_vectors_detected": self.zombie_vectors_detected,
            "zombie_vectors_purged": self.zombie_vectors_purged,
            "orphan_vectors_detected": self.orphan_vectors_detected,
            "orphan_vectors_purged": self.orphan_vectors_purged,
            "sensitive_vectors_detected": self.sensitive_vectors_detected,
            "sensitive_vectors_purged": self.sensitive_vectors_purged,
            "stale_generations_detected": self.stale_generations_detected,
            "stale_generations_repaired": self.stale_generations_repaired,
            "fixed_count": self.fixed_count,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
            "missing_vectors": self.missing_vectors,
            "zombie_vectors": self.zombie_vectors,
            "orphan_vectors": self.orphan_vectors,
            "sensitive_vectors": self.sensitive_vectors,
            "stale_generations": self.stale_generations,
        }


# ============================================================================
# 3. IDEMPOTENCY KEY GENERATION
# ============================================================================

def compute_sync_idempotency_key(
    memory_id: str,
    operation: Any,
    target_status: Any = "ACTIVE",
    target_generation: Any = 1,
    content_hash: Optional[str] = None,
    content: Optional[str] = None,
) -> str:
    """
    Generate a deterministic, monotonic idempotency key for vector synchronization.
    Prevents duplicate queue insertions for identical state and generation.
    Supports both:
      (memory_id, operation, target_status, target_generation, content_hash)
      (memory_id, operation, target_generation, content)
    """
    clean_mid = (memory_id or "").strip()
    clean_op = (operation.value if hasattr(operation, "value") else str(operation or "")).strip().upper()

    # Handle positional signature if 3rd argument is int (target_generation)
    if isinstance(target_status, int):
        gen = target_status
        raw_hash_or_content = target_generation
        stat = "ACTIVE"
    else:
        stat = target_status.value if hasattr(target_status, "value") else str(target_status or "ACTIVE")
        gen = target_generation if isinstance(target_generation, int) else 1
        raw_hash_or_content = content_hash or content

    clean_stat = stat.strip().upper()

    if raw_hash_or_content:
        import hashlib
        raw_s = str(raw_hash_or_content).strip()
        if len(raw_s) == 64:  # Already sha256 hex
            chash = raw_s[:12]
        else:
            chash = hashlib.sha256(raw_s.encode("utf-8")).hexdigest()[:12]
    else:
        chash = "nohash"

    return f"v533_sync_{clean_mid}_{clean_op}_{clean_stat}_g{gen}_{chash}"
