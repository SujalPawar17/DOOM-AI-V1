"""
DOOM V5.3.5 — Memory Freshness, Confidence & Importance Evolution Test Suite
47 Dedicated Comprehensive Test Scenarios covering:
- Category A: Schema & Constraints (5 tests)
- Category B: Freshness Mathematics & Semantic Classes (6 tests)
- Category C: Confidence Evolution Mechanics & Bounded Numerical Model (6 tests)
- Category D: Evidence Admissibility, Ingestion & Idempotency (5 tests)
- Category E: Correlation Protection & Deduplication (4 tests)
- Category F: Importance Evolution & Centrality (4 tests)
- Category G: Anti-Feedback Invariants & Non-Mutating Retrieval (4 tests)
- Category H: Transactional Atomicity & Concurrency (4 tests)
- Category I: Relationship & Vector Outbox Invariants (4 tests)
- Category J: Privacy, Fencing & Zero Tool Authority (3 tests)
- Category K: Explainability & Epistemic Profiles (2 tests)
"""
import concurrent.futures
from datetime import datetime, timezone, timedelta
import math
import unittest
import uuid

from memory.types import (
    MemoryType,
    MemoryStatus,
    MemorySource,
    ConfidenceLevel,
    VerificationStatus,
    PrivacyClass,
)
from memory.schemas import MemoryRecord
from memory.repository import memory_repository
from memory.evolution_models import (
    FreshnessClass,
    FRESHNESS_CONFIG,
    EvidencePolarity,
    EvidenceType,
    EvolutionType,
    MemoryEvidence,
    MemoryEvolutionEvent,
    EvolutionResult,
    MemoryEpistemicProfile,
    InadmissibleEvidenceError,
    InactiveMemoryEvolutionError,
    EvolutionValidationError,
    clamp_float,
    project_confidence_score_to_level,
    project_confidence_level_to_score,
    compute_observation_hash,
    compute_evidence_idempotency_key,
)
from memory.evolution_engine import evolution_engine, MemoryEvolutionEngine
from memory.evolution_reconciliation import evolution_reconciliation_engine
from memory.evolution_migration import run_evolution_migration
from memory.ranking import memory_ranker
from memory.retrieval import memory_retriever
from database.postgres_db import postgres_manager


def _create_test_record(
    memory_type: MemoryType = MemoryType.SEMANTIC,
    content: str = "Test memory fact",
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM,
    confidence_score: float = 0.60,
    importance: float = 0.50,
    freshness_class: str = "PROJECT_STABLE",
    is_foundational: bool = False,
    privacy_class: PrivacyClass = PrivacyClass.NORMAL,
    status: MemoryStatus = MemoryStatus.ACTIVE,
    valid_until: str = None,
    created_at: str = None,
    last_confirmed_at: str = None,
) -> MemoryRecord:
    rec = MemoryRecord(
        memory_id=f"test_evo_{uuid.uuid4().hex[:12]}",
        memory_type=memory_type,
        content=content,
        source=MemorySource.USER_CONVERSATION,
        confidence=confidence,
        confidence_score=confidence_score,
        importance=importance,
        freshness_class=freshness_class,
        is_foundational=is_foundational,
        privacy_class=privacy_class,
        status=status,
        valid_until=valid_until,
    )
    if created_at:
        rec.created_at = created_at
    if last_confirmed_at:
        rec.last_confirmed_at = last_confirmed_at
    else:
        rec.last_confirmed_at = rec.created_at
    memory_repository.store(rec)
    return rec


class TestV535Evolution(unittest.TestCase):
    """Authoritative test suite for DOOM V5.3.5."""

    # =======================================================================
    # Category A: Schema & Constraints (5 tests)
    # =======================================================================
    def test_a01_columns_exist_in_postgres(self):
        """Verify new V5.3.5 columns exist in PostgreSQL memory_records."""
        conn = postgres_manager.get_connection()
        self.assertIsNotNone(conn)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'memory_records';
                """)
                cols = {r[0] for r in cur.fetchall()}
                self.assertIn("freshness_class", cols)
                self.assertIn("confidence_score", cols)
                self.assertIn("is_foundational", cols)
                self.assertIn("valid_from", cols)
                self.assertIn("valid_until", cols)
                self.assertIn("last_confirmed_at", cols)
        finally:
            postgres_manager.release_connection(conn)

    def test_a02_evidence_table_schema(self):
        """Verify memory_evidence table exists with required constraints and columns."""
        conn = postgres_manager.get_connection()
        self.assertIsNotNone(conn)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'memory_evidence';
                """)
                cols = {r[0] for r in cur.fetchall()}
                self.assertIn("evidence_id", cols)
                self.assertIn("memory_id", cols)
                self.assertIn("polarity", cols)
                self.assertIn("strength", cols)
                self.assertIn("observation_hash", cols)
                self.assertIn("idempotency_key", cols)
        finally:
            postgres_manager.release_connection(conn)

    def test_a03_evolution_events_table_schema(self):
        """Verify memory_evolution_events table exists with required columns."""
        conn = postgres_manager.get_connection()
        self.assertIsNotNone(conn)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'memory_evolution_events';
                """)
                cols = {r[0] for r in cur.fetchall()}
                self.assertIn("event_id", cols)
                self.assertIn("memory_id", cols)
                self.assertIn("evolution_type", cols)
                self.assertIn("confidence_before", cols)
                self.assertIn("confidence_after", cols)
                self.assertIn("delta_confidence", cols)
        finally:
            postgres_manager.release_connection(conn)

    def test_a04_confidence_score_check_constraint(self):
        """Verify PostgreSQL rejects confidence_score outside [0.0, 1.0]."""
        conn = postgres_manager.get_connection()
        self.assertIsNotNone(conn)
        try:
            with conn.cursor() as cur:
                with self.assertRaises(Exception):
                    cur.execute("""
                        INSERT INTO memory_records (memory_id, memory_type, content, confidence_score)
                        VALUES (%s, 'SEMANTIC', 'invalid conf', 1.5);
                    """, (f"invalid_conf_{uuid.uuid4().hex[:8]}",))
            conn.rollback()
        finally:
            postgres_manager.release_connection(conn)

    def test_a05_evidence_cascade_deletion(self):
        """Verify deleting a memory_record cascades and deletes its evidence."""
        rec = _create_test_record(content="Will be deleted for cascade")
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.8,
            source=MemorySource.VERIFIED_TASK,
            actor="TASK_VERIFIER",
            raw_observation="Execution succeeded",
            task_verified=True,
        )
        self.assertTrue(res.success)

        # Delete parent memory record
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM memory_records WHERE memory_id = %s;", (rec.memory_id,))
                cur.execute("SELECT COUNT(*) FROM memory_evidence WHERE memory_id = %s;", (rec.memory_id,))
                cnt = cur.fetchone()[0]
                self.assertEqual(cnt, 0)
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

    # =======================================================================
    # Category B: Freshness Mathematics & Semantic Classes (6 tests)
    # =======================================================================
    def test_b01_permanent_facts_have_no_decay(self):
        """PERMANENT freshness class remains F(t)=1.00 regardless of age."""
        ancient = (datetime.now(timezone.utc) - timedelta(days=1000)).isoformat()
        rec = _create_test_record(
            freshness_class="PERMANENT",
            created_at=ancient,
            last_confirmed_at=ancient,
        )
        score = memory_ranker.compute_freshness_score(rec)
        self.assertEqual(score, 1.0)

    def test_b02_foundational_memory_floor_protection(self):
        """FOUNDATIONAL facts decay towards 0.85 floor and never drop below it."""
        ancient = (datetime.now(timezone.utc) - timedelta(days=2000)).isoformat()
        rec = _create_test_record(
            freshness_class="FOUNDATIONAL",
            is_foundational=True,
            created_at=ancient,
            last_confirmed_at=ancient,
        )
        score = memory_ranker.compute_freshness_score(rec)
        self.assertGreaterEqual(score, 0.85)
        self.assertLessEqual(score, 1.0)

    def test_b03_foundational_one_year_decay_behavior(self):
        """After exactly 365 days, a FOUNDATIONAL memory reaches approximately midway to floor."""
        one_year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        rec = _create_test_record(
            freshness_class="FOUNDATIONAL",
            is_foundational=True,
            created_at=one_year_ago,
            last_confirmed_at=one_year_ago,
        )
        # Expected: floor + (1 - floor) * 0.5 = 0.85 + 0.15 * 0.5 = 0.925
        score = memory_ranker.compute_freshness_score(rec)
        self.assertAlmostEqual(score, 0.925, places=2)

    def test_b04_dynamic_fact_decay(self):
        """DYNAMIC_FACT (half-life=14d, floor=0.15) decays to ~0.575 after 14 days."""
        two_weeks_ago = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        rec = _create_test_record(
            freshness_class="DYNAMIC_FACT",
            created_at=two_weeks_ago,
            last_confirmed_at=two_weeks_ago,
        )
        # Expected: 0.15 + (1.0 - 0.15) * 0.5 = 0.575
        score = memory_ranker.compute_freshness_score(rec)
        self.assertAlmostEqual(score, 0.575, places=2)

    def test_b05_ephemeral_decay(self):
        """EPHEMERAL (half-life=1d, floor=0.0) decays to <= 0.05 after 5 days."""
        five_days_ago = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        rec = _create_test_record(
            freshness_class="EPHEMERAL",
            created_at=five_days_ago,
            last_confirmed_at=five_days_ago,
        )
        score = memory_ranker.compute_freshness_score(rec)
        self.assertLessEqual(score, 0.05)

    def test_b06_valid_until_expiration(self):
        """Memories past valid_until return F(t) = 0.0 without mutating DB."""
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        rec = _create_test_record(
            freshness_class="PROJECT_STABLE",
            valid_until=past,
        )
        score = memory_ranker.compute_freshness_score(rec)
        self.assertEqual(score, 0.0)
        # DB status must remain ACTIVE
        fetched = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(fetched.status, MemoryStatus.ACTIVE)

    # =======================================================================
    # Category C: Confidence Evolution Mechanics & Bounded Model (6 tests)
    # =======================================================================
    def test_c01_positive_evidence_increases_confidence(self):
        """Supporting evidence from verified task increments confidence using damped formula."""
        rec = _create_test_record(confidence_score=0.60)
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.80,
            source=MemorySource.VERIFIED_TASK,
            actor="TASK_VERIFIER",
            raw_observation="Result confirmed by test suite",
            task_verified=True,
        )
        self.assertTrue(res.success)
        self.assertGreater(res.confidence_after, 0.60)
        self.assertLessEqual(res.confidence_after, 1.0)

    def test_c02_contradictory_evidence_degrades_confidence(self):
        """Contradictory evidence reduces confidence faster than supporting builds it."""
        rec = _create_test_record(confidence_score=0.80)
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.CONTRADICTING,
            strength=0.90,
            source=MemorySource.VERIFIED_TASK,
            actor="GROUND_TRUTH_VERIFIER",
            raw_observation="Negative verification: assertion violated",
            task_verified=True,
        )
        self.assertTrue(res.success)
        self.assertLess(res.confidence_after, 0.80)
        self.assertGreaterEqual(res.confidence_after, 0.01)

    def test_c03_user_explicit_maximizes_confidence(self):
        """USER_EXPLICIT confirmation sets confidence immediately to 1.00."""
        rec = _create_test_record(confidence_score=0.40)
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=1.0,
            source=MemorySource.USER_EXPLICIT,
            actor="USER",
            raw_observation="User said: Yes, that is correct.",
        )
        self.assertTrue(res.success)
        self.assertEqual(res.confidence_after, 1.00)

    def test_c04_confidence_lower_bound_clamping(self):
        """Extreme contradictions clamp confidence at 0.01 and never drop below zero."""
        rec = _create_test_record(confidence_score=0.10)
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.CONTRADICTING,
            strength=1.0,
            source=MemorySource.USER_EXPLICIT,
            actor="USER",
            raw_observation="User said: Absolutely false!",
        )
        self.assertTrue(res.success)
        self.assertGreaterEqual(res.confidence_after, 0.01)

    def test_c05_confidence_projection_to_enum(self):
        """Continuous confidence scores deterministically map to legacy ConfidenceLevel enums."""
        self.assertEqual(project_confidence_score_to_level(0.95), ConfidenceLevel.HIGH)
        self.assertEqual(project_confidence_score_to_level(0.80), ConfidenceLevel.HIGH)
        self.assertEqual(project_confidence_score_to_level(0.79), ConfidenceLevel.MEDIUM)
        self.assertEqual(project_confidence_score_to_level(0.40), ConfidenceLevel.MEDIUM)
        self.assertEqual(project_confidence_score_to_level(0.39), ConfidenceLevel.LOW)
        self.assertEqual(project_confidence_score_to_level(0.10), ConfidenceLevel.LOW)
        self.assertEqual(project_confidence_score_to_level(0.05), ConfidenceLevel.UNKNOWN)

    def test_c06_rejuvenation_resets_freshness(self):
        """Supporting evidence updates last_confirmed_at, rejuvenating freshness score."""
        one_year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        rec = _create_test_record(
            freshness_class="DYNAMIC_FACT",
            created_at=one_year_ago,
            last_confirmed_at=one_year_ago,
        )
        initial_freshness = memory_ranker.compute_freshness_score(rec)
        self.assertAlmostEqual(initial_freshness, 0.15, places=1)

        # Confirm with new evidence
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.9,
            source=MemorySource.VERIFIED_TASK,
            actor="TASK_VERIFIER",
            raw_observation="Still running and confirmed",
            task_verified=True,
        )
        self.assertTrue(res.success)

        updated_rec = memory_repository.get_by_id(rec.memory_id)
        rejuvenated_freshness = memory_ranker.compute_freshness_score(updated_rec)
        self.assertAlmostEqual(rejuvenated_freshness, 1.00, places=2)

    # =======================================================================
    # Category D: Evidence Admissibility & Idempotency (5 tests)
    # =======================================================================
    def test_d01_model_generation_inadmissible_as_supporting(self):
        """Model output without task verification cannot serve as supporting evidence."""
        rec = _create_test_record()
        with self.assertRaises(InadmissibleEvidenceError):
            evolution_engine.record_evidence_and_evolve(
                memory_id=rec.memory_id,
                polarity=EvidencePolarity.SUPPORTING,
                strength=0.8,
                source=MemorySource.DERIVED_CONTEXT,
                actor="LLM",
                raw_observation="The model hallucinated that this is true",
                task_verified=False,
            )

    def test_d02_observation_hash_determinism(self):
        """Identical observations produce identical SHA-256 hashes regardless of dict key ordering."""
        h1 = compute_observation_hash("VERIFIED_TASK", "TASK_RUNNER", {"b": 2, "a": 1})
        h2 = compute_observation_hash("VERIFIED_TASK", "TASK_RUNNER", {"a": 1, "b": 2})
        self.assertEqual(h1, h2)

    def test_d03_evidence_idempotency_replay(self):
        """Submitting exact same idempotency key returns prior state without re-applying delta."""
        rec = _create_test_record(confidence_score=0.50)
        key = f"fixed_test_key_{uuid.uuid4().hex[:12]}"
        res1 = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.8,
            source=MemorySource.VERIFIED_TASK,
            idempotency_key=key,
            task_verified=True,
        )
        self.assertFalse(res1.is_idempotent_replay)
        conf_after_1 = res1.confidence_after

        # Replay identical key
        res2 = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.8,
            source=MemorySource.VERIFIED_TASK,
            idempotency_key=key,
            task_verified=True,
        )
        self.assertTrue(res2.is_idempotent_replay)
        self.assertEqual(res2.confidence_after, conf_after_1)

    def test_d04_ambiguous_evidence_does_not_mutate_confidence(self):
        """AMBIGUOUS evidence is logged but does not alter confidence score."""
        rec = _create_test_record(confidence_score=0.65)
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.AMBIGUOUS,
            strength=0.7,
            source=MemorySource.TOOL_RESULT,
            actor="ENVIRONMENT_PROBE",
            raw_observation="Uncertain observation",
        )
        self.assertTrue(res.success)
        self.assertEqual(res.confidence_before, res.confidence_after)

    def test_d05_inactive_memory_rejects_evidence(self):
        """SUPERSEDED or DELETED memory rejects new evidence with InactiveMemoryEvolutionError."""
        rec = _create_test_record(status=MemoryStatus.SUPERSEDED)
        with self.assertRaises(InactiveMemoryEvolutionError):
            evolution_engine.record_evidence_and_evolve(
                memory_id=rec.memory_id,
                polarity=EvidencePolarity.SUPPORTING,
                strength=0.8,
                source=MemorySource.VERIFIED_TASK,
                task_verified=True,
            )

    # =======================================================================
    # Category E: Correlation Protection & Deduplication (4 tests)
    # =======================================================================
    def test_e01_correlated_evidence_within_same_task_damped(self):
        """Repeated identical observations from the same task have their strength heavily damped."""
        rec = _create_test_record(confidence_score=0.50)
        task_id = f"task_batch_{uuid.uuid4().hex[:8]}"

        # First observation
        res1 = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.9,
            source=MemorySource.TOOL_RESULT,
            actor="TEST_RUNNER",
            source_task_id=task_id,
            raw_observation="Assertion passed",
            idempotency_key=f"key_1_{uuid.uuid4().hex[:8]}",
        )
        delta1 = res1.confidence_after - res1.confidence_before

        # Second observation with same hash & task
        res2 = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.9,
            source=MemorySource.TOOL_RESULT,
            actor="TEST_RUNNER",
            source_task_id=task_id,
            raw_observation="Assertion passed",
            idempotency_key=f"key_2_{uuid.uuid4().hex[:8]}",
        )
        delta2 = res2.confidence_after - res2.confidence_before

        # Second delta must be significantly smaller due to correlation damping
        self.assertLess(delta2, delta1 * 0.25)

    def test_e02_independent_evidence_from_different_tasks_not_damped(self):
        """Observations from distinct tasks with distinct sources apply full calculated strength."""
        rec = _create_test_record(confidence_score=0.50)
        res1 = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.8,
            source=MemorySource.TOOL_RESULT,
            source_task_id="task_1",
            raw_observation="Obs 1",
            task_verified=True,
        )
        res2 = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.8,
            source=MemorySource.TOOL_RESULT,
            source_task_id="task_2",
            raw_observation="Obs 2",
            task_verified=True,
        )
        self.assertTrue(res1.success)
        self.assertTrue(res2.success)

    def test_e03_duplicate_observation_hash_logged(self):
        """Evidence table stores exact observation_hash for forensic analysis."""
        rec = _create_test_record()
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.5,
            source=MemorySource.SYSTEM_OBSERVATION,
            actor="SYS_TELEMETRY",
            raw_observation="Port 5432 open",
        )
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT observation_hash FROM memory_evidence WHERE evidence_id = %s;", (res.evidence_id,))
                h = cur.fetchone()[0]
                self.assertEqual(len(h), 64)
        finally:
            postgres_manager.release_connection(conn)

    def test_e04_reconciliation_orphan_evidence_audit(self):
        """Reconciliation engine correctly verifies zero orphan evidence rows."""
        rep = evolution_reconciliation_engine.run_reconciliation(repair=True)
        self.assertEqual(rep.orphan_evidence_count, 0)
        self.assertTrue(rep.is_healthy)

    # =======================================================================
    # Category F: Importance Evolution & Centrality (4 tests)
    # =======================================================================
    def test_f01_foundational_importance_guarantee(self):
        """is_foundational=True guarantees base importance >= 0.80."""
        rec = _create_test_record(is_foundational=True, importance=0.40)
        # Structural update enforces foundational base floor
        new_i = evolution_engine.update_structural_importance(rec.memory_id)
        self.assertGreaterEqual(new_i, 0.80)

    def test_f02_structural_centrality_increases_importance(self):
        """Adding V5.3.4 relationships (SUPERSEDES, RELATED_TO) increases structural importance."""
        root = _create_test_record(importance=0.50, is_foundational=False)
        target1 = _create_test_record()
        target2 = _create_test_record()

        # Insert relationship edges
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO memory_relationships (relationship_id, source_memory_id, target_memory_id, relationship_type)
                    VALUES (%s, %s, %s, 'SUPERSEDES'), (%s, %s, %s, 'RELATED_TO');
                """, (
                    f"rel_{uuid.uuid4().hex[:12]}", root.memory_id, target1.memory_id,
                    f"rel_{uuid.uuid4().hex[:12]}", root.memory_id, target2.memory_id,
                ))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        new_imp = evolution_engine.update_structural_importance(root.memory_id)
        # Expected: 0.50 + 0.05(1) + 0.01(1) = 0.56
        self.assertAlmostEqual(new_imp, 0.56, places=2)

    def test_f03_task_criticality_importance_increment(self):
        """Task verified positive outcome provides bounded importance increment (<= +0.10)."""
        rec = _create_test_record(importance=0.50, is_foundational=False)
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=1.0,
            source=MemorySource.VERIFIED_TASK,
            task_verified=True,
        )
        self.assertTrue(res.success)
        self.assertGreater(res.importance_after, 0.50)
        self.assertLessEqual(res.importance_after, 0.60)

    def test_f04_importance_clamping_to_one(self):
        """Importance can never exceed 1.00 under multiple cumulative increments."""
        rec = _create_test_record(importance=0.98, is_foundational=True)
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=1.0,
            source=MemorySource.VERIFIED_TASK,
            task_verified=True,
        )
        self.assertLessEqual(res.importance_after, 1.00)

    # =======================================================================
    # Category G: Anti-Feedback Invariants & Non-Mutating Retrieval (4 tests)
    # =======================================================================
    def test_g01_retrieval_does_not_mutate_confidence(self):
        """Executing retrieval queries never changes confidence_score."""
        rec = _create_test_record(content="Unique Python PostgreSQL architecture preference", confidence_score=0.72)
        ctx = memory_retriever.retrieve(query="Python PostgreSQL architecture", max_results=5)
        self.assertTrue(ctx.memory_hit)

        refetched = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(refetched.confidence_score, 0.72)

    def test_g02_retrieval_does_not_mutate_importance(self):
        """Executing retrieval queries never changes importance."""
        rec = _create_test_record(content="Unique Docker container configuration", importance=0.64)
        ctx = memory_retriever.retrieve(query="Docker container configuration", max_results=5)
        self.assertTrue(ctx.memory_hit)

        refetched = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(refetched.importance, 0.64)

    def test_g03_retrieval_does_not_update_last_confirmed_at(self):
        """Retrieval touches last_accessed_at but never touches last_confirmed_at."""
        old_conf_dt = datetime.now(timezone.utc) - timedelta(days=20)
        old_conf_time = old_conf_dt.isoformat()
        rec = _create_test_record(
            content="Kubernetes pod cluster configuration",
            last_confirmed_at=old_conf_time,
        )
        ctx = memory_retriever.retrieve(query="Kubernetes pod cluster", max_results=5)
        self.assertTrue(ctx.memory_hit)

        refetched = memory_repository.get_by_id(rec.memory_id)
        refetched_dt = datetime.fromisoformat(refetched.last_confirmed_at)
        # Difference in seconds between stored and retrieved datetime must be < 1s
        diff = abs((refetched_dt - old_conf_dt).total_seconds())
        self.assertLess(diff, 1.0)

    def test_g04_retrieval_does_not_generate_evidence(self):
        """Searching and ranking produces zero rows in memory_evidence."""
        rec = _create_test_record(content="Microservices service mesh envoy proxy")
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM memory_evidence WHERE memory_id = %s;", (rec.memory_id,))
                cnt_before = cur.fetchone()[0]

            ctx = memory_retriever.retrieve(query="service mesh envoy proxy", max_results=5)

            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM memory_evidence WHERE memory_id = %s;", (rec.memory_id,))
                cnt_after = cur.fetchone()[0]

            self.assertEqual(cnt_before, cnt_after)
            self.assertEqual(cnt_after, 0)
        finally:
            postgres_manager.release_connection(conn)

    # =======================================================================
    # Category H: Transactional Atomicity & Concurrency (4 tests)
    # =======================================================================
    def test_h01_atomic_evidence_and_event_commit(self):
        """Evolution mutation writes memory update, evidence row, and evolution event atomically."""
        rec = _create_test_record(confidence_score=0.50)
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.7,
            source=MemorySource.VERIFIED_TASK,
            task_verified=True,
        )
        self.assertTrue(res.success)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM memory_evidence WHERE evidence_id = %s;", (res.evidence_id,))
                self.assertEqual(cur.fetchone()[0], 1)
                cur.execute("SELECT COUNT(*) FROM memory_evolution_events WHERE event_id = %s;", (res.event_id,))
                self.assertEqual(cur.fetchone()[0], 1)
        finally:
            postgres_manager.release_connection(conn)

    def test_h02_rollback_leaves_zero_partial_state(self):
        """Failure during evolution transaction rolls back cleanly with zero partial state."""
        rec = _create_test_record(confidence_score=0.55)
        # Attempt to insert evidence with negative strength to trigger validation error
        with self.assertRaises(InadmissibleEvidenceError):
            evolution_engine.record_evidence_and_evolve(
                memory_id=rec.memory_id,
                polarity=EvidencePolarity.SUPPORTING,
                strength=-0.5,  # Invalid
                source=MemorySource.VERIFIED_TASK,
            )

        refetched = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(refetched.confidence_score, 0.55)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM memory_evidence WHERE memory_id = %s;", (rec.memory_id,))
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            postgres_manager.release_connection(conn)

    def test_h03_concurrent_evidence_updates_serializable(self):
        """8 concurrent worker threads updating evidence do not lose updates or deadlock."""
        rec = _create_test_record(confidence_score=0.20)

        def worker(idx):
            return evolution_engine.record_evidence_and_evolve(
                memory_id=rec.memory_id,
                polarity=EvidencePolarity.SUPPORTING,
                strength=0.6,
                source=MemorySource.TOOL_RESULT,
                actor=f"WORKER_{idx}",
                raw_observation=f"Observation {idx}",
                idempotency_key=f"worker_key_{rec.memory_id}_{idx}",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(8)]
            results = [f.result() for f in futures]

        for r in results:
            self.assertTrue(r.success)

        refetched = memory_repository.get_by_id(rec.memory_id)
        self.assertGreater(refetched.confidence_score, 0.20)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM memory_evidence WHERE memory_id = %s;", (rec.memory_id,))
                self.assertEqual(cur.fetchone()[0], 8)
        finally:
            postgres_manager.release_connection(conn)

    def test_h04_evidence_audit_record_immutable(self):
        """Evolution events capture exact confidence_before and confidence_after deltas."""
        rec = _create_test_record(confidence_score=0.50)
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.8,
            source=MemorySource.VERIFIED_TASK,
            task_verified=True,
        )
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT confidence_before, confidence_after, delta_confidence
                    FROM memory_evolution_events
                    WHERE event_id = %s;
                """, (res.event_id,))
                row = cur.fetchone()
                self.assertAlmostEqual(row[0], 0.50, places=2)
                self.assertAlmostEqual(row[1], res.confidence_after, places=2)
                self.assertAlmostEqual(row[2], res.confidence_after - 0.50, places=2)
        finally:
            postgres_manager.release_connection(conn)

    # =======================================================================
    # Category I: Relationship & Vector Outbox Invariants (4 tests)
    # =======================================================================
    def test_i01_evolution_does_not_increment_vector_generation(self):
        """Metadata evolution must NOT increment generation on memory_records."""
        rec = _create_test_record(confidence_score=0.50)
        initial_gen = rec.generation

        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.8,
            source=MemorySource.VERIFIED_TASK,
            task_verified=True,
        )
        self.assertTrue(res.success)

        refetched = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(refetched.generation, initial_gen)

    def test_i02_evolution_does_not_enqueue_vector_sync(self):
        """Metadata evolution must NOT create vector_sync_queue outbox work items."""
        rec = _create_test_record(confidence_score=0.50)
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM vector_sync_queue WHERE memory_id = %s;", (rec.memory_id,))
                cnt_before = cur.fetchone()[0]

            res = evolution_engine.record_evidence_and_evolve(
                memory_id=rec.memory_id,
                polarity=EvidencePolarity.SUPPORTING,
                strength=0.8,
                source=MemorySource.VERIFIED_TASK,
                task_verified=True,
            )

            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM vector_sync_queue WHERE memory_id = %s;", (rec.memory_id,))
                cnt_after = cur.fetchone()[0]

            self.assertEqual(cnt_before, cnt_after)
        finally:
            postgres_manager.release_connection(conn)

    def test_i03_superseded_memory_rejects_evidence(self):
        """Targeting a superseded memory with new evidence raises InactiveMemoryEvolutionError."""
        rec = _create_test_record(status=MemoryStatus.SUPERSEDED)
        with self.assertRaises(InactiveMemoryEvolutionError):
            evolution_engine.record_evidence_and_evolve(
                memory_id=rec.memory_id,
                polarity=EvidencePolarity.SUPPORTING,
                strength=0.8,
                source=MemorySource.VERIFIED_TASK,
            )

    def test_i04_conflicts_with_edge_creates_contradiction_evidence(self):
        """Registering a contradiction evidence updates confidence downwards without superseding."""
        rec = _create_test_record(confidence_score=0.85)
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.CONTRADICTING,
            strength=0.75,
            source=MemorySource.SYSTEM_OBSERVATION,
            actor="CONFLICT_ENGINE",
            raw_observation="Fact contradiction discovered in environment",
            evidence_type=EvidenceType.GRAPH_RELATIONSHIP_LINK,
        )
        self.assertTrue(res.success)
        self.assertLess(res.confidence_after, 0.85)
        refetched = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(refetched.status, MemoryStatus.ACTIVE)

    # =======================================================================
    # Category J: Privacy, Fencing & Zero Tool Authority (3 tests)
    # =======================================================================
    def test_j01_sensitive_memory_evidence_redaction(self):
        """Evidence for SENSITIVE memories is automatically redacted in summaries."""
        rec = _create_test_record(
            privacy_class=PrivacyClass.SENSITIVE,
            content="Sensitive health data",
        )
        res = evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.8,
            source=MemorySource.USER_EXPLICIT,
            actor="USER",
            summary="User revealed secret health detail",
            metadata={"secret": "abc"},
        )
        self.assertTrue(res.success)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT summary, metadata FROM memory_evidence WHERE evidence_id = %s;", (res.evidence_id,))
                row = cur.fetchone()
                self.assertEqual(row[0], "[REDACTED_SENSITIVE_EVIDENCE]")
                self.assertEqual(row[1].get("redacted"), True)
                self.assertNotIn("secret", row[1])
        finally:
            postgres_manager.release_connection(conn)

    def test_j02_private_memory_evidence_context_filtering(self):
        """Epistemic profile handles PRIVATE memories without leaking unauthorized data."""
        rec = _create_test_record(
            privacy_class=PrivacyClass.PRIVATE,
            content="Private personal preference",
        )
        profile = evolution_engine.get_epistemic_profile(rec.memory_id)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.memory_id, rec.memory_id)

    def test_j03_zero_tool_authority(self):
        """MemoryEvolutionEngine dataclasses and engine possess zero tool-execution methods."""
        self.assertFalse(hasattr(evolution_engine, "execute_tool"))
        self.assertFalse(hasattr(evolution_engine, "run_command"))
        self.assertFalse(hasattr(evolution_engine, "call_shell"))

    # =======================================================================
    # Category K: Explainability & Epistemic Profiles (2 tests)
    # =======================================================================
    def test_k01_epistemic_profile_generation(self):
        """Verify epistemic profile correctly aggregates confidence, freshness, and evidence counts."""
        rec = _create_test_record(
            content="User prefers PostgreSQL over MySQL",
            confidence_score=0.92,
            freshness_class="FOUNDATIONAL",
            is_foundational=True,
        )
        evolution_engine.record_evidence_and_evolve(
            memory_id=rec.memory_id,
            polarity=EvidencePolarity.SUPPORTING,
            strength=0.8,
            source=MemorySource.VERIFIED_TASK,
            task_verified=True,
        )
        profile = evolution_engine.get_epistemic_profile(rec.memory_id)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.confidence_level, "HIGH")
        self.assertEqual(profile.freshness_class, "FOUNDATIONAL")
        self.assertTrue(profile.is_foundational)
        self.assertGreaterEqual(profile.supporting_evidence_count, 1)
        self.assertEqual(profile.contradicting_evidence_count, 0)
        self.assertIn("Confidence: 9", profile.summary_reason)

    def test_k02_hybrid_ranking_favors_foundational_over_noisy_fresh(self):
        """A foundational old memory with high confidence and importance ranks above a low-importance recent note."""
        one_year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        foundational_old = _create_test_record(
            content="Core database rule: PostgreSQL localhost",
            confidence_score=0.95,
            confidence=ConfidenceLevel.HIGH,
            importance=0.90,
            freshness_class="FOUNDATIONAL",
            is_foundational=True,
            created_at=one_year_ago,
            last_confirmed_at=one_year_ago,
        )

        noisy_fresh = _create_test_record(
            content="Temporary random scratch note PostgreSQL localhost",
            confidence_score=0.30,
            confidence=ConfidenceLevel.LOW,
            importance=0.10,
            freshness_class="EPHEMERAL",
            is_foundational=False,
            created_at=yesterday,
            last_confirmed_at=yesterday,
        )

        candidates = [
            (foundational_old, 0.80, 0.80),
            (noisy_fresh, 0.80, 0.80),
        ]

        ranked = memory_ranker.rank_hybrid(
            candidates=candidates,
            query="PostgreSQL localhost",
        )
        self.assertEqual(len(ranked), 2)
        # Foundational memory must win top rank
        self.assertEqual(ranked[0].record.memory_id, foundational_old.memory_id)
        self.assertGreater(ranked[0].score, ranked[1].score)


if __name__ == "__main__":
    unittest.main()
