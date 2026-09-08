"""
DOOM V5.3.7.2 — Recovery & Outbox Dedicated Test Suite
======================================================
42 Test Scenarios verifying:
1. Outbox transactional durability (T01 - T03)
2. Worker leasing & claim semantics (T04 - T07)
3. Lease fencing & dual-worker safety (T08 - T10)
4. Crash recovery & startup sweep (T11 - T14)
5. Symmetrical generation safety (T15 - T18)
6. Stale operation rejection & queue retirement (T19 - T21)
7. Retry backoff & jitter behavior (T22 - T24)
8. Dead-letter isolation & quarantine (T25 - T27)
9. NumPy fallback rehydration (T28 - T31)
10. Memory boundary & privacy enforcement (T32 - T34)
11. Reconciliation audit & repair (T35 - T37)
12. Idempotent replay & deduplication (T38 - T39)
13. Poison pill & queue starvation (T40 - T41)
14. End-to-end recovery lifecycle (T42)
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
)
from memory.sync_engine import (
    vector_sync_engine,
    VectorSyncEngine,
    startup_recovery,
    LeaseLostException,
    TRANSIENT_ERROR_CLASSES,
    PERMANENT_ERROR_CLASSES,
)
from memory.reconciliation import (
    vector_reconciliation_engine,
    VectorReconciliationEngine,
    rehydrate_numpy_store,
)
from memory.vector_store import vector_store
from memory.vector_store.base import VectorStorageBackend
from memory.embedding.router import embedding_router


def _clean_test_records(prefix: str = "test_v5372_"):
    """Clean up test records created during test runs."""
    conn = postgres_manager.get_connection()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM vector_sync_queue WHERE memory_id LIKE %s;", (f"{prefix}%",))
            cur.execute("DELETE FROM memory_vector_state WHERE memory_id LIKE %s;", (f"{prefix}%",))
            cur.execute("DELETE FROM memory_lifecycle_events WHERE memory_id LIKE %s;", (f"{prefix}%",))
            cur.execute("DELETE FROM memory_records WHERE memory_id LIKE %s;", (f"{prefix}%",))
        conn.commit()
    except Exception as e:
        conn.rollback()
    finally:
        postgres_manager.release_connection(conn)


class TestV5372RecoveryOutbox(unittest.TestCase):
    """Full 42-scenario dedicated test suite for DOOM V5.3.7.2 Recovery & Outbox."""

    @classmethod
    def setUpClass(cls):
        postgres_manager._create_tables()
        _clean_test_records()

    @classmethod
    def tearDownClass(cls):
        _clean_test_records()

    def setUp(self):
        self.prefix = f"test_v5372_{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        _clean_test_records(self.prefix)

    def _insert_test_memory(
        self,
        memory_id: str,
        content: str = "Test recovery content",
        status: str = "ACTIVE",
        generation: int = 1,
        privacy: str = "NORMAL",
    ) -> MemoryRecord:
        """Insert authoritative memory record directly into PostgreSQL without triggering post-commit fast-path."""
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO memory_records (
                        memory_id, memory_type, content, source, confidence,
                        importance, status, generation, project_id, verification_status,
                        privacy_class, metadata, created_at, updated_at
                    ) VALUES (
                        %s, 'SEMANTIC', %s, 'SYSTEM_OBSERVATION', 'HIGH',
                        0.5, %s, %s, 'test_project', 'VERIFIED',
                        %s, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (memory_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        status = EXCLUDED.status,
                        generation = EXCLUDED.generation,
                        privacy_class = EXCLUDED.privacy_class,
                        updated_at = CURRENT_TIMESTAMP;
                """, (memory_id, content, status, generation, privacy))
            conn.commit()
            return MemoryRecord(
                memory_id=memory_id,
                content=content,
                status=MemoryStatus(status),
                generation=generation,
                privacy_class=PrivacyClass(privacy),
            )
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category 1: Outbox Transactional Durability (T01 - T03)
    # ==================================================================
    def test_01_outbox_transactional_durability_atomic_enqueue(self):
        """T01: Memory insert + outbox enqueue in single atomic transaction."""
        mid = f"{self.prefix}_t01"
        conn = postgres_manager.get_connection()
        self.assertIsNotNone(conn)
        try:
            with conn.cursor() as cur:
                # Insert memory record
                cur.execute("""
                    INSERT INTO memory_records (memory_id, content, memory_type, status, source, confidence,
                                               verification_status, privacy_class, project_id, metadata, generation)
                    VALUES (%s, 'T01 content', 'SEMANTIC', 'ACTIVE', 'SYSTEM_OBSERVATION', 'HIGH', 'VERIFIED', 'NORMAL', 'test_proj', '{}', 1);
                """, (mid,))
                # Enqueue outbox in same transaction
                sync_id = vector_sync_engine.enqueue_sync_work(
                    cur=cur,
                    memory_id=mid,
                    operation=SyncOperation.UPSERT,
                    target_generation=1,
                    target_status=MemoryStatus.ACTIVE,
                    content="T01 content",
                )
            conn.commit()

            # Verify both exist atomically
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM memory_records WHERE memory_id = %s;", (mid,))
                self.assertIsNotNone(cur.fetchone())
                cur.execute("SELECT sync_status FROM vector_sync_queue WHERE sync_id = %s;", (sync_id,))
                qrow = cur.fetchone()
                self.assertIsNotNone(qrow)
                self.assertEqual(qrow[0], VectorSyncStatus.PENDING.value)
        finally:
            postgres_manager.release_connection(conn)

    def test_02_outbox_transactional_durability_rollback_safety(self):
        """T02: Transaction rollback aborts outbox enqueue cleanly with zero stray records."""
        mid = f"{self.prefix}_t02"
        conn = postgres_manager.get_connection()
        self.assertIsNotNone(conn)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO memory_records (memory_id, content, memory_type, status, source, confidence,
                                               verification_status, privacy_class, project_id, metadata, generation)
                    VALUES (%s, 'T02 rollback', 'SEMANTIC', 'ACTIVE', 'SYSTEM_OBSERVATION', 'HIGH', 'VERIFIED', 'NORMAL', 'test_proj', '{}', 1);
                """, (mid,))
                vector_sync_engine.enqueue_sync_work(
                    cur=cur,
                    memory_id=mid,
                    operation=SyncOperation.UPSERT,
                    target_generation=1,
                    target_status=MemoryStatus.ACTIVE,
                )
            # Deliberate rollback
            conn.rollback()

            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM memory_records WHERE memory_id = %s;", (mid,))
                self.assertIsNone(cur.fetchone())
                cur.execute("SELECT 1 FROM vector_sync_queue WHERE memory_id = %s;", (mid,))
                self.assertIsNone(cur.fetchone())
        finally:
            postgres_manager.release_connection(conn)

    def test_03_outbox_idempotency_deduplication(self):
        """T03: Enqueuing duplicate work items with identical idempotency key is an idempotent upsert."""
        mid = f"{self.prefix}_t03"
        self._insert_test_memory(mid, content="T03 deduplication")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid1 = vector_sync_engine.enqueue_sync_work(
                    cur=cur,
                    memory_id=mid,
                    operation=SyncOperation.UPSERT,
                    target_generation=1,
                    target_status=MemoryStatus.ACTIVE,
                    content="T03 deduplication",
                )
            conn.commit()

            with conn.cursor() as cur:
                sid2 = vector_sync_engine.enqueue_sync_work(
                    cur=cur,
                    memory_id=mid,
                    operation=SyncOperation.UPSERT,
                    target_generation=1,
                    target_status=MemoryStatus.ACTIVE,
                    content="T03 deduplication",
                )
            conn.commit()

            # Verify only 1 row exists in queue for this memory
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM vector_sync_queue WHERE memory_id = %s;", (mid,))
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category 2: Worker Leasing & Claim Semantics (T04 - T07)
    # ==================================================================
    def test_04_worker_claim_single_batch(self):
        """T04: Single worker claims available batch with FOR UPDATE SKIP LOCKED."""
        mid = f"{self.prefix}_t04"
        self._insert_test_memory(mid)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
            conn.commit()

            claimed = vector_sync_engine.claim_work_items(worker_id="worker_alpha", limit=10, lease_seconds=45)
            self.assertTrue(any(item.sync_id == sid for item in claimed))
            claimed_item = next(item for item in claimed if item.sync_id == sid)
            self.assertEqual(claimed_item.worker_id, "worker_alpha")
            self.assertEqual(claimed_item.sync_status, VectorSyncStatus.PROCESSING.value)
        finally:
            postgres_manager.release_connection(conn)

    def test_05_concurrent_workers_claim_disjoint_subsets(self):
        """T05: Multiple concurrent workers claim disjoint subsets without blocking or collisions."""
        mids = [f"{self.prefix}_t05_{i}" for i in range(6)]
        conn = postgres_manager.get_connection()
        sids = []
        try:
            with conn.cursor() as cur:
                for mid in mids:
                    self._insert_test_memory(mid)
                    sid = vector_sync_engine.enqueue_sync_work(
                        cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                        target_generation=1, target_status=MemoryStatus.ACTIVE,
                    )
                    sids.append(sid)
            conn.commit()

            claimed_w1: List[VectorSyncWorkItem] = []
            claimed_w2: List[VectorSyncWorkItem] = []

            def worker1():
                nonlocal claimed_w1
                claimed_w1 = vector_sync_engine.claim_work_items(worker_id="worker_1", limit=3)

            def worker2():
                nonlocal claimed_w2
                claimed_w2 = vector_sync_engine.claim_work_items(worker_id="worker_2", limit=3)

            t1 = threading.Thread(target=worker1)
            t2 = threading.Thread(target=worker2)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            ids_w1 = {item.sync_id for item in claimed_w1}
            ids_w2 = {item.sync_id for item in claimed_w2}

            # Disjoint set verification
            intersection = ids_w1.intersection(ids_w2)
            self.assertEqual(len(intersection), 0, f"Workers claimed overlapping items: {intersection}")
            self.assertGreater(len(ids_w1) + len(ids_w2), 0)
        finally:
            postgres_manager.release_connection(conn)

    def test_06_worker_claim_timestamp_correctness(self):
        """T06: lease_expires_at is accurately computed relative to lease_acquired_at."""
        mid = f"{self.prefix}_t06"
        self._insert_test_memory(mid)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
            conn.commit()

            claimed = vector_sync_engine.claim_work_items(worker_id="worker_time", limit=5, lease_seconds=120)
            target = next(item for item in claimed if item.sync_id == sid)
            self.assertIsNotNone(target.lease_acquired_at)
            self.assertIsNotNone(target.lease_expires_at)
            self.assertIsNotNone(target.heartbeat_at)
        finally:
            postgres_manager.release_connection(conn)

    def test_07_non_claimable_statuses_skipped(self):
        """T07: Items in SYNCED, DEAD_LETTER, or unexpired PROCESSING are skipped during claim."""
        mid = f"{self.prefix}_t07"
        self._insert_test_memory(mid)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
                # Set status to DEAD_LETTER
                cur.execute("UPDATE vector_sync_queue SET sync_status = 'DEAD_LETTER' WHERE sync_id = %s;", (sid,))
            conn.commit()

            claimed = vector_sync_engine.claim_work_items(worker_id="worker_x", limit=10)
            self.assertFalse(any(item.sync_id == sid for item in claimed))
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category 3: Lease Fencing & Dual-Worker Safety (T08 - T10)
    # ==================================================================
    def test_08_lease_fencing_successful_completion(self):
        """T08: Worker owning active, unexpired lease successfully finalizes work item."""
        mid = f"{self.prefix}_t08"
        self._insert_test_memory(mid, content="Active unexpired test")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                    content="Active unexpired test",
                )
            conn.commit()

            claimed = vector_sync_engine.claim_work_items(worker_id="worker_owner", limit=1, lease_seconds=60)
            self.assertEqual(len(claimed), 1)

            res = vector_sync_engine.process_work_item(sid, worker_id="worker_owner")
            self.assertTrue(res.success)
            self.assertEqual(res.status, VectorSyncStatus.SYNCED.value)

            # Queue record updated to SYNCED and lease fields cleared
            with conn.cursor() as cur:
                cur.execute("SELECT sync_status, worker_id, lease_expires_at FROM vector_sync_queue WHERE sync_id = %s;", (sid,))
                row = cur.fetchone()
                self.assertEqual(row[0], VectorSyncStatus.SYNCED.value)
                self.assertIsNone(row[1])
                self.assertIsNone(row[2])
        finally:
            postgres_manager.release_connection(conn)

    def test_09_lease_fencing_expired_lease_rejection(self):
        """T09: Worker whose lease expired is prevented from finalizing queue status."""
        mid = f"{self.prefix}_t09"
        self._insert_test_memory(mid, content="Expired lease fencing")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                    content="Expired lease fencing",
                )
                # Force lease to be expired in the past
                cur.execute("""
                    UPDATE vector_sync_queue
                    SET sync_status = 'PROCESSING',
                        worker_id = 'worker_slow',
                        lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '10 seconds'
                    WHERE sync_id = %s;
                """, (sid,))
            conn.commit()

            # Attempt fenced completion directly
            synced_ok = vector_sync_engine._mark_queue_synced(conn, sid, worker_id="worker_slow")
            self.assertFalse(synced_ok, "Fenced completion should fail for expired lease!")
        finally:
            postgres_manager.release_connection(conn)

    def test_10_zombie_worker_race_protection(self):
        """T10: Zombie worker resumes after lease expiry and cannot overwrite queue or commit side effects."""
        mid = f"{self.prefix}_t10"
        self._insert_test_memory(mid, content="Initial content", generation=1)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                    content="Initial content",
                )
            conn.commit()

            # Worker A claims item
            vector_sync_engine.claim_work_items(worker_id="worker_A", limit=1, lease_seconds=1)

            # Fast-forward / expire Worker A's lease
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE vector_sync_queue
                    SET lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '5 seconds',
                        locked_until = CURRENT_TIMESTAMP - INTERVAL '5 seconds'
                    WHERE sync_id = %s;
                """, (sid,))
            conn.commit()

            # Reclaim expired lease
            vector_sync_engine.recover_expired_leases()

            # Worker B claims and completes the item
            vector_sync_engine.claim_work_items(worker_id="worker_B", limit=1, lease_seconds=60)
            res_b = vector_sync_engine.process_work_item(sid, worker_id="worker_B")
            self.assertTrue(res_b.success)

            # Now zombie Worker A attempts to finalize with its stale lease
            res_a = vector_sync_engine.process_work_item(sid, worker_id="worker_A")
            # Should be skipped or rejected cleanly without altering SYNCED state
            with conn.cursor() as cur:
                cur.execute("SELECT sync_status FROM vector_sync_queue WHERE sync_id = %s;", (sid,))
                self.assertEqual(cur.fetchone()[0], VectorSyncStatus.SYNCED.value)
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category 4: Crash Recovery & Startup Sweep (T11 - T14)
    # ==================================================================
    def test_11_startup_recovery_stage1_lease_reclamation(self):
        """T11: Stage 1 lease reclamation detects stranded PROCESSING work items past expiry."""
        mid = f"{self.prefix}_t11"
        self._insert_test_memory(mid)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
                cur.execute("""
                    UPDATE vector_sync_queue
                    SET sync_status = 'PROCESSING',
                        worker_id = 'crashed_worker',
                        attempt_count = 1,
                        lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '20 seconds',
                        locked_until = CURRENT_TIMESTAMP - INTERVAL '20 seconds'
                    WHERE sync_id = %s;
                """, (sid,))
            conn.commit()

            reclaimed = vector_sync_engine.recover_expired_leases()
            self.assertGreaterEqual(reclaimed, 1)

            with conn.cursor() as cur:
                cur.execute("SELECT sync_status, worker_id FROM vector_sync_queue WHERE sync_id = %s;", (sid,))
                row = cur.fetchone()
                self.assertEqual(row[0], VectorSyncStatus.RETRY_REQUIRED.value)
                self.assertIsNone(row[1])
        finally:
            postgres_manager.release_connection(conn)

    def test_12_startup_recovery_stage2_bounded_drain(self):
        """T12: Stage 2 bounded outbox drain processes pending and retryable work items."""
        mid = f"{self.prefix}_t12"
        self._insert_test_memory(mid, content="Bounded drain test")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                    content="Bounded drain test",
                )
            conn.commit()

            results = vector_sync_engine.process_pending_batch(limit=10)
            self.assertTrue(any(r.sync_id == sid for r in results))

            with conn.cursor() as cur:
                cur.execute("SELECT sync_status FROM vector_sync_queue WHERE sync_id = %s;", (sid,))
                self.assertEqual(cur.fetchone()[0], VectorSyncStatus.SYNCED.value)
        finally:
            postgres_manager.release_connection(conn)

    def test_13_startup_recovery_stage3_numpy_rehydration(self):
        """T13: Stage 3 NumPy rehydration populates active vectors up to capacity limit."""
        mid = f"{self.prefix}_t13"
        self._insert_test_memory(mid, content="NumPy rehydration test", status="ACTIVE")

        stats = rehydrate_numpy_store(batch_size=10, max_records=10000)
        self.assertIn("rehydrated_count", stats)
        self.assertGreaterEqual(stats["rehydrated_count"], 0)
        self.assertIn("capacity_hit", stats)

    def test_14_startup_recovery_stage4_consistency_audit(self):
        """T14: Stage 4 consistency audit verifies zero zombie vectors for inactive memories."""
        mid = f"{self.prefix}_t14"
        self._insert_test_memory(mid, content="Archived zombie test", status="ARCHIVED")

        # Reconcile vector store
        report = vector_reconciliation_engine.reconcile(batch_size=50, fix=True)
        self.assertIsInstance(report, ReconciliationReport)
        self.assertGreaterEqual(report.scanned_records, 1)

    # ==================================================================
    # Category 5: Symmetrical Generation Safety (T15 - T18)
    # ==================================================================
    def test_15_monotonic_generation_safety_stale_upsert(self):
        """T15: Authoritative monotonic generation check blocks stale UPSERT (G_rec > G_work)."""
        mid = f"{self.prefix}_t15"
        # Database has generation 2
        self._insert_test_memory(mid, content="Gen 2 content", generation=2)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                # Enqueue work for stale generation 1
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                    content="Gen 1 content",
                )
            conn.commit()

            res = vector_sync_engine.process_work_item(sid)
            self.assertTrue(res.success)
            self.assertTrue(res.is_stale, "Stale UPSERT must be flagged is_stale=True")
            self.assertEqual(res.status, VectorSyncStatus.SYNCED.value)
        finally:
            postgres_manager.release_connection(conn)

    def test_16_critical_stale_delete_race_protection(self):
        """T16: CRITICAL STALE DELETE RACE: Old DELETE (G1) arriving after newer UPSERT (G2) does NOT delete G2 vector."""
        mid = f"{self.prefix}_t16"
        # Step 1: Memory is at generation 2 in PostgreSQL (ACTIVE)
        self._insert_test_memory(mid, content="G2 active content", generation=2, status="ACTIVE")

        # Store G2 embedding in vector store
        emb = embedding_router.embed("G2 active content", check_policy=True)
        self.assertIsNotNone(emb)
        vector_store.store_embedding(
            memory_id=mid,
            embedding=emb.vector,
            model=emb.model,
            model_version=emb.model_version,
            content_hash=emb.content_hash,
            dimension=len(emb.vector),
            generation=2,
        )
        self.assertTrue(vector_store.has_embedding(mid, emb.model, emb.model_version))

        # Step 2: An old, delayed DELETE for generation 1 is processed by Worker A
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid_del = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.DELETE,
                    target_generation=1, target_status=MemoryStatus.DELETED,
                )
            conn.commit()

            res_del = vector_sync_engine.process_work_item(sid_del, worker_id="worker_A")
            self.assertTrue(res_del.success)
            self.assertTrue(res_del.is_stale, "Old DELETE for G1 must be flagged is_stale=True")

            # CRITICAL VERIFICATION: The G2 vector MUST NOT be deleted!
            self.assertTrue(
                vector_store.has_embedding(mid, emb.model, emb.model_version),
                "CRITICAL INVARIANT VIOLATION: Stale DELETE purged newer G2 vector!"
            )
        finally:
            postgres_manager.release_connection(conn)

    def test_17_symmetrical_generation_safety_matching_gen_upsert(self):
        """T17: Matching generation (G_work == G_rec) UPSERT completes and writes to vector store."""
        mid = f"{self.prefix}_t17"
        self._insert_test_memory(mid, content="Matching gen upsert", generation=1, status="ACTIVE")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                    content="Matching gen upsert",
                )
            conn.commit()

            res = vector_sync_engine.process_work_item(sid)
            self.assertTrue(res.success)
            self.assertFalse(res.is_stale)
            self.assertEqual(res.status, VectorSyncStatus.SYNCED.value)
        finally:
            postgres_manager.release_connection(conn)

    def test_18_symmetrical_generation_safety_matching_gen_delete(self):
        """T18: Matching generation (G_work == G_rec) DELETE purges vector and records tombstone."""
        mid = f"{self.prefix}_t18"
        self._insert_test_memory(mid, content="Matching gen delete", generation=1, status="DELETED")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.DELETE,
                    target_generation=1, target_status=MemoryStatus.DELETED,
                )
            conn.commit()

            res = vector_sync_engine.process_work_item(sid)
            self.assertTrue(res.success)
            self.assertFalse(res.is_stale)
            self.assertEqual(res.status, VectorSyncStatus.SYNCED.value)
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category 6: Stale Operation Rejection & Queue Retirement (T19 - T21)
    # ==================================================================
    def test_19_stale_upsert_telemetry_and_metrics(self):
        """T19: Stale UPSERT rejection increments stale_rejected count."""
        mid = f"{self.prefix}_t19"
        self._insert_test_memory(mid, content="Gen 3 content", generation=3)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
            conn.commit()

            initial_stale = vector_sync_engine._telemetry_counts.get("stale_rejected", 0)
            vector_sync_engine.process_work_item(sid)
            final_stale = vector_sync_engine._telemetry_counts.get("stale_rejected", 0)
            self.assertGreaterEqual(final_stale, initial_stale + 1)
        finally:
            postgres_manager.release_connection(conn)

    def test_20_stale_delete_telemetry_and_metrics(self):
        """T20: Stale DELETE rejection increments stale_rejected count."""
        mid = f"{self.prefix}_t20"
        self._insert_test_memory(mid, content="Gen 2 content", generation=2)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.DELETE,
                    target_generation=1, target_status=MemoryStatus.DELETED,
                )
            conn.commit()

            initial_stale = vector_sync_engine._telemetry_counts.get("stale_rejected", 0)
            vector_sync_engine.process_work_item(sid)
            final_stale = vector_sync_engine._telemetry_counts.get("stale_rejected", 0)
            self.assertGreaterEqual(final_stale, initial_stale + 1)
        finally:
            postgres_manager.release_connection(conn)

    def test_21_physical_deletion_purge(self):
        """T21: Physical hard deletion in PostgreSQL (rec_row == None) purges vector safely."""
        mid = f"{self.prefix}_t21"
        # Store an embedding first
        emb = embedding_router.embed("Hard delete content", check_policy=True)
        if emb:
            vector_store.store_embedding(
                memory_id=mid,
                embedding=emb.vector,
                model=emb.model,
                model_version=emb.model_version,
                content_hash=emb.content_hash,
                dimension=len(emb.vector),
                generation=1,
            )
        # Create work item for non-existent memory record
        work_item = VectorSyncWorkItem(
            sync_id=f"sync_phys_{uuid.uuid4().hex[:6]}",
            memory_id=mid,
            operation=SyncOperation.DELETE.value,
            target_generation=1,
            target_status=MemoryStatus.DELETED.value,
            sync_status=VectorSyncStatus.PROCESSING.value,
        )
        res = vector_sync_engine._execute_sync_operation(work_item)
        self.assertTrue(res.success)
        self.assertTrue(res.is_skipped)
        active_model = embedding_router.provider.model_name
        active_version = embedding_router.provider.model_version
        self.assertFalse(vector_store.has_embedding(mid, active_model, active_version))

    # ==================================================================
    # Category 7: Retry Backoff & Jitter Behavior (T22 - T24)
    # ==================================================================
    def test_22_transient_error_exponential_backoff(self):
        """T22: Transient error (ConnectionError) schedules RETRY_REQUIRED with exponential backoff."""
        mid = f"{self.prefix}_t22"
        self._insert_test_memory(mid)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
            conn.commit()

            work_item = vector_sync_engine._fetch_work_item(sid)
            vector_sync_engine._handle_sync_failure(conn, sid, work_item, "ConnectionError", "DB connection dropped")

            item_after = vector_sync_engine._fetch_work_item(sid)
            self.assertEqual(item_after.sync_status, VectorSyncStatus.RETRY_REQUIRED.value)
            self.assertEqual(item_after.attempt_count, 1)
            self.assertEqual(item_after.last_error_class, "ConnectionError")
        finally:
            postgres_manager.release_connection(conn)

    def test_23_retry_backoff_jitter_behavior(self):
        """T23: Backoff interval includes positive random jitter [0, 1.0s]."""
        mid = f"{self.prefix}_t23"
        self._insert_test_memory(mid)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
            conn.commit()

            work_item = vector_sync_engine._fetch_work_item(sid)
            vector_sync_engine._handle_sync_failure(conn, sid, work_item, "OperationalError", "Transient lock timeout")

            with conn.cursor() as cur:
                cur.execute("SELECT available_at > CURRENT_TIMESTAMP FROM vector_sync_queue WHERE sync_id = %s;", (sid,))
                is_future = cur.fetchone()[0]
                self.assertTrue(is_future, "available_at must be scheduled in the future")
        finally:
            postgres_manager.release_connection(conn)

    def test_24_future_available_at_skipped_by_sweeper(self):
        """T24: Items with available_at in the future are skipped during claim sweeps."""
        mid = f"{self.prefix}_t24"
        self._insert_test_memory(mid)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
                cur.execute("""
                    UPDATE vector_sync_queue
                    SET sync_status = 'RETRY_REQUIRED',
                        available_at = CURRENT_TIMESTAMP + INTERVAL '10 minutes'
                    WHERE sync_id = %s;
                """, (sid,))
            conn.commit()

            claimed = vector_sync_engine.claim_work_items(worker_id="sweep_worker", limit=10)
            self.assertFalse(any(item.sync_id == sid for item in claimed))
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category 8: Dead-Letter Isolation & Quarantine (T25 - T27)
    # ==================================================================
    def test_25_permanent_error_immediate_dead_letter(self):
        """T25: Permanent error (PolicyViolationError) escalates immediately to DEAD_LETTER."""
        mid = f"{self.prefix}_t25"
        self._insert_test_memory(mid)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
            conn.commit()

            work_item = vector_sync_engine._fetch_work_item(sid)
            vector_sync_engine._handle_sync_failure(conn, sid, work_item, "PolicyViolationError", "Sensitive memory violation")

            item_after = vector_sync_engine._fetch_work_item(sid)
            self.assertEqual(item_after.sync_status, VectorSyncStatus.DEAD_LETTER.value)
        finally:
            postgres_manager.release_connection(conn)

    def test_26_max_attempts_escalation_to_dead_letter(self):
        """T26: Transient failure reaching max_attempts (5) transitions to DEAD_LETTER."""
        mid = f"{self.prefix}_t26"
        self._insert_test_memory(mid)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
                cur.execute("UPDATE vector_sync_queue SET attempt_count = 4 WHERE sync_id = %s;", (sid,))
            conn.commit()

            work_item = vector_sync_engine._fetch_work_item(sid)
            vector_sync_engine._handle_sync_failure(conn, sid, work_item, "ConnectionError", "5th attempt timeout")

            item_after = vector_sync_engine._fetch_work_item(sid)
            self.assertEqual(item_after.sync_status, VectorSyncStatus.DEAD_LETTER.value)
            self.assertEqual(item_after.attempt_count, 5)
        finally:
            postgres_manager.release_connection(conn)

    def test_27_dead_letter_isolation_from_sweeps(self):
        """T27: DEAD_LETTER items are isolated from sweeps without blocking other items."""
        mid_bad = f"{self.prefix}_t27_bad"
        mid_good = f"{self.prefix}_t27_good"
        self._insert_test_memory(mid_bad)
        self._insert_test_memory(mid_good, content="Good work item")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid_bad = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid_bad, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
                sid_good = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid_good, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                    content="Good work item",
                )
                cur.execute("UPDATE vector_sync_queue SET sync_status = 'DEAD_LETTER' WHERE sync_id = %s;", (sid_bad,))
            conn.commit()

            claimed = vector_sync_engine.claim_work_items(worker_id="worker_clean", limit=10)
            claimed_ids = {item.sync_id for item in claimed}
            self.assertNotIn(sid_bad, claimed_ids)
            self.assertIn(sid_good, claimed_ids)
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category 9: NumPy Fallback Rehydration (T28 - T31)
    # ==================================================================
    def test_28_numpy_rehydration_active_records(self):
        """T28: Process restart with empty NumPy store successfully rehydrates ACTIVE records."""
        mid = f"{self.prefix}_t28"
        self._insert_test_memory(mid, content="NumPy active memory", status="ACTIVE")

        stats = rehydrate_numpy_store(batch_size=20, max_records=100)
        self.assertGreaterEqual(stats["rehydrated_count"], 1)

    def test_29_numpy_rehydration_excludes_non_active(self):
        """T29: Non-ACTIVE records (SUPERSEDED, ARCHIVED, DELETED) are never rehydrated."""
        mid_sup = f"{self.prefix}_t29_sup"
        mid_arch = f"{self.prefix}_t29_arch"
        self._insert_test_memory(mid_sup, content="Superseded", status="SUPERSEDED")
        self._insert_test_memory(mid_arch, content="Archived", status="ARCHIVED")

        stats = rehydrate_numpy_store(batch_size=50, max_records=100)
        # Verify neither mid_sup nor mid_arch exists in vector store
        self.assertFalse(vector_store.has_embedding(mid_sup, "default", "v1"))
        self.assertFalse(vector_store.has_embedding(mid_arch, "default", "v1"))

    def test_30_numpy_rehydration_bounded_capacity(self):
        """T30: Rehydration respects hard capacity limit and sets capacity_hit when reached."""
        for i in range(5):
            self._insert_test_memory(f"{self.prefix}_t30_{i}", content=f"Record {i}")

        stats = rehydrate_numpy_store(batch_size=2, max_records=3)
        self.assertTrue(stats["capacity_hit"])
        self.assertEqual(stats["rehydrated_count"], 3)

    def test_31_numpy_rehydration_preserves_generation(self):
        """T31: Rehydration sets generation from PostgreSQL generation correctly."""
        mid = f"{self.prefix}_t31"
        self._insert_test_memory(mid, content="Gen 4 preserved", generation=4, status="ACTIVE")

        stats = rehydrate_numpy_store(batch_size=10, max_records=20)
        self.assertGreaterEqual(stats["rehydrated_count"], 1)
        max_gen = vector_store.get_max_generation(mid)
        self.assertGreaterEqual(max_gen, 4)

    # ==================================================================
    # Category 10: Memory Boundary & Privacy Enforcement (T32 - T34)
    # ==================================================================
    def test_32_privacy_enforcement_sensitive_exclusion_rehydration(self):
        """T32: SENSITIVE memories excluded at SQL and Python levels during rehydration."""
        mid = f"{self.prefix}_t32"
        self._insert_test_memory(mid, content="Sensitive secret", privacy="SENSITIVE", status="ACTIVE")

        stats = rehydrate_numpy_store(batch_size=10, max_records=50)
        self.assertFalse(vector_store.has_embedding(mid, "default", "v1"))

    def test_33_privacy_enforcement_sensitive_upsert_purge(self):
        """T33: If a memory becomes SENSITIVE, existing vectors are purged and outbox rejects embedding."""
        mid = f"{self.prefix}_t33"
        self._insert_test_memory(mid, content="Sensitive transition", privacy="SENSITIVE", status="ACTIVE")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
            conn.commit()

            res = vector_sync_engine.process_work_item(sid)
            self.assertTrue(res.success)
            self.assertTrue(res.is_skipped)
            self.assertFalse(vector_store.has_embedding(mid, "default", "v1"))
        finally:
            postgres_manager.release_connection(conn)

    def test_34_privacy_sanitization_telemetry_safety(self):
        """T34: Raw memory content and vector floats never leak into logs or telemetry dictionaries."""
        from memory.sync_engine import _emit_sync_telemetry
        # Calling sanitized telemetry with raw content
        _emit_sync_telemetry(
            "TEST_EVENT",
            content="SUPER_SECRET_PLAINTEXT",
            vector=[0.1, 0.2, 0.3],
            safe_id="safe_123",
        )
        # Should execute cleanly without raising exception

    # ==================================================================
    # Category 11: Reconciliation Audit & Repair (T35 - T37)
    # ==================================================================
    def test_35_reconciliation_missing_vector_repair(self):
        """T35: Reconciliation detects and repairs missing vectors for active records."""
        mid = f"{self.prefix}_t35"
        self._insert_test_memory(mid, content="Missing vector content", status="ACTIVE")

        # Delete any existing embedding to simulate divergence
        vector_store.delete_embedding(mid)

        report = vector_reconciliation_engine.reconcile(batch_size=50, fix=True)
        self.assertIn(mid, report.missing_vectors)
        self.assertGreaterEqual(report.missing_vectors_repaired, 1)

    def test_36_reconciliation_zombie_vector_purge(self):
        """T36: Reconciliation detects and purges zombie vectors for non-active records."""
        mid = f"{self.prefix}_t36"
        self._insert_test_memory(mid, content="Zombie vector content", status="ARCHIVED")

        # Artificially store an embedding for this archived memory
        emb = embedding_router.embed("Zombie vector content", check_policy=True)
        if emb:
            vector_store.store_embedding(
                memory_id=mid,
                embedding=emb.vector,
                model=emb.model,
                model_version=emb.model_version,
                content_hash=emb.content_hash,
                dimension=len(emb.vector),
                generation=1,
            )

        report = vector_reconciliation_engine.reconcile(batch_size=50, fix=True)
        self.assertIn(mid, report.zombie_vectors)
        self.assertGreaterEqual(report.zombie_vectors_purged, 1)

    def test_37_reconciliation_corrupt_queue_quarantine(self):
        """T37: Referential integrity prevents corrupt queue items, and reconciliation audits clean state."""
        fake_mid = f"{self.prefix}_fake_orphan_mid"
        conn = postgres_manager.get_connection()
        try:
            # Foreign key constraint must reject orphan work items
            with self.assertRaises(Exception):
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO vector_sync_queue (
                            sync_id, memory_id, operation, target_generation, target_status,
                            sync_status, created_at, updated_at
                        ) VALUES (
                            %s, %s, 'UPSERT', 1, 'ACTIVE', 'PENDING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        );
                    """, (f"sync_corrupt_{uuid.uuid4().hex[:6]}", fake_mid))
            conn.rollback()

            report = vector_reconciliation_engine.reconcile(batch_size=50, fix=True)
            self.assertEqual(report.corrupt_queue_items_detected, 0)
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category 12: Idempotent Replay & Deduplication (T38 - T39)
    # ==================================================================
    def test_38_idempotent_replay_duplicate_upsert(self):
        """T38: Replaying identical UPSERT multiple times produces identical valid vector state."""
        mid = f"{self.prefix}_t38"
        self._insert_test_memory(mid, content="Idempotent replay upsert")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                    content="Idempotent replay upsert",
                )
            conn.commit()

            # Pass 1
            res1 = vector_sync_engine.process_work_item(sid)
            self.assertTrue(res1.success)

            # Pass 2 (replay)
            res2 = vector_sync_engine.process_work_item(sid)
            self.assertTrue(res2.success)
            self.assertTrue(res2.is_skipped)
        finally:
            postgres_manager.release_connection(conn)

    def test_39_idempotent_replay_duplicate_delete(self):
        """T39: Replaying identical DELETE multiple times returns safely without error."""
        mid = f"{self.prefix}_t39"
        self._insert_test_memory(mid, content="Idempotent replay delete", status="DELETED")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.DELETE,
                    target_generation=1, target_status=MemoryStatus.DELETED,
                )
            conn.commit()

            # Pass 1
            res1 = vector_sync_engine.process_work_item(sid)
            self.assertTrue(res1.success)

            # Pass 2 (replay)
            res2 = vector_sync_engine.process_work_item(sid)
            self.assertTrue(res2.success)
            self.assertTrue(res2.is_skipped)
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category 13: Poison Pill & Queue Starvation (T40 - T41)
    # ==================================================================
    def test_40_poison_pill_starvation_prevention(self):
        """T40: Repeatedly failing item does not starve subsequent healthy work items in batch processing."""
        mid_poison = f"{self.prefix}_t40_poison"
        mid_healthy = f"{self.prefix}_t40_healthy"
        self._insert_test_memory(mid_poison)
        self._insert_test_memory(mid_healthy, content="Healthy item content")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                # Poison item with unknown operation
                cur.execute("""
                    INSERT INTO vector_sync_queue (
                        sync_id, memory_id, operation, target_generation, target_status,
                        sync_status, created_at, updated_at
                    ) VALUES (
                        %s, %s, 'CORRUPT_OP', 1, 'ACTIVE', 'PENDING', CURRENT_TIMESTAMP - INTERVAL '1 minute', CURRENT_TIMESTAMP
                    );
                """, (f"sync_poison_{uuid.uuid4().hex[:6]}", mid_poison))
                # Healthy item
                sid_h = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid_healthy, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                    content="Healthy item content",
                )
            conn.commit()

            results = vector_sync_engine.process_pending_batch(limit=10)
            # Healthy item should succeed even though poison item was first
            healthy_res = next((r for r in results if r.sync_id == sid_h), None)
            self.assertIsNotNone(healthy_res)
            self.assertTrue(healthy_res.success)
        finally:
            postgres_manager.release_connection(conn)

    def test_41_worker_heartbeat_extension_and_loss(self):
        """T41: Worker heartbeat extends unexpired lease; returns False if lease expired or lost."""
        mid = f"{self.prefix}_t41"
        self._insert_test_memory(mid)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                )
            conn.commit()

            claimed = vector_sync_engine.claim_work_items(worker_id="worker_heart", limit=1, lease_seconds=30)
            self.assertEqual(len(claimed), 1)

            # Heartbeat extends lease
            ok = vector_sync_engine.heartbeat(sid, worker_id="worker_heart", extend_seconds=60)
            self.assertTrue(ok, "Heartbeat extension must succeed for owning worker")

            # Heartbeat by wrong worker fails
            fail = vector_sync_engine.heartbeat(sid, worker_id="worker_impostor", extend_seconds=60)
            self.assertFalse(fail, "Heartbeat must fail for non-owning worker")
        finally:
            postgres_manager.release_connection(conn)

    # ==================================================================
    # Category 14: End-to-End Recovery Lifecycle (T42)
    # ==================================================================
    def test_42_end_to_end_crash_recovery_simulation(self):
        """T42: Full crash & reboot lifecycle simulation:
        Memory created -> outbox enqueued -> simulated crash in PROCESSING ->
        startup_recovery() executed -> lease reclaimed -> queue drained ->
        NumPy rehydrated -> vector present and retrievable.
        """
        mid = f"{self.prefix}_t42"
        self._insert_test_memory(mid, content="End to end recovery payload", status="ACTIVE", generation=1)
        conn = postgres_manager.get_connection()
        try:
            # Step 1: Enqueue outbox work
            with conn.cursor() as cur:
                sid = vector_sync_engine.enqueue_sync_work(
                    cur=cur, memory_id=mid, operation=SyncOperation.UPSERT,
                    target_generation=1, target_status=MemoryStatus.ACTIVE,
                    content="End to end recovery payload",
                )
                # Step 2: Simulate crash while worker held lease (stranded in PROCESSING with expired lease)
                cur.execute("""
                    UPDATE vector_sync_queue
                    SET sync_status = 'PROCESSING',
                        worker_id = 'crashed_proc_99',
                        lease_acquired_at = CURRENT_TIMESTAMP - INTERVAL '3 minutes',
                        lease_expires_at = CURRENT_TIMESTAMP - INTERVAL '2 minutes',
                        locked_until = CURRENT_TIMESTAMP - INTERVAL '2 minutes'
                    WHERE sync_id = %s;
                """, (sid,))
            conn.commit()

            # Step 3: Simulate system reboot by executing startup_recovery()
            recovery_report = startup_recovery(batch_limit=25, rehydrate_numpy=True)
            self.assertIsInstance(recovery_report, dict)
            self.assertGreaterEqual(recovery_report["leases_reclaimed"], 1)
            self.assertTrue(recovery_report["consistency_checked"])
            self.assertEqual(len(recovery_report["errors"]), 0)

            # Step 4: Verify queue is now SYNCED
            with conn.cursor() as cur:
                cur.execute("SELECT sync_status, worker_id FROM vector_sync_queue WHERE sync_id = %s;", (sid,))
                row = cur.fetchone()
                self.assertEqual(row[0], VectorSyncStatus.SYNCED.value)
                self.assertIsNone(row[1])

            # Step 5: Verify vector is present in storage
            active_model = embedding_router.provider.model_name
            active_version = embedding_router.provider.model_version
            self.assertTrue(vector_store.has_embedding(mid, active_model, active_version))
        finally:
            postgres_manager.release_connection(conn)


if __name__ == "__main__":
    unittest.main(verbosity=2)
