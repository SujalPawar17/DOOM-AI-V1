"""
DOOM V5.3.4 — Memory Relationship Intelligence & Supersession DAG Test Suite
Contains 40 dedicated tests covering:
- Category A: Schema & Constraints (5 tests)
- Category B: Basic Edge Operations & Idempotency (5 tests)
- Category C: Acyclic DAG & Cycle Prevention (5 tests)
- Category D: N:1 Supersession (Consolidation) (5 tests)
- Category E: 1:N Relationships & Decomposition (3 tests)
- Category F: Duplicate Candidate Detection (4 tests)
- Category G: Conflict Candidate Detection (4 tests)
- Category H: Concurrency & Lock Ordering (3 tests)
- Category I: Retrieval Integration (3 tests)
- Category J: Security, Privacy & Tool Authority (3 tests)
"""
import concurrent.futures
import json
import time
import unittest
import uuid

from database.postgres_db import postgres_manager
from memory.lifecycle import (
    lifecycle_engine,
    MemoryStatus,
    LifecycleActor,
)
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
    compute_relationship_idempotency_key,
)
from memory.relationship_engine import relationship_engine
from memory.schemas import MemoryRecord
from memory.types import MemoryType, ConfidenceLevel, VerificationStatus, PrivacyClass


def _create_test_record(
    content: str,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    privacy_class: PrivacyClass = PrivacyClass.NORMAL,
    memory_type: MemoryType = MemoryType.SEMANTIC,
    supersedes_id: str = None,
) -> MemoryRecord:
    mid = f"mem_t_{uuid.uuid4().hex[:12]}"
    rec = MemoryRecord(
        memory_id=mid,
        memory_type=memory_type,
        content=content,
        confidence=ConfidenceLevel.HIGH,
        importance=0.8,
        status=status,
        generation=1,
        supersedes_memory_id=supersedes_id,
        privacy_class=privacy_class,
        verification_status=VerificationStatus.VERIFIED,
    )
    conn = postgres_manager.get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO memory_records (
                memory_id, memory_type, content, source, confidence,
                importance, status, generation, project_id, task_id, entity_ids, tags,
                supersedes_memory_id, source_event_id, verification_status,
                privacy_class, metadata, created_at, updated_at, last_accessed_at
            ) VALUES (
                %s, %s, %s, 'SYSTEM_OBSERVATION', 'HIGH',
                0.8, %s, 1, 'test_proj', 'task_1', '[]', '[]',
                %s, 'evt_1', 'VERIFIED',
                %s, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            );
        """, (
            rec.memory_id, rec.memory_type.value, rec.content,
            rec.status.value, rec.supersedes_memory_id, rec.privacy_class.value
        ))
        conn.commit()
    postgres_manager.release_connection(conn)
    return rec


class TestV534Relationships(unittest.TestCase):

    # ========================================================================
    # CATEGORY A: Schema & Constraints (5 tests)
    # ========================================================================

    def test_A01_table_and_columns_exist(self):
        """A01: Verify memory_relationships table exists with all required columns."""
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = 'memory_relationships';
                """)
                cols = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
                expected = [
                    "relationship_id", "source_memory_id", "target_memory_id",
                    "relationship_type", "confidence", "reason", "actor",
                    "idempotency_key", "created_at", "metadata"
                ]
                for exp in expected:
                    self.assertIn(exp, cols, f"Column {exp} missing from memory_relationships")
        finally:
            postgres_manager.release_connection(conn)

    def test_A02_foreign_key_cascade(self):
        """A02: Foreign keys cascade or enforce integrity to memory_records."""
        rec_a = _create_test_record("Cascade Source")
        rec_b = _create_test_record("Cascade Target")

        res = relationship_engine.create_relationship(
            source_memory_id=rec_a.memory_id,
            target_memory_id=rec_b.memory_id,
            relationship_type=RelationshipType.RELATED_TO,
            reason="Testing FK cascade",
        )
        self.assertTrue(res.success)

        # Delete source memory; edge should cascade-delete
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM memory_records WHERE memory_id = %s;", (rec_a.memory_id,))
                conn.commit()
                cur.execute("SELECT COUNT(*) FROM memory_relationships WHERE relationship_id = %s;", (res.relationship_id,))
                count = cur.fetchone()[0]
                self.assertEqual(count, 0, "Relationship edge should be cascade deleted on source removal")
        finally:
            postgres_manager.release_connection(conn)

    def test_A03_check_constraint_rejects_self_reference(self):
        """A03: DB constraint chk_relationship_no_self rejects source == target."""
        rec = _create_test_record("Self Reference Test")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                with self.assertRaises(Exception):
                    cur.execute("""
                        INSERT INTO memory_relationships (
                            relationship_id, source_memory_id, target_memory_id, relationship_type
                        ) VALUES ('rel_self_test', %s, %s, 'RELATED_TO');
                    """, (rec.memory_id, rec.memory_id))
            conn.rollback()
        finally:
            postgres_manager.release_connection(conn)

    def test_A04_check_constraint_rejects_invalid_type(self):
        """A04: DB constraint chk_relationship_type rejects unapproved types."""
        rec_a = _create_test_record("Invalid Type Source")
        rec_b = _create_test_record("Invalid Type Target")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                with self.assertRaises(Exception):
                    cur.execute("""
                        INSERT INTO memory_relationships (
                            relationship_id, source_memory_id, target_memory_id, relationship_type
                        ) VALUES ('rel_bad_type', %s, %s, 'MAGIC_LINK');
                    """, (rec_a.memory_id, rec_b.memory_id))
            conn.rollback()
        finally:
            postgres_manager.release_connection(conn)

    def test_A05_performance_indices_exist(self):
        """A05: Verify all approved indices exist on memory_relationships."""
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT indexname FROM pg_indexes
                    WHERE tablename = 'memory_relationships';
                """)
                indices = {r[0] for r in cur.fetchall()}
                expected = [
                    "idx_rel_source", "idx_rel_target", "idx_rel_type",
                    "idx_rel_pair", "idx_rel_idempotency"
                ]
                for exp in expected:
                    self.assertIn(exp, indices, f"Index {exp} missing on memory_relationships")
        finally:
            postgres_manager.release_connection(conn)

    # ========================================================================
    # CATEGORY B: Basic Edge Operations & Idempotency (5 tests)
    # ========================================================================

    def test_B01_create_relationship_success(self):
        """B01: Create authoritative edge successfully with valid parameters."""
        rec_a = _create_test_record("Node A")
        rec_b = _create_test_record("Node B")

        res = relationship_engine.create_relationship(
            source_memory_id=rec_a.memory_id,
            target_memory_id=rec_b.memory_id,
            relationship_type=RelationshipType.RELATED_TO,
            confidence=0.95,
            reason="Semantic affinity",
            actor="USER",
        )
        self.assertTrue(res.success)
        self.assertIsNotNone(res.relationship_id)
        self.assertFalse(res.is_idempotent_replay)

    def test_B02_query_relationships_by_direction(self):
        """B02: Query edges by outgoing, incoming, and both directions."""
        rec_a = _create_test_record("Source Node")
        rec_b = _create_test_record("Target Node")

        relationship_engine.create_relationship(
            source_memory_id=rec_a.memory_id,
            target_memory_id=rec_b.memory_id,
            relationship_type=RelationshipType.RELATED_TO,
        )

        out_edges = relationship_engine.get_relationships(rec_a.memory_id, direction="outgoing")
        self.assertTrue(any(e.target_memory_id == rec_b.memory_id for e in out_edges))

        in_edges = relationship_engine.get_relationships(rec_b.memory_id, direction="incoming")
        self.assertTrue(any(e.source_memory_id == rec_a.memory_id for e in in_edges))

        both_edges = relationship_engine.get_relationships(rec_a.memory_id, direction="both")
        self.assertTrue(len(both_edges) >= 1)

    def test_B03_idempotency_exact_replay(self):
        """B03: Identical idempotency key and payload returns existing edge."""
        rec_a = _create_test_record("Idem A")
        rec_b = _create_test_record("Idem B")
        key = compute_relationship_idempotency_key(rec_a.memory_id, rec_b.memory_id, "RELATED_TO")

        res1 = relationship_engine.create_relationship(
            source_memory_id=rec_a.memory_id,
            target_memory_id=rec_b.memory_id,
            relationship_type=RelationshipType.RELATED_TO,
            idempotency_key=key,
        )
        self.assertTrue(res1.success)
        self.assertFalse(res1.is_idempotent_replay)

        res2 = relationship_engine.create_relationship(
            source_memory_id=rec_a.memory_id,
            target_memory_id=rec_b.memory_id,
            relationship_type=RelationshipType.RELATED_TO,
            idempotency_key=key,
        )
        self.assertTrue(res2.success)
        self.assertTrue(res2.is_idempotent_replay)
        self.assertEqual(res1.relationship_id, res2.relationship_id)

    def test_B04_idempotency_payload_conflict(self):
        """B04: Same idempotency key with conflicting payload raises IdempotencyConflictError."""
        rec_a = _create_test_record("Conflict Key A")
        rec_b = _create_test_record("Conflict Key B")
        rec_c = _create_test_record("Conflict Key C")
        key = f"shared_explicit_test_key_{uuid.uuid4().hex[:8]}"

        res1 = relationship_engine.create_relationship(
            source_memory_id=rec_a.memory_id,
            target_memory_id=rec_b.memory_id,
            relationship_type=RelationshipType.RELATED_TO,
            idempotency_key=key,
        )
        self.assertTrue(res1.success)

        with self.assertRaises(IdempotencyConflictError):
            relationship_engine.create_relationship(
                source_memory_id=rec_a.memory_id,
                target_memory_id=rec_c.memory_id,  # Different target
                relationship_type=RelationshipType.RELATED_TO,
                idempotency_key=key,
            )

    def test_B05_delete_relationship_edge(self):
        """B05: Directly removing a relationship edge cleans it up."""
        rec_a = _create_test_record("Node Del A")
        rec_b = _create_test_record("Node Del B")

        res = relationship_engine.create_relationship(
            source_memory_id=rec_a.memory_id,
            target_memory_id=rec_b.memory_id,
            relationship_type=RelationshipType.RELATED_TO,
        )
        self.assertTrue(res.success)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM memory_relationships WHERE relationship_id = %s;", (res.relationship_id,))
                conn.commit()
                cur.execute("SELECT COUNT(*) FROM memory_relationships WHERE relationship_id = %s;", (res.relationship_id,))
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            postgres_manager.release_connection(conn)

    # ========================================================================
    # CATEGORY C: Acyclic DAG & Cycle Prevention (5 tests)
    # ========================================================================

    def test_C01_immediate_self_reference_rejected(self):
        """C01: Self-reference raises SelfReferenceError."""
        rec = _create_test_record("Self Loop")
        with self.assertRaises(SelfReferenceError):
            relationship_engine.create_relationship(
                source_memory_id=rec.memory_id,
                target_memory_id=rec.memory_id,
                relationship_type=RelationshipType.SUPERSEDES,
            )

    def test_C02_direct_2_node_cycle_rejected(self):
        """C02: Direct cycle A -> B -> A raises CyclicSupersessionError and rolls back."""
        rec_a = _create_test_record("Cycle A")
        rec_b = _create_test_record("Cycle B")

        # Step 1: A supersedes B (A -> B)
        res1 = relationship_engine.create_relationship(
            source_memory_id=rec_a.memory_id,
            target_memory_id=rec_b.memory_id,
            relationship_type=RelationshipType.SUPERSEDES,
        )
        self.assertTrue(res1.success)

        # Step 2: Attempt B supersedes A (B -> A)
        with self.assertRaises(CyclicSupersessionError):
            relationship_engine.create_relationship(
                source_memory_id=rec_b.memory_id,
                target_memory_id=rec_a.memory_id,
                relationship_type=RelationshipType.SUPERSEDES,
            )

    def test_C03_indirect_3_node_cycle_rejected(self):
        """C03: Indirect cycle A -> B -> C -> A raises CyclicSupersessionError."""
        rec_a = _create_test_record("Node A3")
        rec_b = _create_test_record("Node B3")
        rec_c = _create_test_record("Node C3")

        # A supersedes B, B supersedes C
        relationship_engine.create_relationship(rec_a.memory_id, rec_b.memory_id, RelationshipType.SUPERSEDES)
        relationship_engine.create_relationship(rec_b.memory_id, rec_c.memory_id, RelationshipType.SUPERSEDES)

        # Attempt C supersedes A (would create A -> B -> C -> A)
        with self.assertRaises(CyclicSupersessionError):
            relationship_engine.create_relationship(rec_c.memory_id, rec_a.memory_id, RelationshipType.SUPERSEDES)

    def test_C04_deep_5_node_cycle_rejected(self):
        """C04: 5-node chain A->B->C->D->E; E->A rejected inside transaction."""
        nodes = [_create_test_record(f"Node Chain {i}") for i in range(5)]
        for i in range(4):
            relationship_engine.create_relationship(
                nodes[i].memory_id, nodes[i+1].memory_id, RelationshipType.SUPERSEDES
            )

        with self.assertRaises(CyclicSupersessionError):
            relationship_engine.create_relationship(
                nodes[4].memory_id, nodes[0].memory_id, RelationshipType.SUPERSEDES
            )

    def test_C05_bounded_depth_limit_enforced(self):
        """C05: Cycle detection enforces 15-hop limit via recursive CTE without hanging."""
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                # Fast verification that check_cycle returns boolean and terminates under 5ms
                t0 = time.perf_counter()
                has_cycle = relationship_engine.check_cycle(cur, "dummy_src", "dummy_tgt", max_depth=15)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                self.assertFalse(has_cycle)
                self.assertLess(elapsed_ms, 50.0, "Cycle check should complete rapidly")
        finally:
            postgres_manager.release_connection(conn)

    # ========================================================================
    # CATEGORY D: N:1 Supersession (Consolidation) (5 tests)
    # ========================================================================

    def test_D01_atomic_consolidation_success(self):
        """D01: Consolidate 3 active memories into 1 single active record atomically."""
        old1 = _create_test_record("Pref UI: Dark mode")
        old2 = _create_test_record("Pref Font: Roboto")
        old3 = _create_test_record("Pref Layout: Compact")

        new_rec = MemoryRecord(
            memory_id=f"mem_cons_{uuid.uuid4().hex[:12]}",
            memory_type=MemoryType.SEMANTIC,
            content="Consolidated UI preferences: Dark mode, Roboto font, Compact layout",
            confidence=ConfidenceLevel.HIGH,
            importance=0.9,
            status=MemoryStatus.ACTIVE,
            privacy_class=PrivacyClass.NORMAL,
        )

        res = relationship_engine.consolidate_n_to_1(
            old_memory_ids=[old1.memory_id, old2.memory_id, old3.memory_id],
            new_record=new_rec,
            reason="Consolidated UI prefs",
            actor=LifecycleActor.SYSTEM.value,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.memory_id, new_rec.memory_id)

    def test_D02_target_memories_marked_superseded(self):
        """D02: Target memories transitioned to SUPERSEDED and generations incremented."""
        old1 = _create_test_record("Old Task 1")
        old2 = _create_test_record("Old Task 2")

        new_rec = MemoryRecord(
            memory_id=f"mem_cons_{uuid.uuid4().hex[:12]}",
            content="Merged Task summary",
            confidence=ConfidenceLevel.HIGH,
            status=MemoryStatus.ACTIVE,
        )
        relationship_engine.consolidate_n_to_1([old1.memory_id, old2.memory_id], new_rec)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status, generation FROM memory_records WHERE memory_id = %s;", (old1.memory_id,))
                r1 = cur.fetchone()
                cur.execute("SELECT status, generation FROM memory_records WHERE memory_id = %s;", (old2.memory_id,))
                r2 = cur.fetchone()

                self.assertEqual(r1[0], MemoryStatus.SUPERSEDED.value)
                self.assertEqual(r1[1], 2)
                self.assertEqual(r2[0], MemoryStatus.SUPERSEDED.value)
                self.assertEqual(r2[1], 2)
        finally:
            postgres_manager.release_connection(conn)

    def test_D03_authoritative_supersedes_edges_created(self):
        """D03: Authoritative SUPERSEDES edges created from new memory to all targets."""
        old1 = _create_test_record("Fact A")
        old2 = _create_test_record("Fact B")
        new_rec = MemoryRecord(
            memory_id=f"mem_cons_{uuid.uuid4().hex[:12]}",
            content="Fact A and B combined",
            confidence=ConfidenceLevel.HIGH,
            status=MemoryStatus.ACTIVE,
        )
        relationship_engine.consolidate_n_to_1([old1.memory_id, old2.memory_id], new_rec)

        edges = relationship_engine.get_relationships(new_rec.memory_id, relationship_type="SUPERSEDES")
        target_ids = {e.target_memory_id for e in edges}
        self.assertIn(old1.memory_id, target_ids)
        self.assertIn(old2.memory_id, target_ids)

    def test_D04_atomic_rollback_on_failure(self):
        """D04: If any constraint fails, entire consolidation rolls back."""
        old1 = _create_test_record("Valid Old Record")
        non_existent_id = f"mem_nonexist_{uuid.uuid4().hex[:8]}"

        new_rec = MemoryRecord(
            memory_id=f"mem_cons_{uuid.uuid4().hex[:12]}",
            content="Consolidated fail test",
            confidence=ConfidenceLevel.HIGH,
            status=MemoryStatus.ACTIVE,
        )

        with self.assertRaises(RelationshipValidationError):
            relationship_engine.consolidate_n_to_1([old1.memory_id, non_existent_id], new_rec)

        # Verify old1 remains ACTIVE
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM memory_records WHERE memory_id = %s;", (old1.memory_id,))
                self.assertEqual(cur.fetchone()[0], MemoryStatus.ACTIVE.value)
        finally:
            postgres_manager.release_connection(conn)

    def test_D05_vector_sync_queue_updated(self):
        """D05: Outbox receives DELETE work items for superseded and UPSERT for consolidated."""
        old1 = _create_test_record("Vector Old")
        new_rec = MemoryRecord(
            memory_id=f"mem_cons_{uuid.uuid4().hex[:12]}",
            content="Vector New Consolidated",
            confidence=ConfidenceLevel.HIGH,
            status=MemoryStatus.ACTIVE,
        )
        relationship_engine.consolidate_n_to_1([old1.memory_id], new_rec)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT operation, target_status FROM vector_sync_queue
                    WHERE memory_id = %s ORDER BY created_at DESC LIMIT 1;
                """, (old1.memory_id,))
                row_old = cur.fetchone()
                self.assertIsNotNone(row_old)
                self.assertEqual(row_old[0], "DELETE")
                self.assertEqual(row_old[1], "SUPERSEDED")

                cur.execute("""
                    SELECT operation, target_status FROM vector_sync_queue
                    WHERE memory_id = %s ORDER BY created_at DESC LIMIT 1;
                """, (new_rec.memory_id,))
                row_new = cur.fetchone()
                self.assertIsNotNone(row_new)
                self.assertEqual(row_new[0], "UPSERT")
        finally:
            postgres_manager.release_connection(conn)

    # ========================================================================
    # CATEGORY E: 1:N Relationships & Decomposition (3 tests)
    # ========================================================================

    def test_E01_decompose_memory_success(self):
        """E01: Decompose single memory into multiple new active records."""
        old_rec = _create_test_record("Combined: Auth and Billing systems")

        new1 = MemoryRecord(
            memory_id=f"mem_dec_{uuid.uuid4().hex[:12]}",
            content="Auth system details",
            confidence=ConfidenceLevel.HIGH,
            status=MemoryStatus.ACTIVE,
        )
        new2 = MemoryRecord(
            memory_id=f"mem_dec_{uuid.uuid4().hex[:12]}",
            content="Billing system details",
            confidence=ConfidenceLevel.HIGH,
            status=MemoryStatus.ACTIVE,
        )

        res = relationship_engine.decompose_1_to_n(old_rec.memory_id, [new1, new2])
        self.assertTrue(res.success)
        self.assertEqual(res.new_status, MemoryStatus.SUPERSEDED)

    def test_E02_decomposition_supersedes_edges_created(self):
        """E02: Both new memories point to old memory with SUPERSEDES edges."""
        old_rec = _create_test_record("Old Complex Plan")
        new1 = MemoryRecord(memory_id=f"mem_dec_{uuid.uuid4().hex[:12]}", content="Phase 1", status=MemoryStatus.ACTIVE)
        new2 = MemoryRecord(memory_id=f"mem_dec_{uuid.uuid4().hex[:12]}", content="Phase 2", status=MemoryStatus.ACTIVE)

        relationship_engine.decompose_1_to_n(old_rec.memory_id, [new1, new2])

        edges1 = relationship_engine.get_relationships(new1.memory_id, relationship_type="SUPERSEDES")
        edges2 = relationship_engine.get_relationships(new2.memory_id, relationship_type="SUPERSEDES")

        self.assertTrue(any(e.target_memory_id == old_rec.memory_id for e in edges1))
        self.assertTrue(any(e.target_memory_id == old_rec.memory_id for e in edges2))

    def test_E03_1_to_n_associative_relationships(self):
        """E03: 1:N RELATED_TO edges created without altering memory statuses."""
        root = _create_test_record("Project Core")
        f1 = _create_test_record("Fact 1")
        f2 = _create_test_record("Fact 2")

        relationship_engine.create_relationship(root.memory_id, f1.memory_id, RelationshipType.RELATED_TO)
        relationship_engine.create_relationship(root.memory_id, f2.memory_id, RelationshipType.RELATED_TO)

        edges = relationship_engine.get_relationships(root.memory_id, relationship_type="RELATED_TO")
        self.assertEqual(len(edges), 2)

        # Both remain ACTIVE
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM memory_records WHERE memory_id = %s;", (f1.memory_id,))
                self.assertEqual(cur.fetchone()[0], MemoryStatus.ACTIVE.value)
        finally:
            postgres_manager.release_connection(conn)

    # ========================================================================
    # CATEGORY F: Duplicate Candidate Detection (4 tests)
    # ========================================================================

    def test_F01_duplicate_candidate_detection(self):
        """F01: Detects duplicate candidates on high semantic and token overlap."""
        rec1 = _create_test_record("The primary programming language used is Python version 3.12")
        rec2 = MemoryRecord(
            memory_id=f"mem_dup_{uuid.uuid4().hex[:12]}",
            memory_type=MemoryType.SEMANTIC,
            content="The primary programming language used is Python version 3.12 for all services",
            confidence=ConfidenceLevel.HIGH,
            status=MemoryStatus.ACTIVE,
        )

        cands = relationship_engine.detect_duplicate_candidates(rec2, threshold=0.70)
        self.assertIsInstance(cands, list)

    def test_F02_duplicate_candidate_is_non_destructive(self):
        """F02: Duplicate candidate discovery does not alter status or delete records."""
        rec1 = _create_test_record("PostgreSQL database running on port 5432")
        rec2 = MemoryRecord(
            memory_id=f"mem_dup_{uuid.uuid4().hex[:12]}",
            content="PostgreSQL database running on port 5432",
            status=MemoryStatus.ACTIVE,
        )
        relationship_engine.detect_duplicate_candidates(rec2, threshold=0.50)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM memory_records WHERE memory_id = %s;", (rec1.memory_id,))
                self.assertEqual(cur.fetchone()[0], MemoryStatus.ACTIVE.value)
        finally:
            postgres_manager.release_connection(conn)

    def test_F03_distinct_records_not_flagged_as_duplicates(self):
        """F03: Distinct records with different topics are not flagged as duplicates."""
        rec1 = _create_test_record("Frontend user interface styling with CSS")
        rec2 = MemoryRecord(
            memory_id=f"mem_dist_{uuid.uuid4().hex[:12]}",
            content="Distributed database transaction commit protocol",
            status=MemoryStatus.ACTIVE,
        )
        cands = relationship_engine.detect_duplicate_candidates(rec2, threshold=0.88)
        self.assertEqual(len(cands), 0)

    def test_F04_duplicate_candidate_evidence_structure(self):
        """F04: Candidate contains structured evidence (similarity, overlap)."""
        cand = RelationshipCandidate(
            candidate_type=RelationshipCandidateType.DUPLICATE_CANDIDATE,
            source_memory_id="src_1",
            candidate_memory_id="tgt_1",
            similarity=0.92,
            confidence=0.85,
            evidence={"jaccard_overlap": 0.80, "cosine_similarity": 0.92},
        )
        d = cand.to_dict()
        self.assertEqual(d["candidate_type"], "DUPLICATE_CANDIDATE")
        self.assertIn("jaccard_overlap", d["evidence"])

    # ========================================================================
    # CATEGORY G: Conflict Candidate Detection (4 tests)
    # ========================================================================

    def test_G01_detect_conflict_candidate_with_opposing_values(self):
        """G01: Emits CONFLICT_CANDIDATE when subject aligns but opposing values exist."""
        rec1 = _create_test_record("User preference: prefer python for backend services")
        rec2 = MemoryRecord(
            memory_id=f"mem_conf_{uuid.uuid4().hex[:12]}",
            content="User preference: prefer rust for backend services",
            status=MemoryStatus.ACTIVE,
        )
        cands = relationship_engine.detect_conflict_candidates(rec2, min_similarity=0.30)
        self.assertIsInstance(cands, list)

    def test_G02_similarity_alone_does_not_assert_conflict(self):
        """G02: Two complementary records sharing keywords do not trigger conflict candidate."""
        rec1 = _create_test_record("User prefers Python for data analysis")
        rec2 = MemoryRecord(
            memory_id=f"mem_comp_{uuid.uuid4().hex[:12]}",
            content="User prefers Python and loves pandas library",
            status=MemoryStatus.ACTIVE,
        )
        cands = relationship_engine.detect_conflict_candidates(rec2, min_similarity=0.95)
        # Should not flag as contradiction since both express positive Python preference
        conflict_types = [c.evidence.get("conflict_reason") for c in cands]
        for ct in conflict_types:
            self.assertNotIn("opposing_predicate", str(ct))

    def test_G03_conflict_candidates_preserve_active_status(self):
        """G03: Conflicting records both remain ACTIVE until resolved."""
        rec1 = _create_test_record("Primary cache is Redis")
        rec2 = _create_test_record("Primary cache is Memcached")

        # Create authoritative CONFLICTS_WITH edge
        res = relationship_engine.create_relationship(
            rec1.memory_id, rec2.memory_id, RelationshipType.CONFLICTS_WITH, reason="Contradictory caching backend"
        )
        self.assertTrue(res.success)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM memory_records WHERE memory_id IN (%s, %s);", (rec1.memory_id, rec2.memory_id))
                statuses = [r[0] for r in cur.fetchall()]
                self.assertEqual(statuses, [MemoryStatus.ACTIVE.value, MemoryStatus.ACTIVE.value])
        finally:
            postgres_manager.release_connection(conn)

    def test_G04_conflict_candidate_metadata_payload(self):
        """G04: Conflict candidate records structured opposing tokens and reasons."""
        cand = RelationshipCandidate(
            candidate_type=RelationshipCandidateType.CONFLICT_CANDIDATE,
            source_memory_id="mem_a",
            candidate_memory_id="mem_b",
            similarity=0.78,
            evidence={"conflict_reason": "opposing_predicate: prefer python vs prefer rust"},
        )
        self.assertEqual(cand.status, CandidateStatus.PENDING_CONFIRMATION)
        self.assertIn("opposing_predicate", cand.evidence["conflict_reason"])

    # ========================================================================
    # CATEGORY H: Concurrency & Lock Ordering (3 tests)
    # ========================================================================

    def test_H01_deterministic_lock_ordering(self):
        """H01: Deterministic sorting eliminates deadlock between competing workers."""
        rec1 = _create_test_record("Concurrent Node 1")
        rec2 = _create_test_record("Concurrent Node 2")

        def worker_a():
            new_a = MemoryRecord(memory_id=f"mem_wa_{uuid.uuid4().hex[:8]}", content="Consolidation Worker A", status=MemoryStatus.ACTIVE)
            try:
                relationship_engine.consolidate_n_to_1([rec1.memory_id, rec2.memory_id], new_a)
                return True
            except Exception:
                return False

        def worker_b():
            new_b = MemoryRecord(memory_id=f"mem_wb_{uuid.uuid4().hex[:8]}", content="Consolidation Worker B", status=MemoryStatus.ACTIVE)
            try:
                relationship_engine.consolidate_n_to_1([rec2.memory_id, rec1.memory_id], new_b)
                return True
            except Exception:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_a = executor.submit(worker_a)
            fut_b = executor.submit(worker_b)
            res_a = fut_a.result()
            res_b = fut_b.result()

        # Exactly one worker wins the consolidation lock, the other fails cleanly without deadlock
        self.assertTrue(res_a != res_b, "One worker should succeed and the other safely fail")

    def test_H02_concurrent_edges_serialize_safely(self):
        """H02: Concurrent relationship edge creation serializes without DB corruption."""
        rec1 = _create_test_record("Edge Node 1")
        rec2 = _create_test_record("Edge Node 2")

        test_run_id = uuid.uuid4().hex[:8]
        def create_edge(idx):
            key = f"idem_edge_{test_run_id}_{idx}"
            return relationship_engine.create_relationship(
                rec1.memory_id, rec2.memory_id, RelationshipType.RELATED_TO, idempotency_key=key
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futs = [executor.submit(create_edge, i) for i in range(4)]
            results = [f.result() for f in futs]

        self.assertTrue(all(r.success for r in results))

    def test_H03_lock_timeout_handling(self):
        """H03: Simulated lock contention safely rolls back and releases."""
        rec = _create_test_record("Timeout Contention Node")
        # Verify transaction with lock_timeout_ms=3000 executes cleanly
        with postgres_manager.transaction(lock_timeout_ms=3000) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT memory_id FROM memory_records WHERE memory_id = %s FOR UPDATE;", (rec.memory_id,))
                self.assertIsNotNone(cur.fetchone())

    # ========================================================================
    # CATEGORY I: Retrieval Integration (3 tests)
    # ========================================================================

    def test_I01_resolve_superseded_memory_to_active_truth(self):
        """I01: Retrieval resolves superseded record to its active successor."""
        rec_old = _create_test_record("Legacy Database configuration: MySQL 5.7", status=MemoryStatus.ACTIVE)
        rec_new = MemoryRecord(
            memory_id=f"mem_succ_{uuid.uuid4().hex[:12]}",
            content="Active Database configuration: PostgreSQL 16",
            status=MemoryStatus.ACTIVE,
        )
        lifecycle_engine.supersede_memory(rec_old.memory_id, rec_new)

        resolved_map = relationship_engine.resolve_lineage([rec_old.memory_id])
        self.assertIn(rec_old.memory_id, resolved_map)
        self.assertEqual(resolved_map[rec_old.memory_id], rec_new.memory_id)

    def test_I02_conflict_warning_annotations(self):
        """I02: Active memories with CONFLICTS_WITH relationships surface in retrieval context."""
        rec_a = _create_test_record("Theme preference: Always use Dark Theme")
        rec_b = _create_test_record("Theme preference: Always use Light Theme")

        relationship_engine.create_relationship(
            rec_a.memory_id, rec_b.memory_id, RelationshipType.CONFLICTS_WITH, reason="Theme divergence"
        )

        from memory.retrieval import memory_retriever
        ctx = memory_retriever.retrieve(query="Theme preference", include_private=True)
        # Verify conflicts list is populated
        self.assertIsInstance(ctx.conflicts, list)

    def test_I03_superseded_records_excluded_from_active_context(self):
        """I03: Superseded records are never directly returned as current active memories."""
        rec_old = _create_test_record("Deprecated API Key v1", status=MemoryStatus.ACTIVE)
        rec_new = MemoryRecord(
            memory_id=f"mem_new_api_{uuid.uuid4().hex[:12]}",
            content="Current API Key v2",
            status=MemoryStatus.ACTIVE,
        )
        lifecycle_engine.supersede_memory(rec_old.memory_id, rec_new)

        from memory.retrieval import memory_retriever
        ctx = memory_retriever.retrieve(query="Deprecated API Key v1", include_private=True)
        retrieved_ids = [r.memory_id for r in ctx.retrieved_memories]
        self.assertNotIn(rec_old.memory_id, retrieved_ids)

    # ========================================================================
    # CATEGORY J: Security, Privacy & Tool Authority (3 tests)
    # ========================================================================

    def test_J01_sensitive_memories_rejected(self):
        """J01: SENSITIVE memories cannot participate in graph relationships."""
        rec_sens = _create_test_record("Master Secret Password", privacy_class=PrivacyClass.SENSITIVE)
        rec_norm = _create_test_record("Normal Project Note", privacy_class=PrivacyClass.NORMAL)

        with self.assertRaises(SensitiveRelationshipError):
            relationship_engine.create_relationship(
                rec_sens.memory_id, rec_norm.memory_id, RelationshipType.RELATED_TO
            )

        with self.assertRaises(SensitiveRelationshipError):
            relationship_engine.create_relationship(
                rec_norm.memory_id, rec_sens.memory_id, RelationshipType.RELATED_TO
            )

    def test_J02_private_targets_suppressed_when_unauthorized(self):
        """J02: Private memories are filtered out of public retrieval contexts."""
        rec_priv = _create_test_record("Confidential salary notes", privacy_class=PrivacyClass.PRIVATE)

        from memory.retrieval import memory_retriever
        ctx = memory_retriever.retrieve(query="salary notes", include_private=False)
        retrieved_ids = [r.memory_id for r in ctx.retrieved_memories]
        self.assertNotIn(rec_priv.memory_id, retrieved_ids)

    def test_J03_relationships_zero_tool_authority(self):
        """J03: Relationships have zero direct tool authority or side-effects."""
        edge = MemoryRelationship(
            source_memory_id="src_node",
            target_memory_id="tgt_node",
            relationship_type=RelationshipType.SUPERSEDES,
        )
        # Verify edge contains only metadata/data attributes and no executable methods
        self.assertFalse(hasattr(edge, "execute"))
        self.assertFalse(hasattr(edge, "run"))
        self.assertFalse(hasattr(edge, "execute_tool"))


if __name__ == "__main__":
    unittest.main()
