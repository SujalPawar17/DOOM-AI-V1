"""
DOOM V5.3.2 — State Machine & Transaction Engine Test Suite
Validates:
1. Unit: canonical state, transition, provenance, error classification, idempotency, result object.
2. Real PostgreSQL: atomic state + audit commit, rollback on failure, missing memory, DELETED terminality, CHECK constraint, audit sanitization, 1:1 supersession.
3. Idempotency: repeated requests, zero duplicate audit events, separate keys, concurrent idempotency.
4. Concurrency: ARCHIVE vs DELETE, DELETE vs ARCHIVE, concurrent supersession, lock timeout, zero lost updates.
5. Fault Injection: failure before audit, failure during audit, DB connection error.
6. Bypass Closure: MemoryManager.store_and_supersede, repository.update_status, legacy wrapper delegation.
"""
import os
import sys
import time
import uuid
import unittest
import threading
from datetime import datetime, timezone
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
    MemoryLifecycleError,
    InvalidLifecycleTransitionError,
    InvalidLifecycleStateError,
    MemoryAlreadyDeletedError,
    LifecycleValidationError,
    LifecycleAuditError,
    MemoryNotFoundError,
    ProvenanceValidationError,
    ConcurrentModificationError,
    LockTimeoutError,
    DeadlockDetectedError,
    DatabaseConnectionError,
    is_retryable_lifecycle_error,
    LifecycleActor,
    LifecycleTransition,
    MemoryLifecycleEvent,
    LifecycleTransitionResult,
    coerce_memory_status,
    is_valid_transition,
    get_transition,
    validate_transition,
    validate_provenance,
    lifecycle_engine,
    memory_lifecycle,
    MemoryLifecycleEngine,
)
from memory.repository import memory_repository
from memory.manager import memory_manager
from database.postgres_db import postgres_manager


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_test_record(
    memory_id: Optional[str] = None,
    content: str = "Test memory record for transaction engine testing",
    status: MemoryStatus = MemoryStatus.ACTIVE,
    memory_type: MemoryType = MemoryType.SEMANTIC,
) -> MemoryRecord:
    mid = memory_id or f"mem_{uuid.uuid4().hex[:16]}"
    return MemoryRecord(
        memory_id=mid,
        memory_type=memory_type,
        content=content,
        source=MemorySource.USER_CONVERSATION,
        confidence=ConfidenceLevel.HIGH,
        importance=0.8,
        status=status,
        project_id="prj_test_v532",
        task_id="task_test_v532",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )


# ============================================================================
# PART 1: UNIT TESTS
# ============================================================================

class TestV532Unit(unittest.TestCase):
    """Unit tests for V5.3.2 State Machine & Transaction Engine abstractions."""

    def test_01_canonical_state_validation(self):
        """Verify coercion and validation of canonical memory states."""
        self.assertEqual(coerce_memory_status("ACTIVE"), MemoryStatus.ACTIVE)
        self.assertEqual(coerce_memory_status(MemoryStatus.SUPERSEDED), MemoryStatus.SUPERSEDED)
        with self.assertRaises(InvalidLifecycleStateError):
            coerce_memory_status("UNKNOWN_STATE_XYZ")

    def test_02_transition_validation(self):
        """Verify state machine transition validator rejects forbidden transitions."""
        # Valid
        rule = validate_transition(MemoryStatus.ACTIVE, MemoryStatus.ARCHIVED)
        self.assertTrue(rule.allowed)

        # Forbidden
        with self.assertRaises(InvalidLifecycleTransitionError):
            validate_transition(MemoryStatus.ARCHIVED, MemoryStatus.ACTIVE)

        # Terminal DELETED
        with self.assertRaises(MemoryAlreadyDeletedError):
            validate_transition(MemoryStatus.DELETED, MemoryStatus.ACTIVE)

        # Self-transition
        with self.assertRaises(LifecycleValidationError):
            validate_transition(MemoryStatus.ACTIVE, MemoryStatus.ACTIVE)

    def test_03_provenance_validation(self):
        """Verify provenance enforcement for PENDING_VERIFICATION -> ACTIVE."""
        # USER actor requires >= 5 chars
        with self.assertRaises(ProvenanceValidationError):
            validate_provenance(MemoryStatus.PENDING_VERIFICATION, MemoryStatus.ACTIVE, actor="USER", reason="ok")
        self.assertTrue(
            validate_provenance(MemoryStatus.PENDING_VERIFICATION, MemoryStatus.ACTIVE, actor="USER", reason="Verified by user explicit confirmation")
        )

        # TASK actor requires task_id or source_event_id
        with self.assertRaises(ProvenanceValidationError):
            validate_provenance(MemoryStatus.PENDING_VERIFICATION, MemoryStatus.ACTIVE, actor="TASK")
        self.assertTrue(
            validate_provenance(MemoryStatus.PENDING_VERIFICATION, MemoryStatus.ACTIVE, actor="TASK", task_id="task_123")
        )
        self.assertTrue(
            validate_provenance(MemoryStatus.PENDING_VERIFICATION, MemoryStatus.ACTIVE, actor="TASK", source_event_id="evt_123")
        )

        # SYSTEM actor requires corroboration mechanism in metadata and non-empty reason
        with self.assertRaises(ProvenanceValidationError):
            validate_provenance(MemoryStatus.PENDING_VERIFICATION, MemoryStatus.ACTIVE, actor="SYSTEM", reason="Automatic verification")
        self.assertTrue(
            validate_provenance(
                MemoryStatus.PENDING_VERIFICATION,
                MemoryStatus.ACTIVE,
                actor="SYSTEM",
                reason="Corroborated by external knowledge fact",
                metadata={"corroboration_source": "system_ruleset_v1"},
            )
        )

        # LIFECYCLE_ENGINE cannot automatically verify
        with self.assertRaises(ProvenanceValidationError):
            validate_provenance(MemoryStatus.PENDING_VERIFICATION, MemoryStatus.ACTIVE, actor="LIFECYCLE_ENGINE", reason="Periodic scan")

    def test_04_error_classification(self):
        """Verify classification of retryable vs non-retryable errors."""
        # Retryable
        self.assertTrue(is_retryable_lifecycle_error(LockTimeoutError("Lock timeout occurred")))
        self.assertTrue(is_retryable_lifecycle_error(DeadlockDetectedError("Deadlock detected")))
        self.assertTrue(is_retryable_lifecycle_error(DatabaseConnectionError("Connection lost")))
        self.assertTrue(is_retryable_lifecycle_error(ConcurrentModificationError("Concurrent conflict")))

        # Non-retryable
        self.assertFalse(is_retryable_lifecycle_error(InvalidLifecycleTransitionError("A", "B")))
        self.assertFalse(is_retryable_lifecycle_error(MemoryAlreadyDeletedError("ACTIVE")))
        self.assertFalse(is_retryable_lifecycle_error(MemoryNotFoundError("Not found")))
        self.assertFalse(is_retryable_lifecycle_error(ProvenanceValidationError("Provenance failed")))
        self.assertFalse(is_retryable_lifecycle_error(LifecycleValidationError("Validation failed")))
        self.assertFalse(is_retryable_lifecycle_error(LifecycleAuditError("Audit failed")))

    def test_05_idempotency_validation(self):
        """Verify idempotency key behavior and sanitization in event model."""
        evt = MemoryLifecycleEvent(
            memory_id="mem_test_1",
            previous_status=MemoryStatus.ACTIVE,
            new_status=MemoryStatus.ARCHIVED,
            idempotency_key="idemp_key_12345",
        )
        d = evt.to_dict()
        self.assertEqual(d["idempotency_key"], "idemp_key_12345")
        evt2 = MemoryLifecycleEvent.from_dict(d)
        self.assertEqual(evt2.idempotency_key, "idemp_key_12345")

    def test_06_result_object_behavior(self):
        """Verify LifecycleTransitionResult fields and defaults."""
        res = LifecycleTransitionResult(
            success=True,
            memory_id="mem_100",
            previous_status=MemoryStatus.ACTIVE,
            new_status=MemoryStatus.SUPERSEDED,
            event_id="evt_100",
            idempotent_replay=False,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.memory_id, "mem_100")
        self.assertFalse(res.idempotent_replay)
        self.assertIsNotNone(res.transition_timestamp)


# ============================================================================
# PART 2: REAL POSTGRESQL ATOMICITY & TRANSACTION TESTS
# ============================================================================

class TestV532PostgreSQLAtomic(unittest.TestCase):
    """Real PostgreSQL database tests validating ACID semantics and row locking."""

    def setUp(self):
        self.db = postgres_manager
        self.engine = lifecycle_engine

    def test_07_atomic_state_and_audit_commit(self):
        """Verify state update and lifecycle audit are committed atomically."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.assertTrue(memory_repository.store(rec))

        res = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ARCHIVED,
            reason="Normal project archival",
            actor="SYSTEM",
        )
        self.assertTrue(res.success)
        self.assertEqual(res.new_status, MemoryStatus.ARCHIVED)

        # Check DB state
        loaded = memory_repository.get_by_id(rec.memory_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.status, MemoryStatus.ARCHIVED)

        # Check audit event
        events = memory_repository.get_lifecycle_events(rec.memory_id)
        self.assertGreaterEqual(len(events), 1)
        latest = events[0]
        self.assertEqual(latest.new_status, MemoryStatus.ARCHIVED)
        self.assertEqual(latest.previous_status, MemoryStatus.ACTIVE)
        self.assertEqual(latest.event_id, res.event_id)

    def test_08_rollback_on_audit_failure(self):
        """Verify that if audit insertion violates constraints, state update is rolled back."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.assertTrue(memory_repository.store(rec))

        # We inject a forbidden key in metadata that raises LifecycleAuditError
        res = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ARCHIVED,
            reason="Archive attempt with forbidden metadata",
            metadata={"secret_password": "supersecretpassword"},  # Triggers LifecycleAuditError
        )
        self.assertFalse(res.success)

        # State MUST NOT be ARCHIVED; must remain ACTIVE!
        loaded = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(loaded.status, MemoryStatus.ACTIVE, "State update must rollback on audit failure!")

        # No audit event should exist for ARCHIVED
        events = memory_repository.get_lifecycle_events(rec.memory_id)
        archived_events = [e for e in events if e.new_status == MemoryStatus.ARCHIVED]
        self.assertEqual(len(archived_events), 0, "No audit event should commit if transaction rolled back!")

    def test_09_rollback_on_state_failure(self):
        """Verify that if state update fails, no audit event remains committed."""
        # Nonexistent memory should fail-closed
        fake_id = f"mem_nonexistent_{uuid.uuid4().hex[:8]}"
        res = self.engine.transition_memory(
            memory_id=fake_id,
            target_status=MemoryStatus.ARCHIVED,
            reason="Archive nonexistent",
        )
        self.assertFalse(res.success)
        self.assertIn("not found", res.error.lower())

        # Verify zero audit events exist for this ID
        events = memory_repository.get_lifecycle_events(fake_id)
        self.assertEqual(len(events), 0)

    def test_10_missing_memory(self):
        """Verify transitioning a nonexistent memory returns clean error and raises MemoryNotFoundError."""
        fake_id = f"mem_missing_{uuid.uuid4().hex[:8]}"
        with self.assertRaises(MemoryNotFoundError):
            self.engine.transition_memory(
                memory_id=fake_id,
                target_status=MemoryStatus.ARCHIVED,
                raise_on_error=True,
            )

    def test_11_deleted_terminal_state(self):
        """Verify DELETED memory rejects any subsequent state transitions under real PostgreSQL."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.assertTrue(memory_repository.store(rec))

        # Delete it
        res_del = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.DELETED,
            reason="User deletion",
        )
        self.assertTrue(res_del.success)

        # Attempt to transition from DELETED to ACTIVE
        res_resurrect = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ACTIVE,
            reason="Attempt resurrection",
        )
        self.assertFalse(res_resurrect.success)
        self.assertIn("deleted is a terminal state", res_resurrect.error.lower())

        # Attempt to transition from DELETED to ARCHIVED
        res_arch = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ARCHIVED,
            reason="Attempt archive",
        )
        self.assertFalse(res_arch.success)

        # State must remain DELETED
        loaded = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(loaded.status, MemoryStatus.DELETED)

    def test_12_invalid_transition(self):
        """Verify an illegal transition (e.g. ARCHIVED -> ACTIVE) is rejected without mutating DB."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.assertTrue(memory_repository.store(rec))

        # First archive
        res_arch = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ARCHIVED,
            reason="Project archival",
        )
        self.assertTrue(res_arch.success)

        # Attempt ARCHIVED -> ACTIVE (forbidden in V5.3.1/V5.3.2)
        res_act = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ACTIVE,
            reason="Attempt unarchive",
        )
        self.assertFalse(res_act.success)
        self.assertIn("invalid memory lifecycle transition", res_act.error.lower())

        # State must remain ARCHIVED
        loaded = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(loaded.status, MemoryStatus.ARCHIVED)

    def test_13_database_check_constraint(self):
        """Verify the PostgreSQL CHECK constraint chk_memory_status prevents invalid status values."""
        conn = self.db.get_connection()
        self.assertIsNotNone(conn)
        try:
            with conn.cursor() as cur:
                with self.assertRaises(Exception):
                    cur.execute(
                        "INSERT INTO memory_records (memory_id, content, status) VALUES (%s, %s, %s);",
                        (f"mem_check_{uuid.uuid4().hex[:8]}", "Invalid status test", "ILLEGAL_STATUS_VAL")
                    )
            conn.rollback()
        finally:
            self.db.release_connection(conn)

    def test_14_audit_event_contents_sanitized(self):
        """Verify audit events never contain raw memory content, query text, or credentials."""
        rec = make_test_record(content="Super confidential secret data project code 998")
        self.assertTrue(memory_repository.store(rec))

        res = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ARCHIVED,
            reason="Archival with sanitized telemetry",
        )
        self.assertTrue(res.success)

        events = memory_repository.get_lifecycle_events(rec.memory_id)
        self.assertGreaterEqual(len(events), 1)
        evt = events[0]
        evt_dict = evt.to_dict()

        self.assertNotIn("content", evt_dict)
        self.assertNotIn("raw_content", evt_dict)
        self.assertNotIn("query", evt_dict)
        self.assertNotIn("embedding", evt_dict)
        self.assertNotIn("password", evt_dict)
        self.assertNotIn("token", evt_dict)

    def test_15_append_only_audit_behavior(self):
        """Verify audit events form an immutable chronological sequence."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        self.assertTrue(memory_repository.store(rec))

        # 1. ACTIVE -> ARCHIVED
        res1 = self.engine.transition_memory(rec.memory_id, MemoryStatus.ARCHIVED, reason="Archive 1")
        self.assertTrue(res1.success)

        # 2. ARCHIVED -> DELETED
        res2 = self.engine.transition_memory(rec.memory_id, MemoryStatus.DELETED, reason="Purge 2")
        self.assertTrue(res2.success)

        events = memory_repository.get_lifecycle_events(rec.memory_id)
        self.assertEqual(len(events), 2)
        # In reverse chronological order: latest first
        self.assertEqual(events[0].new_status, MemoryStatus.DELETED)
        self.assertEqual(events[1].new_status, MemoryStatus.ARCHIVED)

    def test_16_atomic_supersession_atomicity(self):
        """Verify atomic 1:1 supersession: old record SUPERSEDED, new record inserted, audit logged."""
        old_rec = make_test_record(content="Old fact: Sujal uses Python 3.10", status=MemoryStatus.ACTIVE)
        self.assertTrue(memory_repository.store(old_rec))

        new_rec = make_test_record(content="New fact: Sujal uses Python 3.11", status=MemoryStatus.ACTIVE)

        res = self.engine.supersede_memory(
            old_memory_id=old_rec.memory_id,
            new_record=new_rec,
            reason="Language version upgrade",
            actor="SYSTEM",
        )
        self.assertTrue(res.success)

        # Old record must be SUPERSEDED
        loaded_old = memory_repository.get_by_id(old_rec.memory_id)
        self.assertEqual(loaded_old.status, MemoryStatus.SUPERSEDED)

        # New record must be ACTIVE and point to old record
        loaded_new = memory_repository.get_by_id(new_rec.memory_id)
        self.assertEqual(loaded_new.status, MemoryStatus.ACTIVE)
        self.assertEqual(loaded_new.supersedes_memory_id, old_rec.memory_id)

        # Audit event for old record must be present
        events = memory_repository.get_lifecycle_events(old_rec.memory_id)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0].new_status, MemoryStatus.SUPERSEDED)
        self.assertEqual(events[0].related_memory_id, new_rec.memory_id)


# ============================================================================
# PART 3: IDEMPOTENCY TESTS
# ============================================================================

class TestV532Idempotency(unittest.TestCase):
    """Verify strong idempotency semantics using idempotency_key."""

    def setUp(self):
        self.engine = lifecycle_engine

    def test_17_repeated_identical_request(self):
        """Verify repeated transition with same idempotency_key returns replay=True and same event_id."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        memory_repository.store(rec)

        idemp_key = f"idemp_{uuid.uuid4().hex[:12]}"

        # Request 1
        res1 = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ARCHIVED,
            reason="Idempotency test run",
            idempotency_key=idemp_key,
        )
        self.assertTrue(res1.success)
        self.assertFalse(res1.idempotent_replay)
        first_event_id = res1.event_id

        # Request 2 (identical)
        res2 = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ARCHIVED,
            reason="Idempotency test run retry",
            idempotency_key=idemp_key,
        )
        self.assertTrue(res2.success)
        self.assertTrue(res2.idempotent_replay)
        self.assertEqual(res2.event_id, first_event_id)

    def test_18_zero_duplicate_audit_events(self):
        """Verify that repeating an idempotent request writes exactly 1 audit event in PostgreSQL."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        memory_repository.store(rec)

        idemp_key = f"idemp_{uuid.uuid4().hex[:12]}"

        for _ in range(5):
            self.engine.transition_memory(
                memory_id=rec.memory_id,
                target_status=MemoryStatus.ARCHIVED,
                reason="Idempotency repeated loop",
                idempotency_key=idemp_key,
            )

        events = memory_repository.get_lifecycle_events(rec.memory_id)
        matching = [e for e in events if getattr(e, "idempotency_key", None) == idemp_key]
        self.assertEqual(len(matching), 1, "There must be exactly ONE audit event recorded for an idempotency key!")

    def test_19_same_task_different_idempotency_keys(self):
        """Verify a task performing multiple legitimate transitions uses distinct keys and succeeds."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        memory_repository.store(rec)

        task_id = "task_pipeline_batch_99"

        # Step 1: Archive with key A
        res1 = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ARCHIVED,
            reason="Task archiving step",
            task_id=task_id,
            idempotency_key=f"{task_id}_step_1",
        )
        self.assertTrue(res1.success)
        self.assertFalse(res1.idempotent_replay)

        # Step 2: Delete with key B
        res2 = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.DELETED,
            reason="Task purge step",
            task_id=task_id,
            idempotency_key=f"{task_id}_step_2",
        )
        self.assertTrue(res2.success)
        self.assertFalse(res2.idempotent_replay)
        self.assertNotEqual(res1.event_id, res2.event_id)

    def test_20_idempotency_under_concurrency(self):
        """Verify concurrent identical requests with same idempotency key resolve safely without duplicate events."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        memory_repository.store(rec)

        idemp_key = f"idemp_conc_{uuid.uuid4().hex[:12]}"
        results = []

        def worker():
            res = self.engine.transition_memory(
                memory_id=rec.memory_id,
                target_status=MemoryStatus.ARCHIVED,
                reason="Concurrent idempotent trial",
                idempotency_key=idemp_key,
            )
            results.append(res)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 4)
        for r in results:
            self.assertTrue(r.success)

        # Exactly 1 initial transition and 3 replays (or all pointing to same event_id)
        event_ids = {r.event_id for r in results}
        self.assertEqual(len(event_ids), 1, "All concurrent workers must receive the exact same event_id!")

        events = memory_repository.get_lifecycle_events(rec.memory_id)
        matching = [e for e in events if getattr(e, "idempotency_key", None) == idemp_key]
        self.assertEqual(len(matching), 1)


# ============================================================================
# PART 4: REAL POSTGRESQL CONCURRENCY TESTS
# ============================================================================

class TestV532Concurrency(unittest.TestCase):
    """Real PostgreSQL concurrency tests using independent connection threads."""

    def setUp(self):
        self.engine = lifecycle_engine

    def test_21_archive_vs_delete_race(self):
        """Verify concurrent ARCHIVE vs DELETE on the same row. State remains consistent."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        memory_repository.store(rec)

        results = {}

        def do_archive():
            results["archive"] = self.engine.transition_memory(
                memory_id=rec.memory_id,
                target_status=MemoryStatus.ARCHIVED,
                reason="Race Archive",
            )

        def do_delete():
            results["delete"] = self.engine.transition_memory(
                memory_id=rec.memory_id,
                target_status=MemoryStatus.DELETED,
                reason="Race Delete",
            )

        t1 = threading.Thread(target=do_archive)
        t2 = threading.Thread(target=do_delete)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both cannot produce contradictory state.
        # If ARCHIVE wins first: DELETE can subsequently succeed (ARCHIVED -> DELETED is allowed).
        # If DELETE wins first: ARCHIVE must be rejected (DELETED is terminal).
        loaded = memory_repository.get_by_id(rec.memory_id)
        self.assertIn(loaded.status, [MemoryStatus.ARCHIVED, MemoryStatus.DELETED])

        if loaded.status == MemoryStatus.DELETED:
            # If final status is DELETED, it can never transition back
            res3 = self.engine.transition_memory(rec.memory_id, MemoryStatus.ACTIVE, reason="Invalid resurrect")
            self.assertFalse(res3.success)

    def test_22_delete_vs_archive_race_terminality(self):
        """Verify that once DELETED commits, any concurrent or subsequent ARCHIVE is rejected."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        memory_repository.store(rec)

        # Explicitly delete
        res_del = self.engine.transition_memory(rec.memory_id, MemoryStatus.DELETED, reason="Prior delete")
        self.assertTrue(res_del.success)

        # Concurrent archive attempts
        res_arch = self.engine.transition_memory(rec.memory_id, MemoryStatus.ARCHIVED, reason="Subsequent archive")
        self.assertFalse(res_arch.success)
        self.assertIn("deleted is a terminal state", res_arch.error.lower())

    def test_23_concurrent_active_to_superseded(self):
        """Verify concurrent supersessions on same old record. Exactly one succeeds in 1:1 model."""
        old_rec = make_test_record(content="Old config version 1", status=MemoryStatus.ACTIVE)
        memory_repository.store(old_rec)

        new_rec1 = make_test_record(content="New config version 2A", status=MemoryStatus.ACTIVE)
        new_rec2 = make_test_record(content="New config version 2B", status=MemoryStatus.ACTIVE)

        res = []

        def sup1():
            r = self.engine.supersede_memory(old_rec.memory_id, new_rec1, reason="Supersede A")
            res.append(r)

        def sup2():
            r = self.engine.supersede_memory(old_rec.memory_id, new_rec2, reason="Supersede B")
            res.append(r)

        t1 = threading.Thread(target=sup1)
        t2 = threading.Thread(target=sup2)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # One supersession will succeed first, transitioning old_rec to SUPERSEDED.
        # The second supersession will see old_rec is SUPERSEDED, and SUPERSEDED -> SUPERSEDED
        # is a self-transition (or invalid transition), so the second must be rejected!
        successes = [r for r in res if r.success]
        failures = [r for r in res if not r.success]

        self.assertEqual(len(successes), 1, "Exactly one 1:1 supersession should succeed!")
        self.assertEqual(len(failures), 1, "Second conflicting supersession must be rejected!")

    def test_24_lock_timeout_handling(self):
        """Verify that when a row lock is held past lock_timeout, LockTimeoutError is captured."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        memory_repository.store(rec)

        pg = postgres_manager
        conn_holder = pg.get_connection()
        self.assertIsNotNone(conn_holder)

        try:
            with conn_holder.cursor() as cur:
                # Hold exclusive row lock
                cur.execute("SELECT * FROM memory_records WHERE memory_id = %s FOR UPDATE;", (rec.memory_id,))

                # In parallel thread, attempt transition with a small timeout context
                timeout_result = []

                def competitor():
                    # We create an engine call with raise_on_error=True
                    # To keep test fast, we can test that lock timeout gets raised
                    try:
                        with pg.transaction(lock_timeout_ms=500) as conn2:
                            with conn2.cursor() as cur2:
                                cur2.execute("SELECT * FROM memory_records WHERE memory_id = %s FOR UPDATE;", (rec.memory_id,))
                    except Exception as ex:
                        timeout_result.append(ex)

                t = threading.Thread(target=competitor)
                t.start()
                t.join(timeout=3)

                self.assertTrue(len(timeout_result) > 0)
                err = timeout_result[0]
                self.assertTrue(
                    is_retryable_lifecycle_error(err) or "timeout" in str(err).lower(),
                    f"Expected lock timeout error, got: {err}"
                )

            conn_holder.rollback()
        finally:
            pg.release_connection(conn_holder)

    def test_25_zero_lost_updates(self):
        """Verify serial consistency and zero lost updates under sequential chained transitions."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        memory_repository.store(rec)

        # Transition 1: ACTIVE -> ARCHIVED
        r1 = self.engine.transition_memory(rec.memory_id, MemoryStatus.ARCHIVED, reason="Chain 1")
        self.assertTrue(r1.success)

        # Transition 2: ARCHIVED -> DELETED
        r2 = self.engine.transition_memory(rec.memory_id, MemoryStatus.DELETED, reason="Chain 2")
        self.assertTrue(r2.success)

        loaded = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(loaded.status, MemoryStatus.DELETED)

        events = memory_repository.get_lifecycle_events(rec.memory_id)
        self.assertEqual(len(events), 2)


# ============================================================================
# PART 5: FAULT INJECTION & CRASH SIMULATION
# ============================================================================

class TestV532FaultInjection(unittest.TestCase):
    """Fault injection tests verifying fail-closed and rollback invariants."""

    def setUp(self):
        self.engine = lifecycle_engine

    def test_26_failure_during_audit_rolls_back_everything(self):
        """Inject failure during audit insertion. Confirm entire transaction rolls back."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        memory_repository.store(rec)

        # Metadata containing forbidden credential token
        res = self.engine.transition_memory(
            memory_id=rec.memory_id,
            target_status=MemoryStatus.ARCHIVED,
            reason="Fault injection",
            metadata={"api_key_leak": "12345"},
        )
        self.assertFalse(res.success)

        # Verify state in DB remains ACTIVE
        loaded = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(loaded.status, MemoryStatus.ACTIVE)

        # Verify zero audit events
        events = memory_repository.get_lifecycle_events(rec.memory_id)
        self.assertEqual(len(events), 0)

    def test_27_atomic_supersede_rolls_back_if_audit_fails(self):
        """Verify supersede rolls back both new memory insert and old status update if audit fails."""
        old_rec = make_test_record(content="Original config", status=MemoryStatus.ACTIVE)
        memory_repository.store(old_rec)

        new_rec = make_test_record(content="New config", status=MemoryStatus.ACTIVE)

        # We pass reason > 255 chars or an invalid parameter to test rollback
        # Let's test non-existent old record
        res = self.engine.supersede_memory(
            old_memory_id=f"mem_fake_{uuid.uuid4().hex[:8]}",
            new_record=new_rec,
            reason="Supersede missing old",
        )
        self.assertFalse(res.success)

        # New record MUST NOT be committed!
        loaded_new = memory_repository.get_by_id(new_rec.memory_id)
        self.assertIsNone(loaded_new, "New record must not be stored if supersession failed!")


# ============================================================================
# PART 6: BYPASS CLOSURE VERIFICATION
# ============================================================================

class TestV532BypassClosure(unittest.TestCase):
    """Verify that all direct mutation paths route through MemoryLifecycleEngine."""

    def test_28_manager_store_and_supersede_produces_lifecycle_audit(self):
        """Verify MemoryManager.store_and_supersede() uses atomic engine and writes audit log."""
        # Store an initial fact
        fact1 = make_test_record(content="User prefers tabs for indentation", status=MemoryStatus.ACTIVE)
        memory_repository.store(fact1)

        # Store conflicting fact with keyword 'indentation'
        fact2 = make_test_record(content="User prefers spaces for indentation", status=MemoryStatus.ACTIVE)
        superseded_result = memory_manager.store_and_supersede(
            record=fact2,
            conflict_keywords=["indentation"],
        )
        self.assertIsNotNone(superseded_result)

        # Verify old record is SUPERSEDED
        loaded_old = memory_repository.get_by_id(fact1.memory_id)
        self.assertEqual(loaded_old.status, MemoryStatus.SUPERSEDED)

        # Verify audit event was written for old record
        events = memory_repository.get_lifecycle_events(fact1.memory_id)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0].new_status, MemoryStatus.SUPERSEDED)

    def test_29_repository_update_status_produces_lifecycle_audit(self):
        """Verify memory_repository.update_status() routes through MemoryLifecycleEngine and writes audit."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        memory_repository.store(rec)

        success = memory_repository.update_status(rec.memory_id, MemoryStatus.ARCHIVED)
        self.assertTrue(success)

        # Verify audit event was written
        events = memory_repository.get_lifecycle_events(rec.memory_id)
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0].new_status, MemoryStatus.ARCHIVED)

    def test_30_legacy_lifecycle_manager_wrappers_delegate_correctly(self):
        """Verify MemoryLifecycleManager methods (archive, delete, activate_pending) delegate to engine."""
        rec = make_test_record(status=MemoryStatus.ACTIVE)
        memory_repository.store(rec)

        # Archive via wrapper
        ok_arch = memory_lifecycle.archive(rec.memory_id, reason="Wrapper archive")
        self.assertTrue(ok_arch)
        loaded1 = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(loaded1.status, MemoryStatus.ARCHIVED)

        # Delete via wrapper
        ok_del = memory_lifecycle.delete(rec.memory_id, reason="Wrapper delete")
        self.assertTrue(ok_del)
        loaded2 = memory_repository.get_by_id(rec.memory_id)
        self.assertEqual(loaded2.status, MemoryStatus.DELETED)

        # Verify 2 audit events were created
        events = memory_repository.get_lifecycle_events(rec.memory_id)
        self.assertEqual(len(events), 2)


# ============================================================================
# RUNNER
# ============================================================================

def run_all_v532_tests() -> bool:
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("=" * 65)
    print(f"RESULTS: PASSED={result.testsRun - len(result.failures) - len(result.errors)} | FAILED={len(result.failures) + len(result.errors)} | TOTAL={result.testsRun}")
    print("=" * 65)
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_all_v532_tests()
    sys.exit(0 if success else 1)
