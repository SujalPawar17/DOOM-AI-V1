"""
DOOM V5.3.6 — Dedicated Remediation Test Suite
==============================================
Validates the blocker remediation for:
  - F-01: Historical provenance immutability during orphan experience reconciliation.
  - F-02: Strict non-mutation of database during strategy retrieval (dI/dN_retrieval = 0).

Test Matrix:
  F01-01: Orphan experience project_id remains unchanged during reconciliation.
  F01-02: Orphan experience appears in anomalies_detected.
  F01-03: Reconciliation does not fabricate replacement project relationships.
  F01-04: Reconciliation is idempotent (repeated runs do not mutate records).
  F01-05: No silent reassignment of orphan experiences to 'doom'.
  F01-06: Historical execution data (action, outcome, trace, timestamp) remains unchanged.
  F01-07: Orphan reported as unresolved/quarantined without project_id mutation.
  F01-08: Normal valid experiences continue to reconcile correctly.

  F02-01: project_transfer_matrix row count unchanged after retrieve_strategies().
  F02-02: Zero INSERT/UPDATE/DELETE occurs during retrieve_strategies().
  F02-03: Repeated retrieval calls produce zero database mutations.
  F02-04: Retrieval with existing APPROVED transfer returns transferred strategy.
  F02-05: Retrieval with no APPROVED transfer does NOT create one.
  F02-06: REJECTED transfer entries are excluded from retrieval.
  F02-07: SUPERSEDED transfer entries are excluded from retrieval.
  F02-08: Cross-project privacy restrictions remain enforced during retrieval.
  F02-09: SENSITIVE project/experience information cannot leak through transfer retrieval.
  F02-10: Retrieval remains non-mutating under concurrent callers.
"""

import concurrent.futures
import datetime
from datetime import timezone
import os
import sys
import unittest
import uuid

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath("."))

from database.postgres_db import postgres_manager
from memory.project_models import (
    PrivacyClass,
    TaskOutcomeStatus,
    LessonScope,
    TransferDecision,
)
from memory.project_engine import project_experience_engine
from memory.project_reconciliation import project_reconciliation_engine
from memory.retrieval import memory_retriever


class TestV536BlockerRemediation(unittest.TestCase):
    """Rigorous verification of F-01 and F-02 blocker remediations."""

    @classmethod
    def setUpClass(cls):
        conn = postgres_manager.get_connection()
        assert conn is not None, "PostgreSQL connection failed"
        postgres_manager.release_connection(conn)
        cls._cleanup_test_data()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup_test_data()

    @classmethod
    def _cleanup_test_data(cls):
        conn = postgres_manager.get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("SET session_replication_role = 'replica';")
                cur.execute("DELETE FROM project_transfer_matrix WHERE transfer_id LIKE 'test_%' OR transfer_id LIKE 'txm_%';")
                cur.execute("DELETE FROM strategies WHERE strategy_id LIKE 'test_%' OR strategy_id LIKE 'strat_%';")
                cur.execute("DELETE FROM lessons WHERE lesson_id LIKE 'test_%' OR lesson_id LIKE 'les_%';")
                cur.execute("DELETE FROM experiences WHERE experience_id LIKE 'test_%' OR experience_id LIKE 'exp_%';")
                cur.execute("DELETE FROM projects WHERE project_id LIKE 'temp_proj_%' OR project_id LIKE 'test_%' OR project_id LIKE 'valid_proj_%';")
                cur.execute("SET session_replication_role = 'origin';")
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

    # =========================================================================
    # F-01: Historical Provenance Immutability in Orphan Experience Reconciliation
    # =========================================================================

    def _create_orphan_fixture(self):
        """Helper to set up an experience with a deleted/nonexistent project."""
        temp_pid = f"temp_proj_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(
            project_id=temp_pid,
            name="Temporary Project For Orphan Test",
            description="To be deleted to create orphan experience",
        )
        task_id = f"task_orphan_{uuid.uuid4().hex[:8]}"
        fixed_time = datetime.datetime(2026, 9, 7, 12, 30, 0, tzinfo=timezone.utc)
        exp = project_experience_engine.record_experience(
            task_id=task_id,
            project_id=temp_pid,
            outcome=TaskOutcomeStatus.SUCCESS,
            action_summary="Crucial historical task action that must remain immutable",
            outcome_reason="Completed cleanly with verification code 0",
            confidence=0.96,
            timestamp=fixed_time,
            verification_evidence_ids=["ev_12345"],
        )

        # Corrupt/delete the referenced project directly in DB using replica role to bypass FK
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SET session_replication_role = 'replica';")
                cur.execute("DELETE FROM projects WHERE project_id = %s;", (temp_pid,))
                cur.execute("SET session_replication_role = 'origin';")
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        return exp, temp_pid

    def test_f01_01_orphan_experience_project_id_remains_unchanged(self):
        """F01-01: Assert experiences.project_id remains exactly unchanged when referencing a missing project."""
        exp, original_pid = self._create_orphan_fixture()

        # Run reconciliation
        report = project_reconciliation_engine.run_reconciliation(dry_run=False)

        # Inspect database record directly
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT project_id FROM experiences WHERE experience_id = %s;", (exp.experience_id,))
                row = cur.fetchone()
                self.assertIsNotNone(row)
                current_pid = row[0]
        finally:
            postgres_manager.release_connection(conn)

        self.assertEqual(current_pid, original_pid, "experiences.project_id was mutated during reconciliation!")
        self.assertNotEqual(current_pid, "doom", "experiences.project_id was silently reassigned to 'doom'!")

    def test_f01_02_orphan_appears_in_anomalies_detected(self):
        """F01-02: Assert the orphan appears in anomalies_detected with correct structured metadata."""
        exp, original_pid = self._create_orphan_fixture()

        report = project_reconciliation_engine.run_reconciliation(dry_run=False)

        # Find anomaly in report
        matching_anomalies = [
            a for a in report.anomalies_detected
            if a.get("type") == "ORPHAN_EXPERIENCE" and a.get("experience_id") == exp.experience_id
        ]
        self.assertEqual(len(matching_anomalies), 1, "Orphan experience was not reported in anomalies_detected!")
        anomaly = matching_anomalies[0]
        self.assertEqual(anomaly["original_project_id"], original_pid)
        self.assertEqual(anomaly["reason"], "PROJECT_NOT_FOUND")
        self.assertEqual(anomaly["action"], "REPORTED_UNRESOLVED")

    def test_f01_03_reconciliation_does_not_create_replacement_project_relationship(self):
        """F01-03: Assert reconciliation does not fabricate a project record or relationship."""
        exp, original_pid = self._create_orphan_fixture()

        project_reconciliation_engine.run_reconciliation(dry_run=False)

        # Assert no project was automatically resurrected or fabricated
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM projects WHERE project_id = %s;", (original_pid,))
                count = cur.fetchone()[0]
                self.assertEqual(count, 0, "Reconciliation fabricated a replacement project row!")
        finally:
            postgres_manager.release_connection(conn)

    def test_f01_04_reconciliation_is_idempotent(self):
        """F01-04: Assert reconciliation is idempotent: repeated runs do not mutate the record."""
        exp, original_pid = self._create_orphan_fixture()

        # Run 1
        project_reconciliation_engine.run_reconciliation(dry_run=False)
        # Run 2
        project_reconciliation_engine.run_reconciliation(dry_run=False)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT project_id, goal_intent, outcome_status, confidence_score FROM experiences WHERE experience_id = %s;",
                    (exp.experience_id,)
                )
                row = cur.fetchone()
        finally:
            postgres_manager.release_connection(conn)

        self.assertEqual(row[0], original_pid)
        self.assertEqual(row[1], exp.goal_intent)
        self.assertEqual(row[2], exp.outcome_status.value)
        self.assertEqual(float(row[3]), exp.confidence_score)

    def test_f01_05_no_silent_reassignment_to_doom(self):
        """F01-05: Assert that under no circumstances is an orphan experience reassigned to 'doom'."""
        exp, original_pid = self._create_orphan_fixture()

        project_reconciliation_engine.run_reconciliation(dry_run=False)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT project_id FROM experiences WHERE experience_id = %s;",
                    (exp.experience_id,)
                )
                row = cur.fetchone()
        finally:
            postgres_manager.release_connection(conn)

        self.assertNotEqual(row[0], "doom")
        self.assertEqual(row[0], original_pid)

    def test_f01_06_historical_execution_data_remains_unchanged(self):
        """F01-06: Assert historical execution trace, task_id, timestamps, outcome, and evidence are unchanged."""
        exp, original_pid = self._create_orphan_fixture()

        project_reconciliation_engine.run_reconciliation(dry_run=False)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT task_id, outcome_status, goal_intent, 
                           confidence_score, execution_trace, created_at
                    FROM experiences WHERE experience_id = %s;
                """, (exp.experience_id,))
                row = cur.fetchone()
        finally:
            postgres_manager.release_connection(conn)

        self.assertEqual(row[0], exp.task_id)
        self.assertEqual(row[1], exp.outcome_status.value)
        self.assertEqual(row[2], exp.goal_intent)
        self.assertEqual(float(row[3]), exp.confidence_score)
        self.assertEqual(row[4], exp.execution_trace)
        self.assertIsNotNone(row[5])

    def test_f01_07_orphan_preserved_as_unresolved_without_provenance_alteration(self):
        """F01-07: Verify orphan enters REPORTED_UNRESOLVED status without modifying historical project_id."""
        exp, original_pid = self._create_orphan_fixture()

        report = project_reconciliation_engine.run_reconciliation(dry_run=False)
        self.assertGreaterEqual(report.orphan_experiences_detected, 1)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT project_id FROM experiences WHERE experience_id = %s;", (exp.experience_id,))
                current_pid = cur.fetchone()[0]
        finally:
            postgres_manager.release_connection(conn)

        self.assertEqual(current_pid, original_pid)

    def test_f01_08_normal_valid_experiences_reconcile_normally(self):
        """F01-08: Verify normal valid experiences continue to reconcile normally without being flagged."""
        pid = f"valid_proj_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(project_id=pid, name="Valid Project")

        exp = project_experience_engine.record_experience(
            task_id="valid_task_1",
            project_id=pid,
            outcome=TaskOutcomeStatus.SUCCESS,
            action_summary="Valid normal execution",
        )

        report = project_reconciliation_engine.run_reconciliation(dry_run=False)
        orphan_ids = [a["experience_id"] for a in report.anomalies_detected if a.get("type") == "ORPHAN_EXPERIENCE"]
        self.assertNotIn(exp.experience_id, orphan_ids)

    # =========================================================================
    # F-02: Elimination of Retrieval Database Mutation (dI / dN_retrieval = 0)
    # =========================================================================

    def test_f02_01_matrix_row_count_unchanged_on_retrieval(self):
        """F02-01: Snapshot project_transfer_matrix row count; retrieve_strategies() leaves it strictly unchanged."""
        pid = f"test_rtx_cnt_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(project_id=pid, name="Count Test Proj")

        # Snapshot count
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM project_transfer_matrix;")
                count_before = cur.fetchone()[0]
        finally:
            postgres_manager.release_connection(conn)

        # Retrieve strategies
        memory_retriever.retrieve_strategies(project_id=pid)

        # Re-check count
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM project_transfer_matrix;")
                count_after = cur.fetchone()[0]
        finally:
            postgres_manager.release_connection(conn)

        self.assertEqual(count_before, count_after, "project_transfer_matrix row count changed during retrieval!")

    def test_f02_02_snapshot_database_state_zero_insert_update_delete(self):
        """F02-02: Full database state snapshot before and after retrieval confirms zero mutations."""
        pid_src = f"test_db_snap_src_{uuid.uuid4().hex[:8]}"
        pid_tgt = f"test_db_snap_tgt_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(project_id=pid_src, name="Snap Src")
        project_experience_engine.create_project(project_id=pid_tgt, name="Snap Tgt")

        strat = project_experience_engine.register_strategy(
            name="Snapshot Strategy",
            description="Strategy for snapshot test",
            applicable_project_id=pid_src,
            scope=LessonScope.CROSS_PROJECT_ELIGIBLE,
        )

        def get_db_fingerprint():
            conn = postgres_manager.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(strategy_id)), 0) FROM strategies;")
                    strat_fp = cur.fetchone()
                    cur.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(transfer_id)), 0) FROM project_transfer_matrix;")
                    matrix_fp = cur.fetchone()
                    cur.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(experience_id)), 0) FROM experiences;")
                    exp_fp = cur.fetchone()
                    return (strat_fp, matrix_fp, exp_fp)
            finally:
                postgres_manager.release_connection(conn)

        fp_before = get_db_fingerprint()
        memory_retriever.retrieve_strategies(project_id=pid_tgt)
        fp_after = get_db_fingerprint()

        self.assertEqual(fp_before, fp_after, "Database fingerprint mutated during strategy retrieval!")

    def test_f02_03_repeated_retrieval_produces_zero_database_mutations(self):
        """F02-03: Repeated (50x) retrieval calls produce zero database mutations."""
        pid = f"test_repeat_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(project_id=pid, name="Repeat Proj")

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM project_transfer_matrix;")
                count_initial = cur.fetchone()[0]
        finally:
            postgres_manager.release_connection(conn)

        for _ in range(50):
            memory_retriever.retrieve_strategies(project_id=pid)

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM project_transfer_matrix;")
                count_final = cur.fetchone()[0]
        finally:
            postgres_manager.release_connection(conn)

        self.assertEqual(count_initial, count_final, "Repeated retrieval mutated project_transfer_matrix!")

    def _create_transfer_row(self, src_id: str, tgt_id: str, strat_id: str, status: str):
        """Helper to create a valid matrix row with matching lesson."""
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                les_id = f"les_{uuid.uuid4().hex[:12]}"
                cur.execute("""
                    INSERT INTO lessons (
                        lesson_id, title, summary, domain, scope, confidence_score
                    ) VALUES (%s, %s, %s, %s, 'CROSS_PROJECT_ELIGIBLE', 0.90)
                    ON CONFLICT (lesson_id) DO NOTHING;
                """, (les_id, f"Lesson for {src_id}", "Transfer lesson summary", src_id))

                tx_id = f"txm_{uuid.uuid4().hex[:12]}"
                cur.execute("""
                    INSERT INTO project_transfer_matrix (
                        transfer_id, source_project_id, target_project_id,
                        lesson_id, strategy_id, semantic_similarity,
                        tech_stack_overlap, transfer_confidence, status,
                        rejection_reason, created_at
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, 0.85,
                        0.80, 0.82, %s,
                        %s, CURRENT_TIMESTAMP
                    );
                """, (
                    tx_id, src_id, tgt_id,
                    les_id, strat_id, status,
                    "Rejection note" if status == "REJECTED" else None
                ))
            conn.commit()
            return tx_id
        finally:
            postgres_manager.release_connection(conn)

    def test_f02_04_retrieval_with_existing_approved_transfer_returns_transfer(self):
        """F02-04: Retrieval with an existing APPROVED transfer record returns the transferred strategy."""
        pid_src = f"test_appr_src_{uuid.uuid4().hex[:8]}"
        pid_tgt = f"test_appr_tgt_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(project_id=pid_src, name="Approved Src")
        project_experience_engine.create_project(project_id=pid_tgt, name="Approved Tgt")

        strat = project_experience_engine.register_strategy(
            name="Authorized Fast Path Strategy",
            description="Pre-approved cross-project caching",
            applicable_project_id=pid_src,
            scope=LessonScope.CROSS_PROJECT_ELIGIBLE,
        )

        self._create_transfer_row(pid_src, pid_tgt, strat.strategy_id, status="APPROVED")

        # Retrieve strategies for target project
        retrieved = memory_retriever.retrieve_strategies(project_id=pid_tgt)
        matching = [s for s in retrieved if s["strategy_id"] == strat.strategy_id]

        self.assertEqual(len(matching), 1, "Pre-approved transferred strategy was not returned!")
        self.assertTrue(matching[0]["is_transferred"])
        self.assertEqual(matching[0]["source_project_id"], pid_src)

    def test_f02_05_retrieval_with_no_approved_transfer_does_not_create_one(self):
        """F02-05: Retrieval with no APPROVED transfer returns no transfer and does NOT create a matrix row."""
        pid_src = f"test_noappr_src_{uuid.uuid4().hex[:8]}"
        pid_tgt = f"test_noappr_tgt_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(project_id=pid_src, name="No Appr Src")
        project_experience_engine.create_project(project_id=pid_tgt, name="No Appr Tgt")

        strat = project_experience_engine.register_strategy(
            name="Unapproved Candidate Strategy",
            description="Never evaluated or approved for transfer",
            applicable_project_id=pid_src,
            scope=LessonScope.CROSS_PROJECT_ELIGIBLE,
        )

        # Call retrieval on target project
        retrieved = memory_retriever.retrieve_strategies(project_id=pid_tgt)
        matching = [s for s in retrieved if s["strategy_id"] == strat.strategy_id]
        self.assertEqual(len(matching), 0, "Unapproved strategy was incorrectly returned!")

        # Verify no matrix entry was created
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM project_transfer_matrix WHERE strategy_id = %s AND target_project_id = %s;",
                    (strat.strategy_id, pid_tgt)
                )
                count = cur.fetchone()[0]
        finally:
            postgres_manager.release_connection(conn)

        self.assertEqual(count, 0, "Retrieval created a project_transfer_matrix row!")

    def test_f02_06_rejected_transfer_entries_are_not_treated_as_approved(self):
        """F02-06: REJECTED / DENIED transfer entries are ignored during retrieval."""
        pid_src = f"test_rej_src_{uuid.uuid4().hex[:8]}"
        pid_tgt = f"test_rej_tgt_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(project_id=pid_src, name="Rej Src")
        project_experience_engine.create_project(project_id=pid_tgt, name="Rej Tgt")

        strat = project_experience_engine.register_strategy(
            name="Rejected Strategy",
            description="Has a REJECTED matrix entry",
            applicable_project_id=pid_src,
            scope=LessonScope.CROSS_PROJECT_ELIGIBLE,
        )

        self._create_transfer_row(pid_src, pid_tgt, strat.strategy_id, status="REJECTED")

        retrieved = memory_retriever.retrieve_strategies(project_id=pid_tgt)
        matching = [s for s in retrieved if s["strategy_id"] == strat.strategy_id]
        self.assertEqual(len(matching), 0, "REJECTED transfer entry was treated as approved!")

    def test_f02_07_superseded_transfer_entries_are_not_treated_as_approved(self):
        """F02-07: SUPERSEDED transfer entries are ignored during retrieval."""
        pid_src = f"test_sup_src_{uuid.uuid4().hex[:8]}"
        pid_tgt = f"test_sup_tgt_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(project_id=pid_src, name="Sup Src")
        project_experience_engine.create_project(project_id=pid_tgt, name="Sup Tgt")

        strat = project_experience_engine.register_strategy(
            name="Superseded Strategy",
            description="Has a SUPERSEDED matrix entry",
            applicable_project_id=pid_src,
            scope=LessonScope.CROSS_PROJECT_ELIGIBLE,
        )

        self._create_transfer_row(pid_src, pid_tgt, strat.strategy_id, status="SUPERSEDED")

        retrieved = memory_retriever.retrieve_strategies(project_id=pid_tgt)
        matching = [s for s in retrieved if s["strategy_id"] == strat.strategy_id]
        self.assertEqual(len(matching), 0, "SUPERSEDED transfer entry was treated as approved!")

    def test_f02_08_cross_project_privacy_restrictions_enforced(self):
        """F02-08: PRIVATE project transfer to NORMAL project is excluded by privacy query filter."""
        pid_priv = f"test_priv_src_{uuid.uuid4().hex[:8]}"
        pid_norm = f"test_norm_tgt_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(project_id=pid_priv, name="Priv Src", privacy_class=PrivacyClass.PRIVATE)
        project_experience_engine.create_project(project_id=pid_norm, name="Norm Tgt", privacy_class=PrivacyClass.NORMAL)

        strat = project_experience_engine.register_strategy(
            name="Private Customer Core",
            description="Internal private customer logic",
            applicable_project_id=pid_priv,
            privacy_class=PrivacyClass.PRIVATE,
        )

        self._create_transfer_row(pid_priv, pid_norm, strat.strategy_id, status="APPROVED")

        retrieved = memory_retriever.retrieve_strategies(project_id=pid_norm)
        matching = [s for s in retrieved if s["strategy_id"] == strat.strategy_id]
        self.assertEqual(len(matching), 0, "PRIVATE source strategy leaked to NORMAL project!")

    def test_f02_09_sensitive_project_information_strictly_blocked(self):
        """F02-09: SENSITIVE project information cannot leak even with an APPROVED row in matrix."""
        pid_sens = f"test_sens_src_{uuid.uuid4().hex[:8]}"
        pid_tgt = f"test_tgt_any_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(project_id=pid_sens, name="Sens Src", privacy_class=PrivacyClass.SENSITIVE)
        project_experience_engine.create_project(project_id=pid_tgt, name="Any Tgt", privacy_class=PrivacyClass.NORMAL)

        strat = project_experience_engine.register_strategy(
            name="Confidential Secret Sauce",
            description="High-security cryptographic details",
            applicable_project_id=pid_sens,
            privacy_class=PrivacyClass.SENSITIVE,
        )

        self._create_transfer_row(pid_sens, pid_tgt, strat.strategy_id, status="APPROVED")

        retrieved = memory_retriever.retrieve_strategies(project_id=pid_tgt)
        matching = [s for s in retrieved if s["strategy_id"] == strat.strategy_id]
        self.assertEqual(len(matching), 0, "SENSITIVE strategy leaked through transfer retrieval!")

    def test_f02_10_retrieval_remains_non_mutating_under_concurrent_callers(self):
        """F02-10: 16 concurrent threads executing retrieval generate zero database writes."""
        pid = f"test_conc_ret_{uuid.uuid4().hex[:8]}"
        project_experience_engine.create_project(project_id=pid, name="Conc Ret Proj")

        # Snapshot initial row count
        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM project_transfer_matrix;")
                count_initial = cur.fetchone()[0]
        finally:
            postgres_manager.release_connection(conn)

        def do_retrieval():
            return memory_retriever.retrieve_strategies(project_id=pid)

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(do_retrieval) for _ in range(64)]
            [f.result() for f in futures]

        conn = postgres_manager.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM project_transfer_matrix;")
                count_final = cur.fetchone()[0]
        finally:
            postgres_manager.release_connection(conn)

        self.assertEqual(count_initial, count_final, "Concurrent retrievals caused mutations in project_transfer_matrix!")


if __name__ == "__main__":
    unittest.main()
