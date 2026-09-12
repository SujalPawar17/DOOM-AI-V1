"""V8.7 transactional ledger store. Records state only. Never executes plans."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from orchestration.task.errors import MAX_TASKS, MAX_TRANSITIONS_PER_TASK, InvalidTaskTransition, TaskNotFound
from orchestration.task.ledger_errors import (
    LedgerCapacityError,
    LedgerConcurrencyError,
    LedgerMigrationError,
    LedgerSerializationError,
    LedgerTransactionError,
    LedgerUnavailable,
    LedgerValidationError,
    TaskAlreadyExists,
)
from orchestration.task.ledger_models import LedgerHistory, LedgerTaskRecord
from orchestration.task.ledger_schema import LEDGER_DDL
from orchestration.task.state import ensure_transition
from orchestration.task.types import TERMINAL_STATES, TaskSnapshot, TaskState, TaskTransition

IN_FLIGHT = frozenset({TaskState.RUNNING, TaskState.RECOVERING})
MAX_ID = 128
MAX_HASH = 256
MAX_REASON = 128


def _now_ms() -> int:
    return int(time.time() * 1000)


def _check_id(name: str, value: str) -> str:
    text = str(value or "")
    if len(text) > MAX_ID:
        raise LedgerValidationError("IDENTIFIER_TOO_LONG")
    if "\x00" in text:
        raise LedgerValidationError("INVALID_IDENTIFIER")
    return text


def _check_hash(value: str) -> str:
    text = str(value or "")
    if len(text) > MAX_HASH:
        raise LedgerValidationError("HASH_TOO_LONG")
    if "\x00" in text:
        raise LedgerValidationError("INVALID_HASH")
    return text


def _check_reason(value: str) -> str:
    return str(value or "")[:MAX_REASON]


def snapshot_from_row(row: Dict[str, Any], transitions: Tuple[TaskTransition, ...] = ()) -> TaskSnapshot:
    return TaskSnapshot(
        task_id=str(row["task_id"]),
        goal_id=str(row["goal_id"]),
        owner_id=str(row["owner_id"]),
        session_id=str(row.get("session_id") or ""),
        computer_session_id=str(row.get("computer_session_id") or ""),
        plan_hash=str(row["plan_hash"]),
        recovery_plan_hash=str(row.get("recovery_plan_hash") or ""),
        state=TaskState(str(row["state"])),
        created_unix_ms=int(row["created_unix_ms"]),
        started_unix_ms=int(row.get("started_unix_ms") or 0),
        completed_unix_ms=int(row.get("completed_unix_ms") or 0),
        attempts_used=int(row.get("attempts_used") or 0),
        transition_count=int(row.get("transition_count") or 0),
        failure_reason=str(row.get("failure_reason") or ""),
        execution_started=bool(row.get("execution_started")),
        transitions=transitions,
        version=int(row.get("version") or 1),
    )


class MemoryLedgerStore:
    """Explicit in-process ledger for tests. Not a silent production fallback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._events: Dict[str, List[TaskTransition]] = {}

    def create(self, snap: TaskSnapshot, pending_recovery: bool = False) -> LedgerTaskRecord:
        if type(snap) is not TaskSnapshot:
            raise LedgerSerializationError()
        tid = _check_id("task_id", snap.task_id)
        with self._lock:
            if tid in self._tasks:
                raise TaskAlreadyExists()
            if len(self._tasks) >= MAX_TASKS:
                raise LedgerCapacityError()
            row = _row_from_snapshot(snap, pending_recovery)
            self._tasks[tid] = row
            self._events[tid] = []
            return LedgerTaskRecord(snapshot=snap, version=int(row["version"]), pending_recovery=pending_recovery)

    def persist_transition(
        self,
        task_id: str,
        expected_version: int,
        nxt: TaskState,
        *,
        reason: str,
        now_ms: int,
        plan_hash: str = "",
        recovery_plan_hash: str = "",
        started_unix_ms: int = -1,
        completed_unix_ms: int = -1,
        attempts_used: int = -1,
        execution_started: Optional[bool] = None,
        pending_recovery: Optional[bool] = None,
    ) -> LedgerTaskRecord:
        tid = _check_id("task_id", task_id)
        with self._lock:
            row = self._tasks.get(tid)
            if row is None:
                raise TaskNotFound()
            if int(row["version"]) != int(expected_version):
                raise LedgerConcurrencyError()
            current = TaskState(str(row["state"]))
            ensure_transition(current, nxt)
            events = self._events.setdefault(tid, [])
            if len(events) >= MAX_TRANSITIONS_PER_TASK:
                raise LedgerValidationError("TRANSITION_LIMIT_EXCEEDED")
            event = TaskTransition(
                from_state=current.value,
                to_state=nxt.value,
                timestamp_unix_ms=int(now_ms),
                reason_code=_check_reason(reason),
                plan_hash=_check_hash(plan_hash or row["plan_hash"]),
            )
            events.append(event)
            row["state"] = nxt.value
            row["version"] = int(row["version"]) + 1
            row["transition_count"] = len(events)
            row["failure_reason"] = _check_reason(reason)
            row["updated_unix_ms"] = int(now_ms)
            if started_unix_ms >= 0:
                row["started_unix_ms"] = int(started_unix_ms)
            if nxt in TERMINAL_STATES:
                row["completed_unix_ms"] = int(completed_unix_ms if completed_unix_ms >= 0 else now_ms)
            if recovery_plan_hash:
                row["recovery_plan_hash"] = _check_hash(recovery_plan_hash)
            if attempts_used >= 0:
                row["attempts_used"] = int(attempts_used)
            if execution_started is not None:
                row["execution_started"] = bool(execution_started)
            if pending_recovery is not None:
                row["pending_recovery"] = bool(pending_recovery)
            snap = snapshot_from_row(row, tuple(events))
            return LedgerTaskRecord(
                snapshot=snap,
                version=int(row["version"]),
                pending_recovery=bool(row["pending_recovery"]),
            )

    def get(self, task_id: str) -> LedgerTaskRecord:
        tid = _check_id("task_id", task_id)
        with self._lock:
            row = self._tasks.get(tid)
            if row is None:
                raise TaskNotFound()
            events = tuple(self._events.get(tid, []))
            snap = snapshot_from_row(row, events)
            return LedgerTaskRecord(snapshot=snap, version=int(row["version"]), pending_recovery=bool(row["pending_recovery"]))

    def history(self, task_id: str) -> LedgerHistory:
        rec = self.get(task_id)
        return LedgerHistory(task_id=rec.snapshot.task_id, transitions=rec.snapshot.transitions)

    def list_tasks(
        self,
        *,
        owner_id: str = "",
        goal_id: str = "",
        state: str = "",
        created_from_ms: int = -1,
        created_to_ms: int = -1,
    ) -> List[LedgerTaskRecord]:
        with self._lock:
            out = []
            for row in self._tasks.values():
                if owner_id and row["owner_id"] != owner_id:
                    continue
                if goal_id and row["goal_id"] != goal_id:
                    continue
                if state and row["state"] != state:
                    continue
                created = int(row["created_unix_ms"])
                if created_from_ms >= 0 and created < int(created_from_ms):
                    continue
                if created_to_ms >= 0 and created > int(created_to_ms):
                    continue
                events = tuple(self._events.get(row["task_id"], []))
                snap = snapshot_from_row(row, events)
                out.append(LedgerTaskRecord(snapshot=snap, version=int(row["version"]), pending_recovery=bool(row["pending_recovery"])))
                if len(out) >= MAX_TASKS:
                    break
            return out

    def clear(self) -> None:
        with self._lock:
            self._tasks.clear()
            self._events.clear()


def _row_from_snapshot(snap: TaskSnapshot, pending_recovery: bool) -> Dict[str, Any]:
    if type(snap) is not TaskSnapshot:
        raise LedgerSerializationError()
    _check_id("task_id", snap.task_id)
    _check_id("goal_id", snap.goal_id)
    _check_id("owner_id", snap.owner_id)
    _check_id("session_id", snap.session_id)
    _check_id("computer_session_id", snap.computer_session_id)
    _check_hash(snap.plan_hash)
    _check_hash(snap.recovery_plan_hash)
    return {
        "task_id": snap.task_id,
        "goal_id": snap.goal_id,
        "owner_id": snap.owner_id,
        "session_id": snap.session_id,
        "computer_session_id": snap.computer_session_id,
        "plan_hash": snap.plan_hash,
        "recovery_plan_hash": snap.recovery_plan_hash,
        "state": snap.state.value,
        "created_unix_ms": int(snap.created_unix_ms),
        "started_unix_ms": int(snap.started_unix_ms),
        "completed_unix_ms": int(snap.completed_unix_ms),
        "attempts_used": int(snap.attempts_used),
        "transition_count": int(snap.transition_count),
        "failure_reason": _check_reason(snap.failure_reason),
        "execution_started": bool(snap.execution_started),
        "pending_recovery": bool(pending_recovery),
        "version": int(getattr(snap, "version", 1) or 1),
        "updated_unix_ms": _now_ms(),
    }


class PostgresLedgerStore:
    def __init__(self, manager: Any) -> None:
        self._db = manager

    def ensure_schema(self) -> None:
        conn = self._db.get_connection()
        if not conn:
            raise LedgerUnavailable()
        try:
            cur = conn.cursor()
            for stmt in LEDGER_DDL:
                cur.execute(stmt)
            conn.commit()
            cur.close()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise LedgerMigrationError()
        finally:
            self._db.release_connection(conn)

    def create(self, snap: TaskSnapshot, pending_recovery: bool = False) -> LedgerTaskRecord:
        if type(snap) is not TaskSnapshot:
            raise LedgerSerializationError()
        row = _row_from_snapshot(snap, pending_recovery)
        conn = self._db.get_connection()
        if not conn:
            raise LedgerUnavailable()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM doom_v8_tasks")
            if int(cur.fetchone()[0]) >= MAX_TASKS:
                raise LedgerCapacityError()
            cur.execute(
                """
                INSERT INTO doom_v8_tasks (
                    task_id, goal_id, owner_id, session_id, computer_session_id,
                    plan_hash, recovery_plan_hash, state, created_unix_ms, started_unix_ms,
                    completed_unix_ms, attempts_used, transition_count, failure_reason,
                    execution_started, pending_recovery, version, updated_unix_ms
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                (
                    row["task_id"], row["goal_id"], row["owner_id"], row["session_id"],
                    row["computer_session_id"], row["plan_hash"], row["recovery_plan_hash"],
                    row["state"], row["created_unix_ms"], row["started_unix_ms"],
                    row["completed_unix_ms"], row["attempts_used"], row["transition_count"],
                    row["failure_reason"], row["execution_started"], row["pending_recovery"],
                    row["version"], row["updated_unix_ms"],
                ),
            )
            conn.commit()
            cur.close()
            return LedgerTaskRecord(snapshot=snap, version=row["version"], pending_recovery=pending_recovery)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            if getattr(exc, "pgcode", "") == "23505":
                raise TaskAlreadyExists()
            if isinstance(exc, (TaskAlreadyExists, LedgerCapacityError, LedgerValidationError, LedgerSerializationError)):
                raise
            raise LedgerTransactionError()
        finally:
            self._db.release_connection(conn)

    def persist_transition(
        self,
        task_id: str,
        expected_version: int,
        nxt: TaskState,
        *,
        reason: str,
        now_ms: int,
        plan_hash: str = "",
        recovery_plan_hash: str = "",
        started_unix_ms: int = -1,
        completed_unix_ms: int = -1,
        attempts_used: int = -1,
        execution_started: Optional[bool] = None,
        pending_recovery: Optional[bool] = None,
    ) -> LedgerTaskRecord:
        tid = _check_id("task_id", task_id)
        conn = self._db.get_connection()
        if not conn:
            raise LedgerUnavailable()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM doom_v8_tasks WHERE task_id = %s FOR UPDATE",
                (tid,),
            )
            raw = cur.fetchone()
            if raw is None:
                raise TaskNotFound()
            cols = [d[0] for d in cur.description]
            row = dict(zip(cols, raw))
            if int(row["version"]) != int(expected_version):
                raise LedgerConcurrencyError()
            current = TaskState(str(row["state"]))
            ensure_transition(current, nxt)
            seq = int(row["transition_count"]) + 1
            if seq > MAX_TRANSITIONS_PER_TASK:
                raise LedgerValidationError("TRANSITION_LIMIT_EXCEEDED")
            ph = _check_hash(plan_hash or row["plan_hash"])
            cur.execute(
                """
                INSERT INTO doom_v8_task_transitions (
                    task_id, sequence_number, from_state, to_state, timestamp_unix_ms,
                    reason_code, plan_hash, version
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (tid, seq, current.value, nxt.value, int(now_ms), _check_reason(reason), ph, int(row["version"]) + 1),
            )
            started = int(row["started_unix_ms"]) if started_unix_ms < 0 else int(started_unix_ms)
            done = int(row["completed_unix_ms"])
            if nxt in TERMINAL_STATES:
                done = int(completed_unix_ms if completed_unix_ms >= 0 else now_ms)
            rec_hash = row["recovery_plan_hash"] if not recovery_plan_hash else _check_hash(recovery_plan_hash)
            attempts = int(row["attempts_used"]) if attempts_used < 0 else int(attempts_used)
            exec_flag = bool(row["execution_started"]) if execution_started is None else bool(execution_started)
            pending = bool(row["pending_recovery"]) if pending_recovery is None else bool(pending_recovery)
            cur.execute(
                """
                UPDATE doom_v8_tasks SET
                    state = %s, version = %s, transition_count = %s, failure_reason = %s,
                    started_unix_ms = %s, completed_unix_ms = %s, recovery_plan_hash = %s,
                    attempts_used = %s, execution_started = %s, pending_recovery = %s,
                    updated_unix_ms = %s
                WHERE task_id = %s AND version = %s
                """,
                (
                    nxt.value, int(row["version"]) + 1, seq, _check_reason(reason),
                    started, done, rec_hash, attempts, exec_flag, pending, int(now_ms),
                    tid, int(expected_version),
                ),
            )
            if cur.rowcount != 1:
                raise LedgerConcurrencyError()
            cur.execute(
                """
                SELECT from_state, to_state, timestamp_unix_ms, reason_code, plan_hash
                FROM doom_v8_task_transitions
                WHERE task_id = %s ORDER BY sequence_number ASC LIMIT %s
                """,
                (tid, MAX_TRANSITIONS_PER_TASK),
            )
            events = tuple(
                TaskTransition(
                    from_state=str(r[0]), to_state=str(r[1]), timestamp_unix_ms=int(r[2]),
                    reason_code=str(r[3]), plan_hash=str(r[4] or ""),
                )
                for r in cur.fetchall()
            )
            conn.commit()
            cur.close()
            row["state"] = nxt.value
            row["version"] = int(row["version"]) + 1
            row["transition_count"] = seq
            row["failure_reason"] = _check_reason(reason)
            row["started_unix_ms"] = started
            row["completed_unix_ms"] = done
            row["recovery_plan_hash"] = rec_hash
            row["attempts_used"] = attempts
            row["execution_started"] = exec_flag
            row["pending_recovery"] = pending
            snap = snapshot_from_row(row, events)
            return LedgerTaskRecord(
                snapshot=snap,
                version=int(row["version"]),
                pending_recovery=pending,
            )
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            if isinstance(exc, (
                TaskNotFound, LedgerConcurrencyError, LedgerValidationError,
                LedgerUnavailable, InvalidTaskTransition, LedgerSerializationError,
            )):
                raise
            raise LedgerTransactionError()
        finally:
            self._db.release_connection(conn)

    def get(self, task_id: str) -> LedgerTaskRecord:
        tid = _check_id("task_id", task_id)
        conn = self._db.get_connection()
        if not conn:
            raise LedgerUnavailable()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM doom_v8_tasks WHERE task_id = %s", (tid,))
            raw = cur.fetchone()
            if raw is None:
                raise TaskNotFound()
            cols = [d[0] for d in cur.description]
            row = dict(zip(cols, raw))
            cur.execute(
                """
                SELECT from_state, to_state, timestamp_unix_ms, reason_code, plan_hash
                FROM doom_v8_task_transitions
                WHERE task_id = %s ORDER BY sequence_number ASC LIMIT %s
                """,
                (tid, MAX_TRANSITIONS_PER_TASK),
            )
            events = tuple(
                TaskTransition(
                    from_state=str(r[0]), to_state=str(r[1]), timestamp_unix_ms=int(r[2]),
                    reason_code=str(r[3]), plan_hash=str(r[4] or ""),
                )
                for r in cur.fetchall()
            )
            cur.close()
            snap = snapshot_from_row(row, events)
            return LedgerTaskRecord(snapshot=snap, version=int(row["version"]), pending_recovery=bool(row["pending_recovery"]))
        finally:
            self._db.release_connection(conn)

    def history(self, task_id: str) -> LedgerHistory:
        rec = self.get(task_id)
        return LedgerHistory(task_id=rec.snapshot.task_id, transitions=rec.snapshot.transitions)

    def list_tasks(
        self,
        *,
        owner_id: str = "",
        goal_id: str = "",
        state: str = "",
        created_from_ms: int = -1,
        created_to_ms: int = -1,
    ) -> List[LedgerTaskRecord]:
        conn = self._db.get_connection()
        if not conn:
            raise LedgerUnavailable()
        try:
            cur = conn.cursor()
            sql = "SELECT * FROM doom_v8_tasks"
            params: List[Any] = []
            clauses = []
            if owner_id:
                clauses.append("owner_id = %s")
                params.append(owner_id)
            if goal_id:
                clauses.append("goal_id = %s")
                params.append(goal_id)
            if state:
                clauses.append("state = %s")
                params.append(state)
            if created_from_ms >= 0:
                clauses.append("created_unix_ms >= %s")
                params.append(int(created_from_ms))
            if created_to_ms >= 0:
                clauses.append("created_unix_ms <= %s")
                params.append(int(created_to_ms))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY created_unix_ms DESC LIMIT %s"
            params.append(MAX_TASKS)
            cur.execute(sql, tuple(params))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.close()
            return [
                LedgerTaskRecord(snapshot=snapshot_from_row(row), version=int(row["version"]), pending_recovery=bool(row["pending_recovery"]))
                for row in rows
            ]
        finally:
            self._db.release_connection(conn)
