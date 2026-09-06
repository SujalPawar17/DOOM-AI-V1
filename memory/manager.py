"""
DOOM V5.1 — Memory Manager
THE authoritative V5.1 memory interface.
All durable memory operations must flow through this class.

Production code must NOT write canonical memory by bypassing this manager.
Legacy memory modules (episodic.py, semantic.py) remain for backward compatibility
but new V5.1 experience/preference/semantic memories use this manager exclusively.

Memory failure in this manager must NEVER propagate to task execution status.
"""
import time
from typing import Any, Dict, List, Optional

from memory.schemas import MemoryRecord, MemoryContext
from memory.types import (
    MemoryType, MemoryStatus, MemorySource,
    ConfidenceLevel, VerificationStatus, PrivacyClass,
)


class MemoryTelemetry:
    """Tracks memory operation metrics. Never logs private content."""
    __slots__ = (
        "retrieval_count", "write_count", "write_rejected_count",
        "supersede_count", "archive_count", "delete_count",
        "total_retrieval_ms", "total_write_ms",
        "memory_hit_count", "memory_miss_count",
    )

    def __init__(self):
        self.retrieval_count = 0
        self.write_count = 0
        self.write_rejected_count = 0
        self.supersede_count = 0
        self.archive_count = 0
        self.delete_count = 0
        self.total_retrieval_ms = 0.0
        self.total_write_ms = 0.0
        self.memory_hit_count = 0
        self.memory_miss_count = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "retrieval_count": self.retrieval_count,
            "write_count": self.write_count,
            "write_rejected_count": self.write_rejected_count,
            "supersede_count": self.supersede_count,
            "archive_count": self.archive_count,
            "delete_count": self.delete_count,
            "memory_hit_count": self.memory_hit_count,
            "memory_miss_count": self.memory_miss_count,
            "avg_retrieval_ms": (
                self.total_retrieval_ms / self.retrieval_count
                if self.retrieval_count > 0 else 0.0
            ),
            "avg_write_ms": (
                self.total_write_ms / self.write_count
                if self.write_count > 0 else 0.0
            ),
        }


class MemoryManager:
    """
    Single authoritative interface for V5.1 durable memory operations.

    All writes pass through MemoryWritePolicy → MemoryValidator → MemoryRepository.
    All reads pass through MemoryRetriever → MemoryRanker → MemoryContextBuilder.

    This manager enforces:
    - Memory policy compliance on every write
    - Graceful failure (never propagates exceptions to task execution)
    - Deduplication via supersession
    - Privacy boundary enforcement
    - Telemetry tracking (no private content in metrics)
    """

    def __init__(self):
        self.telemetry = MemoryTelemetry()

    # ------------------------------------------------------------------
    # Store
    # ------------------------------------------------------------------

    def store(self, record: MemoryRecord) -> Optional[MemoryRecord]:
        """
        Store a MemoryRecord after policy evaluation.
        Returns the stored record on success, None on rejection/failure.
        Never raises — memory failure must not break task execution.
        """
        t_start = time.time()
        try:
            from memory.policy import memory_write_policy
            from memory.repository import memory_repository

            # Policy evaluation
            decision = memory_write_policy.evaluate(
                content=record.content,
                memory_type=record.memory_type,
                source=record.source,
                task_verified=(record.verification_status == VerificationStatus.VERIFIED),
                user_explicit=(record.source == MemorySource.USER_EXPLICIT),
                importance=record.importance,
                privacy_class=record.privacy_class if record.privacy_class != PrivacyClass.NORMAL else None,
                extra_tags=record.tags,
            )

            if not decision.approved:
                self.telemetry.write_rejected_count += 1
                print(f"[MEMORY MANAGER] Write rejected: {decision.rejection_reason}")
                return None

            # Apply policy decisions to record
            record.confidence = decision.confidence
            record.verification_status = decision.verification_status
            record.privacy_class = decision.privacy_class
            record.tags = list(set(record.tags + decision.tags))

            # Persist
            stored = memory_repository.store(record)
            if stored:
                self.telemetry.write_count += 1
                self._broadcast("MEMORY_STORED", memory_id=record.memory_id,
                                memory_type=record.memory_type.value,
                                source=record.source.value)
                return record
            return None
        except Exception as e:
            print(f"[MEMORY MANAGER] Store failed (non-fatal): {e}")
            return None
        finally:
            elapsed = (time.time() - t_start) * 1000.0
            self.telemetry.total_write_ms += elapsed

    def store_with_supersession(
        self,
        record: MemoryRecord,
        conflict_keywords: Optional[List[str]] = None,
    ) -> Optional[MemoryRecord]:
        """
        Store a record, superseding any conflicting active memories of the same type.
        Used for preferences and facts that replace older versions.
        """
        try:
            from memory.repository import memory_repository
            from memory.lifecycle import memory_lifecycle

            if conflict_keywords:
                conflicts = memory_repository.find_conflicting_active(
                    memory_type=record.memory_type,
                    content_keywords=conflict_keywords,
                    project_id=record.project_id,
                )
                for old_record in conflicts:
                    if old_record.memory_id != record.memory_id:
                        # V5.3.2 Atomic 1:1 Supersession via MemoryLifecycleEngine
                        trans_res = memory_lifecycle.engine.supersede_memory(
                            old_memory_id=old_record.memory_id,
                            new_record=record,
                            reason=f"Superseded by newer {record.memory_type.value}",
                            actor="SYSTEM",
                        )
                        if trans_res.success:
                            self.telemetry.supersede_count += 1
                            self._broadcast("MEMORY_SUPERSEDED",
                                           old_id=old_record.memory_id,
                                           new_id=record.memory_id)
                            return record
                        else:
                            print(f"[MEMORY MANAGER] Supersession failed: {trans_res.error}")
                            return None
        except Exception as e:
            print(f"[MEMORY MANAGER] Supersession check failed (non-fatal): {e}")

        # No conflicts found — normal store
        return self.store(record)

    def store_and_supersede(
        self,
        record: MemoryRecord,
        conflict_keywords: Optional[List[str]] = None,
    ) -> Optional[MemoryRecord]:
        """V5.3.2 alias for store_with_supersession."""
        return self.store_with_supersession(record=record, conflict_keywords=conflict_keywords)


    # ------------------------------------------------------------------
    # Retrieve
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        memory_types: Optional[List[MemoryType]] = None,
        include_private: bool = False,
    ) -> MemoryContext:
        """
        Retrieve relevant memories as a controlled MemoryContext.
        Returns empty MemoryContext on failure (never raises).
        """
        t_start = time.time()
        try:
            from memory.retrieval import memory_retriever
            ctx = memory_retriever.retrieve(
                query=query,
                project_id=project_id,
                task_id=task_id,
                memory_types=memory_types,
                include_private=include_private,
            )
            self.telemetry.retrieval_count += 1
            self.telemetry.total_retrieval_ms += (time.time() - t_start) * 1000.0
            if ctx.memory_hit:
                self.telemetry.memory_hit_count += 1
                self._broadcast("MEMORY_RETRIEVAL_COMPLETED",
                               query=query[:60],
                               count=ctx.memory_count,
                               latency_ms=ctx.retrieval_latency_ms)
            else:
                self.telemetry.memory_miss_count += 1
            return ctx
        except Exception as e:
            print(f"[MEMORY MANAGER] Retrieve failed (non-fatal): {e}")
            self.telemetry.retrieval_count += 1
            self.telemetry.total_retrieval_ms += (time.time() - t_start) * 1000.0
            self.telemetry.memory_miss_count += 1
            return MemoryContext(query=query)

    # ------------------------------------------------------------------
    # Get / Update
    # ------------------------------------------------------------------

    def get(self, memory_id: str) -> Optional[MemoryRecord]:
        """Fetch a single memory record by ID."""
        try:
            from memory.repository import memory_repository
            return memory_repository.get_by_id(memory_id)
        except Exception as e:
            print(f"[MEMORY MANAGER] Get failed (non-fatal): {e}")
            return None

    def update(self, memory_id: str, new_content: str) -> bool:
        """Update content of an existing ACTIVE memory."""
        try:
            from memory.repository import memory_repository
            success = memory_repository.update_content(memory_id, new_content)
            if success:
                self._broadcast("MEMORY_UPDATED", memory_id=memory_id)
            return success
        except Exception as e:
            print(f"[MEMORY MANAGER] Update failed (non-fatal): {e}")
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def supersede(self, old_memory_id: str, new_record: MemoryRecord) -> Optional[MemoryRecord]:
        """Explicitly supersede an old memory with a new one."""
        try:
            from memory.lifecycle import memory_lifecycle
            success = memory_lifecycle.supersede(old_memory_id, new_record)
            if success:
                self.telemetry.supersede_count += 1
                self._broadcast("MEMORY_SUPERSEDED",
                               old_id=old_memory_id,
                               new_id=new_record.memory_id)
                return new_record
            return None
        except Exception as e:
            print(f"[MEMORY MANAGER] Supersede failed (non-fatal): {e}")
            return None

    def archive(self, memory_id: str) -> bool:
        """Archive a memory record."""
        try:
            from memory.lifecycle import memory_lifecycle
            success = memory_lifecycle.archive(memory_id)
            if success:
                self.telemetry.archive_count += 1
                self._broadcast("MEMORY_ARCHIVED", memory_id=memory_id)
            return success
        except Exception as e:
            print(f"[MEMORY MANAGER] Archive failed (non-fatal): {e}")
            return False

    def delete(self, memory_id: str) -> bool:
        """
        Logically delete a memory (DELETED status — record preserved in DB).
        Called when user explicitly says "Forget X."
        """
        try:
            from memory.lifecycle import memory_lifecycle
            success = memory_lifecycle.delete(memory_id)
            if success:
                self.telemetry.delete_count += 1
                self._broadcast("MEMORY_DELETED", memory_id=memory_id)
            return success
        except Exception as e:
            print(f"[MEMORY MANAGER] Delete failed (non-fatal): {e}")
            return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        project_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[MemoryRecord]:
        """
        Controlled search interface.
        Returns list of ACTIVE MemoryRecords matching criteria.
        """
        try:
            from memory.repository import memory_repository
            return memory_repository.search(
                query=query,
                memory_type=memory_type,
                status=MemoryStatus.ACTIVE,
                project_id=project_id,
                limit=limit,
            )
        except Exception as e:
            print(f"[MEMORY MANAGER] Search failed (non-fatal): {e}")
            return []

    def count(self) -> int:
        """Count total active memory records."""
        try:
            from memory.repository import memory_repository
            return memory_repository.count_active()
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # User controls
    # ------------------------------------------------------------------

    def remember(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.SEMANTIC,
        project_id: Optional[str] = None,
    ) -> Optional[MemoryRecord]:
        """
        User command: 'Remember X.'
        Stores as USER_EXPLICIT source with HIGH confidence.
        """
        record = MemoryRecord(
            memory_type=memory_type,
            content=content,
            source=MemorySource.USER_EXPLICIT,
            confidence=ConfidenceLevel.HIGH,
            verification_status=VerificationStatus.VERIFIED,
            importance=0.8,
            project_id=project_id,
            privacy_class=PrivacyClass.NORMAL,
            tags=["user_explicit", "remember"],
        )
        return self.store(record)

    def forget(self, memory_id: str) -> bool:
        """
        User command: 'Forget X.'
        Logically deletes the specified memory.
        """
        return self.delete(memory_id)

    def forget_by_search(self, query: str) -> List[str]:
        """
        Find and forget memories matching a query.
        Returns list of deleted memory_ids.
        """
        candidates = self.search(query=query, limit=5)
        deleted_ids = []
        for record in candidates:
            if self.delete(record.memory_id):
                deleted_ids.append(record.memory_id)
        return deleted_ids

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _broadcast(self, event_type: str, **payload) -> None:
        """Emit memory events through the task engine broadcaster if available."""
        try:
            from core.task_engine import task_engine
            if hasattr(task_engine, "_state_broadcaster") and task_engine._state_broadcaster:
                task_engine._state_broadcaster({
                    "type": "memory_event",
                    "event": event_type,
                    **payload
                })
        except Exception:
            pass


# Global singleton — THE authoritative V5.1 memory interface
memory_manager = MemoryManager()
