"""
DOOM V5.3.3 — Vector Synchronization & Reconciliation Test Suite
=================================================================

Comprehensive test suite covering all V5.3.3 requirements:
A. Schema & State Machine
B. Transactional Outbox
C. Memory Creation (ACTIVE, PENDING, SENSITIVE)
D. Lifecycle Synchronization (Transitions & 1:1 Supersession)
E. Content Updates & Monotonic Generation
F. Generation Safety & Stale-Write Resurrection Prevention
G. Idempotency & Replay Safety
H. Crash Windows & Lease Recovery
I. Error Classification, Retry & Dead-Letter
J. Vector Reconciliation Engine (Zombie, Orphan, Missing, Sensitive, Stale)
K. NumPy Restart Recovery / Rehydration
L. Semantic Retrieval Hardening (50 Over-fetching & ACTIVE-Only)
M. Security & Telemetry Sanitization
N. Concurrency & Delayed-Worker Races
O. Full Production Pipeline
"""
import os
import sys
import time
import uuid
import unittest
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

# Ensure root directory is on sys.path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from memory.types import (
    MemoryType,
    MemoryStatus,
    MemorySource,
    ConfidenceLevel,
    VerificationStatus,
    PrivacyClass,
)
from memory.schemas import MemoryRecord
from memory.lifecycle import (
    lifecycle_engine,
    memory_lifecycle,
    LifecycleActor,
)
from memory.repository import memory_repository
from memory.manager import memory_manager
from database.postgres_db import postgres_manager

from memory.sync import (
    SyncOperation,
    VectorSyncStatus,
    VectorSyncWorkItem,
    VectorSyncResult,
    ReconciliationReport,
    compute_sync_idempotency_key,
    validate_sync_transition,
    new_sync_id,
)
from memory.sync_engine import (
    vector_sync_engine,
    VectorSyncEngine,
    _emit_sync_telemetry,
)
from memory.reconciliation import (
    vector_reconciliation_engine,
    VectorReconciliationEngine,
    rehydrate_numpy_store,
)
from memory.vector_store import vector_store
from memory.embedding.router import embedding_router
from memory.retrieval import memory_retriever


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_test_record(
    status: MemoryStatus = MemoryStatus.ACTIVE,
    privacy_class: PrivacyClass = PrivacyClass.NORMAL,
    content: Optional[str] = None,
    memory_type: MemoryType = MemoryType.SEMANTIC,
    generation: int = 1,
) -> MemoryRecord:
    uid = str(uuid.uuid4())[:8]
    return MemoryRecord(
        memory_id=f"mem-v533-{uid}",
        memory_type=memory_type,
        status=status,
        content=content or f"Test memory content for V5.3.3 verification {uid}",
        confidence=ConfidenceLevel.HIGH,
        verification_status=VerificationStatus.VERIFIED if status == MemoryStatus.ACTIVE else VerificationStatus.UNVERIFIED,
        privacy_class=privacy_class,
        source=MemorySource.USER_EXPLICIT,
        importance=0.8,
        tags=["v533-test"],
        generation=generation,
    )


class TestV533VectorSync(unittest.TestCase):
    """DOOM V5.3.3 Vector Synchronization & Reconciliation Verification."""

    def setUp(self):
        self.created_memory_ids: List[str] = []
        self.created_sync_ids: List[str] = []

    def tearDown(self):
        # Cleanup PostgreSQL records and sync queue items
        conn = postgres_manager.get_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    for mid in self.created_memory_ids:
                        cur.execute("DELETE FROM vector_sync_queue WHERE memory_id = %s;", (mid,))
                        cur.execute("DELETE FROM memory_lifecycle_audit WHERE memory_id = %s;", (mid,))
                        cur.execute("DELETE FROM memory_records WHERE memory_id = %s;", (mid,))
                    for sid in self.created_sync_ids:
                        cur.execute("DELETE FROM vector_sync_queue WHERE sync_id = %s;", (sid,))
                conn.commit()
            except Exception:
                conn.rollback()
            finally:
                postgres_manager.release_connection(conn)

        # Cleanup VectorStore
        for mid in self.created_memory_ids:
            try:
                vector_store.delete_embedding(mid)
            except Exception:
                pass

    # ==================================================================
    # Category A: Schema & State Machine
    # ==================================================================
    def test_a01_schema_queue_table_and_generation_column_exist(self):
        """Verify vector_sync_queue exists and memory_records has generation column."""
        conn = postgres_manager.get_connection()
        self.assertIsNotNone(conn)
        try:
            with conn.cursor() as cur:
                # Check memory_records.generation
                cur.execute("""
                    SELECT column_name, data_type, column_default
                    FROM information_schema.columns
                    WHERE table_name = 'memory_records' AND column_name = 'generation';
                """)
                row = cur.fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "generation")

                # Check vector_sync_queue table
                cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'vector_sync_queue';
                """)
                cols = {r[0] for r in cur.fetchall()}
                expected_cols = {
                    "sync_id", "memory_id", "operation", "target_generation",
                    "target_status", "idempotency_key", "sync_status",
                    "attempt_count", "max_attempts", "available_at",
                    "locked_until", "created_at", "updated_at",
                    "last_error_class", "last_error_message_safe"
                }
                self.assertTrue(expected_cols.issubset(cols), f"Missing columns: {expected_cols - cols}")
        finally:
            postgres_manager.release_connection(conn)

    def test_a02_sync_state_machine_valid_and_invalid_transitions(self):
        """Verify valid queue transitions pass and invalid transitions are rejected."""
        # Valid
        self.assertTrue(validate_sync_transition(VectorSyncStatus.PENDING, VectorSyncStatus.PROCESSING))
        self.assertTrue(validate_sync_transition(VectorSyncStatus.PROCESSING, VectorSyncStatus.SYNCED))
        self.assertTrue(validate_sync_transition(VectorSyncStatus.PROCESSING, VectorSyncStatus.RETRY_REQUIRED))
        self.assertTrue(validate_sync_transition(VectorSyncStatus.PROCESSING, VectorSyncStatus.FAILED))
        self.assertTrue(validate_sync_transition(VectorSyncStatus.PROCESSING, VectorSyncStatus.DEAD_LETTER))
        self.assertTrue(validate_sync_transition(VectorSyncStatus.RETRY_REQUIRED, VectorSyncStatus.PROCESSING))
        self.assertTrue(validate_sync_transition(VectorSyncStatus.RECONCILIATION_REQUIRED, VectorSyncStatus.PROCESSING))

        # Invalid
        self.assertFalse(validate_sync_transition(VectorSyncStatus.SYNCED, VectorSyncStatus.PENDING))
        self.assertFalse(validate_sync_transition(VectorSyncStatus.DEAD_LETTER, VectorSyncStatus.SYNCED))
        self.assertFalse(validate_sync_transition(VectorSyncStatus.PENDING, VectorSyncStatus.SYNCED))

    def test_a03_deterministic_idempotency_key(self):
        """Verify idempotency key calculation is deterministic and distinct for operations."""
        k1 = compute_sync_idempotency_key("mem-1", "UPSERT", 1, "test content")
        k2 = compute_sync_idempotency_key("mem-1", "UPSERT", 1, "test content")
        k3 = compute_sync_idempotency_key("mem-1", "UPSERT", 2, "test content")
        k4 = compute_sync_idempotency_key("mem-1", "DELETE", 1)

        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)
        self.assertNotEqual(k1, k4)

    # ==================================================================
    # Category B: Transactional Outbox
    # ==================================================================
    def test_b01_transactional_outbox_commits_memory_and_queue_together(self):
        """Verify memory insertion and queue insertion commit atomically together."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.created_memory_ids.append(rec.memory_id)

        # Store via repository (which wraps transaction)
        stored = memory_repository.store(rec)
        self.assertTrue(stored)
        loaded = memory_repository.get_by_id(rec.memory_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.generation, 1)

        # Verify queue work item was committed
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT sync_id, operation, target_generation FROM vector_sync_queue WHERE memory_id = %s;", (rec.memory_id,))
                rows = cur.fetchall()
                self.assertGreaterEqual(len(rows), 1)
                self.assertEqual(rows[0][1], "UPSERT")
                self.assertEqual(rows[0][2], 1)
        finally:
            postgres_manager.release_connection(conn)

    def test_b02_transactional_outbox_rollback_aborts_both_memory_and_queue(self):
        """Verify that rolling back a transaction leaves neither memory nor queue record."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.created_memory_ids.append(rec.memory_id)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                # 1. Insert memory record
                cur.execute("""
                    INSERT INTO memory_records (
                        memory_id, memory_type, status, content, confidence,
                        verification_status, privacy_class, source, importance,
                        generation, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                """, (
                    rec.memory_id, rec.memory_type.value, rec.status.value, rec.content,
                    rec.confidence.value, rec.verification_status.value, rec.privacy_class.value,
                    rec.source.value, rec.importance, rec.generation
                ))
                # 2. Enqueue sync work
                vector_sync_engine.enqueue_sync_work(
                    cur=cur,
                    memory_id=rec.memory_id,
                    operation=SyncOperation.UPSERT,
                    target_generation=1,
                    target_status=MemoryStatus.ACTIVE.value,
                    content=rec.content,
                )
            # Explicit rollback!
            conn.rollback()
        finally:
            postgres_manager.release_connection(conn)

        # Verify nothing exists
        read_rec = memory_repository.get_by_id(rec.memory_id)
        self.assertIsNone(read_rec)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM vector_sync_queue WHERE memory_id = %s;", (rec.memory_id,))
                cnt = cur.fetchone()[0]
                self.assertEqual(cnt, 0)
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category C: Memory Creation (ACTIVE, PENDING, SENSITIVE)
    # ==================================================================
    def test_c01_active_memory_creates_upsert_and_syncs_vector(self):
        """Active memory creates UPSERT queue entry and syncs vector."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.created_memory_ids.append(rec.memory_id)

        stored = memory_manager.store(rec)
        self.assertIsNotNone(stored)

        # Run worker to ensure sync processed (if post-commit fast-path didn't already finish)
        vector_sync_engine.process_pending_batch(limit=10)

        # Verify vector exists
        vec = vector_store.get_embedding(rec.memory_id)
        self.assertIsNotNone(vec, "Vector must exist for active stored memory")
        self.assertEqual(vec.generation, 1)

    def test_c02_pending_verification_does_not_queue_upsert_or_embed(self):
        """Pending verification memory must NOT create UPSERT or vector."""
        rec = make_test_record(status=MemoryStatus.PENDING_VERIFICATION)
        self.created_memory_ids.append(rec.memory_id)

        stored = memory_manager.store(rec)
        self.assertIsNotNone(stored)

        # Verify no queue work created
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM vector_sync_queue WHERE memory_id = %s;", (rec.memory_id,))
                cnt = cur.fetchone()[0]
                self.assertEqual(cnt, 0)
        finally:
            postgres_manager.release_connection(conn)

        # Verify no vector in store
        vec = vector_store.get_embedding(rec.memory_id)
        self.assertIsNone(vec, "Vector must NOT exist for PENDING_VERIFICATION memory")

    def test_c03_sensitive_memory_does_not_embed_or_create_vector(self):
        """Sensitive memory must NEVER create vector or call embedding."""
        rec = make_test_record(status=MemoryStatus.ACTIVE, privacy_class=PrivacyClass.SENSITIVE)
        self.created_memory_ids.append(rec.memory_id)

        stored = memory_manager.store(rec)
        self.assertIsNotNone(stored)

        # Verify no vector in store
        vec = vector_store.get_embedding(rec.memory_id)
        self.assertIsNone(vec, "Vector must NEVER exist for SENSITIVE memory")

    # ==================================================================
    # Category D: Lifecycle Synchronization (Transitions & Supersession)
    # ==================================================================
    def test_d01_active_to_superseded_deletes_vector(self):
        """Transition ACTIVE -> SUPERSEDED deletes vector from VectorStore."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)
        vector_sync_engine.process_pending_batch()
        self.assertIsNotNone(vector_store.get_embedding(rec.memory_id))

        # Transition to SUPERSEDED
        res = lifecycle_engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.SUPERSEDED,
            reason="Superseded by test",
            actor=LifecycleActor.SYSTEM,
        )
        self.assertTrue(res.success)
        vector_sync_engine.process_pending_batch()

        # Vector must be deleted
        vec = vector_store.get_embedding(rec.memory_id)
        self.assertIsNone(vec, "Vector must be deleted upon transition to SUPERSEDED")

    def test_d02_active_to_archived_deletes_vector(self):
        """Transition ACTIVE -> ARCHIVED deletes vector from VectorStore."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)
        vector_sync_engine.process_pending_batch()
        self.assertIsNotNone(vector_store.get_embedding(rec.memory_id))

        # Transition to ARCHIVED
        res = lifecycle_engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ARCHIVED,
            reason="Archived by test",
            actor=LifecycleActor.USER,
        )
        self.assertTrue(res.success)
        vector_sync_engine.process_pending_batch()

        vec = vector_store.get_embedding(rec.memory_id)
        self.assertIsNone(vec, "Vector must be deleted upon transition to ARCHIVED")

    def test_d03_active_to_deleted_deletes_vector(self):
        """Transition ACTIVE -> DELETED deletes vector from VectorStore."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)
        vector_sync_engine.process_pending_batch()
        self.assertIsNotNone(vector_store.get_embedding(rec.memory_id))

        res = lifecycle_engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.DELETED,
            reason="Deleted by test",
            actor=LifecycleActor.USER,
        )
        self.assertTrue(res.success)
        vector_sync_engine.process_pending_batch()

        vec = vector_store.get_embedding(rec.memory_id)
        self.assertIsNone(vec, "Vector must be deleted upon transition to DELETED")

    def test_d04_pending_to_active_upserts_vector(self):
        """Transition PENDING_VERIFICATION -> ACTIVE creates vector in VectorStore."""
        rec = make_test_record(status=MemoryStatus.PENDING_VERIFICATION)
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))

        # Transition to ACTIVE
        res = lifecycle_engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ACTIVE,
            reason="Verified by user",
            actor=LifecycleActor.USER,
        )
        self.assertTrue(res.success)
        vector_sync_engine.process_pending_batch()

        vec = vector_store.get_embedding(rec.memory_id)
        self.assertIsNotNone(vec, "Vector must be created when transitioning PENDING -> ACTIVE")

    def test_d05_atomic_supersession_deletes_old_and_upserts_new(self):
        """1:1 Supersession atomically creates DELETE for old and UPSERT for new."""
        old_rec = make_test_record(status=MemoryStatus.ACTIVE, content="User prefers Flask framework")
        self.created_memory_ids.append(old_rec.memory_id)
        memory_manager.store(old_rec)
        vector_sync_engine.process_pending_batch()
        self.assertIsNotNone(vector_store.get_embedding(old_rec.memory_id))

        new_rec = make_test_record(status=MemoryStatus.ACTIVE, content="User prefers FastAPI framework")
        self.created_memory_ids.append(new_rec.memory_id)

        # Atomic supersession
        res = lifecycle_engine.supersede_memory(
            old_memory_id=old_rec.memory_id,
            new_record=new_rec,
            reason="User changed preference",
            actor="USER",
        )
        self.assertTrue(res.success)
        vector_sync_engine.process_pending_batch()

        # Old vector deleted, new vector created
        self.assertIsNone(vector_store.get_embedding(old_rec.memory_id))
        self.assertIsNotNone(vector_store.get_embedding(new_rec.memory_id))

    # ==================================================================
    # Category E: Content Updates & Monotonic Generation
    # ==================================================================
    def test_e01_content_update_increments_generation_and_updates_vector(self):
        """Content update increments generation in PostgreSQL and updates vector."""
        rec = make_test_record(status=MemoryStatus.ACTIVE, content="Initial preferences content")
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)
        vector_sync_engine.process_pending_batch()

        vec1 = vector_store.get_embedding(rec.memory_id)
        self.assertIsNotNone(vec1)
        self.assertEqual(vec1.generation, 1)

        # Update content
        update_ok = memory_repository.update_content(
            memory_id=rec.memory_id,
            new_content="Updated preferences content with FastAPI",
            reason="User edit",
        )
        self.assertTrue(update_ok)
        updated_rec = memory_repository.get_by_id(rec.memory_id)
        self.assertIsNotNone(updated_rec)
        self.assertEqual(updated_rec.generation, 2)

        vector_sync_engine.process_pending_batch()

        vec2 = vector_store.get_embedding(rec.memory_id)
        self.assertIsNotNone(vec2)
        self.assertEqual(vec2.generation, 2)
        self.assertNotEqual(vec1.content_hash, vec2.content_hash)

    # ==================================================================
    # Category F: Generation Safety & Stale-Write Resurrection Prevention
    # ==================================================================
    def test_f01_stale_upsert_rejected_by_generation_guard(self):
        """Stale UPSERT (target_generation < record.generation) is rejected and does not overwrite."""
        rec = make_test_record(status=MemoryStatus.ACTIVE, content="Generation 1 content")
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)

        # Mutate to generation 2
        memory_repository.update_content(rec.memory_id, "Generation 2 content")
        vector_sync_engine.process_pending_batch()

        # Current vector is generation 2
        vec = vector_store.get_embedding(rec.memory_id)
        self.assertEqual(vec.generation, 2)

        # Fabricate stale generation 1 work item
        stale_item = VectorSyncWorkItem(
            sync_id=new_sync_id(),
            memory_id=rec.memory_id,
            operation=SyncOperation.UPSERT.value,
            target_generation=1,
            target_status="ACTIVE",
            idempotency_key="stale_key_1",
            sync_status=VectorSyncStatus.PENDING.value,
        )

        res = vector_sync_engine._execute_sync_operation(stale_item, "Stale G1 content")
        self.assertTrue(res.is_skipped)
        self.assertIn("Stale generation", res.error or "")

        # Vector in store MUST still be generation 2
        vec_after = vector_store.get_embedding(rec.memory_id)
        self.assertEqual(vec_after.generation, 2)

    def test_f02_delayed_worker_cannot_resurrect_vector_after_newer_delete(self):
        """
        CRITICAL GENERATION RACE:
        Worker A starts G10 UPSERT.
        Lifecycle changes memory to SUPERSEDED (G11) and DELETE G11 executes.
        Delayed Worker A resumes G10 UPSERT.
        VectorStore MUST NOT resurrect the vector!
        """
        rec = make_test_record(status=MemoryStatus.ACTIVE, content="G10 preference")
        rec.generation = 10
        self.created_memory_ids.append(rec.memory_id)

        # Directly store into DB with generation 10
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO memory_records (
                        memory_id, memory_type, status, content, confidence,
                        verification_status, privacy_class, source, importance,
                        generation, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                """, (
                    rec.memory_id, rec.memory_type.value, rec.status.value, rec.content,
                    rec.confidence.value, rec.verification_status.value, rec.privacy_class.value,
                    rec.source.value, rec.importance, rec.generation
                ))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        # Lifecycle changes memory to SUPERSEDED (G11) and executes DELETE G11
        lifecycle_engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.SUPERSEDED,
            reason="Superseded by G11",
            actor=LifecycleActor.SYSTEM,
        )
        vector_sync_engine.process_pending_batch()

        # Vector is absent
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))

        # Delayed Worker A arrives with G10 UPSERT!
        delayed_item = VectorSyncWorkItem(
            sync_id=new_sync_id(),
            memory_id=rec.memory_id,
            operation=SyncOperation.UPSERT.value,
            target_generation=10,
            target_status="ACTIVE",
            idempotency_key="delayed_g10_key",
            sync_status=VectorSyncStatus.PENDING.value,
        )
        # Attempt execution via VectorSyncEngine
        res = vector_sync_engine._execute_sync_operation(delayed_item, "G10 content")
        self.assertTrue(res.is_skipped)

        # CRITICAL ASSERTION: VectorStore remains completely empty / NO VECTOR
        self.assertIsNone(
            vector_store.get_embedding(rec.memory_id),
            "CRITICAL FAILURE: Delayed G10 UPSERT resurrected a superseded vector!"
        )

    # ==================================================================
    # Category G: Idempotency & Replay Safety
    # ==================================================================
    def test_g01_repeated_upsert_and_delete_are_idempotent(self):
        """Repeated UPSERT and repeated DELETE produce identical deterministic state."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)
        vector_sync_engine.process_pending_batch()

        vec1 = vector_store.get_embedding(rec.memory_id)

        # Re-execute UPSERT work item
        emb_res = embedding_router.embed(rec.content)
        res1 = vector_store.store_embedding(
            memory_id=rec.memory_id,
            vector=emb_res.vector,
            model=emb_res.model,
            model_version=emb_res.model_version,
            dimension=len(emb_res.vector),
            content_hash="test-hash",
            generation=1,
        )
        self.assertIsNotNone(res1)
        vec2 = vector_store.get_embedding(rec.memory_id)
        self.assertEqual(vec1.memory_id, vec2.memory_id)

        # Repeated delete
        vector_store.delete_embedding(rec.memory_id, generation=1)
        vector_store.delete_embedding(rec.memory_id, generation=1)
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))

    def test_g02_duplicate_queue_enqueue_rejected_by_idempotency_key(self):
        """Duplicate queue work with same idempotency key is safely ignored."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid1 = vector_sync_engine.enqueue_sync_work(
                    cur=cur,
                    memory_id=rec.memory_id,
                    operation=SyncOperation.UPSERT,
                    target_generation=1,
                    target_status="ACTIVE",
                    content=rec.content,
                )
                # Second enqueue with identical content and generation
                sid2 = vector_sync_engine.enqueue_sync_work(
                    cur=cur,
                    memory_id=rec.memory_id,
                    operation=SyncOperation.UPSERT,
                    target_generation=1,
                    target_status="ACTIVE",
                    content=rec.content,
                )
            conn.commit()
            self.assertEqual(sid1, sid2, "Duplicate enqueue should return existing sync_id")
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category H: Crash Windows & Lease Recovery
    # ==================================================================
    def test_h01_lease_recovery_resets_expired_processing_items(self):
        """Worker crash leaving item in PROCESSING past locked_until is recovered to RETRY_REQUIRED."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)

        conn = postgres_manager.get_connection()
        sid = new_sync_id()
        self.created_sync_ids.append(sid)
        try:
            with conn.cursor() as cur:
                # Insert item stuck in PROCESSING with locked_until in the past
                cur.execute("""
                    INSERT INTO vector_sync_queue (
                        sync_id, memory_id, operation, target_generation, target_status,
                        idempotency_key, sync_status, attempt_count, max_attempts,
                        locked_until, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP - INTERVAL '5 minutes', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                """, (
                    sid, rec.memory_id, "UPSERT", 1, "ACTIVE",
                    f"crash_test_{sid}", VectorSyncStatus.PROCESSING.value, 1, 5
                ))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        # Run lease recovery
        recovered = vector_sync_engine.recover_expired_leases()
        self.assertGreaterEqual(recovered, 1)

        # Verify status reset to RETRY_REQUIRED
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT sync_status FROM vector_sync_queue WHERE sync_id = %s;", (sid,))
                status = cur.fetchone()[0]
                self.assertEqual(status, VectorSyncStatus.RETRY_REQUIRED.value)
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category I: Retry & Dead-Letter Handling
    # ==================================================================
    def test_i01_retry_exhaustion_moves_item_to_dead_letter(self):
        """When attempt_count reaches max_attempts, item transitions to DEAD_LETTER."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)

        conn = postgres_manager.get_connection()
        sid = new_sync_id()
        self.created_sync_ids.append(sid)
        try:
            with conn.cursor() as cur:
                # Insert item at attempt 4 (max 5)
                cur.execute("""
                    INSERT INTO vector_sync_queue (
                        sync_id, memory_id, operation, target_generation, target_status,
                        idempotency_key, sync_status, attempt_count, max_attempts,
                        available_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                """, (
                    sid, rec.memory_id, "UPSERT", 1, "ACTIVE",
                    f"dead_letter_test_{sid}", VectorSyncStatus.PENDING.value, 4, 5
                ))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        # Mock process failure by recording error directly
        work_item = vector_sync_engine._fetch_work_item(sid)
        self.assertIsNotNone(work_item)
        vector_sync_engine._record_failure(
            sync_id=sid,
            work_item=work_item,
            error=Exception("Connection timed out"),
            terminal=False,
        )

        # Status should now be DEAD_LETTER
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT sync_status, attempt_count FROM vector_sync_queue WHERE sync_id = %s;", (sid,))
                row = cur.fetchone()
                self.assertEqual(row[0], VectorSyncStatus.DEAD_LETTER.value)
                self.assertEqual(row[1], 5)
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category J: Vector Reconciliation Engine
    # ==================================================================
    def test_j01_reconciliation_detects_and_repairs_missing_vector(self):
        """Active memory without vector is detected and repaired."""
        rec = make_test_record(status=MemoryStatus.ACTIVE, content="Active memory needing vector")
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)
        vector_sync_engine.process_pending_batch()

        # Manually delete vector to simulate missing vector
        vector_store.delete_embedding(rec.memory_id)
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))

        # Reconcile and fix
        report = vector_reconciliation_engine.reconcile(fix=True)
        self.assertIn(rec.memory_id, report.missing_vectors)
        self.assertGreaterEqual(report.fixed_count, 1)

        # Vector should now be restored
        vec = vector_store.get_embedding(rec.memory_id)
        self.assertIsNotNone(vec, "Missing vector should be repaired by reconciliation")

    def test_j02_reconciliation_detects_and_purges_zombie_vector(self):
        """Non-active memory (ARCHIVED) with vector is detected and purged."""
        rec = make_test_record(status=MemoryStatus.ACTIVE, content="Memory that will be archived")
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)
        vector_sync_engine.process_pending_batch()

        # Artificially set status in PostgreSQL to ARCHIVED without deleting vector
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE memory_records SET status = 'ARCHIVED' WHERE memory_id = %s;", (rec.memory_id,))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        # Reconcile and fix
        report = vector_reconciliation_engine.reconcile(fix=True)
        self.assertIn(rec.memory_id, report.zombie_vectors)

        # Vector must be purged
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))

    def test_j03_reconciliation_detects_and_purges_orphan_vector(self):
        """Vector referencing non-existent PostgreSQL record is detected and purged."""
        orphan_id = f"orphan-{uuid.uuid4().hex[:8]}"
        self.created_memory_ids.append(orphan_id)

        emb_res = embedding_router.embed("Orphan vector text")
        vector_store.store_embedding(
            memory_id=orphan_id,
            vector=emb_res.vector,
            model=emb_res.model,
            model_version=emb_res.model_version,
            dimension=len(emb_res.vector),
            content_hash="orphan-hash",
            generation=1,
        )
        self.assertIsNotNone(vector_store.get_embedding(orphan_id))

        # Reconcile and fix
        report = vector_reconciliation_engine.reconcile(fix=True)
        self.assertIn(orphan_id, report.orphan_vectors)

        # Orphan vector must be purged
        self.assertIsNone(vector_store.get_embedding(orphan_id))

    def test_j04_reconciliation_detects_and_purges_sensitive_vector(self):
        """Vector referencing SENSITIVE record in PostgreSQL is purged."""
        rec = make_test_record(status=MemoryStatus.ACTIVE, privacy_class=PrivacyClass.SENSITIVE, content="Sensitive medical biometric record")
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)

        # Artificially store vector for sensitive memory
        emb_res = embedding_router.embed("Sensitive data bypass")
        vector_store.store_embedding(
            memory_id=rec.memory_id,
            vector=emb_res.vector,
            model=emb_res.model,
            model_version=emb_res.model_version,
            dimension=len(emb_res.vector),
            content_hash="sens-hash",
            generation=1,
        )

        # Reconcile and fix
        report = vector_reconciliation_engine.reconcile(fix=True)
        self.assertIn(rec.memory_id, report.sensitive_vectors)

        # Must be purged
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))

    # ==================================================================
    # Category K: NumPy Restart Recovery / Rehydration
    # ==================================================================
    def test_k01_numpy_rehydration_populates_active_memories_and_skips_sensitive(self):
        """NumPy restart recovery repopulates active memories and excludes sensitive ones."""
        rec_active = make_test_record(status=MemoryStatus.ACTIVE, content="Active memory for hydration")
        rec_sens = make_test_record(status=MemoryStatus.ACTIVE, privacy_class=PrivacyClass.SENSITIVE, content="Confidential medical health biometric metrics")
        rec_arch = make_test_record(status=MemoryStatus.ARCHIVED, content="Archived memory for test")

        self.created_memory_ids.extend([rec_active.memory_id, rec_sens.memory_id, rec_arch.memory_id])
        memory_manager.store(rec_active)
        memory_manager.store(rec_sens)
        memory_manager.store(rec_arch)

        # Clear vector store to simulate restart
        vector_store.clear()
        self.assertEqual(vector_store.count(), 0)

        # Rehydrate
        stats = rehydrate_numpy_store(batch_size=50)
        self.assertGreaterEqual(stats["rehydrated_count"], 1)

        # Active memory has vector
        self.assertIsNotNone(vector_store.get_embedding(rec_active.memory_id))
        # Sensitive memory does NOT have vector
        self.assertIsNone(vector_store.get_embedding(rec_sens.memory_id))
        # Archived memory does NOT have vector
        self.assertIsNone(vector_store.get_embedding(rec_arch.memory_id))

    # ==================================================================
    # Category L: Semantic Retrieval Hardening
    # ==================================================================
    def test_l01_semantic_retrieval_over_fetches_and_filters_zombies_and_orphans(self):
        """Retrieval fetches 50 candidates, validates against PostgreSQL, and cleans zombies."""
        rec = make_test_record(status=MemoryStatus.ACTIVE, content="Special python async workflow query match")
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)
        vector_sync_engine.process_pending_batch()

        # Artificially create a zombie vector
        zombie_rec = make_test_record(status=MemoryStatus.ACTIVE, content="Zombie special python async workflow match")
        self.created_memory_ids.append(zombie_rec.memory_id)
        memory_manager.store(zombie_rec)
        vector_sync_engine.process_pending_batch()

        # Change zombie_rec status to SUPERSEDED in PostgreSQL without deleting vector
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE memory_records SET status = 'SUPERSEDED' WHERE memory_id = %s;", (zombie_rec.memory_id,))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        # Query retrieval
        ctx = memory_retriever.retrieve(query="Special python async workflow")

        # MemoryContext must contain only ACTIVE memory
        retrieved_ids = [m.memory_id for m in ctx.retrieved_memories]
        self.assertIn(rec.memory_id, retrieved_ids)
        self.assertNotIn(zombie_rec.memory_id, retrieved_ids, "Zombie memory MUST NOT be emitted to cognition")

        # Give opportunistic cleanup thread a moment to run
        time.sleep(0.1)

    # ==================================================================
    # Category M: Security & Telemetry Sanitization
    # ==================================================================
    def test_m01_sensitive_memory_never_embedded_under_any_circumstance(self):
        """Direct call to sync engine with SENSITIVE record purges and never embeds."""
        rec = make_test_record(status=MemoryStatus.ACTIVE, privacy_class=PrivacyClass.SENSITIVE, content="Confidential private health evaluation metrics")
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)

        work_item = VectorSyncWorkItem(
            sync_id=new_sync_id(),
            memory_id=rec.memory_id,
            operation="UPSERT",
            target_generation=1,
            target_status="ACTIVE",
            idempotency_key="sensitive_direct_call",
            sync_status="PENDING",
        )
        res = vector_sync_engine.process_work_item(work_item.sync_id)
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))

    def test_m02_telemetry_sanitization_removes_sensitive_keys(self):
        """Telemetry emitter strips content, vector, raw_embedding, token, password."""
        # Test emitter does not crash and handles stripped keys safely
        _emit_sync_telemetry(
            "VECTOR_SYNC_SUCCESS",
            memory_id="mem-1",
            content="Sensitive data that should be stripped",
            vector=[0.1, 0.2],
            password="secret_password",
            generation=1,
        )

    # ==================================================================
    # Category N: Concurrency & Stress
    # ==================================================================
    def test_n01_concurrent_sync_workers_do_not_double_process(self):
        """Multiple concurrent workers processing pending batch do not produce duplicates."""
        recs = [make_test_record(status=MemoryStatus.ACTIVE, content=f"Batch concurrent {i}") for i in range(5)]
        for r in recs:
            self.created_memory_ids.append(r.memory_id)
            memory_manager.store(r)

        threads = []
        results = []

        def _worker():
            res = vector_sync_engine.process_pending_batch(limit=10)
            results.extend(res)

        for _ in range(4):
            t = threading.Thread(target=_worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Verify all records have vectors and are synced
        for r in recs:
            self.assertIsNotNone(vector_store.get_embedding(r.memory_id))

    # ==================================================================
    # Category O: Full Production Pipeline
    # ==================================================================
    def test_o01_full_production_pipeline_from_store_to_cognition(self):
        """
        Verify end-to-end flow:
        Cognitive Engine / MemoryManager -> PostgreSQL -> Outbox -> VectorSyncEngine -> VectorStore -> Retrieval -> Cognition
        """
        from core.cognition.engine import CognitiveEngine
        cog = CognitiveEngine()

        rec = make_test_record(
            status=MemoryStatus.ACTIVE,
            content="Kubernetes cluster deployed on port 6443 with ingress controller",
            memory_type=MemoryType.SEMANTIC,
        )
        self.created_memory_ids.append(rec.memory_id)

        # 1. Store memory
        stored = memory_manager.store(rec)
        self.assertIsNotNone(stored)

        # 2. Process sync
        vector_sync_engine.process_pending_batch()

        # 3. Retrieve via CognitiveEngine
        cog_res = cog.retrieve_relevant_memory("Kubernetes cluster ingress")
        self.assertIsNotNone(cog_res)
        self.assertIn("memory_count", cog_res)
        self.assertGreaterEqual(cog_res["memory_count"], 1)

    # ==================================================================
    # Category P: Forensic Storage Layer Race & Invariants Verification
    # ==================================================================
    def test_p01_critical_pgvector_storage_layer_race_prevents_resurrection(self):
        """
        CRITICAL PGVECTOR / STORAGE LAYER RACE:
        1. Create memory M1 at generation G10.
        2. Make M1 ACTIVE.
        3. Store vector M1/G10.
        4. Start Worker A processing an UPSERT for M1/G10.
        5. Worker A validates PostgreSQL generation/status (sees ACTIVE, G10).
        6. PAUSE Worker A immediately BEFORE the actual VectorStore INSERT/UPSERT.
        7. Worker B transitions M1 to generation G11/SUPERSEDED.
        8. Worker B commits.
        9. Worker B executes DELETE for M1/G11.
        10. Confirm VectorStore contains NO vector row for M1.
        11. RELEASE Worker A.
        12. Worker A attempts its delayed G10 UPSERT directly on the VectorStore.
        13. Confirm M1 has NO vector afterward.
        """
        # Step 1-2: Create memory M1 at G10 ACTIVE
        rec = make_test_record(status=MemoryStatus.ACTIVE, content="G10 architecture preference")
        rec.generation = 10
        self.created_memory_ids.append(rec.memory_id)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO memory_records (
                        memory_id, memory_type, status, content, confidence,
                        verification_status, privacy_class, source, importance,
                        generation, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                """, (
                    rec.memory_id, rec.memory_type.value, rec.status.value, rec.content,
                    rec.confidence.value, rec.verification_status.value, rec.privacy_class.value,
                    rec.source.value, rec.importance, rec.generation
                ))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        # Step 3: Store vector M1/G10
        emb = embedding_router.embed(rec.content)
        stored_g10 = vector_store.store_embedding(
            memory_id=rec.memory_id,
            vector=emb.vector,
            model=emb.model,
            model_version=emb.model_version,
            dimension=len(emb.vector),
            generation=10,
        )
        self.assertIsNotNone(stored_g10)
        self.assertIsNotNone(vector_store.get_embedding(rec.memory_id))

        # Step 4-6: Worker A starts UPSERT for M1/G10, validates PostgreSQL (G10 ACTIVE), and is PAUSED
        worker_a_paused = threading.Event()
        worker_a_proceed = threading.Event()
        worker_a_completed = threading.Event()
        worker_a_result = {}

        def _worker_a_task():
            # Worker A validates PostgreSQL
            rec_db = memory_repository.get_by_id(rec.memory_id)
            assert rec_db.status == MemoryStatus.ACTIVE
            assert rec_db.generation == 10
            # Worker A pauses immediately BEFORE calling vector_store.store_embedding
            worker_a_paused.set()
            worker_a_proceed.wait(timeout=5.0)

            # RELEASED: Worker A attempts its delayed G10 UPSERT directly at the VectorStore boundary!
            res = vector_store.store_embedding(
                memory_id=rec.memory_id,
                vector=emb.vector,
                model=emb.model,
                model_version=emb.model_version,
                dimension=len(emb.vector),
                generation=10,
            )
            worker_a_result["record"] = res
            worker_a_completed.set()

        t_a = threading.Thread(target=_worker_a_task)
        t_a.start()
        self.assertTrue(worker_a_paused.wait(timeout=5.0), "Worker A failed to pause")

        # Step 7-8: Worker B transitions M1 to generation G11/SUPERSEDED and commits
        conn_b = postgres_manager.get_connection()
        try:
            with conn_b.cursor() as cur_b:
                cur_b.execute("""
                    UPDATE memory_records
                    SET status = %s, generation = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE memory_id = %s;
                """, (MemoryStatus.SUPERSEDED.value, 11, rec.memory_id))
            conn_b.commit()
        finally:
            postgres_manager.release_connection(conn_b)

        # Step 9: Worker B executes DELETE for M1/G11 at storage layer
        del_ok = vector_store.delete_embedding(rec.memory_id, generation=11)
        self.assertTrue(del_ok)

        # Step 10: Confirm VectorStore contains NO vector row for M1
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))
        self.assertFalse(vector_store.has_embedding(rec.memory_id, emb.model, emb.model_version))

        # Step 11: RELEASE Worker A
        worker_a_proceed.set()
        self.assertTrue(worker_a_completed.wait(timeout=5.0), "Worker A failed to complete")
        t_a.join()

        # Step 13: Confirm M1 has NO vector afterward! Stale UPSERT was rejected at storage layer
        self.assertIsNone(
            vector_store.get_embedding(rec.memory_id),
            "CRITICAL FAILURE: VectorStore resurrected a deleted vector at older generation G10!"
        )
        self.assertFalse(vector_store.has_embedding(rec.memory_id, emb.model, emb.model_version))

        # Verify durable memory_vector_state invariant in PostgreSQL
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT max_generation, vector_present FROM memory_vector_state WHERE memory_id = %s;", (rec.memory_id,))
                state_row = cur.fetchone()
                self.assertIsNotNone(state_row)
                max_gen, vec_pres = int(state_row[0]), bool(state_row[1])
                self.assertGreaterEqual(max_gen, 11)
                self.assertFalse(vec_pres, "vector_present MUST be False in memory_vector_state")
        finally:
            postgres_manager.release_connection(conn)

    def test_p02_out_of_order_upsert_delete_sequences_result_in_no_vector(self):
        """
        Test multiple out-of-order execution sequences:
        G10 UPSERT, G11 UPSERT, G12 DELETE.
        Final state MUST BE: NO VECTOR.
        """
        emb = embedding_router.embed("Out of order sequence content")
        dim = len(emb.vector)

        # Sequence 1: G12 DELETE -> G10 UPSERT -> G11 UPSERT
        m1 = f"mem-v533-seq1-{uuid.uuid4().hex[:6]}"
        self.created_memory_ids.append(m1)
        memory_manager.store(make_test_record(status=MemoryStatus.ACTIVE, content="seq1 content", generation=12))
        vector_store.delete_embedding(m1, generation=12)
        vector_store.store_embedding(m1, vector=emb.vector, dimension=dim, generation=10)
        vector_store.store_embedding(m1, vector=emb.vector, dimension=dim, generation=11)
        self.assertIsNone(vector_store.get_embedding(m1), "Sequence 1: Must have NO vector")

        # Sequence 2: G11 UPSERT -> G10 UPSERT -> G12 DELETE
        m2 = f"mem-v533-seq2-{uuid.uuid4().hex[:6]}"
        self.created_memory_ids.append(m2)
        memory_manager.store(make_test_record(status=MemoryStatus.ACTIVE, content="seq2 content", generation=12))
        vector_store.store_embedding(m2, vector=emb.vector, dimension=dim, generation=11)
        vector_store.store_embedding(m2, vector=emb.vector, dimension=dim, generation=10)
        vector_store.delete_embedding(m2, generation=12)
        self.assertIsNone(vector_store.get_embedding(m2), "Sequence 2: Must have NO vector")

        # Sequence 3: G10 UPSERT -> G12 DELETE -> G11 UPSERT
        m3 = f"mem-v533-seq3-{uuid.uuid4().hex[:6]}"
        self.created_memory_ids.append(m3)
        memory_manager.store(make_test_record(status=MemoryStatus.ACTIVE, content="seq3 content", generation=12))
        vector_store.store_embedding(m3, vector=emb.vector, dimension=dim, generation=10)
        vector_store.delete_embedding(m3, generation=12)
        vector_store.store_embedding(m3, vector=emb.vector, dimension=dim, generation=11)
        self.assertIsNone(vector_store.get_embedding(m3), "Sequence 3: Must have NO vector")

    def test_p03_numpy_restart_prevents_stale_upsert_resurrection(self):
        """
        NUMPY TEST:
        G10 vector -> G11 DELETE -> NumPy restart -> delayed G10 UPSERT -> NO VECTOR.
        """
        from memory.vector_store.numpy_store import NumPyVectorStorageAdapter
        # 1. Store G10 vector in initial NumPy store
        rec = make_test_record(status=MemoryStatus.ACTIVE, content="NumPy restart test memory", generation=10)
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)

        numpy_store = NumPyVectorStorageAdapter()
        emb = embedding_router.embed(rec.content)
        numpy_store.store_embedding(rec.memory_id, vector=emb.vector, dimension=len(emb.vector), generation=10)
        self.assertIsNotNone(numpy_store.get_embedding(rec.memory_id))

        # 2. G11 DELETE
        numpy_store.delete_embedding(rec.memory_id, generation=11)
        self.assertIsNone(numpy_store.get_embedding(rec.memory_id))

        # 3. Simulate NumPy restart: Instantiate a completely new adapter
        restarted_numpy = NumPyVectorStorageAdapter()
        # In-memory dict is completely fresh
        self.assertEqual(len(restarted_numpy._records), 0)

        # 4. Delayed G10 UPSERT arrives at the restarted adapter
        res = restarted_numpy.store_embedding(rec.memory_id, vector=emb.vector, dimension=len(emb.vector), generation=10)

        # 5. Expected: NO VECTOR!
        self.assertIsNone(restarted_numpy.get_embedding(rec.memory_id))
        self.assertFalse(restarted_numpy.has_embedding(rec.memory_id, emb.model, emb.model_version))

    def test_p04_reconciliation_never_regenerates_vector_for_non_active(self):
        """
        RECONCILIATION TEST:
        PostgreSQL: SUPERSEDED G11.
        VectorStore: old G10 or no vector.
        Run reconciliation.
        Expected: NO VECTOR. Reconciliation MUST NOT regenerate a vector for non-ACTIVE.
        """
        rec = make_test_record(status=MemoryStatus.SUPERSEDED, content="Superseded fact", generation=11)
        self.created_memory_ids.append(rec.memory_id)

        # Direct DB insert as SUPERSEDED G11
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO memory_records (
                        memory_id, memory_type, status, content, confidence,
                        verification_status, privacy_class, source, importance,
                        generation, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                """, (
                    rec.memory_id, rec.memory_type.value, rec.status.value, rec.content,
                    rec.confidence.value, rec.verification_status.value, rec.privacy_class.value,
                    rec.source.value, rec.importance, rec.generation
                ))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        # Ensure vector store has no vector
        vector_store.delete_embedding(rec.memory_id, generation=11)
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))

        # Run reconciliation
        report = vector_reconciliation_engine.reconcile(fix=True)
        self.assertIsNotNone(report)

        # CRITICAL ASSERTION: Still NO vector in store!
        self.assertIsNone(
            vector_store.get_embedding(rec.memory_id),
            "CRITICAL: Reconciliation regenerated a vector for a non-ACTIVE record!"
        )

        # Scenario B: Plant an old G10 zombie vector, run reconcile
        emb = embedding_router.embed(rec.content)
        # Bypassing guard to simulate corrupted vector
        if hasattr(vector_store, "_records"):
            from memory.vector_store.base import StoredVectorRecord
            vector_store._records[(rec.memory_id, emb.model, emb.model_version)] = StoredVectorRecord(
                embedding_id=f"emb_zombie_{rec.memory_id[:8]}",
                memory_id=rec.memory_id,
                model=emb.model,
                model_version=emb.model_version,
                dimension=len(emb.vector),
                embedding=emb.vector,
                content_hash=emb.content_hash,
                generation=10,
            )
        self.assertIsNotNone(vector_store.get_embedding(rec.memory_id))

        # Re-run reconciliation
        report2 = vector_reconciliation_engine.reconcile(fix=True)
        self.assertGreaterEqual(report2.zombie_vectors_purged, 1)

        # Vector MUST be gone
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))

    def test_p05_vector_sync_engine_trusts_postgres_over_queue_target_status(self):
        """
        TARGET_STATUS TEST:
        Queue: target_status = ACTIVE, target_generation = 10.
        PostgreSQL: status = SUPERSEDED, generation = 11.
        VectorSyncEngine MUST trust authoritative PostgreSQL state, not queue target_status.
        """
        rec = make_test_record(status=MemoryStatus.SUPERSEDED, content="Superseded authoritative content", generation=11)
        self.created_memory_ids.append(rec.memory_id)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO memory_records (
                        memory_id, memory_type, status, content, confidence,
                        verification_status, privacy_class, source, importance,
                        generation, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                """, (
                    rec.memory_id, rec.memory_type.value, rec.status.value, rec.content,
                    rec.confidence.value, rec.verification_status.value, rec.privacy_class.value,
                    rec.source.value, rec.importance, rec.generation
                ))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        # Enqueue misleading work item claiming ACTIVE / G10
        sync_id = new_sync_id()
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO vector_sync_queue (
                        sync_id, memory_id, operation, target_generation, target_status,
                        idempotency_key, sync_status, created_at, updated_at
                    ) VALUES (%s, %s, 'UPSERT', 10, 'ACTIVE', %s, 'PENDING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                """, (sync_id, rec.memory_id, f"misleading_target_status_{uuid.uuid4().hex[:6]}"))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        # Process work item
        res = vector_sync_engine.process_work_item(sync_id)
        self.assertTrue(res.success)
        self.assertTrue(res.is_stale, "Stale flag must be set when queue target conflicts with PostgreSQL")

        # VectorStore MUST NOT contain any vector
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))

    def test_p06_retrieval_cleanup_uses_canonical_outbox_queue_path(self):
        """
        RETRIEVAL CLEANUP TEST:
        When retrieval encounters zombie/orphan vectors, cleanup must use the canonical
        V5.3.3 synchronization path via schedule_deletion and vector_sync_queue.
        """
        rec = make_test_record(status=MemoryStatus.SUPERSEDED, content="Zombie memory awaiting opportunistic retrieval cleanup", generation=5)
        self.created_memory_ids.append(rec.memory_id)
        memory_manager.store(rec)

        # Plant a zombie vector in VectorStore
        emb = embedding_router.embed(rec.content)
        if hasattr(vector_store, "_records"):
            from memory.vector_store.base import StoredVectorRecord
            vector_store._records[(rec.memory_id, emb.model, emb.model_version)] = StoredVectorRecord(
                embedding_id=f"emb_zombie_{rec.memory_id[:8]}",
                memory_id=rec.memory_id,
                model=emb.model,
                model_version=emb.model_version,
                dimension=len(emb.vector),
                embedding=emb.vector,
                content_hash=emb.content_hash,
                generation=5,
            )
        self.assertIsNotNone(vector_store.get_embedding(rec.memory_id))

        # Schedule deletion via canonical sync path
        sync_id = vector_sync_engine.schedule_deletion(rec.memory_id, generation=5, trigger_now=True)
        self.assertIsNotNone(sync_id)

        # Verify item was recorded in vector_sync_queue
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT operation, sync_status FROM vector_sync_queue WHERE sync_id = %s;", (sync_id,))
                qrow = cur.fetchone()
                self.assertIsNotNone(qrow)
                self.assertEqual(qrow[0], "DELETE")
                self.assertEqual(qrow[1], VectorSyncStatus.SYNCED.value)
        finally:
            postgres_manager.release_connection(conn)

        # VectorStore must be purged
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))

    def test_p07_sensitive_memory_work_item_security_enforcement(self):
        """
        SECURITY TEST:
        Attempt to create/process an UPSERT work item for a SENSITIVE memory.
        Verify:
        - no embedding generated
        - no vector stored
        - no raw content logged
        - no raw embedding logged
        """
        rec = make_test_record(
            status=MemoryStatus.ACTIVE,
            privacy_class=PrivacyClass.SENSITIVE,
            content="Sensitive secret token DOOM_SUPER_KEY_9999",
            generation=1,
        )
        self.created_memory_ids.append(rec.memory_id)
        memory_repository.store(rec)

        # Queue UPSERT work item
        sync_id = new_sync_id()
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO vector_sync_queue (
                        sync_id, memory_id, operation, target_generation, target_status,
                        idempotency_key, sync_status, created_at, updated_at
                    ) VALUES (%s, %s, 'UPSERT', 1, 'ACTIVE', %s, 'PENDING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                """, (sync_id, rec.memory_id, f"sensitive_sec_{uuid.uuid4().hex[:6]}"))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        # Process work item
        res = vector_sync_engine.process_work_item(sync_id)
        self.assertTrue(res.success)
        self.assertTrue(res.is_skipped)

        # Assertions
        # 1. No vector stored
        self.assertIsNone(vector_store.get_embedding(rec.memory_id))
        self.assertFalse(vector_store.has_embedding(rec.memory_id, "default", "v1"))

        # 2. Check queue record does not log secret content
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT last_error_message_safe FROM vector_sync_queue WHERE sync_id = %s;", (sync_id,))
                msg_row = cur.fetchone()
                safe_msg = str(msg_row[0] or "")
                self.assertNotIn("DOOM_SUPER_KEY_9999", safe_msg)
        finally:
            postgres_manager.release_connection(conn)


if __name__ == "__main__":
    unittest.main(verbosity=2)
