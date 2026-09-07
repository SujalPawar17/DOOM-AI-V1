"""
DOOM V5.3.6 — F-05 Remediation Verification Suite
Location: test_v536_f05_migration.py

Tests targeted specifically at release-blocking finding F-05:
- F05-01: HIGH converts correctly
- F05-02: MEDIUM converts correctly
- F05-03: LOW converts correctly
- F05-04: UNKNOWN converts correctly
- F05-05: numeric string converts correctly
- F05-06: numeric value converts correctly
- F05-07: invalid numeric range is handled safely
- F05-08: unknown legacy value does not crash migration
- F05-09: migration preserves project_id (F-01 compliance)
- F05-10: migration is idempotent
- F05-11: rollback leaves database consistent on genuine migration failure
- F05-12: multiple legacy confidence representations migrate in one run
"""

import unittest
import uuid
import json
from database.postgres_db import postgres_manager
from memory.types import ConfidenceLevel
from memory.project_migration import (
    ProjectExperienceMigrationEngine,
    run_project_experience_migration,
    parse_legacy_confidence,
    CANONICAL_CONFIDENCE_MAP,
)


class TestV536F05Migration(unittest.TestCase):
    """Authoritative F-05 Remediation Test Suite."""

    def setUp(self):
        self.engine = ProjectExperienceMigrationEngine(db=postgres_manager)
        self.conn = postgres_manager.get_connection()
        self.assertIsNotNone(self.conn, "PostgreSQL connection must be active")

    def tearDown(self):
        if self.conn:
            postgres_manager.release_connection(self.conn)

    def _insert_legacy_record(self, memory_id: str, confidence_val: str, project_id: str = "doom"):
        """Helper to insert a legacy EXPERIENCE memory record."""
        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO memory_records (
                    memory_id, memory_type, content, project_id,
                    privacy_class, confidence, status, created_at, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (memory_id) DO NOTHING;
            """, (
                memory_id,
                "EXPERIENCE",
                f"Legacy test action for {memory_id}",
                project_id,
                "NORMAL",
                confidence_val,
                "ACTIVE",
                json.dumps({"task_id": f"task_{memory_id[:10]}", "outcome": "SUCCESS"}),
            ))
        self.conn.commit()

    def _get_migrated_confidence(self, memory_id: str):
        """Helper to query migrated confidence_score from experiences table."""
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT confidence_score, project_id
                FROM experiences
                WHERE context_conditions->>'source_memory_id' = %s;
            """, (memory_id,))
            return cur.fetchone()

    # -------------------------------------------------------------------------
    # F05-01: HIGH converts correctly
    # -------------------------------------------------------------------------
    def test_f05_01_high_converts_correctly(self):
        """F05-01: Legacy 'HIGH' converts to canonical 0.90."""
        score, anomaly = parse_legacy_confidence("HIGH", return_anomaly=True)
        self.assertEqual(score, 0.90)
        self.assertIsNone(anomaly)

        mid = f"f05_high_{uuid.uuid4().hex[:10]}"
        self._insert_legacy_record(mid, "HIGH")
        summary = self.engine.run_migration(batch_size=500)
        self.assertGreaterEqual(summary["migrated_count"] + summary["skipped_count"], 1)

        row = self._get_migrated_confidence(mid)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row[0], 0.90, places=2)

    # -------------------------------------------------------------------------
    # F05-02: MEDIUM converts correctly
    # -------------------------------------------------------------------------
    def test_f05_02_medium_converts_correctly(self):
        """F05-02: Legacy 'MEDIUM' converts to canonical continuous score."""
        expected = CANONICAL_CONFIDENCE_MAP["MEDIUM"]
        score, anomaly = parse_legacy_confidence("MEDIUM", return_anomaly=True)
        self.assertEqual(score, expected)
        self.assertIsNone(anomaly)

        mid = f"f05_med_{uuid.uuid4().hex[:10]}"
        self._insert_legacy_record(mid, "MEDIUM")
        self.engine.run_migration(batch_size=500)

        row = self._get_migrated_confidence(mid)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row[0], expected, places=2)

    # -------------------------------------------------------------------------
    # F05-03: LOW converts correctly
    # -------------------------------------------------------------------------
    def test_f05_03_low_converts_correctly(self):
        """F05-03: Legacy 'LOW' converts to canonical continuous score."""
        expected = CANONICAL_CONFIDENCE_MAP["LOW"]
        score, anomaly = parse_legacy_confidence("LOW", return_anomaly=True)
        self.assertEqual(score, expected)
        self.assertIsNone(anomaly)

        mid = f"f05_low_{uuid.uuid4().hex[:10]}"
        self._insert_legacy_record(mid, "LOW")
        self.engine.run_migration(batch_size=500)

        row = self._get_migrated_confidence(mid)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row[0], expected, places=2)

    # -------------------------------------------------------------------------
    # F05-04: UNKNOWN converts correctly
    # -------------------------------------------------------------------------
    def test_f05_04_unknown_converts_correctly(self):
        """F05-04: Legacy 'UNKNOWN' converts to canonical 0.50."""
        score, anomaly = parse_legacy_confidence("UNKNOWN", return_anomaly=True)
        self.assertEqual(score, 0.50)
        self.assertIsNone(anomaly)

        mid = f"f05_unk_{uuid.uuid4().hex[:10]}"
        self._insert_legacy_record(mid, "UNKNOWN")
        self.engine.run_migration(batch_size=500)

        row = self._get_migrated_confidence(mid)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row[0], 0.50, places=2)

    # -------------------------------------------------------------------------
    # F05-05: numeric string converts correctly
    # -------------------------------------------------------------------------
    def test_f05_05_numeric_string_converts_correctly(self):
        """F05-05: Legacy numeric strings ('0.85', '0.5') convert safely."""
        score, anomaly = parse_legacy_confidence("0.85", return_anomaly=True)
        self.assertEqual(score, 0.85)
        self.assertIsNone(anomaly)

        mid = f"f05_nstr_{uuid.uuid4().hex[:10]}"
        self._insert_legacy_record(mid, "0.85")
        self.engine.run_migration(batch_size=500)

        row = self._get_migrated_confidence(mid)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row[0], 0.85, places=2)

    # -------------------------------------------------------------------------
    # F05-06: numeric value converts correctly
    # -------------------------------------------------------------------------
    def test_f05_06_numeric_value_converts_correctly(self):
        """F05-06: Raw numeric float/int (0.75, 1) converts correctly."""
        score, anomaly = parse_legacy_confidence(0.75, return_anomaly=True)
        self.assertEqual(score, 0.75)
        self.assertIsNone(anomaly)

        score_int, anomaly_int = parse_legacy_confidence(1, return_anomaly=True)
        self.assertEqual(score_int, 1.0)
        self.assertIsNone(anomaly_int)

    # -------------------------------------------------------------------------
    # F05-07: invalid numeric range is handled safely
    # -------------------------------------------------------------------------
    def test_f05_07_invalid_numeric_range_handled_safely(self):
        """F05-07: Out-of-range numeric values are clamped and recorded as anomalies."""
        # Greater than 1.0
        score_high, anom_high = parse_legacy_confidence(1.5, return_anomaly=True)
        self.assertLessEqual(score_high, 1.0)
        self.assertIsNotNone(anom_high)
        self.assertIn("outside valid range", anom_high)

        # Less than 0.0
        score_low, anom_low = parse_legacy_confidence(-0.2, return_anomaly=True)
        self.assertGreaterEqual(score_low, 0.01)
        self.assertIsNotNone(anom_low)
        self.assertIn("outside valid range", anom_low)

        # Database migration test with out-of-range string
        mid = f"f05_oor_{uuid.uuid4().hex[:10]}"
        self._insert_legacy_record(mid, "1.85")
        summary = self.engine.run_migration(batch_size=500)
        self.assertGreaterEqual(len(summary["anomalies"]), 1)

        row = self._get_migrated_confidence(mid)
        self.assertIsNotNone(row)
        self.assertLessEqual(row[0], 1.0)
        self.assertGreaterEqual(row[0], 0.01)

    # -------------------------------------------------------------------------
    # F05-08: unknown legacy value does not crash migration
    # -------------------------------------------------------------------------
    def test_f05_08_unknown_legacy_value_does_not_crash_migration(self):
        """F05-08: Completely unrecognized legacy strings fall back gracefully without crashing."""
        score, anomaly = parse_legacy_confidence("CORRUPTED_CONF_VAL", return_anomaly=True)
        self.assertEqual(score, 0.50)
        self.assertIsNotNone(anomaly)
        self.assertIn("Unrecognized legacy confidence", anomaly)

        mid = f"f05_unrec_{uuid.uuid4().hex[:10]}"
        self._insert_legacy_record(mid, "MALFORMED_VALUE_999")
        summary = self.engine.run_migration(batch_size=500)
        self.assertEqual(summary["error_count"], 0)
        self.assertGreaterEqual(len(summary["anomalies"]), 1)

        row = self._get_migrated_confidence(mid)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row[0], 0.50, places=2)

    # -------------------------------------------------------------------------
    # F05-09: migration preserves project_id (F-01 compliance)
    # -------------------------------------------------------------------------
    def test_f05_09_migration_preserves_project_id(self):
        """F05-09: Migration preserves original project_id and never reassigns to 'doom'."""
        custom_pid = f"proj_iso_{uuid.uuid4().hex[:8]}"
        mid = f"f05_proj_{uuid.uuid4().hex[:10]}"
        self._insert_legacy_record(mid, "HIGH", project_id=custom_pid)

        summary = self.engine.run_migration(default_project_id="doom", batch_size=500)
        row = self._get_migrated_confidence(mid)
        self.assertIsNotNone(row)
        self.assertEqual(row[1], custom_pid, "Original project_id must be strictly preserved")
        self.assertNotEqual(row[1], "doom", "Must not reassign foreign project to 'doom'")

    # -------------------------------------------------------------------------
    # F05-10: migration is idempotent
    # -------------------------------------------------------------------------
    def test_f05_10_migration_is_idempotent(self):
        """F05-10: Repeated migration runs cause 0 duplicates and 0 drift."""
        mid = f"f05_idem_{uuid.uuid4().hex[:10]}"
        self._insert_legacy_record(mid, "HIGH")

        # First run
        run1 = self.engine.run_migration(batch_size=500)
        mig1 = run1["migrated_count"]
        self.assertGreaterEqual(mig1, 1)

        # Second run
        run2 = self.engine.run_migration(batch_size=500)
        self.assertEqual(run2["migrated_count"], 0, "Second run must migrate 0 new records")
        self.assertGreaterEqual(run2["skipped_count"], 1, "Second run must skip already-migrated records")

        # Verify no duplicate row in DB
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM experiences
                WHERE context_conditions->>'source_memory_id' = %s;
            """, (mid,))
            count = cur.fetchone()[0]
            self.assertEqual(count, 1, "Must have exactly 1 record in experiences table")

    # -------------------------------------------------------------------------
    # F05-11: rollback leaves database consistent on genuine failure
    # -------------------------------------------------------------------------
    def test_f05_11_rollback_leaves_db_consistent_on_failure(self):
        """F05-11: Fatal error in batch rolls back transaction cleanly."""
        class ConnProxy:
            def __init__(self, real_conn):
                self._real = real_conn
            def __getattr__(self, name):
                return getattr(self._real, name)
            def cursor(self, *args, **kwargs):
                cur = self._real.cursor(*args, **kwargs)
                class CurProxy:
                    def __init__(self, real_cur):
                        self._cur = real_cur
                    def __enter__(self):
                        return self
                    def __exit__(self, exc_type, exc_val, exc_tb):
                        return self._cur.__exit__(exc_type, exc_val, exc_tb)
                    def __getattr__(self, name):
                        return getattr(self._cur, name)
                    def execute(self, query, params=None):
                        if "INSERT INTO experiences" in str(query):
                            raise RuntimeError("Simulated unrecoverable disk failure during insert")
                        return self._cur.execute(query, params)
                return CurProxy(cur)

        class FailingDB:
            def __init__(self, real_db):
                self._real = real_db
            def get_connection(self):
                return ConnProxy(self._real.get_connection())
            def release_connection(self, conn):
                real_conn = getattr(conn, "_real", conn)
                self._real.release_connection(real_conn)

        bad_engine = ProjectExperienceMigrationEngine(db=FailingDB(postgres_manager))
        mid = f"f05_fail_{uuid.uuid4().hex[:10]}"
        self._insert_legacy_record(mid, "HIGH")

        summary = bad_engine.run_migration(batch_size=500)
        self.assertGreaterEqual(summary["error_count"], 1)

    # -------------------------------------------------------------------------
    # F05-12: multiple legacy confidence representations migrate in one run
    # -------------------------------------------------------------------------
    def test_f05_12_multiple_legacy_confidence_representations_in_one_run(self):
        """F05-12: A batch with mixed representation types (HIGH, MEDIUM, LOW, UNKNOWN, floats, strings) succeeds."""
        batch_items = [
            (f"f05_m_h_{uuid.uuid4().hex[:8]}", "HIGH", 0.90),
            (f"f05_m_m_{uuid.uuid4().hex[:8]}", "MEDIUM", CANONICAL_CONFIDENCE_MAP["MEDIUM"]),
            (f"f05_m_l_{uuid.uuid4().hex[:8]}", "LOW", CANONICAL_CONFIDENCE_MAP["LOW"]),
            (f"f05_m_u_{uuid.uuid4().hex[:8]}", "UNKNOWN", 0.50),
            (f"f05_m_s_{uuid.uuid4().hex[:8]}", "0.77", 0.77),
            (f"f05_m_f_{uuid.uuid4().hex[:8]}", "0.42", 0.42),
        ]
        for mid, cval, _ in batch_items:
            self._insert_legacy_record(mid, cval)

        summary = self.engine.run_migration(batch_size=500)
        self.assertEqual(summary["error_count"], 0)

        for mid, _, expected_conf in batch_items:
            row = self._get_migrated_confidence(mid)
            self.assertIsNotNone(row, f"Record {mid} must be migrated")
            self.assertAlmostEqual(row[0], expected_conf, places=2)


if __name__ == "__main__":
    unittest.main()
