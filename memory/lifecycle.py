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


class LifecycleValidationError(MemoryLifecycleError):
    """Raised when lifecycle transition parameters or event metadata fail validation."""
    pass


class LifecycleAuditError(MemoryLifecycleError):
    """Raised when writing or serializing a lifecycle audit event fails."""
    pass


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
            created_at=data["created_at"].isoformat() if hasattr(data.get("created_at"), "isoformat") else str(data.get("created_at", _utcnow())),
        )


# ============================================================================
# 6. BACKWARD-COMPATIBLE V5.1 LIFECYCLE MANAGER INTERFACE
# ============================================================================

class MemoryLifecycleManager:
    """
    Manages the lifecycle transitions of memory records.
    V5.3.1 maintains backward compatibility with V5.1 callers while enforcing
    the canonical state transition matrix and audit event generation.
    """

    def supersede(
        self,
        old_memory_id: str,
        new_record: Any,
        reason: str = "Superseded by newer record",
        actor: str = LifecycleActor.SYSTEM.value,
    ) -> bool:
        """
        Supersede an existing memory with a newer one.
        - Old memory -> SUPERSEDED status
        - New memory -> links supersedes_memory_id to old
        - Preserves history in database
        """
        from memory.repository import memory_repository

        # Validate transition from old record's current status
        old_rec = memory_repository.get_by_id(old_memory_id)
        if not old_rec:
            return False

        try:
            validate_transition(old_rec.status, MemoryStatus.SUPERSEDED, memory_id=old_memory_id, reason=reason)
        except MemoryLifecycleError as ex:
            print(f"[MEMORY LIFECYCLE] Supersede rejected: {ex}")
            return False

        # Link new record to old
        new_record.supersedes_memory_id = old_memory_id

        # Store new record first
        stored = memory_repository.store(new_record)
        if not stored:
            print(f"[MEMORY LIFECYCLE] Failed to store new record before superseding {old_memory_id}")
            return False

        # Transition old record to SUPERSEDED
        superseded = memory_repository.update_status(old_memory_id, MemoryStatus.SUPERSEDED)
        if superseded:
            # Record audit event
            evt = MemoryLifecycleEvent(
                memory_id=old_memory_id,
                previous_status=old_rec.status,
                new_status=MemoryStatus.SUPERSEDED,
                transition_reason=reason,
                actor=actor,
                related_memory_id=new_record.memory_id,
            )
            memory_repository.store_lifecycle_event(evt)
            print(f"[MEMORY LIFECYCLE] Superseded {old_memory_id} -> new record {new_record.memory_id}")
            return True
        return False

    def archive(
        self,
        memory_id: str,
        reason: str = "Archived memory",
        actor: str = LifecycleActor.SYSTEM.value,
    ) -> bool:
        """Move an ACTIVE memory to ARCHIVED state."""
        from memory.repository import memory_repository
        rec = memory_repository.get_by_id(memory_id)
        if not rec:
            return False

        try:
            validate_transition(rec.status, MemoryStatus.ARCHIVED, memory_id=memory_id, reason=reason)
        except MemoryLifecycleError as ex:
            print(f"[MEMORY LIFECYCLE] Archive rejected: {ex}")
            return False

        success = memory_repository.update_status(memory_id, MemoryStatus.ARCHIVED)
        if success:
            evt = MemoryLifecycleEvent(
                memory_id=memory_id,
                previous_status=rec.status,
                new_status=MemoryStatus.ARCHIVED,
                transition_reason=reason,
                actor=actor,
            )
            memory_repository.store_lifecycle_event(evt)
            print(f"[MEMORY LIFECYCLE] Archived memory {memory_id}")
        return success

    def delete(
        self,
        memory_id: str,
        reason: str = "Logical deletion",
        actor: str = LifecycleActor.SYSTEM.value,
    ) -> bool:
        """Logically delete a memory: mark status as DELETED."""
        from memory.repository import memory_repository
        rec = memory_repository.get_by_id(memory_id)
        if not rec:
            return False

        try:
            validate_transition(rec.status, MemoryStatus.DELETED, memory_id=memory_id, reason=reason)
        except MemoryLifecycleError as ex:
            print(f"[MEMORY LIFECYCLE] Delete rejected: {ex}")
            return False

        success = memory_repository.update_status(memory_id, MemoryStatus.DELETED)
        if success:
            evt = MemoryLifecycleEvent(
                memory_id=memory_id,
                previous_status=rec.status,
                new_status=MemoryStatus.DELETED,
                transition_reason=reason,
                actor=actor,
            )
            memory_repository.store_lifecycle_event(evt)
            print(f"[MEMORY LIFECYCLE] Logically deleted memory {memory_id}")
        return success

    def expire_temporary(self, memory_id: str) -> bool:
        """Expire a temporary memory by archiving it."""
        return self.archive(memory_id, reason="Expired temporary memory")

    def activate_pending(
        self,
        memory_id: str,
        reason: str = "Verification confirmed",
        actor: str = LifecycleActor.SYSTEM.value,
    ) -> bool:
        """Promote a PENDING_VERIFICATION memory to ACTIVE."""
        from memory.repository import memory_repository
        rec = memory_repository.get_by_id(memory_id)
        if not rec:
            return False

        try:
            validate_transition(rec.status, MemoryStatus.ACTIVE, memory_id=memory_id, reason=reason)
        except MemoryLifecycleError as ex:
            print(f"[MEMORY LIFECYCLE] Activation rejected: {ex}")
            return False

        success = memory_repository.update_status(memory_id, MemoryStatus.ACTIVE)
        if success:
            evt = MemoryLifecycleEvent(
                memory_id=memory_id,
                previous_status=rec.status,
                new_status=MemoryStatus.ACTIVE,
                transition_reason=reason,
                actor=actor,
            )
            memory_repository.store_lifecycle_event(evt)
            print(f"[MEMORY LIFECYCLE] Activated pending memory {memory_id}")
        return success


memory_lifecycle = MemoryLifecycleManager()
