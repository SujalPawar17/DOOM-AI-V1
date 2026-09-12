"""V8.7 persistent task ledger tests. Isolated in-memory ledger; optional schema probe."""
from __future__ import annotations

import ast
import os
import pickle
import threading
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_V8_LEDGER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")

from orchestration.goal.kernel import process_goal
from orchestration.goal.plan_validator import build_goal_plan
from orchestration.task.engine import create_task, start_task
from test_v8_harness import start
from orchestration.task.errors import InvalidTaskPlan, InvalidTaskTransition, TaskNotFound
from orchestration.task import ledger as ledger_mod
from orchestration.task.ledger import (
    ledger_create,
    ledger_get,
    ledger_history,
    ledger_list,
    persist_transition,
    rehydrate,
    reset_ledger_for_tests,
    use_memory_ledger_for_tests,
)
from orchestration.task.ledger_errors import (
    LedgerCapacityError,
    LedgerConcurrencyError,
    LedgerSerializationError,
    LedgerTransactionError,
    LedgerUnavailable,
    LedgerValidationError,
    TaskAlreadyExists,
)
from orchestration.task.ledger_schema import LEDGER_DDL
from orchestration.task.ledger_store import MAX_ID, MAX_HASH, MemoryLedgerStore, _check_id
from orchestration.task.registry import get_task, load_snapshot_for_inspection, reset_task_registry_for_tests
from orchestration.task.types import TaskSnapshot, TaskState

ROOT = Path(__file__).resolve().parent
TASK_PKG = ROOT / "orchestration" / "task"
HASH = "h" * 64


def _v8_on():
    os.environ["PROACTIVE_V8_ENABLED"] = "true"


def _snap(**kw):
    base = dict(
        task_id="t1",
        goal_id="g1",
        owner_id="owner",
        session_id="s1",
        computer_session_id="c1",
        plan_hash=HASH,
        recovery_plan_hash="",
        state=TaskState.CREATED,
        created_unix_ms=1,
        started_unix_ms=0,
        completed_unix_ms=0,
        attempts_used=0,
        transition_count=0,
        failure_reason="",
        execution_started=False,
        transitions=(),
        version=1,
    )
    base.update(kw)
    return TaskSnapshot(**base)


def _plan():
    _v8_on()
    goal = process_goal("hello", context={"owner_id": "owner_A", "session_id": "sess"}).goal
    plan = build_goal_plan(goal, [{
        "step_id": "s1", "capability_id": "conversation", "action": "RESPOND",
        "parameters": {}, "dependencies": (), "verification_required": False,
        "verification_type": "", "retry_count": 0, "timeout_ms": 8000,
    }])
    return goal, plan


class TestV87TaskLedger(unittest.TestCase):
    def setUp(self):
        os.environ["PROACTIVE_V8_LEDGER_ENABLED"] = "false"
        os.environ["PROACTIVE_V8_ENABLED"] = "false"
        reset_ledger_for_tests()
        reset_task_registry_for_tests()
        self.store = use_memory_ledger_for_tests()

    def tearDown(self):
        reset_ledger_for_tests()
        reset_task_registry_for_tests()
        os.environ["PROACTIVE_V8_LEDGER_ENABLED"] = "false"
        os.environ["PROACTIVE_V8_ENABLED"] = "false"

    def test_01_migration_ddl_creates_schema_objects(self):
        joined = "\n".join(LEDGER_DDL)
        self.assertIn("doom_v8_tasks", joined)
        self.assertIn("doom_v8_task_transitions", joined)
        self.assertIn("PRIMARY KEY", joined)
        self.assertIn("idx_v8_tasks_owner", joined)
        sql = (TASK_PKG / "migrations" / "v87_ledger.sql").read_text(encoding="utf-8")
        self.assertIn("doom_v8_tasks", sql)

    def test_02_task_create_persists(self):
        rec = ledger_create(_snap())
        self.assertEqual(rec.snapshot.task_id, "t1")
        self.assertTrue(rec.persistence_ok)

    def test_03_task_read_immutable(self):
        rec = ledger_create(_snap())
        with self.assertRaises(FrozenInstanceError):
            rec.snapshot.state = TaskState.RUNNING  # type: ignore[misc]
        pub = rec.as_public()
        pub["state"] = "RUNNING"
        self.assertEqual(ledger_get("t1").snapshot.state, TaskState.CREATED)

    def test_04_duplicate_task_id_rejected(self):
        ledger_create(_snap())
        with self.assertRaises(TaskAlreadyExists):
            ledger_create(_snap())

    def test_05_transition_persists(self):
        ledger_create(_snap())
        rec = persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        self.assertEqual(rec.snapshot.state, TaskState.READY)

    def test_06_transition_history_persists(self):
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        hist = ledger_history("t1")
        self.assertEqual(len(hist.transitions), 1)
        with self.assertRaises(FrozenInstanceError):
            hist.transitions[0].reason_code = "X"  # type: ignore[misc]

    def test_07_task_and_transition_commit_together(self):
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        rec = ledger_get("t1")
        self.assertEqual(rec.snapshot.state, TaskState.READY)
        self.assertEqual(rec.snapshot.transition_count, 1)
        self.assertEqual(len(ledger_history("t1").transitions), 1)

    def test_08_failed_transition_does_not_mutate(self):
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        with self.assertRaises(InvalidTaskTransition):
            persist_transition("t1", 2, TaskState.CREATED, reason="BAD", now_ms=3)
        self.assertEqual(ledger_get("t1").snapshot.state, TaskState.READY)
        self.assertEqual(ledger_get("t1").version, 2)

    def test_09_version_increments(self):
        ledger_create(_snap())
        rec = persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        self.assertEqual(rec.version, 2)

    def test_10_stale_version_rejected(self):
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        with self.assertRaises(LedgerConcurrencyError):
            persist_transition("t1", 1, TaskState.RUNNING, reason="RUNNING", now_ms=3)

    def test_11_concurrent_transition_conflict(self):
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        persist_transition("t1", 2, TaskState.RUNNING, reason="RUNNING", now_ms=3)
        errors = []

        def worker(nxt):
            try:
                persist_transition("t1", 3, nxt, reason=nxt.value, now_ms=4)
            except Exception as exc:
                errors.append(type(exc))

        t1 = threading.Thread(target=worker, args=(TaskState.COMPLETED,))
        t2 = threading.Thread(target=worker, args=(TaskState.CANCELLED,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(errors.count(LedgerConcurrencyError), 1)
        self.assertIn(ledger_get("t1").snapshot.state, (TaskState.COMPLETED, TaskState.CANCELLED))

    def test_12_terminal_cannot_restart(self):
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        persist_transition("t1", 2, TaskState.RUNNING, reason="RUNNING", now_ms=3)
        persist_transition("t1", 3, TaskState.COMPLETED, reason="SUCCESS", now_ms=4)
        with self.assertRaises(InvalidTaskTransition):
            persist_transition("t1", 4, TaskState.RUNNING, reason="RESTART", now_ms=5)

    def test_13_cancellation_persists(self):
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        persist_transition("t1", 2, TaskState.CANCELLED, reason="CANCELLED", now_ms=3)
        self.assertEqual(ledger_get("t1").snapshot.state, TaskState.CANCELLED)

    def test_14_emergency_stop_persists(self):
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        persist_transition("t1", 2, TaskState.RUNNING, reason="RUNNING", now_ms=3)
        persist_transition("t1", 3, TaskState.ABORTED, reason="EMERGENCY_STOPPED", now_ms=4)
        self.assertEqual(ledger_get("t1").snapshot.failure_reason, "EMERGENCY_STOPPED")

    def test_15_recovery_state_persists(self):
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        persist_transition("t1", 2, TaskState.RUNNING, reason="RUNNING", now_ms=3)
        rec = persist_transition("t1", 3, TaskState.RECOVERING, reason="TIMEOUT", now_ms=4)
        self.assertEqual(rec.snapshot.state, TaskState.RECOVERING)

    def test_16_recovery_plan_hash_separate(self):
        rec_hash = "r" * 64
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        persist_transition("t1", 2, TaskState.RUNNING, reason="RUNNING", now_ms=3)
        rec = persist_transition(
            "t1", 3, TaskState.RECOVERING, reason="TIMEOUT", now_ms=4, recovery_plan_hash=rec_hash,
        )
        self.assertEqual(rec.snapshot.recovery_plan_hash, rec_hash)

    def test_17_original_plan_hash_preserved(self):
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        persist_transition("t1", 2, TaskState.RUNNING, reason="RUNNING", now_ms=3)
        rec = persist_transition(
            "t1", 3, TaskState.RECOVERING, reason="TIMEOUT", now_ms=4, recovery_plan_hash="r" * 64,
        )
        self.assertEqual(rec.snapshot.plan_hash, HASH)

    def test_18_no_authorization_inheritance(self):
        rec = ledger_create(_snap(state=TaskState.READY))
        self.assertFalse(rec.as_public()["authorizes_execution"])

    def test_19_task_id_does_not_authorize(self):
        ledger_create(_snap(state=TaskState.READY))
        rec = ledger_get("t1")
        self.assertFalse(rec.as_public().get("approved", False))
        self.assertFalse(rec.as_public()["authorizes_execution"])

    def test_20_plan_hash_does_not_authorize(self):
        rec = ledger_create(_snap())
        self.assertEqual(rec.as_public()["plan_hash"], HASH)
        self.assertFalse(rec.as_public()["authorizes_execution"])

    def test_21_restart_rehydrates_task(self):
        ledger_create(_snap(state=TaskState.WAITING))
        reset_task_registry_for_tests()
        snaps = rehydrate()
        self.assertEqual(len(snaps), 1)
        self.assertEqual(get_task("t1").state, TaskState.WAITING)

    def test_22_running_after_restart_does_not_execute(self):
        ledger_create(_snap(state=TaskState.RUNNING))
        spy = {"n": 0}

        def boom(*_a, **_k):
            spy["n"] += 1
            raise AssertionError("no execute")

        with patch("orchestration.task.engine.execute_plan", boom):
            rehydrate()
            _v8_on()
            with self.assertRaises(InvalidTaskPlan):
                start("t1", adapters={"conversation": boom})
        self.assertEqual(spy["n"], 0)
        self.assertEqual(ledger_get("t1").snapshot.state, TaskState.ABORTED)

    def test_23_recovering_after_restart_does_not_execute(self):
        ledger_create(_snap(state=TaskState.RECOVERING))
        rehydrate()
        self.assertEqual(ledger_get("t1").snapshot.state, TaskState.ABORTED)
        with self.assertRaises(InvalidTaskPlan):
            _v8_on()
            start("t1", adapters={})

    def test_24_waiting_after_restart_does_not_execute(self):
        ledger_create(_snap(state=TaskState.WAITING))
        spy = {"n": 0}

        def boom(*_a, **_k):
            spy["n"] += 1

        with patch("orchestration.task.engine.execute_plan", boom):
            rehydrate()
            _v8_on()
            with self.assertRaises(InvalidTaskPlan):
                start("t1", adapters={"conversation": boom})
        self.assertEqual(spy["n"], 0)
        self.assertEqual(ledger_get("t1").snapshot.state, TaskState.WAITING)

    def test_25_ready_after_restart_does_not_execute(self):
        ledger_create(_snap(state=TaskState.READY))
        spy = {"n": 0}

        def boom(*_a, **_k):
            spy["n"] += 1

        with patch("orchestration.task.engine.execute_plan", boom):
            rehydrate()
            _v8_on()
            with self.assertRaises(InvalidTaskPlan):
                start("t1", adapters={"conversation": boom})
        self.assertEqual(spy["n"], 0)

    def test_26_stale_inflight_becomes_aborted(self):
        ledger_create(_snap(state=TaskState.RUNNING))
        rehydrate()
        self.assertEqual(ledger_get("t1").snapshot.state, TaskState.ABORTED)

    def test_27_no_automatic_replay(self):
        ledger_create(_snap(state=TaskState.RUNNING, plan_hash=HASH))
        with patch("orchestration.executor.execute_plan") as ex:
            rehydrate()
            ex.assert_not_called()

    def test_28_no_automatic_recovery(self):
        ledger_create(_snap(state=TaskState.RECOVERING))
        with patch("orchestration.recovery.engine.recover_execution") as rec:
            rehydrate()
            rec.assert_not_called()

    def test_29_bounded_task_count(self):
        self.store.clear()
        for i in range(256):
            ledger_create(_snap(task_id=f"id{i:03d}"))
        with self.assertRaises(LedgerCapacityError):
            ledger_create(_snap(task_id="overflow"))

    def test_30_bounded_transition_count(self):
        ledger_create(_snap())
        persist_transition("t1", 1, TaskState.READY, reason="READY", now_ms=2)
        persist_transition("t1", 2, TaskState.RUNNING, reason="RUNNING", now_ms=3)
        version = 3
        now = 4
        for i in range(62):
            nxt = TaskState.WAITING if i % 2 == 0 else TaskState.RUNNING
            persist_transition("t1", version, nxt, reason=nxt.value, now_ms=now)
            version += 1
            now += 1
        rec = ledger_get("t1")
        self.assertEqual(rec.snapshot.transition_count, 64)
        nxt = TaskState.RUNNING if rec.snapshot.state is TaskState.WAITING else TaskState.WAITING
        with self.assertRaises(LedgerValidationError):
            persist_transition("t1", rec.version, nxt, reason="OVERFLOW", now_ms=now)

    def test_31_oversized_identifiers_rejected(self):
        with self.assertRaises(LedgerValidationError):
            _check_id("task_id", "x" * (MAX_ID + 1))
        with self.assertRaises(LedgerValidationError):
            ledger_create(_snap(task_id="x" * 129))
        with self.assertRaises(LedgerValidationError):
            ledger_create(_snap(plan_hash="p" * (MAX_HASH + 1)))

    def test_32_unsafe_serialization_rejected(self):
        with self.assertRaises(LedgerSerializationError):
            ledger_create("not-a-snapshot")  # type: ignore[arg-type]

    def test_33_pickle_prohibited(self):
        blob = pickle.dumps(_snap())
        with self.assertRaises(LedgerSerializationError):
            ledger_create(blob)  # type: ignore[arg-type]
        for path in TASK_PKG.glob("ledger*.py"):
            self.assertNotIn("pickle", path.read_text(encoding="utf-8"))

    def test_34_sql_injection_parameterized(self):
        evil = "'; DROP TABLE doom_v8_tasks; --"
        with self.assertRaises(TaskNotFound):
            ledger_get(evil)
        ledger_create(_snap(task_id="safe_id"))
        self.assertEqual(ledger_get("safe_id").snapshot.task_id, "safe_id")

    def test_35_arbitrary_sql_unavailable(self):
        self.assertFalse(hasattr(self.store, "execute"))
        self.assertFalse(hasattr(ledger_mod, "execute_sql"))

    def test_36_rehydrate_execute_flag_rejected(self):
        ledger_create(_snap())
        with self.assertRaises(LedgerUnavailable):
            rehydrate(execute=True)

    def test_37_flag_off_without_store(self):
        reset_ledger_for_tests()
        with self.assertRaises(LedgerUnavailable):
            ledger_create(_snap())

    def test_38_engine_persists_when_store_attached(self):
        goal, plan = _plan()
        snap = create_task(plan, goal)
        rec = ledger_get(snap.task_id)
        self.assertEqual(rec.snapshot.state, TaskState.READY)
        listed = ledger_list(owner_id=snap.owner_id, goal_id=snap.goal_id, state="READY")
        self.assertEqual(len(listed), 1)

    def test_39_persist_failure_does_not_claim_completed(self):
        goal, plan = _plan()
        snap = create_task(plan, goal)
        inner = self.store

        class Boom(MemoryLedgerStore):
            def persist_transition(self, *a, **k):
                raise LedgerTransactionError()

        boom = Boom()
        boom._tasks = inner._tasks
        boom._events = inner._events
        ledger_mod._STORE = boom
        from orchestration.task.registry import apply_transition
        with self.assertRaises(LedgerTransactionError):
            apply_transition(snap.task_id, TaskState.RUNNING, reason="RUNNING", now_ms=9)
        self.assertEqual(get_task(snap.task_id).state, TaskState.READY)

    def test_40_no_resume_api(self):
        self.assertFalse(hasattr(ledger_mod, "resume_task"))
        self.assertFalse(hasattr(ledger_mod, "replay"))

    def test_41_postgres_schema_if_available(self):
        from database.postgres_db import postgres_manager
        if not getattr(postgres_manager, "_connected", False):
            self.skipTest("postgres unavailable")
        from orchestration.task.ledger_store import PostgresLedgerStore
        store = PostgresLedgerStore(postgres_manager)
        store.ensure_schema()
        conn = postgres_manager.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM doom_v8_tasks LIMIT 1")
            cur.close()
        finally:
            postgres_manager.release_connection(conn)

    def test_42_ast_isolation(self):
        forbidden_mod = {
            "subprocess", "importlib", "ctypes", "pyautogui", "playwright", "selenium",
            "requests", "httpx", "pickle", "openai", "groq",
        }
        forbidden_names = {"eval", "exec", "__import__", "ALL_TOOLS", "TaskEngine", "computer_tools"}
        files = list(TASK_PKG.glob("ledger*.py"))
        self.assertTrue(files)
        for path in files:
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("MemoryManager", src)
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], forbidden_mod, path.name)
                if isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".")[0]
                    self.assertNotIn(root, forbidden_mod, path.name)
                    self.assertFalse(node.module.startswith("proactive.computer"))
                    self.assertNotEqual(node.module, "tools")
                    self.assertFalse(node.module.startswith("memory"))
                if isinstance(node, ast.Name) and node.id in forbidden_names:
                    self.fail(f"{path} uses {node.id}")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"eval", "exec"})
                if isinstance(node, ast.ClassDef) and node.name == "TaskEngine":
                    self.fail("legacy TaskEngine")

    def test_43_no_learning_or_explainability_symbols(self):
        for path in TASK_PKG.glob("ledger*.py"):
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("Explainability", src)
            self.assertNotIn("strategy_memory", src)
            self.assertNotIn("embedding", src.lower())

    def test_44_created_at_range_filter(self):
        ledger_create(_snap(task_id="early", created_unix_ms=10))
        ledger_create(_snap(task_id="late", created_unix_ms=90))
        only_late = ledger_list(created_from_ms=50, created_to_ms=100)
        ids = {r.snapshot.task_id for r in only_late}
        self.assertEqual(ids, {"late"})

    def test_45_v8_disabled_skips_engine_ledger_writes(self):
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        g, p = _plan()
        os.environ["PROACTIVE_V8_ENABLED"] = "false"
        snap = create_task(p, g)
        self.assertEqual(snap.state, TaskState.BLOCKED)
        with self.assertRaises(TaskNotFound):
            ledger_get(snap.task_id)

    def test_46_fake_approved_does_not_authorize(self):
        rec = ledger_create(_snap(state=TaskState.READY))
        pub = rec.as_public()
        pub["approved"] = True
        pub["authorizes_execution"] = True
        self.assertFalse(ledger_get("t1").as_public()["authorizes_execution"])
        self.assertNotIn("approved", ledger_get("t1").as_public())

    def test_47_malformed_plan_hash_rejected(self):
        with self.assertRaises(LedgerValidationError):
            ledger_create(_snap(plan_hash="abc\x00def"))

    def test_48_connection_and_migration_failure(self):
        from orchestration.task.ledger_errors import LedgerMigrationError
        from orchestration.task.ledger_store import PostgresLedgerStore

        class Dead:
            def get_connection(self):
                return None

            def release_connection(self, _c):
                return None

        with self.assertRaises(LedgerUnavailable):
            PostgresLedgerStore(Dead()).ensure_schema()

        class BoomConn:
            def cursor(self):
                raise RuntimeError("ddl-fail")

            def rollback(self):
                return None

            def commit(self):
                return None

        class BoomDb:
            def get_connection(self):
                return BoomConn()

            def release_connection(self, _c):
                return None

        with self.assertRaises(LedgerMigrationError):
            PostgresLedgerStore(BoomDb()).ensure_schema()

    def test_49_execution_success_persist_fail_not_completed(self):
        from orchestration.executor_errors import ExecutionStatus
        from orchestration.task.engine import task_result

        goal, plan = _plan()
        snap = create_task(plan, goal)
        inner = self.store

        class Boom:
            def persist_transition(self, task_id, expected_version, nxt, **k):
                if nxt is TaskState.COMPLETED:
                    raise LedgerTransactionError()
                return inner.persist_transition(task_id, expected_version, nxt, **k)

            def create(self, *a, **k):
                return inner.create(*a, **k)

        ledger_mod._STORE = Boom()
        with self.assertRaises(LedgerTransactionError):
            start(snap.task_id, adapters={"conversation": lambda step, _p: "SUCCESS"})
        self.assertNotEqual(get_task(snap.task_id).state, TaskState.COMPLETED)
        tr = task_result(snap.task_id)
        self.assertIsNotNone(tr.execution_result)
        self.assertEqual(tr.execution_result.status, ExecutionStatus.SUCCESS)
        self.assertNotEqual(tr.final_state, TaskState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
