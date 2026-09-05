"""
DOOM V5.1 — Memory Lifecycle Manager
Basic lifecycle operations: supersede, archive, expire temporary records.
V5.1 SCOPE ONLY: No advanced decay algorithms, no automatic relevance decay.
Advanced lifecycle (V5.3) will extend this module.
"""
from typing import Optional

from memory.schemas import MemoryRecord
from memory.types import MemoryStatus, VerificationStatus


class MemoryLifecycleManager:
    """
    Manages the lifecycle transitions of memory records.

    V5.1 operations:
    - supersede(): Mark old record as SUPERSEDED, link new record to it
    - archive(): Move an ACTIVE record to ARCHIVED state
    - expire_temporary(): Deactivate low-importance temporary records
    - delete(): Logical deletion (DELETED status, record preserved in DB)

    NOT implemented in V5.1 (reserved for V5.3):
    - Automatic relevance decay
    - Time-based expiration schedules
    - Confidence decay over time
    - Advanced lifecycle state machines
    """

    def supersede(
        self,
        old_memory_id: str,
        new_record: MemoryRecord,
    ) -> bool:
        """
        Supersede an existing memory with a newer one.
        - Old memory → SUPERSEDED status
        - New memory → links supersedes_memory_id to old
        - History is PRESERVED (old record remains in DB, just inactive)

        Returns True if supersession succeeded.
        """
        from memory.repository import memory_repository

        # Link new record to the old one
        new_record.supersedes_memory_id = old_memory_id

        # Store the new record first
        stored = memory_repository.store(new_record)
        if not stored:
            print(f"[MEMORY LIFECYCLE] Failed to store new record before superseding {old_memory_id}")
            return False

        # Mark old record as SUPERSEDED
        superseded = memory_repository.update_status(old_memory_id, MemoryStatus.SUPERSEDED)
        if not superseded:
            print(f"[MEMORY LIFECYCLE] Warning: new record stored but failed to mark {old_memory_id} as SUPERSEDED")

        print(f"[MEMORY LIFECYCLE] Superseded {old_memory_id} → new record {new_record.memory_id}")
        return True

    def archive(self, memory_id: str) -> bool:
        """
        Archive a memory: mark as ARCHIVED.
        ARCHIVED memories are not returned in standard retrieval.
        They are preserved for audit and historical access.
        """
        from memory.repository import memory_repository
        success = memory_repository.update_status(memory_id, MemoryStatus.ARCHIVED)
        if success:
            print(f"[MEMORY LIFECYCLE] Archived memory {memory_id}")
        return success

    def delete(self, memory_id: str) -> bool:
        """
        Logical deletion: mark as DELETED.
        The record is preserved in DB for audit purposes but never returned in retrieval.
        This is NOT a physical DELETE — history is always retained unless explicitly wiped.
        """
        from memory.repository import memory_repository
        success = memory_repository.update_status(memory_id, MemoryStatus.DELETED)
        if success:
            print(f"[MEMORY LIFECYCLE] Logically deleted memory {memory_id}")
        return success

    def expire_temporary(self, memory_id: str) -> bool:
        """
        Expire a temporary/low-importance memory by archiving it.
        V5.1: Called explicitly. No automatic scheduling in V5.1.
        """
        return self.archive(memory_id)

    def activate_pending(self, memory_id: str) -> bool:
        """
        Promote a PENDING_VERIFICATION memory to ACTIVE.
        Called after external verification evidence confirms the memory.
        """
        from memory.repository import memory_repository
        success = memory_repository.update_status(memory_id, MemoryStatus.ACTIVE)
        if success:
            print(f"[MEMORY LIFECYCLE] Activated pending memory {memory_id}")
        return success


memory_lifecycle = MemoryLifecycleManager()
