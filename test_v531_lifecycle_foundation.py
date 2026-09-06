"""
DOOM V5.3.1 — Memory Lifecycle Foundation Test Suite
Validates:
1. Canonical lifecycle states exist & no duplicate definitions
2. Canonical transition matrix (valid vs forbidden transitions)
3. DELETED is strictly terminal (raises MemoryAlreadyDeletedError)
4. Self-transitions are rejected
5. Typed exception hierarchy & message sanitization
6. Invalid state handling
7. Lifecycle event model validation & required fields
8. Security: zero raw memory content, query text, embeddings, or secrets
9. Bounded metadata & sanitized reason
10. Event serialization (to_dict / from_dict roundtrip)
11. Actor model stability
12. Real PostgreSQL database schema initialization & idempotency
13. Lifecycle event persistence and retrieval in PostgreSQL
14. Foreign key integrity & ON DELETE CASCADE
15. Preservation of existing memory_records & V5.2 retrieval behavior
16. Lifecycle failure isolation
"""
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

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
    MemoryLifecycleError,
    InvalidLifecycleTransitionError,
    InvalidLifecycleStateError,
    MemoryAlreadyDeletedError,
    LifecycleValidationError,
    LifecycleAuditError,
    LifecycleActor,
    LifecycleTransition,
    MemoryLifecycleEvent,
    LIFECYCLE_TRANSITIONS,
    coerce_memory_status,
    is_valid_transition,
    get_transition,
    validate_transition,
    new_lifecycle_event_id,
    memory_lifecycle,
)
from memory.repository import memory_repository
from database.postgres_db import postgres_manager


# ============================================================================
# PART 1: UNIT TESTS (Foundation, States, Matrix, Exceptions, Security)
# ============================================================================

class TestV531LifecycleUnit(unittest.TestCase):
    """Unit tests for V5.3.1 Lifecycle Foundation without external I/O."""

    def test_01_canonical_lifecycle_states_exist(self):
        """Verify the 5 approved canonical lifecycle states exist."""
        expected_states = {
            "PENDING_VERIFICATION",
            "ACTIVE",
            "SUPERSEDED",
            "ARCHIVED",
            "DELETED",
        }
        actual_states = {s.value for s in MemoryStatus}
        self.assertEqual(actual_states, expected_states)
        self.assertEqual(len(MemoryStatus), 5)

    def test_02_no_duplicate_state_definitions(self):
        """Verify single source of truth: memory.types and memory.lifecycle share same enum."""
        from memory.types import MemoryStatus as MS_Types
        from memory.lifecycle import MemoryStatus as MS_Lifecycle
        self.assertIs(MS_Types, MS_Lifecycle, "MemoryStatus must be a single canonical enum.")

    def test_03_valid_transitions_in_matrix(self):
        """Verify all canonical valid transitions return True in is_valid_transition."""
        valid_transitions = [
            (MemoryStatus.PENDING_VERIFICATION, MemoryStatus.ACTIVE),
            (MemoryStatus.PENDING_VERIFICATION, MemoryStatus.DELETED),
            (MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED),
            (MemoryStatus.ACTIVE, MemoryStatus.ARCHIVED),
            (MemoryStatus.ACTIVE, MemoryStatus.DELETED),
            (MemoryStatus.SUPERSEDED, MemoryStatus.ARCHIVED),
            (MemoryStatus.SUPERSEDED, MemoryStatus.DELETED),
            (MemoryStatus.ARCHIVED, MemoryStatus.DELETED),
        ]

        for from_s, to_s in valid_transitions:
            self.assertTrue(
                is_valid_transition(from_s, to_s),
                f"Transition {from_s.value} -> {to_s.value} should be allowed."
            )
            rule = validate_transition(from_s, to_s, reason="Valid test reason")
            self.assertIsInstance(rule, LifecycleTransition)
            self.assertTrue(rule.allowed)

    def test_04_forbidden_transitions_in_matrix(self):
        """Verify forbidden transitions raise InvalidLifecycleTransitionError."""
        forbidden_transitions = [
            (MemoryStatus.PENDING_VERIFICATION, MemoryStatus.SUPERSEDED),
            (MemoryStatus.PENDING_VERIFICATION, MemoryStatus.ARCHIVED),
            (MemoryStatus.ACTIVE, MemoryStatus.PENDING_VERIFICATION),
            (MemoryStatus.SUPERSEDED, MemoryStatus.ACTIVE),  # V5.3.1: no restoration yet
            (MemoryStatus.SUPERSEDED, MemoryStatus.PENDING_VERIFICATION),
            (MemoryStatus.ARCHIVED, MemoryStatus.ACTIVE),   # V5.3.1: no restoration yet
            (MemoryStatus.ARCHIVED, MemoryStatus.SUPERSEDED),
            (MemoryStatus.ARCHIVED, MemoryStatus.PENDING_VERIFICATION),
        ]

        for from_s, to_s in forbidden_transitions:
            self.assertFalse(
                is_valid_transition(from_s, to_s),
                f"Transition {from_s.value} -> {to_s.value} should be forbidden."
            )
            with self.assertRaises(InvalidLifecycleTransitionError):
                validate_transition(from_s, to_s)

    def test_05_deleted_is_terminal_no_outgoing(self):
        """Verify DELETED has zero outgoing transitions and raises MemoryAlreadyDeletedError."""
        all_targets = [
            MemoryStatus.PENDING_VERIFICATION,
            MemoryStatus.ACTIVE,
            MemoryStatus.SUPERSEDED,
            MemoryStatus.ARCHIVED,
            MemoryStatus.DELETED,
        ]

        for target in all_targets:
            self.assertFalse(
                is_valid_transition(MemoryStatus.DELETED, target),
                f"DELETED -> {target.value} must not be a valid transition."
            )
            with self.assertRaises(MemoryAlreadyDeletedError) as ctx:
                validate_transition(MemoryStatus.DELETED, target)
            self.assertIn("DELETED is a terminal state", str(ctx.exception))

    def test_06_self_transition_forbidden(self):
        """Verify transitioning a state to itself is rejected with LifecycleValidationError."""
        for state in MemoryStatus:
            self.assertFalse(is_valid_transition(state, state))
            # If DELETED, MemoryAlreadyDeletedError is raised first (which subclasses MemoryLifecycleError)
            if state == MemoryStatus.DELETED:
                with self.assertRaises(MemoryAlreadyDeletedError):
                    validate_transition(state, state)
            else:
                with self.assertRaises(LifecycleValidationError):
                    validate_transition(state, state)

    def test_07_typed_exception_hierarchy(self):
        """Verify all lifecycle exceptions inherit from MemoryLifecycleError."""
        self.assertTrue(issubclass(InvalidLifecycleStateError, MemoryLifecycleError))
        self.assertTrue(issubclass(InvalidLifecycleTransitionError, MemoryLifecycleError))
        self.assertTrue(issubclass(MemoryAlreadyDeletedError, InvalidLifecycleTransitionError))
        self.assertTrue(issubclass(MemoryAlreadyDeletedError, MemoryLifecycleError))
        self.assertTrue(issubclass(LifecycleValidationError, MemoryLifecycleError))
        self.assertTrue(issubclass(LifecycleAuditError, MemoryLifecycleError))

    def test_08_exception_sanitization_no_raw_leakage(self):
        """Verify exception message sanitizes linebreaks and maintains safe content."""
        ex = MemoryLifecycleError("Safe message\r\nwith newline injection", memory_id="mem_123")
        self.assertEqual(ex.memory_id, "mem_123")
        self.assertNotIn("\r", str(ex))
        self.assertNotIn("\n", str(ex))
        self.assertIn("Safe message  with newline injection", str(ex))

    def test_09_invalid_state_handling(self):
        """Verify coerce_memory_status rejects malformed, unknown, or wrong-type states."""
        self.assertEqual(coerce_memory_status("ACTIVE"), MemoryStatus.ACTIVE)
        self.assertEqual(coerce_memory_status("active"), MemoryStatus.ACTIVE)
        self.assertEqual(coerce_memory_status(MemoryStatus.ARCHIVED), MemoryStatus.ARCHIVED)

        with self.assertRaises(InvalidLifecycleStateError):
            coerce_memory_status("UNKNOWN_STATE_XYZ")

        with self.assertRaises(InvalidLifecycleStateError):
            coerce_memory_status(12345)

        with self.assertRaises(InvalidLifecycleStateError):
            coerce_memory_status(None)

    def test_10_lifecycle_actor_model(self):
        """Verify LifecycleActor enum has canonical actors and stable values."""
        self.assertEqual(LifecycleActor.USER.value, "USER")
        self.assertEqual(LifecycleActor.SYSTEM.value, "SYSTEM")
        self.assertEqual(LifecycleActor.TASK.value, "TASK")
        self.assertEqual(LifecycleActor.LIFECYCLE_ENGINE.value, "LIFECYCLE_ENGINE")

    def test_11_event_model_creation_and_defaults(self):
        """Verify MemoryLifecycleEvent assigns deterministic defaults and accepts string/enum."""
        evt = MemoryLifecycleEvent(
            memory_id="mem_abc123",
            previous_status="PENDING_VERIFICATION",
            new_status=MemoryStatus.ACTIVE,
            transition_reason="Verification confirmed",
            actor=LifecycleActor.TASK.value,
        )
        self.assertTrue(evt.event_id.startswith("evt_"))
        self.assertEqual(evt.memory_id, "mem_abc123")
        self.assertEqual(evt.previous_status, MemoryStatus.PENDING_VERIFICATION)
        self.assertEqual(evt.new_status, MemoryStatus.ACTIVE)
        self.assertEqual(evt.transition_reason, "Verification confirmed")
        self.assertEqual(evt.actor, "TASK")
        self.assertIsInstance(evt.metadata, dict)
        self.assertTrue(len(evt.created_at) > 0)

    def test_12_event_rejects_raw_memory_content_in_metadata(self):
        """Verify security invariant: raw content, query, embedding, secrets raise LifecycleAuditError."""
        forbidden_keys = [
            "content", "raw_content", "query", "raw_query",
            "embedding", "vector", "password", "secret",
            "token", "api_key", "bearer", "authorization",
        ]

        for bad_key in forbidden_keys:
            with self.assertRaises(LifecycleAuditError, msg=f"Key '{bad_key}' should be rejected"):
                MemoryLifecycleEvent(
                    memory_id="mem_sec01",
                    previous_status=MemoryStatus.ACTIVE,
                    new_status=MemoryStatus.ARCHIVED,
                    metadata={bad_key: "sensitive_data_value"},
                )

    def test_13_event_bounded_reason_sanitization(self):
        """Verify event reason is bounded to 255 chars and stripped of linebreaks."""
        long_reason = "A" * 300
        evt = MemoryLifecycleEvent(
            memory_id="mem_bound01",
            previous_status=MemoryStatus.ACTIVE,
            new_status=MemoryStatus.DELETED,
            transition_reason="Line 1\r\nLine 2\n" + long_reason,
        )
        self.assertNotIn("\r", evt.transition_reason)
        self.assertNotIn("\n", evt.transition_reason)
        self.assertLessEqual(len(evt.transition_reason), 255)
        self.assertTrue(evt.transition_reason.endswith("..."))

    def test_14_event_serialization_roundtrip(self):
        """Verify to_dict() and from_dict() serialization preserves all fields."""
        evt = MemoryLifecycleEvent(
            memory_id="mem_ser01",
            previous_status=MemoryStatus.ACTIVE,
            new_status=MemoryStatus.SUPERSEDED,
            transition_reason="Superseded by mem_ser02",
            actor=LifecycleActor.SYSTEM.value,
            related_memory_id="mem_ser02",
            task_id="task_999",
            correlation_id="corr_888",
            confidence_before="HIGH",
            confidence_after="MEDIUM",
            importance_before=0.85,
            importance_after=0.45,
            metadata={"source_rule": "V5.3.1_test", "version": 1},
        )

        d = evt.to_dict()
        self.assertEqual(d["memory_id"], "mem_ser01")
        self.assertEqual(d["previous_status"], "ACTIVE")
        self.assertEqual(d["new_status"], "SUPERSEDED")
        self.assertEqual(d["related_memory_id"], "mem_ser02")
        self.assertEqual(d["importance_before"], 0.85)

        restored = MemoryLifecycleEvent.from_dict(d)
        self.assertEqual(restored.event_id, evt.event_id)
        self.assertEqual(restored.memory_id, evt.memory_id)
        self.assertEqual(restored.previous_status, evt.previous_status)
        self.assertEqual(restored.new_status, evt.new_status)
        self.assertEqual(restored.actor, evt.actor)
        self.assertEqual(restored.related_memory_id, evt.related_memory_id)
        self.assertEqual(restored.importance_before, evt.importance_before)
        self.assertEqual(restored.metadata, evt.metadata)


# ============================================================================
# PART 2: INTEGRATION TESTS (Real PostgreSQL Database & Retrieval Safety)
# ============================================================================

class TestV531LifecycleIntegration(unittest.TestCase):
    """Integration tests validating real PostgreSQL schema, CRUD, and retrieval invariance."""

    @classmethod
    def setUpClass(cls):
        """Ensure PostgreSQL is accessible and initialized."""
        conn = postgres_manager.get_connection()
        if not conn:
            raise unittest.SkipTest("PostgreSQL is not available on localhost:5432.")
        postgres_manager.release_connection(conn)

    def test_15_database_schema_exists_and_idempotent(self):
        """Verify memory_lifecycle_events table exists and schema initialization is idempotent."""
        # Test idempotency by calling initialization again
        postgres_manager._create_tables()

        conn = postgres_manager.get_connection()
        self.assertIsNotNone(conn)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_name = 'memory_lifecycle_events'
                    ORDER BY ordinal_position;
                """)
                columns = {r[0]: r[1] for r in cur.fetchall()}
                expected_cols = [
                    "event_id", "memory_id", "previous_status", "new_status",
                    "transition_reason", "actor", "related_memory_id",
                    "source_event_id", "task_id", "correlation_id",
                    "confidence_before", "confidence_after",
                    "importance_before", "importance_after",
                    "metadata", "created_at"
                ]
                for col in expected_cols:
                    self.assertIn(col, columns, f"Column {col} must exist in memory_lifecycle_events table.")
        finally:
            postgres_manager.release_connection(conn)

    def test_16_store_and_retrieve_lifecycle_event(self):
        """Store a real MemoryRecord and corresponding MemoryLifecycleEvent in PostgreSQL."""
        record = MemoryRecord(
            content="DOOM V5.3.1 Lifecycle integration fact",
            memory_type=MemoryType.SEMANTIC,
            source=MemorySource.VERIFIED_TASK,
            status=MemoryStatus.ACTIVE,
        )
        stored_rec = memory_repository.store(record)
        self.assertTrue(stored_rec, "Record should store successfully in PostgreSQL.")

        # Record lifecycle transition event
        event = MemoryLifecycleEvent(
            memory_id=record.memory_id,
            previous_status=MemoryStatus.PENDING_VERIFICATION,
            new_status=MemoryStatus.ACTIVE,
            transition_reason="Verified by test_16",
            actor=LifecycleActor.TASK.value,
            task_id="task_test_16",
            correlation_id="corr_test_16",
            importance_before=0.5,
            importance_after=0.9,
            metadata={"phase": "V5.3.1"},
        )

        stored_evt = memory_repository.store_lifecycle_event(event)
        self.assertTrue(stored_evt, "Lifecycle event should store successfully.")

        # Retrieve events
        events = memory_repository.get_lifecycle_events(record.memory_id)
        self.assertGreaterEqual(len(events), 1)
        first_evt = events[0]
        self.assertEqual(first_evt.event_id, event.event_id)
        self.assertEqual(first_evt.memory_id, record.memory_id)
        self.assertEqual(first_evt.previous_status, MemoryStatus.PENDING_VERIFICATION)
        self.assertEqual(first_evt.new_status, MemoryStatus.ACTIVE)
        self.assertEqual(first_evt.actor, "TASK")
        self.assertEqual(first_evt.importance_after, 0.9)

        # Retrieve by ID
        fetched_by_id = memory_repository.get_lifecycle_event_by_id(event.event_id)
        self.assertIsNotNone(fetched_by_id)
        self.assertEqual(fetched_by_id.event_id, event.event_id)

    def test_17_foreign_key_and_cascade_delete(self):
        """Verify lifecycle events are cascade deleted when parent record is deleted from DB."""
        record = MemoryRecord(
            content="DOOM V5.3.1 Cascade test memory",
            memory_type=MemoryType.SEMANTIC,
            status=MemoryStatus.ACTIVE,
        )
        memory_repository.store(record)

        event = MemoryLifecycleEvent(
            memory_id=record.memory_id,
            previous_status=MemoryStatus.ACTIVE,
            new_status=MemoryStatus.ARCHIVED,
            transition_reason="Cascade test event",
        )
        memory_repository.store_lifecycle_event(event)
        self.assertEqual(memory_repository.count_lifecycle_events(record.memory_id), 1)

        # Physically delete the parent memory record in PostgreSQL
        conn = postgres_manager.get_connection()
        self.assertIsNotNone(conn)
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM memory_records WHERE memory_id = %s;", (record.memory_id,))
            conn.commit()
        finally:
            postgres_manager.release_connection(conn)

        # Verify child lifecycle event was cascade deleted
        self.assertEqual(memory_repository.count_lifecycle_events(record.memory_id), 0)

    def test_18_existing_memory_records_preserved(self):
        """Verify existing memory_records table continues to function normally with all columns."""
        unique_str = uuid.uuid4().hex[:8]
        record = MemoryRecord(
            content=f"Durable fact preservation check {unique_str}",
            memory_type=MemoryType.SEMANTIC,
            source=MemorySource.USER_EXPLICIT,
            status=MemoryStatus.ACTIVE,
            confidence=ConfidenceLevel.HIGH,
            importance=0.95,
        )
        self.assertTrue(memory_repository.store(record))

        fetched = memory_repository.get_by_id(record.memory_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.content, record.content)
        self.assertEqual(fetched.status, MemoryStatus.ACTIVE)
        self.assertEqual(fetched.confidence, ConfidenceLevel.HIGH)

    def test_19_existing_v52_retrieval_behavior_preserved(self):
        """Verify retrieval invariant: ONLY ACTIVE memories are returned by search/retrieval."""
        tag = f"retrieval_inv_{uuid.uuid4().hex[:6]}"

        rec_active = MemoryRecord(
            content=f"Active memory record for {tag}",
            memory_type=MemoryType.SEMANTIC,
            status=MemoryStatus.ACTIVE,
            tags=[tag],
        )
        rec_superseded = MemoryRecord(
            content=f"Superseded memory record for {tag}",
            memory_type=MemoryType.SEMANTIC,
            status=MemoryStatus.SUPERSEDED,
            tags=[tag],
        )
        rec_archived = MemoryRecord(
            content=f"Archived memory record for {tag}",
            memory_type=MemoryType.SEMANTIC,
            status=MemoryStatus.ARCHIVED,
            tags=[tag],
        )
        rec_deleted = MemoryRecord(
            content=f"Deleted memory record for {tag}",
            memory_type=MemoryType.SEMANTIC,
            status=MemoryStatus.DELETED,
            tags=[tag],
        )
        rec_pending = MemoryRecord(
            content=f"Pending verification record for {tag}",
            memory_type=MemoryType.SEMANTIC,
            status=MemoryStatus.PENDING_VERIFICATION,
            tags=[tag],
        )

        for r in [rec_active, rec_superseded, rec_archived, rec_deleted, rec_pending]:
            memory_repository.store(r)

        # Standard search (defaults to ACTIVE status)
        results = memory_repository.search(query=tag)
        result_ids = [r.memory_id for r in results]

        self.assertIn(rec_active.memory_id, result_ids)
        self.assertNotIn(rec_superseded.memory_id, result_ids)
        self.assertNotIn(rec_archived.memory_id, result_ids)
        self.assertNotIn(rec_deleted.memory_id, result_ids)
        self.assertNotIn(rec_pending.memory_id, result_ids)

    def test_20_lifecycle_foundation_failure_isolation_fault_injection(self):
        """
        [FAULT INJECTION]: Verify that database failure during lifecycle audit logging
        is safely isolated and returns False without crashing the application.
        """
        event = MemoryLifecycleEvent(
            memory_id="mem_nonexistent_xyz",
            previous_status=MemoryStatus.ACTIVE,
            new_status=MemoryStatus.DELETED,
            transition_reason="Failure test",
        )
        # Attempting to store an event with non-existent memory_id violates foreign key constraint
        # store_lifecycle_event must handle this gracefully, roll back, and return False
        result = memory_repository.store_lifecycle_event(event)
        self.assertFalse(result, "store_lifecycle_event must isolate database errors and return False.")

    def test_21_lifecycle_manager_supersede_and_audit(self):
        """Verify MemoryLifecycleManager.supersede updates status, links supersedes_memory_id, and writes audit event."""
        rec_old = MemoryRecord(
            content="Base knowledge item to supersede",
            memory_type=MemoryType.SEMANTIC,
            status=MemoryStatus.ACTIVE,
        )
        memory_repository.store(rec_old)

        rec_new = MemoryRecord(
            content="Updated knowledge item superseding base",
            memory_type=MemoryType.SEMANTIC,
            status=MemoryStatus.ACTIVE,
        )

        ok = memory_lifecycle.supersede(
            old_memory_id=rec_old.memory_id,
            new_record=rec_new,
            reason="Upgraded with higher accuracy",
            actor=LifecycleActor.USER.value,
        )
        self.assertTrue(ok)

        # Verify old record is SUPERSEDED
        old_fetched = memory_repository.get_by_id(rec_old.memory_id)
        self.assertIsNotNone(old_fetched)
        self.assertEqual(old_fetched.status, MemoryStatus.SUPERSEDED)

        # Verify new record links to old
        new_fetched = memory_repository.get_by_id(rec_new.memory_id)
        self.assertIsNotNone(new_fetched)
        self.assertEqual(new_fetched.supersedes_memory_id, rec_old.memory_id)
        self.assertEqual(new_fetched.status, MemoryStatus.ACTIVE)

        # Verify audit event was logged
        events = memory_repository.get_lifecycle_events(rec_old.memory_id)
        self.assertGreaterEqual(len(events), 1)
        latest = events[0]
        self.assertEqual(latest.previous_status, MemoryStatus.ACTIVE)
        self.assertEqual(latest.new_status, MemoryStatus.SUPERSEDED)
        self.assertEqual(latest.related_memory_id, rec_new.memory_id)
        self.assertEqual(latest.actor, "USER")

    def test_22_lifecycle_manager_archive_and_delete_audit(self):
        """Verify MemoryLifecycleManager.archive and delete update status and write audit events."""
        rec = MemoryRecord(
            content="Archival and deletion test record",
            memory_type=MemoryType.SEMANTIC,
            status=MemoryStatus.ACTIVE,
        )
        memory_repository.store(rec)

        # Archive
        archived_ok = memory_lifecycle.archive(rec.memory_id, reason="Project milestone archived")
        self.assertTrue(archived_ok)
        rec_archived = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(rec_archived.status, MemoryStatus.ARCHIVED)

        # Delete
        deleted_ok = memory_lifecycle.delete(rec.memory_id, reason="Explicit user purge")
        self.assertTrue(deleted_ok)
        rec_deleted = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(rec_deleted.status, MemoryStatus.DELETED)

        # Verify 2 audit events in reverse chronological order
        events = memory_repository.get_lifecycle_events(rec.memory_id)
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0].new_status, MemoryStatus.DELETED)
        self.assertEqual(events[0].previous_status, MemoryStatus.ARCHIVED)
        self.assertEqual(events[1].new_status, MemoryStatus.ARCHIVED)
        self.assertEqual(events[1].previous_status, MemoryStatus.ACTIVE)

    def test_23_lifecycle_manager_rejects_illegal_transition(self):
        """Verify MemoryLifecycleManager rejects illegal transitions on terminal DELETED records."""
        rec = MemoryRecord(
            content="Terminal deletion test record",
            memory_type=MemoryType.SEMANTIC,
            status=MemoryStatus.DELETED,
        )
        memory_repository.store(rec)

        rec_new = MemoryRecord(
            content="Attempt to supersede terminal record",
            memory_type=MemoryType.SEMANTIC,
        )
        # Cannot supersede a DELETED record
        result = memory_lifecycle.supersede(rec.memory_id, rec_new)
        self.assertFalse(result, "Superseding a DELETED record must be rejected.")

        # Cannot archive a DELETED record
        result_arch = memory_lifecycle.archive(rec.memory_id)
        self.assertFalse(result_arch, "Archiving a DELETED record must be rejected.")

    def test_24_get_transition_metadata_and_inspection(self):
        """Verify get_transition inspects rules and returns None on invalid states."""
        rule_valid = get_transition(MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED)
        self.assertIsNotNone(rule_valid)
        self.assertTrue(rule_valid.allowed)
        self.assertTrue(len(rule_valid.description) > 0)

        rule_forbidden = get_transition(MemoryStatus.DELETED, MemoryStatus.ACTIVE)
        self.assertIsNotNone(rule_forbidden)
        self.assertFalse(rule_forbidden.allowed)

        rule_invalid = get_transition("UNKNOWN_STATE", MemoryStatus.ACTIVE)
        self.assertIsNone(rule_invalid)

    def test_25_count_lifecycle_events(self):
        """Verify count_lifecycle_events accurately counts total and per-memory events."""
        total_count = memory_repository.count_lifecycle_events()
        self.assertGreater(total_count, 0)

        # Count for nonexistent memory should be 0
        self.assertEqual(memory_repository.count_lifecycle_events("mem_nonexistent_000"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

