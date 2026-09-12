"""V8.7 persistent task ledger API.

Durable orchestration history only. Never executes, authorizes, resumes,
replays, or schedules work. In-flight RUNNING/RECOVERING records are
reconciled to ABORTED on rehydrate. Process restart does not continue
external mutations.
"""

from __future__ import annotations

from typing import Any, List, Optional

from orchestration.task.ledger_errors import LedgerUnavailable
from orchestration.task.ledger_models import LedgerHistory, LedgerTaskRecord
from orchestration.task.ledger_store import IN_FLIGHT, MemoryLedgerStore, PostgresLedgerStore
from orchestration.task.types import TaskSnapshot, TaskState
from proactive.config import is_v8_enabled, is_v8_ledger_enabled

_STORE: Any = None
_MEMORY: Optional[MemoryLedgerStore] = None


def reset_ledger_for_tests() -> None:
    global _STORE, _MEMORY
    if _MEMORY is not None:
        _MEMORY.clear()
    _STORE = None
    _MEMORY = None


def use_memory_ledger_for_tests() -> MemoryLedgerStore:
    """Explicit test double. Not used as a silent production fallback."""
    global _STORE, _MEMORY
    _MEMORY = MemoryLedgerStore()
    _STORE = _MEMORY
    return _MEMORY


def _postgres_store() -> PostgresLedgerStore:
    from database.postgres_db import postgres_manager
    store = PostgresLedgerStore(postgres_manager)
    store.ensure_schema()
    return store


def active_store() -> Any:
    global _STORE
    if _STORE is not None:
        return _STORE
    if not is_v8_ledger_enabled():
        raise LedgerUnavailable()
    _STORE = _postgres_store()
    return _STORE


def ledger_create(snapshot: TaskSnapshot, pending_recovery: bool = False) -> LedgerTaskRecord:
    if _STORE is None and not is_v8_ledger_enabled():
        raise LedgerUnavailable()
    return active_store().create(snapshot, pending_recovery=pending_recovery)


def ledger_get(task_id: str) -> LedgerTaskRecord:
    if _STORE is None and not is_v8_ledger_enabled():
        raise LedgerUnavailable()
    return active_store().get(task_id)


def ledger_history(task_id: str) -> LedgerHistory:
    if _STORE is None and not is_v8_ledger_enabled():
        raise LedgerUnavailable()
    return active_store().history(task_id)


def ledger_list(
    *,
    owner_id: str = "",
    goal_id: str = "",
    state: str = "",
    created_from_ms: int = -1,
    created_to_ms: int = -1,
) -> List[LedgerTaskRecord]:
    if _STORE is None and not is_v8_ledger_enabled():
        raise LedgerUnavailable()
    return active_store().list_tasks(
        owner_id=owner_id,
        goal_id=goal_id,
        state=state,
        created_from_ms=created_from_ms,
        created_to_ms=created_to_ms,
    )


def persist_transition(
    task_id: str,
    expected_version: int,
    nxt: TaskState,
    **kwargs: Any,
) -> LedgerTaskRecord:
    if _STORE is None and not is_v8_ledger_enabled():
        raise LedgerUnavailable()
    return active_store().persist_transition(task_id, expected_version, nxt, **kwargs)


def try_persist_create(snapshot: TaskSnapshot, pending_recovery: bool = False) -> None:
    """Engine hook. No writes when V8 is off. Explicit ledger_* APIs still work for tests."""
    if not is_v8_enabled():
        return
    if _STORE is None and not is_v8_ledger_enabled():
        return
    store = _STORE if _STORE is not None else active_store()
    store.create(snapshot, pending_recovery=pending_recovery)


def try_persist_transition(snapshot: TaskSnapshot, nxt: TaskState, **kwargs: Any) -> Optional[LedgerTaskRecord]:
    if not is_v8_enabled():
        return None
    if _STORE is None and not is_v8_ledger_enabled():
        return None
    store = _STORE if _STORE is not None else active_store()
    return store.persist_transition(snapshot.task_id, int(snapshot.version), nxt, **kwargs)


def reconcile_stale(now_ms: int) -> List[str]:
    """Mark in-flight persisted tasks ABORTED. Does not execute anything."""
    store = active_store()
    changed: List[str] = []
    for rec in store.list_tasks():
        if rec.snapshot.state in IN_FLIGHT:
            store.persist_transition(
                rec.snapshot.task_id,
                rec.version,
                TaskState.ABORTED,
                reason="STALE_IN_FLIGHT",
                now_ms=now_ms,
                plan_hash=rec.snapshot.plan_hash,
                execution_started=True,
                pending_recovery=False,
            )
            changed.append(rec.snapshot.task_id)
    return changed


def rehydrate(*, execute: bool = False) -> List[TaskSnapshot]:
    """Load durable snapshots for inspection. execute=True is rejected."""
    if execute:
        raise LedgerUnavailable("REHYDRATE_EXECUTE_FORBIDDEN")
    from orchestration.task.registry import load_snapshot_for_inspection, reset_task_registry_for_tests
    import time
    store = active_store()
    reconcile_stale(int(time.time() * 1000))
    reset_task_registry_for_tests()
    snaps = [rec.snapshot for rec in store.list_tasks()]
    for snap in snaps:
        load_snapshot_for_inspection(snap)
    return snaps
