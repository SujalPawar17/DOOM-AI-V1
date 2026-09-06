"""
DOOM V5.3.1 — Memory Lifecycle Foundation
Canonical lifecycle states, transition matrix, typed exceptions,
lifecycle audit event schema, and transition validation infrastructure.

V5.3.1 SCOPE: Foundation only.
- State definitions & transition matrix
- Typed exceptions
- Lifecycle event schema (memory_lifecycle_events)
- Validation helpers
- Backward-compatible V5.1 manager interface
"""
import uuid
import json
import re
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Set

from memory.types import MemoryStatus, ConfidenceLevel, VerificationStatus, PrivacyClass


def _utcnow() -> str:
    """Return current UTC timestamp as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def new_lifecycle_event_id() -> str:
    """Generate a unique lifecycle event ID."""
    return f"evt_{uuid.uuid4().hex[:16]}"


# ============================================================================
# 1. TYPED LIFECYCLE EXCEPTIONS
# ============================================================================

class MemoryLifecycleError(Exception):
    """
    Base exception for all memory lifecycle errors.
    Deterministic, fail-closed, and strictly sanitized.
    NEVER leaks raw memory content, queries, or secrets in messages.
    """
    def __init__(self, message: str, memory_id: Optional[str] = None):
        self.memory_id = memory_id
        # Sanitize message to prevent accidental text injection
        clean_msg = message.replace("\r", " ").replace("\n", " ")
        super().__init__(clean_msg)


class InvalidLifecycleStateError(MemoryLifecycleError):
    """Raised when an unparseable or unsupported lifecycle state value is encountered."""
    pass


class InvalidLifecycleTransitionError(MemoryLifecycleError):
    """Raised when a state transition violates the canonical lifecycle state machine."""
    def __init__(self, from_state: Any, to_state: Any, memory_id: Optional[str] = None, reason: str = ""):
        self.from_state = from_state
        self.to_state = to_state
        detail = f" (Reason: {reason})" if reason else ""
        msg = f"Invalid memory lifecycle transition: '{from_state}' -> '{to_state}'{detail}"
        super().__init__(msg, memory_id=memory_id)


class MemoryAlreadyDeletedError(InvalidLifecycleTransitionError):
    """
    Raised when attempting any state transition on a memory that is already DELETED.
    DELETED is a strictly terminal state in V5.3.
    """
    def __init__(self, to_state: Any, memory_id: Optional[str] = None):
        super().__init__(
            from_state=MemoryStatus.DELETED.value,
            to_state=to_state,
            memory_id=memory_id,
            reason="DELETED is a terminal state and cannot transition to any other status."
        )


class MemoryNotFoundError(MemoryLifecycleError):
    """Raised when a requested memory record does not exist in memory_records."""
    pass


class LifecycleValidationError(MemoryLifecycleError):
    """Raised when lifecycle transition parameters or event metadata fail validation."""
    pass


class LifecycleAuditError(MemoryLifecycleError):
    """Raised when writing or serializing a lifecycle audit event fails."""
    pass


class ProvenanceValidationError(LifecycleValidationError):
    """Raised when provenance verification rules (e.g., PENDING_VERIFICATION -> ACTIVE) fail."""
    pass


class ConcurrentModificationError(MemoryLifecycleError):
    """Raised when a concurrent modification or race condition is detected during transition."""
    pass


class LockTimeoutError(MemoryLifecycleError):
    """Raised when a database row lock cannot be acquired within the timeout."""
    pass


class DeadlockDetectedError(MemoryLifecycleError):
    """Raised when a database deadlock is detected during a lifecycle transaction."""
    pass


class DatabaseConnectionError(MemoryLifecycleError):
    """Raised when unable to acquire a database connection for lifecycle operations."""
    pass


def is_retryable_lifecycle_error(ex: Exception) -> bool:
    """Classify exceptions into retryable (concurrency, timeout, db) vs non-retryable (validation, deleted)."""
    if isinstance(ex, (LockTimeoutError, DeadlockDetectedError, DatabaseConnectionError, ConcurrentModificationError)):
        return True
    from database.postgres_db import (
        LockTimeoutError as DBLockTimeoutError,
        DeadlockDetectedError as DBDeadlockDetectedError,
        DatabaseConnectionError as DBConnectionError,
    )
    if isinstance(ex, (DBLockTimeoutError, DBDeadlockDetectedError, DBConnectionError)):
        return True
    err_str = str(ex).lower()
    if "lock_timeout" in err_str or "canceling statement due to lock timeout" in err_str or "deadlock detected" in err_str:
        return True
    return False


# ============================================================================
# 2. LIFECYCLE ACTOR MODEL
# ============================================================================

class LifecycleActor(str, Enum):
    """Authoritative entities permitted to initiate or record lifecycle transitions."""
    USER             = "USER"             # Explicit user command ("forget this", "archive project X")
    SYSTEM           = "SYSTEM"           # System maintenance, garbage collection, reconciliation
    TASK             = "TASK"             # Verified task execution outcome / GroundTruthVerifier
    LIFECYCLE_ENGINE = "LIFECYCLE_ENGINE" # Autonomous lifecycle rule engine (retention, decay)


# ============================================================================
# 3. LIFECYCLE TRANSITION MODEL & CANONICAL MATRIX
# ============================================================================

@dataclass(frozen=True)
class LifecycleTransition:
    """
    Structured definition of an individual state transition rule.
    Immutable, validated specification of permission and constraints.
    """
    from_state: MemoryStatus
    to_state: MemoryStatus
    allowed: bool
    reason_required: bool = False
    actor_required: bool = True
    audit_required: bool = True
    description: str = ""


# Canonical state transition matrix for DOOM V5.3:
# Matrix covers all pairs of the 5 canonical MemoryStatus values.
LIFECYCLE_TRANSITIONS: Dict[Tuple[MemoryStatus, MemoryStatus], LifecycleTransition] = {
    # ------------------------------------------------------------------------
    # From PENDING_VERIFICATION
    # ------------------------------------------------------------------------
    (MemoryStatus.PENDING_VERIFICATION, MemoryStatus.ACTIVE): LifecycleTransition(
        from_state=MemoryStatus.PENDING_VERIFICATION,
        to_state=MemoryStatus.ACTIVE,
        allowed=True,
        reason_required=False,
        description="Corroboration confirmed by verification evidence or user explicit approval.",
    ),
    (MemoryStatus.PENDING_VERIFICATION, MemoryStatus.DELETED): LifecycleTransition(
        from_state=MemoryStatus.PENDING_VERIFICATION,
        to_state=MemoryStatus.DELETED,
        allowed=True,
        reason_required=False,
        description="Pending claim refuted, contradicted, or discarded without corroboration.",
    ),
    (MemoryStatus.PENDING_VERIFICATION, MemoryStatus.SUPERSEDED): LifecycleTransition(
        from_state=MemoryStatus.PENDING_VERIFICATION,
        to_state=MemoryStatus.SUPERSEDED,
        allowed=False,
        description="FORBIDDEN: Unverified memories cannot be superseded directly; must be activated or deleted.",
    ),
    (MemoryStatus.PENDING_VERIFICATION, MemoryStatus.ARCHIVED): LifecycleTransition(
        from_state=MemoryStatus.PENDING_VERIFICATION,
        to_state=MemoryStatus.ARCHIVED,
        allowed=False,
        description="FORBIDDEN: Unverified memories cannot be archived into durable history.",
    ),

    # ------------------------------------------------------------------------
    # From ACTIVE
    # ------------------------------------------------------------------------
    (MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED): LifecycleTransition(
        from_state=MemoryStatus.ACTIVE,
        to_state=MemoryStatus.SUPERSEDED,
        allowed=True,
        reason_required=False,
        description="Active memory replaced by newer, more authoritative evidence or preference.",
    ),
    (MemoryStatus.ACTIVE, MemoryStatus.ARCHIVED): LifecycleTransition(
        from_state=MemoryStatus.ACTIVE,
        to_state=MemoryStatus.ARCHIVED,
        allowed=True,
        reason_required=False,
        description="Project completed, milestone reached, or memory retired from active context.",
    ),
    (MemoryStatus.ACTIVE, MemoryStatus.DELETED): LifecycleTransition(
        from_state=MemoryStatus.ACTIVE,
        to_state=MemoryStatus.DELETED,
        allowed=True,
        reason_required=False,
        description="Explicit user forget command, privacy exclusion, or security wipe.",
    ),
    (MemoryStatus.ACTIVE, MemoryStatus.PENDING_VERIFICATION): LifecycleTransition(
        from_state=MemoryStatus.ACTIVE,
        to_state=MemoryStatus.PENDING_VERIFICATION,
        allowed=False,
        description="FORBIDDEN: Active memories cannot demote to pending verification.",
    ),

    # ------------------------------------------------------------------------
    # From SUPERSEDED
    # ------------------------------------------------------------------------
    (MemoryStatus.SUPERSEDED, MemoryStatus.ARCHIVED): LifecycleTransition(
        from_state=MemoryStatus.SUPERSEDED,
        to_state=MemoryStatus.ARCHIVED,
        allowed=True,
        reason_required=False,
        description="Archival of superseded historical chains during periodic lifecycle maintenance.",
    ),
    (MemoryStatus.SUPERSEDED, MemoryStatus.DELETED): LifecycleTransition(
        from_state=MemoryStatus.SUPERSEDED,
        to_state=MemoryStatus.DELETED,
        allowed=True,
        reason_required=False,
        description="User explicit deletion of old historical memories.",
    ),
    (MemoryStatus.SUPERSEDED, MemoryStatus.ACTIVE): LifecycleTransition(
        from_state=MemoryStatus.SUPERSEDED,
        to_state=MemoryStatus.ACTIVE,
        allowed=False,
        description="FORBIDDEN in V5.3.1: Restoration/reversion reserved for V5.3.4 conflict management.",
    ),
    (MemoryStatus.SUPERSEDED, MemoryStatus.PENDING_VERIFICATION): LifecycleTransition(
        from_state=MemoryStatus.SUPERSEDED,
        to_state=MemoryStatus.PENDING_VERIFICATION,
        allowed=False,
        description="FORBIDDEN: Superseded memories cannot enter pending verification.",
    ),

    # ------------------------------------------------------------------------
    # From ARCHIVED
    # ------------------------------------------------------------------------
    (MemoryStatus.ARCHIVED, MemoryStatus.ACTIVE): LifecycleTransition(
        from_state=MemoryStatus.ARCHIVED,
        to_state=MemoryStatus.ACTIVE,
        allowed=False,
        description="FORBIDDEN in V5.3.1: Restoration/unarchiving reserved for V5.3.6 project lifecycle.",
    ),
    (MemoryStatus.ARCHIVED, MemoryStatus.DELETED): LifecycleTransition(
        from_state=MemoryStatus.ARCHIVED,
        to_state=MemoryStatus.DELETED,
        allowed=True,
        reason_required=False,
        description="Retention period expiration or explicit user purge.",
    ),
    (MemoryStatus.ARCHIVED, MemoryStatus.SUPERSEDED): LifecycleTransition(
        from_state=MemoryStatus.ARCHIVED,
        to_state=MemoryStatus.SUPERSEDED,
        allowed=False,
        description="FORBIDDEN: Archived memories cannot be superseded; they must be restored first.",
    ),
    (MemoryStatus.ARCHIVED, MemoryStatus.PENDING_VERIFICATION): LifecycleTransition(
        from_state=MemoryStatus.ARCHIVED,
        to_state=MemoryStatus.PENDING_VERIFICATION,
        allowed=False,
        description="FORBIDDEN: Archived memories cannot enter pending verification.",
    ),

    # ------------------------------------------------------------------------
    # From DELETED (Terminal State — ALL Outgoing Transitions Forbidden)
    # ------------------------------------------------------------------------
    (MemoryStatus.DELETED, MemoryStatus.ACTIVE): LifecycleTransition(
        from_state=MemoryStatus.DELETED,
        to_state=MemoryStatus.ACTIVE,
        allowed=False,
        description="FORBIDDEN: DELETED is terminal. Deleted records cannot be resurrected directly.",
    ),
    (MemoryStatus.DELETED, MemoryStatus.ARCHIVED): LifecycleTransition(
        from_state=MemoryStatus.DELETED,
        to_state=MemoryStatus.ARCHIVED,
        allowed=False,
        description="FORBIDDEN: Deleted records cannot be moved to archives.",
    ),
    (MemoryStatus.DELETED, MemoryStatus.SUPERSEDED): LifecycleTransition(
        from_state=MemoryStatus.DELETED,
        to_state=MemoryStatus.SUPERSEDED,
        allowed=False,
        description="FORBIDDEN: Deleted records cannot be superseded.",
    ),
    (MemoryStatus.DELETED, MemoryStatus.PENDING_VERIFICATION): LifecycleTransition(
        from_state=MemoryStatus.DELETED,
        to_state=MemoryStatus.PENDING_VERIFICATION,
        allowed=False,
        description="FORBIDDEN: Deleted records cannot enter pending verification.",
    ),
}


# ============================================================================
# 4. TRANSITION VALIDATION HELPERS
# ============================================================================

def coerce_memory_status(status_val: Any) -> MemoryStatus:
    """Coerce string or enum into a canonical MemoryStatus enum or raise InvalidLifecycleStateError."""
    if isinstance(status_val, MemoryStatus):
        return status_val
    if isinstance(status_val, str):
        try:
            return MemoryStatus(status_val.upper())
        except ValueError:
            raise InvalidLifecycleStateError(f"Unknown lifecycle status: '{status_val}'")
    raise InvalidLifecycleStateError(f"Invalid lifecycle status type: {type(status_val).__name__}")


def is_valid_transition(from_state: Any, to_state: Any) -> bool:
    """Check if a transition between two states is permitted by the canonical matrix."""
    try:
        s_from = coerce_memory_status(from_state)
        s_to = coerce_memory_status(to_state)
    except InvalidLifecycleStateError:
        return False

    if s_from == s_to:
        return False  # Self-transition is not a valid state change

    rule = LIFECYCLE_TRANSITIONS.get((s_from, s_to))
    return rule.allowed if rule else False


def get_transition(from_state: Any, to_state: Any) -> Optional[LifecycleTransition]:
    """Retrieve the transition rule for a pair of states, or None if unknown."""
    try:
        s_from = coerce_memory_status(from_state)
        s_to = coerce_memory_status(to_state)
        return LIFECYCLE_TRANSITIONS.get((s_from, s_to))
    except InvalidLifecycleStateError:
        return None


def validate_transition(
    from_state: Any,
    to_state: Any,
    memory_id: Optional[str] = None,
    reason: Optional[str] = None,
    actor: Optional[Any] = None,
) -> LifecycleTransition:
    """
    Authoritative transition validator.
    Raises typed exceptions if transition is forbidden, malformed, or missing required fields.
    Returns the validated LifecycleTransition rule on success.
    """
    s_from = coerce_memory_status(from_state)
    s_to = coerce_memory_status(to_state)

    if s_from == MemoryStatus.DELETED:
        raise MemoryAlreadyDeletedError(to_state=s_to.value, memory_id=memory_id)

    if s_from == s_to:
        raise LifecycleValidationError(
            f"Cannot transition memory from '{s_from.value}' to itself.",
            memory_id=memory_id,
        )

    rule = LIFECYCLE_TRANSITIONS.get((s_from, s_to))
    if not rule or not rule.allowed:
        desc = rule.description if rule else "No transition path defined."
        raise InvalidLifecycleTransitionError(
            from_state=s_from.value,
            to_state=s_to.value,
            memory_id=memory_id,
            reason=desc,
        )

    # Validate required reason
    if rule.reason_required and (not reason or not reason.strip()):
        raise LifecycleValidationError(
            f"Transition from '{s_from.value}' to '{s_to.value}' requires a non-empty reason.",
            memory_id=memory_id,
        )

    # Validate reason length if provided
    if reason and len(reason) > 255:
        raise LifecycleValidationError(
            f"Transition reason exceeds maximum allowed length of 255 characters (length={len(reason)}).",
            memory_id=memory_id,
        )

    return rule


def validate_provenance(
    from_state: Any,
    to_state: Any,
    actor: Optional[Any] = None,
    reason: Optional[str] = None,
    task_id: Optional[str] = None,
    source_event_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    memory_id: Optional[str] = None,
) -> bool:
    """
    Validate provenance when transitioning PENDING_VERIFICATION -> ACTIVE.
    Rules:
    - USER actor: non-empty reason, minimum 5 characters.
    - TASK actor: task_id OR source_event_id required.
    - SYSTEM actor: requires explicit corroboration mechanism in metadata and non-empty reason.
    - LIFECYCLE_ENGINE actor: forbidden from automatically granting verification authority.
    - Never stores raw verification evidence.
    """
    s_from = coerce_memory_status(from_state)
    s_to = coerce_memory_status(to_state)

    if s_from == MemoryStatus.PENDING_VERIFICATION and s_to == MemoryStatus.ACTIVE:
        act_str = actor.value if hasattr(actor, "value") else str(actor or "")
        act_upper = act_str.upper()

        if act_upper == LifecycleActor.USER.value:
            if not reason or len(reason.strip()) < 5:
                raise ProvenanceValidationError(
                    "USER verification provenance requires a non-empty reason of at least 5 characters.",
                    memory_id=memory_id,
                )
        elif act_upper == LifecycleActor.TASK.value:
            if not (task_id and task_id.strip()) and not (source_event_id and source_event_id.strip()):
                raise ProvenanceValidationError(
                    "TASK verification provenance requires task_id or source_event_id.",
                    memory_id=memory_id,
                )
        elif act_upper == LifecycleActor.SYSTEM.value:
            meta = metadata or {}
            has_corroboration = any(
                k in meta for k in ("corroboration_source", "provenance_rule", "system_corroboration", "verifier_id")
            )
            if not has_corroboration or not (reason and reason.strip()):
                raise ProvenanceValidationError(
                    "SYSTEM verification provenance requires explicit corroboration mechanism in metadata and non-empty reason.",
                    memory_id=memory_id,
                )
        elif act_upper == LifecycleActor.LIFECYCLE_ENGINE.value:
            raise ProvenanceValidationError(
                "LIFECYCLE_ENGINE actor cannot automatically grant verification authority.",
                memory_id=memory_id,
            )
        else:
            raise ProvenanceValidationError(
                f"Unknown or unauthorized verification actor: '{actor}'.",
                memory_id=memory_id,
            )
    return True


@dataclass
class LifecycleTransitionResult:
    """
    Authoritative result object returned by MemoryLifecycleEngine.
    Immutable, deterministic outcome of an atomic state transition.
    """
    success: bool
    memory_id: str
    previous_status: Optional[MemoryStatus] = None
    new_status: Optional[MemoryStatus] = None
    event_id: Optional[str] = None
    transition_timestamp: str = field(default_factory=_utcnow)
    error: Optional[str] = None
    idempotent_replay: bool = False



# ============================================================================
# 5. LIFECYCLE AUDIT EVENT SCHEMA
# ============================================================================

# Forbidden metadata keys that might leak raw text, queries, or secrets
FORBIDDEN_METADATA_KEYS: Set[str] = {
    "content", "raw_content", "query", "raw_query", "embedding", "vector",
    "password", "secret", "token", "api_key", "bearer", "authorization",
    "credential", "access_key", "private_key",
}


@dataclass
class MemoryLifecycleEvent:
    """
    Canonical immutable lifecycle audit event.
    Corresponds directly to PostgreSQL table 'memory_lifecycle_events'.
    Guarantees zero raw memory content, query text, embeddings, or secrets.
    """
    memory_id: str
    previous_status: MemoryStatus
    new_status: MemoryStatus
    event_id: str = field(default_factory=new_lifecycle_event_id)
    transition_reason: str = ""
    actor: str = LifecycleActor.SYSTEM.value
    related_memory_id: Optional[str] = None
    source_event_id: Optional[str] = None
    task_id: Optional[str] = None
    correlation_id: Optional[str] = None
    confidence_before: Optional[str] = None
    confidence_after: Optional[str] = None
    importance_before: Optional[float] = None
    importance_after: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    idempotency_key: Optional[str] = None
    created_at: str = field(default_factory=_utcnow)

    def __post_init__(self):
        # Validate and coerce statuses
        self.previous_status = coerce_memory_status(self.previous_status)
        self.new_status = coerce_memory_status(self.new_status)

        # Enforce sanitized, bounded reason
        if self.transition_reason:
            clean_reason = self.transition_reason.replace("\r", " ").replace("\n", " ").strip()
            if len(clean_reason) > 255:
                clean_reason = clean_reason[:252] + "..."
            self.transition_reason = clean_reason

        # Security check on metadata keys
        if self.metadata:
            for key in self.metadata.keys():
                k_lower = key.lower()
                for forbidden in FORBIDDEN_METADATA_KEYS:
                    if forbidden in k_lower:
                        raise LifecycleAuditError(
                            f"Illegal metadata key in lifecycle audit event: '{key}' contains forbidden token '{forbidden}'."
                        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary for persistence or telemetry."""
        return {
            "event_id": self.event_id,
            "memory_id": self.memory_id,
            "previous_status": self.previous_status.value,
            "new_status": self.new_status.value,
            "transition_reason": self.transition_reason,
            "actor": self.actor,
            "related_memory_id": self.related_memory_id,
            "source_event_id": self.source_event_id,
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "confidence_before": self.confidence_before,
            "confidence_after": self.confidence_after,
            "importance_before": self.importance_before,
            "importance_after": self.importance_after,
            "metadata": self.metadata,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryLifecycleEvent":
        """Deserialize from database row dictionary."""
        return cls(
            event_id=data.get("event_id", new_lifecycle_event_id()),
            memory_id=data.get("memory_id", ""),
            previous_status=coerce_memory_status(data.get("previous_status", MemoryStatus.ACTIVE.value)),
            new_status=coerce_memory_status(data.get("new_status", MemoryStatus.ACTIVE.value)),
            transition_reason=data.get("transition_reason", ""),
            actor=data.get("actor", LifecycleActor.SYSTEM.value),
            related_memory_id=data.get("related_memory_id"),
            source_event_id=data.get("source_event_id"),
            task_id=data.get("task_id"),
            correlation_id=data.get("correlation_id"),
            confidence_before=data.get("confidence_before"),
            confidence_after=data.get("confidence_after"),
            importance_before=float(data["importance_before"]) if data.get("importance_before") is not None else None,
            importance_after=float(data["importance_after"]) if data.get("importance_after") is not None else None,
            metadata=data.get("metadata") or {},
            idempotency_key=data.get("idempotency_key"),
            created_at=data["created_at"].isoformat() if hasattr(data.get("created_at"), "isoformat") else str(data.get("created_at", _utcnow())),
        )


def _emit_lifecycle_telemetry(
    event: str,
    memory_id: str,
    previous_status: Optional[str],
    new_status: Optional[str],
    actor: str,
    event_id: Optional[str],
    task_id: Optional[str],
    correlation_id: Optional[str],
    duration_ms: float,
    success: bool,
    idempotent_replay: bool,
) -> None:
    """
    Emit structured lifecycle telemetry.
    Guaranteed zero raw content, query text, embeddings, or secrets.
    Failures in telemetry are safely absorbed without impacting transactions.
    """
    try:
        telemetry_payload = {
            "event": event,
            "memory_id": memory_id,
            "previous_status": previous_status,
            "new_status": new_status,
            "actor": actor,
            "event_id": event_id,
            "task_id": task_id,
            "correlation_id": correlation_id,
            "duration_ms": round(duration_ms, 3),
            "success": success,
            "idempotent_replay": idempotent_replay,
        }
    except Exception:
        pass


# ============================================================================
# 6. AUTHORITATIVE MEMORY LIFECYCLE ENGINE (V5.3.2)
# ============================================================================

class MemoryLifecycleEngine:
    """
    DOOM V5.3.2 Authoritative Memory Lifecycle Engine.
    Enforces ACID transactions, row-level locking (FOR UPDATE),
    atomic state + audit log commit, idempotency, and provenance verification.
    ZERO tool authority. ZERO network or LLM operations inside transaction.
    """

    def _get_manager(self):
        from database.postgres_db import postgres_manager
        return postgres_manager

    def transition_memory(
        self,
        memory_id: str,
        target_status: Any,
        reason: str = "",
        actor: Any = LifecycleActor.SYSTEM.value,
        source_event_id: Optional[str] = None,
        task_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        related_memory_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> LifecycleTransitionResult:
        """
        Canonical entry point for single-memory lifecycle transitions.
        Execution sequence:
        BEGIN -> LOCK (FOR UPDATE) -> READ STATE -> IDEMPOTENCY CHECK ->
        VALIDATE TRANSITION -> VALIDATE PROVENANCE -> UPDATE STATUS ->
        INSERT AUDIT EVENT -> COMMIT -> POST-COMMIT TELEMETRY
        """
        import time
        t0 = time.perf_counter()
        pg = self._get_manager()
        act_str = actor.value if hasattr(actor, "value") else str(actor)
        target_s = coerce_memory_status(target_status)

        current_status = None
        event_id = None
        try:
            from psycopg2 import extras
            with pg.transaction(lock_timeout_ms=3000) as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    # 1. Pessimistic Row Lock (3000ms timeout enforced by session)
                    cur.execute(
                        "SELECT memory_id, status, confidence, importance FROM memory_records WHERE memory_id = %s FOR UPDATE;",
                        (memory_id,)
                    )
                    row = cur.fetchone()
                    if not row:
                        raise MemoryNotFoundError(f"Memory record not found: '{memory_id}'", memory_id=memory_id)

                    # 2. Authoritative Current State (post-lock)
                    current_status = coerce_memory_status(row["status"])
                    conf_before = row.get("confidence")
                    imp_before = float(row["importance"]) if row.get("importance") is not None else None

                    # 3. Idempotency Check
                    if idempotency_key:
                        cur.execute(
                            "SELECT event_id, memory_id, previous_status, new_status, created_at FROM memory_lifecycle_events WHERE idempotency_key = %s AND memory_id = %s LIMIT 1;",
                            (idempotency_key, memory_id)
                        )
                        existing_evt = cur.fetchone()
                        if existing_evt:
                            duration_ms = (time.perf_counter() - t0) * 1000
                            _emit_lifecycle_telemetry(
                                event="LIFECYCLE_TRANSITION_IDEMPOTENT_REPLAY",
                                memory_id=memory_id,
                                previous_status=existing_evt["previous_status"],
                                new_status=existing_evt["new_status"],
                                actor=act_str,
                                event_id=existing_evt["event_id"],
                                task_id=task_id,
                                correlation_id=correlation_id,
                                duration_ms=duration_ms,
                                success=True,
                                idempotent_replay=True,
                            )
                            return LifecycleTransitionResult(
                                success=True,
                                memory_id=memory_id,
                                previous_status=coerce_memory_status(existing_evt["previous_status"]),
                                new_status=coerce_memory_status(existing_evt["new_status"]),
                                event_id=existing_evt["event_id"],
                                transition_timestamp=str(existing_evt["created_at"]),
                                idempotent_replay=True,
                            )

                    # 4. Validate State Transition
                    validate_transition(
                        from_state=current_status,
                        to_state=target_s,
                        memory_id=memory_id,
                        reason=reason,
                        actor=actor,
                    )

                    # 5. Validate Provenance
                    validate_provenance(
                        from_state=current_status,
                        to_state=target_s,
                        actor=actor,
                        reason=reason,
                        task_id=task_id,
                        source_event_id=source_event_id,
                        metadata=metadata,
                        memory_id=memory_id,
                    )

                    # 6. Apply Status Update
                    cur.execute(
                        "UPDATE memory_records SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE memory_id = %s;",
                        (target_s.value, memory_id)
                    )

                    # 7. Insert Audit Event
                    evt = MemoryLifecycleEvent(
                        memory_id=memory_id,
                        previous_status=current_status,
                        new_status=target_s,
                        transition_reason=reason or "",
                        actor=act_str,
                        related_memory_id=related_memory_id,
                        source_event_id=source_event_id,
                        task_id=task_id,
                        correlation_id=correlation_id,
                        confidence_before=conf_before,
                        confidence_after=conf_before,
                        importance_before=imp_before,
                        importance_after=imp_before,
                        metadata=metadata or {},
                        idempotency_key=idempotency_key,
                    )
                    event_id = evt.event_id

                    cur.execute("""
                        INSERT INTO memory_lifecycle_events (
                            event_id, memory_id, previous_status, new_status,
                            transition_reason, actor, related_memory_id,
                            source_event_id, task_id, correlation_id,
                            confidence_before, confidence_after,
                            importance_before, importance_after,
                            metadata, idempotency_key, created_at
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s,
                            %s, %s,
                            %s, %s,
                            %s, %s, %s
                        );
                    """, (
                        evt.event_id,
                        evt.memory_id,
                        evt.previous_status.value,
                        evt.new_status.value,
                        evt.transition_reason,
                        evt.actor,
                        evt.related_memory_id,
                        evt.source_event_id,
                        evt.task_id,
                        evt.correlation_id,
                        evt.confidence_before,
                        evt.confidence_after,
                        evt.importance_before,
                        evt.importance_after,
                        json.dumps(evt.metadata or {}, default=str),
                        evt.idempotency_key,
                        evt.created_at,
                    ))

            # 8. Post-Commit Telemetry
            duration_ms = (time.perf_counter() - t0) * 1000
            _emit_lifecycle_telemetry(
                event="LIFECYCLE_TRANSITION_COMMITTED",
                memory_id=memory_id,
                previous_status=current_status.value if current_status else None,
                new_status=target_s.value,
                actor=act_str,
                event_id=event_id,
                task_id=task_id,
                correlation_id=correlation_id,
                duration_ms=duration_ms,
                success=True,
                idempotent_replay=False,
            )
            return LifecycleTransitionResult(
                success=True,
                memory_id=memory_id,
                previous_status=current_status,
                new_status=target_s,
                event_id=event_id,
                transition_timestamp=evt.created_at,
                idempotent_replay=False,
            )

        except Exception as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            _emit_lifecycle_telemetry(
                event="LIFECYCLE_TRANSITION_FAILED",
                memory_id=memory_id,
                previous_status=current_status.value if current_status else None,
                new_status=target_s.value if target_s else None,
                actor=act_str,
                event_id=event_id,
                task_id=task_id,
                correlation_id=correlation_id,
                duration_ms=duration_ms,
                success=False,
                idempotent_replay=False,
            )
            if raise_on_error:
                raise
            return LifecycleTransitionResult(
                success=False,
                memory_id=memory_id,
                previous_status=current_status,
                new_status=None,
                error=str(e),
                idempotent_replay=False,
            )

    def supersede_memory(
        self,
        old_memory_id: str,
        new_record: Any,
        reason: str = "Superseded by newer record",
        actor: Any = LifecycleActor.SYSTEM.value,
        idempotency_key: Optional[str] = None,
        task_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> LifecycleTransitionResult:
        """
        Atomic 1:1 Supersession within a single PostgreSQL transaction.
        BEGIN
        1. Lock old memory FOR UPDATE
        2. Read authoritative state; validate transition to SUPERSEDED
        3. Check idempotency
        4. Store new memory record (with supersedes_memory_id = old_memory_id)
        5. Set old memory SUPERSEDED
        6. Insert audit event for old memory
        COMMIT
        """
        import time
        t0 = time.perf_counter()
        pg = self._get_manager()
        act_str = actor.value if hasattr(actor, "value") else str(actor)

        old_status = None
        event_id = None
        try:
            from psycopg2 import extras
            with pg.transaction(lock_timeout_ms=3000) as conn:
                with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                    # 1. Lock old memory FOR UPDATE
                    cur.execute(
                        "SELECT memory_id, status, confidence, importance FROM memory_records WHERE memory_id = %s FOR UPDATE;",
                        (old_memory_id,)
                    )
                    row = cur.fetchone()
                    if not row:
                        raise MemoryNotFoundError(f"Memory record to supersede not found: '{old_memory_id}'", memory_id=old_memory_id)

                    old_status = coerce_memory_status(row["status"])
                    conf_before = row.get("confidence")
                    imp_before = float(row["importance"]) if row.get("importance") is not None else None

                    # 2. Validate transition from old state to SUPERSEDED
                    validate_transition(
                        from_state=old_status,
                        to_state=MemoryStatus.SUPERSEDED,
                        memory_id=old_memory_id,
                        reason=reason,
                        actor=actor,
                    )

                    # 3. Idempotency Check
                    if idempotency_key:
                        cur.execute(
                            "SELECT event_id, memory_id, previous_status, new_status, created_at FROM memory_lifecycle_events WHERE idempotency_key = %s AND memory_id = %s LIMIT 1;",
                            (idempotency_key, old_memory_id)
                        )
                        existing_evt = cur.fetchone()
                        if existing_evt:
                            duration_ms = (time.perf_counter() - t0) * 1000
                            _emit_lifecycle_telemetry(
                                event="LIFECYCLE_SUPERSEDE_IDEMPOTENT_REPLAY",
                                memory_id=old_memory_id,
                                previous_status=existing_evt["previous_status"],
                                new_status=existing_evt["new_status"],
                                actor=act_str,
                                event_id=existing_evt["event_id"],
                                task_id=task_id,
                                correlation_id=correlation_id,
                                duration_ms=duration_ms,
                                success=True,
                                idempotent_replay=True,
                            )
                            return LifecycleTransitionResult(
                                success=True,
                                memory_id=old_memory_id,
                                previous_status=coerce_memory_status(existing_evt["previous_status"]),
                                new_status=coerce_memory_status(existing_evt["new_status"]),
                                event_id=existing_evt["event_id"],
                                transition_timestamp=str(existing_evt["created_at"]),
                                idempotent_replay=True,
                            )

                    # 4. Link new record to old
                    new_record.supersedes_memory_id = old_memory_id

                    # 5. Store new record inside THIS transaction
                    cur.execute("""
                        INSERT INTO memory_records (
                            memory_id, memory_type, content, source, confidence,
                            importance, status, project_id, task_id, entity_ids, tags,
                            supersedes_memory_id, source_event_id, verification_status,
                            privacy_class, metadata, created_at, updated_at, last_accessed_at
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (memory_id) DO UPDATE SET
                            content = EXCLUDED.content,
                            confidence = EXCLUDED.confidence,
                            importance = EXCLUDED.importance,
                            status = EXCLUDED.status,
                            verification_status = EXCLUDED.verification_status,
                            tags = EXCLUDED.tags,
                            metadata = EXCLUDED.metadata,
                            supersedes_memory_id = EXCLUDED.supersedes_memory_id,
                            updated_at = CURRENT_TIMESTAMP;
                    """, (
                        new_record.memory_id,
                        new_record.memory_type.value if hasattr(new_record.memory_type, "value") else str(new_record.memory_type),
                        new_record.content,
                        new_record.source.value if hasattr(new_record.source, "value") else str(new_record.source),
                        new_record.confidence.value if hasattr(new_record.confidence, "value") else str(new_record.confidence),
                        new_record.importance,
                        new_record.status.value if hasattr(new_record.status, "value") else str(new_record.status),
                        new_record.project_id,
                        new_record.task_id or task_id,
                        json.dumps(new_record.entity_ids or [], default=str),
                        json.dumps(new_record.tags or [], default=str),
                        old_memory_id,
                        new_record.source_event_id,
                        new_record.verification_status.value if hasattr(new_record.verification_status, "value") else str(new_record.verification_status),
                        new_record.privacy_class.value if hasattr(new_record.privacy_class, "value") else str(new_record.privacy_class),
                        json.dumps(new_record.metadata or {}, default=str),
                        new_record.created_at,
                        new_record.updated_at,
                        new_record.last_accessed_at,
                    ))

                    # 6. Transition old memory to SUPERSEDED
                    cur.execute(
                        "UPDATE memory_records SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE memory_id = %s;",
                        (MemoryStatus.SUPERSEDED.value, old_memory_id)
                    )

                    # 7. Insert Audit Event for old memory
                    evt = MemoryLifecycleEvent(
                        memory_id=old_memory_id,
                        previous_status=old_status,
                        new_status=MemoryStatus.SUPERSEDED,
                        transition_reason=reason or "",
                        actor=act_str,
                        related_memory_id=new_record.memory_id,
                        source_event_id=new_record.source_event_id,
                        task_id=task_id or new_record.task_id,
                        correlation_id=correlation_id,
                        confidence_before=conf_before,
                        confidence_after=conf_before,
                        importance_before=imp_before,
                        importance_after=imp_before,
                        metadata={},
                        idempotency_key=idempotency_key,
                    )
                    event_id = evt.event_id

                    cur.execute("""
                        INSERT INTO memory_lifecycle_events (
                            event_id, memory_id, previous_status, new_status,
                            transition_reason, actor, related_memory_id,
                            source_event_id, task_id, correlation_id,
                            confidence_before, confidence_after,
                            importance_before, importance_after,
                            metadata, idempotency_key, created_at
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s,
                            %s, %s,
                            %s, %s,
                            %s, %s, %s
                        );
                    """, (
                        evt.event_id,
                        evt.memory_id,
                        evt.previous_status.value,
                        evt.new_status.value,
                        evt.transition_reason,
                        evt.actor,
                        evt.related_memory_id,
                        evt.source_event_id,
                        evt.task_id,
                        evt.correlation_id,
                        evt.confidence_before,
                        evt.confidence_after,
                        evt.importance_before,
                        evt.importance_after,
                        json.dumps(evt.metadata or {}, default=str),
                        evt.idempotency_key,
                        evt.created_at,
                    ))

            # 8. Post-Commit Telemetry
            duration_ms = (time.perf_counter() - t0) * 1000
            _emit_lifecycle_telemetry(
                event="LIFECYCLE_SUPERSEDE_COMMITTED",
                memory_id=old_memory_id,
                previous_status=old_status.value if old_status else None,
                new_status=MemoryStatus.SUPERSEDED.value,
                actor=act_str,
                event_id=event_id,
                task_id=task_id,
                correlation_id=correlation_id,
                duration_ms=duration_ms,
                success=True,
                idempotent_replay=False,
            )
            return LifecycleTransitionResult(
                success=True,
                memory_id=old_memory_id,
                previous_status=old_status,
                new_status=MemoryStatus.SUPERSEDED,
                event_id=event_id,
                transition_timestamp=evt.created_at,
                idempotent_replay=False,
            )

        except Exception as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            _emit_lifecycle_telemetry(
                event="LIFECYCLE_SUPERSEDE_FAILED",
                memory_id=old_memory_id,
                previous_status=old_status.value if old_status else None,
                new_status=MemoryStatus.SUPERSEDED.value,
                actor=act_str,
                event_id=event_id,
                task_id=task_id,
                correlation_id=correlation_id,
                duration_ms=duration_ms,
                success=False,
                idempotent_replay=False,
            )
            if raise_on_error:
                raise
            return LifecycleTransitionResult(
                success=False,
                memory_id=old_memory_id,
                previous_status=old_status,
                new_status=None,
                error=str(e),
                idempotent_replay=False,
            )


# ============================================================================
# 7. BACKWARD-COMPATIBLE V5.1/V5.3.1 LIFECYCLE MANAGER INTERFACE
# ============================================================================

class MemoryLifecycleManager:
    """
    Manages the lifecycle transitions of memory records.
    V5.3.2 routes all transitions through the authoritative MemoryLifecycleEngine
    guaranteeing row-level locking, atomic state + audit, and provenance validation.
    """

    def __init__(self, engine: Optional[MemoryLifecycleEngine] = None):
        self._engine = engine

    @property
    def engine(self) -> MemoryLifecycleEngine:
        if self._engine is None:
            self._engine = lifecycle_engine
        return self._engine

    def supersede(
        self,
        old_memory_id: str,
        new_record: Any,
        reason: str = "Superseded by newer record",
        actor: str = LifecycleActor.SYSTEM.value,
        idempotency_key: Optional[str] = None,
        task_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> bool:
        """
        Supersede an existing memory with a newer one atomically.
        Routes via MemoryLifecycleEngine.supersede_memory().
        """
        res = self.engine.supersede_memory(
            old_memory_id=old_memory_id,
            new_record=new_record,
            reason=reason,
            actor=actor,
            idempotency_key=idempotency_key,
            task_id=task_id,
            correlation_id=correlation_id,
            raise_on_error=False,
        )
        if not res.success:
            print(f"[MEMORY LIFECYCLE] Supersede rejected: {res.error}")
        return res.success

    def archive(
        self,
        memory_id: str,
        reason: str = "Archived memory",
        actor: str = LifecycleActor.SYSTEM.value,
        idempotency_key: Optional[str] = None,
    ) -> bool:
        """Move an ACTIVE memory to ARCHIVED state via MemoryLifecycleEngine."""
        res = self.engine.transition_memory(
            memory_id=memory_id,
            target_status=MemoryStatus.ARCHIVED,
            reason=reason,
            actor=actor,
            idempotency_key=idempotency_key,
            raise_on_error=False,
        )
        if not res.success:
            print(f"[MEMORY LIFECYCLE] Archive rejected: {res.error}")
        return res.success

    def delete(
        self,
        memory_id: str,
        reason: str = "Logical deletion",
        actor: str = LifecycleActor.SYSTEM.value,
        idempotency_key: Optional[str] = None,
    ) -> bool:
        """Logically delete a memory: mark status as DELETED via MemoryLifecycleEngine."""
        res = self.engine.transition_memory(
            memory_id=memory_id,
            target_status=MemoryStatus.DELETED,
            reason=reason,
            actor=actor,
            idempotency_key=idempotency_key,
            raise_on_error=False,
        )
        if not res.success:
            print(f"[MEMORY LIFECYCLE] Delete rejected: {res.error}")
        return res.success

    def expire_temporary(self, memory_id: str) -> bool:
        """Expire a temporary memory by archiving it."""
        return self.archive(memory_id, reason="Expired temporary memory")

    def activate_pending(
        self,
        memory_id: str,
        reason: str = "Verification confirmed",
        actor: str = LifecycleActor.SYSTEM.value,
        task_id: Optional[str] = None,
        source_event_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> bool:
        """Promote a PENDING_VERIFICATION memory to ACTIVE via MemoryLifecycleEngine."""
        res = self.engine.transition_memory(
            memory_id=memory_id,
            target_status=MemoryStatus.ACTIVE,
            reason=reason,
            actor=actor,
            task_id=task_id,
            source_event_id=source_event_id,
            metadata=metadata,
            idempotency_key=idempotency_key,
            raise_on_error=False,
        )
        if not res.success:
            print(f"[MEMORY LIFECYCLE] Activation rejected: {res.error}")
        return res.success


lifecycle_engine = MemoryLifecycleEngine()
memory_lifecycle = MemoryLifecycleManager(engine=lifecycle_engine)
