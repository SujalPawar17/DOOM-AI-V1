"""V8.26 Active Goal Registry — persistence foundation.

Owner-scoped, single-ACTIVE-goal store for bounded plan snapshots.
Informational only. Zero execution authority. No routing integration.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = 1
MAX_TITLE_CHARS = 80
MAX_PLAN_TITLE_CHARS = 80
MAX_STEPS = 6
MAX_STEP_TITLE_CHARS = 80
MAX_BLOCKER_CHARS = 120
MAX_STALENESS_CHARS = 120
MAX_DURABLE_VIEW_CHARS = 300
MAX_OWNER_CHARS = 64
MAX_GOAL_ID_CHARS = 64
MAX_REASON_CHARS = 120
ARCHIVE_RETENTION = 5

_SECRETISH = re.compile(
    r"(?i)("
    r"api[_-]?key|bearer\b|password|passwd|\bpwd\b|"
    r"cookie|csrf|private[_ -]?key|authorization|"
    r"session_id|session_id_hash|ask_session|doom_ask|"
    r"executionidentity|owner_id\s*[:=]|plan_hash|"
    r"authorized_plan_hash|computer_session|browser_session|\bbws_|"
    r"csrf_token|postgres|database.{0,12}(user|pass|cred)|"
    r"-----BEGIN|hidden system prompt|internal (security )?policy|"
    r"\bsecrets?\b|supersecret|sk-[a-z0-9]{8,}"
    r")"
)
_SQLISH = re.compile(r"(?i)\b(select|insert|update|delete)\b.+\bfrom\b|\bcreate table\b")

_LOCK = threading.Lock()
_USE_TEST_STORE = False
_TEST_ROWS: Dict[str, Dict[str, Any]] = {}  # goal_id -> row dict


class StepState(str, Enum):
    """Registry-owned wire values — identical strings to V8.25 continuity StepState."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class GoalLifecycle(str, Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    STALE = "STALE"
    ABANDONED = "ABANDONED"


class RegistryStatus(str, Enum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_SNAPSHOT = "INVALID_SNAPSHOT"
    VERSION_UNSUPPORTED = "VERSION_UNSUPPORTED"
    OWNER_MISMATCH = "OWNER_MISMATCH"
    CONFLICT = "CONFLICT"
    REJECTED = "REJECTED"


_TERMINAL_STEPS = frozenset({StepState.COMPLETED, StepState.SKIPPED})

_VALID_TRANSITIONS = frozenset({
    (GoalLifecycle.ACTIVE, GoalLifecycle.COMPLETED),
    (GoalLifecycle.ACTIVE, GoalLifecycle.STALE),
    (GoalLifecycle.ACTIVE, GoalLifecycle.ABANDONED),
    (GoalLifecycle.STALE, GoalLifecycle.ACTIVE),
    (GoalLifecycle.STALE, GoalLifecycle.ABANDONED),
})


@dataclass(frozen=True)
class GoalSnapshot:
    schema_version: int
    goal_id: str
    owner_id: str
    title: str
    lifecycle: GoalLifecycle
    plan_title: str
    step_titles: Tuple[str, ...]
    step_states: Tuple[StepState, ...]
    active_step_index: int
    blocker_summary: str
    staleness_reason: str
    durable_view: str
    version: int
    created_at: float
    updated_at: float
    last_active_at: float


@dataclass(frozen=True)
class RegistryResult:
    status: RegistryStatus
    snapshot: Optional[GoalSnapshot] = None


def use_test_goal_registry_store(enabled: bool = True) -> None:
    global _USE_TEST_STORE
    with _LOCK:
        _USE_TEST_STORE = bool(enabled)
        if enabled:
            _TEST_ROWS.clear()


def reset_goal_registry_for_tests() -> None:
    with _LOCK:
        _TEST_ROWS.clear()


def new_registry_goal_id() -> str:
    return f"rg_{uuid.uuid4().hex}"


def is_sensitive_goal_content(text: str) -> bool:
    blob = str(text or "")
    if not blob.strip():
        return False
    if _SECRETISH.search(blob):
        return True
    if _SQLISH.search(blob):
        return True
    if "\x00" in blob:
        return True
    return False


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _is_v826_goal_registry_enabled() -> bool:
    """Mirror proactive.config.is_v826_goal_registry_enabled without importing
    the proactive package (proactive/__init__.py eagerly loads worker → DB).
    """
    if not _bool_env("PROACTIVE_V8_ENABLED", False):
        return False
    return _bool_env("PROACTIVE_V826_GOAL_REGISTRY_ENABLED", False)


def _enabled() -> bool:
    return bool(_is_v826_goal_registry_enabled())


def _normalize_owner(owner_id: str) -> str:
    return str(owner_id or "").strip()[:MAX_OWNER_CHARS]


def _clip(text: str, limit: int) -> str:
    return " ".join(str(text or "").strip().split())[:limit]


def validate_snapshot(snapshot: GoalSnapshot, *, expected_owner: str = "") -> RegistryStatus:
    """Validate snapshot bounds and invariants. Returns OK or error status."""
    if snapshot is None:
        return RegistryStatus.REJECTED
    if int(snapshot.schema_version) != SCHEMA_VERSION:
        return RegistryStatus.VERSION_UNSUPPORTED

    owner = _normalize_owner(snapshot.owner_id)
    if not owner:
        return RegistryStatus.REJECTED
    if expected_owner and owner != _normalize_owner(expected_owner):
        return RegistryStatus.OWNER_MISMATCH

    gid = str(snapshot.goal_id or "").strip()[:MAX_GOAL_ID_CHARS]
    if not gid:
        return RegistryStatus.REJECTED

    title = str(snapshot.title or "")
    plan_title = str(snapshot.plan_title or "")
    if len(title) > MAX_TITLE_CHARS or len(plan_title) > MAX_PLAN_TITLE_CHARS:
        return RegistryStatus.REJECTED
    if not title.strip() or not plan_title.strip():
        return RegistryStatus.REJECTED

    if not isinstance(snapshot.lifecycle, GoalLifecycle):
        return RegistryStatus.REJECTED

    titles = tuple(snapshot.step_titles or ())
    states = tuple(snapshot.step_states or ())
    if len(titles) != len(states):
        return RegistryStatus.REJECTED
    if not (1 <= len(titles) <= MAX_STEPS):
        return RegistryStatus.REJECTED
    for t in titles:
        if not isinstance(t, str) or not t.strip() or len(t) > MAX_STEP_TITLE_CHARS:
            return RegistryStatus.REJECTED
        if is_sensitive_goal_content(t):
            return RegistryStatus.REJECTED
    for s in states:
        if not isinstance(s, StepState):
            return RegistryStatus.REJECTED

    act = int(snapshot.active_step_index)
    if act < 0 or act > len(titles):
        return RegistryStatus.REJECTED
    if act > 0:
        st = states[act - 1]
        if st in (StepState.COMPLETED, StepState.SKIPPED, StepState.BLOCKED):
            return RegistryStatus.REJECTED

    if snapshot.lifecycle is GoalLifecycle.COMPLETED:
        if not all(s in _TERMINAL_STEPS for s in states):
            return RegistryStatus.REJECTED

    blocker = str(snapshot.blocker_summary or "")
    stale = str(snapshot.staleness_reason or "")
    durable = str(snapshot.durable_view or "")
    if len(blocker) > MAX_BLOCKER_CHARS or len(stale) > MAX_STALENESS_CHARS:
        return RegistryStatus.REJECTED
    if len(durable) > MAX_DURABLE_VIEW_CHARS:
        return RegistryStatus.REJECTED

    for blob in (title, plan_title, blocker, stale, durable, gid, owner):
        if is_sensitive_goal_content(blob):
            return RegistryStatus.REJECTED

    if int(snapshot.version) < 1:
        return RegistryStatus.REJECTED

    return RegistryStatus.OK


def build_snapshot(
    *,
    owner_id: str,
    title: str,
    plan_title: str,
    step_titles: Tuple[str, ...],
    step_states: Tuple[StepState, ...],
    goal_id: str = "",
    lifecycle: GoalLifecycle = GoalLifecycle.ACTIVE,
    active_step_index: int = 1,
    blocker_summary: str = "",
    staleness_reason: str = "",
    durable_view: str = "",
    version: int = 1,
    created_at: Optional[float] = None,
    updated_at: Optional[float] = None,
    last_active_at: Optional[float] = None,
    schema_version: int = SCHEMA_VERSION,
) -> Tuple[Optional[GoalSnapshot], RegistryStatus]:
    """Construct a bounded GoalSnapshot or return rejection status."""
    now = time.time()
    snap = GoalSnapshot(
        schema_version=int(schema_version),
        goal_id=str(goal_id or new_registry_goal_id())[:MAX_GOAL_ID_CHARS],
        owner_id=_normalize_owner(owner_id),
        title=_clip(title, MAX_TITLE_CHARS),
        lifecycle=lifecycle if isinstance(lifecycle, GoalLifecycle) else GoalLifecycle.ACTIVE,
        plan_title=_clip(plan_title, MAX_PLAN_TITLE_CHARS),
        step_titles=tuple(_clip(t, MAX_STEP_TITLE_CHARS) for t in (step_titles or ())),
        step_states=tuple(step_states or ()),
        active_step_index=int(active_step_index),
        blocker_summary=_clip(blocker_summary, MAX_BLOCKER_CHARS),
        staleness_reason=_clip(staleness_reason, MAX_STALENESS_CHARS),
        durable_view=_clip(durable_view, MAX_DURABLE_VIEW_CHARS),
        version=max(1, int(version)),
        created_at=float(created_at if created_at is not None else now),
        updated_at=float(updated_at if updated_at is not None else now),
        last_active_at=float(last_active_at if last_active_at is not None else now),
    )
    status = validate_snapshot(snap)
    if status is not RegistryStatus.OK:
        return None, status
    return snap, RegistryStatus.OK


def _row_from_snapshot(snap: GoalSnapshot) -> Dict[str, Any]:
    return {
        "goal_id": snap.goal_id,
        "owner_id": snap.owner_id,
        "lifecycle": snap.lifecycle.value,
        "schema_version": int(snap.schema_version),
        "title": snap.title,
        "plan_title": snap.plan_title,
        "step_titles_json": json.dumps(list(snap.step_titles), separators=(",", ":")),
        "step_states_json": json.dumps([s.value for s in snap.step_states], separators=(",", ":")),
        "active_step_index": int(snap.active_step_index),
        "blocker_summary": snap.blocker_summary or "",
        "staleness_reason": snap.staleness_reason or "",
        "durable_view": snap.durable_view or "",
        "version": int(snap.version),
        "created_at": float(snap.created_at),
        "updated_at": float(snap.updated_at),
        "last_active_at": float(snap.last_active_at),
    }


def _snapshot_from_row(row: Dict[str, Any]) -> Tuple[Optional[GoalSnapshot], RegistryStatus]:
    try:
        sv = int(row.get("schema_version") or 0)
        if sv != SCHEMA_VERSION:
            return None, RegistryStatus.VERSION_UNSUPPORTED
        titles_raw = row.get("step_titles_json") or "[]"
        states_raw = row.get("step_states_json") or "[]"
        if isinstance(titles_raw, str):
            titles_list = json.loads(titles_raw)
        else:
            titles_list = list(titles_raw)
        if isinstance(states_raw, str):
            states_list = json.loads(states_raw)
        else:
            states_list = list(states_raw)
        if not isinstance(titles_list, list) or not isinstance(states_list, list):
            return None, RegistryStatus.INVALID_SNAPSHOT
        titles = tuple(str(t) for t in titles_list)
        states: List[StepState] = []
        for s in states_list:
            if isinstance(s, StepState):
                states.append(s)
            else:
                states.append(StepState(str(s)))
        lifecycle = GoalLifecycle(str(row.get("lifecycle") or ""))
        snap = GoalSnapshot(
            schema_version=sv,
            goal_id=str(row.get("goal_id") or ""),
            owner_id=str(row.get("owner_id") or ""),
            title=str(row.get("title") or ""),
            lifecycle=lifecycle,
            plan_title=str(row.get("plan_title") or ""),
            step_titles=titles,
            step_states=tuple(states),
            active_step_index=int(row.get("active_step_index") or 0),
            blocker_summary=str(row.get("blocker_summary") or ""),
            staleness_reason=str(row.get("staleness_reason") or ""),
            durable_view=str(row.get("durable_view") or ""),
            version=int(row.get("version") or 0),
            created_at=float(row.get("created_at") or 0),
            updated_at=float(row.get("updated_at") or 0),
            last_active_at=float(row.get("last_active_at") or 0),
        )
    except Exception:
        return None, RegistryStatus.INVALID_SNAPSHOT
    status = validate_snapshot(snap)
    if status is not RegistryStatus.OK:
        if status is RegistryStatus.VERSION_UNSUPPORTED:
            return None, status
        return None, RegistryStatus.INVALID_SNAPSHOT
    return snap, RegistryStatus.OK


def ensure_goal_registry_schema() -> bool:
    try:
        from database.postgres_db import postgres_manager

        if not postgres_manager.is_connected():
            return False
        conn = postgres_manager.get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS v8_active_goals (
                        goal_id VARCHAR(64) PRIMARY KEY,
                        owner_id VARCHAR(64) NOT NULL,
                        lifecycle VARCHAR(16) NOT NULL
                            CHECK (lifecycle IN ('ACTIVE','COMPLETED','STALE','ABANDONED')),
                        schema_version INTEGER NOT NULL,
                        title VARCHAR(80) NOT NULL,
                        plan_title VARCHAR(80) NOT NULL,
                        step_titles_json TEXT NOT NULL,
                        step_states_json TEXT NOT NULL,
                        active_step_index INTEGER NOT NULL DEFAULT 0,
                        blocker_summary VARCHAR(120) NOT NULL DEFAULT '',
                        staleness_reason VARCHAR(120) NOT NULL DEFAULT '',
                        durable_view VARCHAR(300) NOT NULL DEFAULT '',
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at DOUBLE PRECISION NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL,
                        last_active_at DOUBLE PRECISION NOT NULL
                    );
                    """
                )
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_v8_ag_owner_active "
                    "ON v8_active_goals (owner_id) WHERE lifecycle = 'ACTIVE';"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_v8_ag_owner_updated "
                    "ON v8_active_goals (owner_id, updated_at DESC);"
                )
            conn.commit()
            return True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return False


def _test_active_for_owner(owner: str) -> Optional[Dict[str, Any]]:
    for row in _TEST_ROWS.values():
        if row.get("owner_id") == owner and row.get("lifecycle") == GoalLifecycle.ACTIVE.value:
            return row
    return None


def _purge_archive_test(owner: str) -> None:
    """Keep last ARCHIVE_RETENTION non-ACTIVE goals per owner."""
    terminals = [
        r for r in _TEST_ROWS.values()
        if r.get("owner_id") == owner and r.get("lifecycle") != GoalLifecycle.ACTIVE.value
    ]
    terminals.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=True)
    for row in terminals[ARCHIVE_RETENTION:]:
        _TEST_ROWS.pop(str(row.get("goal_id") or ""), None)


def _purge_archive_pg(cur: Any, owner: str) -> None:
    cur.execute(
        """
        SELECT goal_id FROM v8_active_goals
        WHERE owner_id = %s AND lifecycle <> 'ACTIVE'
        ORDER BY updated_at DESC
        """,
        (owner,),
    )
    rows = cur.fetchall() or []
    if len(rows) <= ARCHIVE_RETENTION:
        return
    drop_ids = [str(r[0]) for r in rows[ARCHIVE_RETENTION:]]
    for gid in drop_ids:
        cur.execute("DELETE FROM v8_active_goals WHERE goal_id = %s AND owner_id = %s", (gid, owner))


def create_active_goal(owner_id: str, snapshot: GoalSnapshot) -> RegistryResult:
    if not _enabled():
        return RegistryResult(RegistryStatus.UNAVAILABLE)
    owner = _normalize_owner(owner_id)
    if not owner:
        return RegistryResult(RegistryStatus.REJECTED)
    status = validate_snapshot(snapshot, expected_owner=owner)
    if status is not RegistryStatus.OK:
        return RegistryResult(status)
    if snapshot.lifecycle is not GoalLifecycle.ACTIVE:
        return RegistryResult(RegistryStatus.REJECTED)

    now = time.time()
    snap = GoalSnapshot(
        schema_version=snapshot.schema_version,
        goal_id=snapshot.goal_id,
        owner_id=owner,
        title=snapshot.title,
        lifecycle=GoalLifecycle.ACTIVE,
        plan_title=snapshot.plan_title,
        step_titles=snapshot.step_titles,
        step_states=snapshot.step_states,
        active_step_index=snapshot.active_step_index,
        blocker_summary=snapshot.blocker_summary,
        staleness_reason="",
        durable_view=snapshot.durable_view,
        version=max(1, int(snapshot.version)),
        created_at=float(snapshot.created_at or now),
        updated_at=now,
        last_active_at=now,
    )
    row = _row_from_snapshot(snap)

    if _USE_TEST_STORE:
        with _LOCK:
            existing = _test_active_for_owner(owner)
            if existing is not None:
                if str(existing.get("goal_id")) == snap.goal_id:
                    # Idempotent same-id upsert while ACTIVE
                    row["created_at"] = float(existing.get("created_at") or snap.created_at)
                    row["version"] = int(existing.get("version") or 1) + 1
                    _TEST_ROWS[snap.goal_id] = row
                    out, st = _snapshot_from_row(row)
                    return RegistryResult(st if out is None else RegistryStatus.OK, out)
                return RegistryResult(RegistryStatus.CONFLICT)
            prior = _TEST_ROWS.get(snap.goal_id)
            if prior is not None and str(prior.get("owner_id")) != owner:
                return RegistryResult(RegistryStatus.OWNER_MISMATCH)
            _TEST_ROWS[snap.goal_id] = row
            out, st = _snapshot_from_row(row)
            return RegistryResult(st if out is None else RegistryStatus.OK, out)

    try:
        if not ensure_goal_registry_schema():
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT goal_id, owner_id, version, created_at FROM v8_active_goals "
                    "WHERE owner_id = %s AND lifecycle = 'ACTIVE'",
                    (owner,),
                )
                active = cur.fetchone()
                if active:
                    agid = str(active[0])
                    if agid == snap.goal_id:
                        new_ver = int(active[2] or 1) + 1
                        cur.execute(
                            """
                            UPDATE v8_active_goals SET
                                schema_version=%s, title=%s, plan_title=%s,
                                step_titles_json=%s, step_states_json=%s,
                                active_step_index=%s, blocker_summary=%s,
                                staleness_reason=%s, durable_view=%s,
                                version=%s, updated_at=%s, last_active_at=%s
                            WHERE goal_id=%s AND owner_id=%s AND lifecycle='ACTIVE'
                            """,
                            (
                                snap.schema_version, snap.title, snap.plan_title,
                                row["step_titles_json"], row["step_states_json"],
                                snap.active_step_index, snap.blocker_summary,
                                "", snap.durable_view, new_ver, now, now,
                                snap.goal_id, owner,
                            ),
                        )
                        conn.commit()
                        cur.execute(
                            "SELECT goal_id, owner_id, lifecycle, schema_version, title, plan_title, "
                            "step_titles_json, step_states_json, active_step_index, blocker_summary, "
                            "staleness_reason, durable_view, version, created_at, updated_at, last_active_at "
                            "FROM v8_active_goals WHERE goal_id=%s",
                            (snap.goal_id,),
                        )
                        fetched = cur.fetchone()
                        if not fetched:
                            return RegistryResult(RegistryStatus.UNAVAILABLE)
                        mapped = _pg_row_to_dict(fetched)
                        out, st = _snapshot_from_row(mapped)
                        return RegistryResult(st if out is None else RegistryStatus.OK, out)
                    return RegistryResult(RegistryStatus.CONFLICT)

                cur.execute(
                    "SELECT owner_id FROM v8_active_goals WHERE goal_id = %s",
                    (snap.goal_id,),
                )
                prior = cur.fetchone()
                if prior and str(prior[0]) != owner:
                    return RegistryResult(RegistryStatus.OWNER_MISMATCH)

                cur.execute(
                    """
                    INSERT INTO v8_active_goals (
                        goal_id, owner_id, lifecycle, schema_version, title, plan_title,
                        step_titles_json, step_states_json, active_step_index,
                        blocker_summary, staleness_reason, durable_view, version,
                        created_at, updated_at, last_active_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    ON CONFLICT (goal_id) DO UPDATE SET
                        owner_id=EXCLUDED.owner_id,
                        lifecycle='ACTIVE',
                        schema_version=EXCLUDED.schema_version,
                        title=EXCLUDED.title,
                        plan_title=EXCLUDED.plan_title,
                        step_titles_json=EXCLUDED.step_titles_json,
                        step_states_json=EXCLUDED.step_states_json,
                        active_step_index=EXCLUDED.active_step_index,
                        blocker_summary=EXCLUDED.blocker_summary,
                        staleness_reason='',
                        durable_view=EXCLUDED.durable_view,
                        version=v8_active_goals.version + 1,
                        updated_at=EXCLUDED.updated_at,
                        last_active_at=EXCLUDED.last_active_at
                    WHERE v8_active_goals.owner_id = EXCLUDED.owner_id
                      AND v8_active_goals.lifecycle <> 'ACTIVE'
                    """,
                    (
                        snap.goal_id, owner, GoalLifecycle.ACTIVE.value, snap.schema_version,
                        snap.title, snap.plan_title, row["step_titles_json"], row["step_states_json"],
                        snap.active_step_index, snap.blocker_summary, "", snap.durable_view,
                        snap.version, snap.created_at, now, now,
                    ),
                )
                if cur.rowcount == 0 and prior:
                    # Conflict with unexpected state
                    return RegistryResult(RegistryStatus.CONFLICT)
            conn.commit()
            return get_active_goal(owner)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            err = str(exc).lower()
            if "unique" in err or "duplicate" in err:
                return RegistryResult(RegistryStatus.CONFLICT)
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return RegistryResult(RegistryStatus.UNAVAILABLE)


def _pg_row_to_dict(fetched: Tuple[Any, ...]) -> Dict[str, Any]:
    return {
        "goal_id": fetched[0],
        "owner_id": fetched[1],
        "lifecycle": fetched[2],
        "schema_version": fetched[3],
        "title": fetched[4],
        "plan_title": fetched[5],
        "step_titles_json": fetched[6],
        "step_states_json": fetched[7],
        "active_step_index": fetched[8],
        "blocker_summary": fetched[9],
        "staleness_reason": fetched[10],
        "durable_view": fetched[11],
        "version": fetched[12],
        "created_at": fetched[13],
        "updated_at": fetched[14],
        "last_active_at": fetched[15],
    }


def get_active_goal(owner_id: str) -> RegistryResult:
    if not _enabled():
        return RegistryResult(RegistryStatus.UNAVAILABLE)
    owner = _normalize_owner(owner_id)
    if not owner:
        return RegistryResult(RegistryStatus.REJECTED)

    if _USE_TEST_STORE:
        with _LOCK:
            row = _test_active_for_owner(owner)
            if row is None:
                return RegistryResult(RegistryStatus.NOT_FOUND)
            out, st = _snapshot_from_row(row)
            if out is None:
                return RegistryResult(st)
            return RegistryResult(RegistryStatus.OK, out)

    try:
        if not ensure_goal_registry_schema():
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT goal_id, owner_id, lifecycle, schema_version, title, plan_title, "
                    "step_titles_json, step_states_json, active_step_index, blocker_summary, "
                    "staleness_reason, durable_view, version, created_at, updated_at, last_active_at "
                    "FROM v8_active_goals WHERE owner_id=%s AND lifecycle='ACTIVE'",
                    (owner,),
                )
                fetched = cur.fetchone()
            if not fetched:
                return RegistryResult(RegistryStatus.NOT_FOUND)
            out, st = _snapshot_from_row(_pg_row_to_dict(fetched))
            if out is None:
                return RegistryResult(st)
            return RegistryResult(RegistryStatus.OK, out)
        except Exception:
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return RegistryResult(RegistryStatus.UNAVAILABLE)


def update_active_goal(
    owner_id: str,
    goal_id: str,
    snapshot: GoalSnapshot,
    expected_version: int,
) -> RegistryResult:
    if not _enabled():
        return RegistryResult(RegistryStatus.UNAVAILABLE)
    owner = _normalize_owner(owner_id)
    gid = str(goal_id or "").strip()[:MAX_GOAL_ID_CHARS]
    if not owner or not gid:
        return RegistryResult(RegistryStatus.REJECTED)
    status = validate_snapshot(snapshot, expected_owner=owner)
    if status is not RegistryStatus.OK:
        return RegistryResult(status)
    if snapshot.goal_id != gid:
        return RegistryResult(RegistryStatus.REJECTED)
    if snapshot.lifecycle is not GoalLifecycle.ACTIVE:
        return RegistryResult(RegistryStatus.REJECTED)

    now = time.time()
    new_ver = int(expected_version) + 1
    snap = GoalSnapshot(
        schema_version=snapshot.schema_version,
        goal_id=gid,
        owner_id=owner,
        title=snapshot.title,
        lifecycle=GoalLifecycle.ACTIVE,
        plan_title=snapshot.plan_title,
        step_titles=snapshot.step_titles,
        step_states=snapshot.step_states,
        active_step_index=snapshot.active_step_index,
        blocker_summary=snapshot.blocker_summary,
        staleness_reason=snapshot.staleness_reason,
        durable_view=snapshot.durable_view,
        version=new_ver,
        created_at=snapshot.created_at,
        updated_at=now,
        last_active_at=now,
    )
    row = _row_from_snapshot(snap)

    if _USE_TEST_STORE:
        with _LOCK:
            existing = _TEST_ROWS.get(gid)
            if existing is None:
                return RegistryResult(RegistryStatus.NOT_FOUND)
            if str(existing.get("owner_id")) != owner:
                return RegistryResult(RegistryStatus.OWNER_MISMATCH)
            if str(existing.get("lifecycle")) != GoalLifecycle.ACTIVE.value:
                return RegistryResult(RegistryStatus.CONFLICT)
            if int(existing.get("version") or 0) != int(expected_version):
                return RegistryResult(RegistryStatus.CONFLICT)
            row["created_at"] = float(existing.get("created_at") or snap.created_at)
            _TEST_ROWS[gid] = row
            out, st = _snapshot_from_row(row)
            return RegistryResult(st if out is None else RegistryStatus.OK, out)

    try:
        if not ensure_goal_registry_schema():
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE v8_active_goals SET
                        schema_version=%s, title=%s, plan_title=%s,
                        step_titles_json=%s, step_states_json=%s,
                        active_step_index=%s, blocker_summary=%s,
                        staleness_reason=%s, durable_view=%s,
                        version=%s, updated_at=%s, last_active_at=%s
                    WHERE goal_id=%s AND owner_id=%s AND lifecycle='ACTIVE' AND version=%s
                    """,
                    (
                        snap.schema_version, snap.title, snap.plan_title,
                        row["step_titles_json"], row["step_states_json"],
                        snap.active_step_index, snap.blocker_summary,
                        snap.staleness_reason, snap.durable_view,
                        new_ver, now, now, gid, owner, int(expected_version),
                    ),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    cur.execute(
                        "SELECT owner_id, lifecycle, version FROM v8_active_goals WHERE goal_id=%s",
                        (gid,),
                    )
                    chk = cur.fetchone()
                    if not chk:
                        return RegistryResult(RegistryStatus.NOT_FOUND)
                    if str(chk[0]) != owner:
                        return RegistryResult(RegistryStatus.OWNER_MISMATCH)
                    return RegistryResult(RegistryStatus.CONFLICT)
            conn.commit()
            return get_active_goal(owner)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return RegistryResult(RegistryStatus.UNAVAILABLE)


def transition_goal(
    owner_id: str,
    goal_id: str,
    from_state: GoalLifecycle,
    to_state: GoalLifecycle,
    reason: str,
    expected_version: int,
) -> RegistryResult:
    if not _enabled():
        return RegistryResult(RegistryStatus.UNAVAILABLE)
    owner = _normalize_owner(owner_id)
    gid = str(goal_id or "").strip()[:MAX_GOAL_ID_CHARS]
    if not owner or not gid:
        return RegistryResult(RegistryStatus.REJECTED)
    if not isinstance(from_state, GoalLifecycle) or not isinstance(to_state, GoalLifecycle):
        return RegistryResult(RegistryStatus.REJECTED)
    if (from_state, to_state) not in _VALID_TRANSITIONS:
        return RegistryResult(RegistryStatus.REJECTED)
    reason_c = _clip(reason, MAX_REASON_CHARS)
    if is_sensitive_goal_content(reason_c):
        return RegistryResult(RegistryStatus.REJECTED)

    now = time.time()

    if _USE_TEST_STORE:
        with _LOCK:
            existing = _TEST_ROWS.get(gid)
            if existing is None:
                return RegistryResult(RegistryStatus.NOT_FOUND)
            if str(existing.get("owner_id")) != owner:
                return RegistryResult(RegistryStatus.OWNER_MISMATCH)
            if str(existing.get("lifecycle")) != from_state.value:
                return RegistryResult(RegistryStatus.CONFLICT)
            if int(existing.get("version") or 0) != int(expected_version):
                return RegistryResult(RegistryStatus.CONFLICT)
            if to_state is GoalLifecycle.ACTIVE:
                other = _test_active_for_owner(owner)
                if other is not None and str(other.get("goal_id")) != gid:
                    return RegistryResult(RegistryStatus.CONFLICT)
            # Validate COMPLETED preconditions against current row before mutation.
            if to_state is GoalLifecycle.COMPLETED:
                tmp, st = _snapshot_from_row(existing)
                if tmp is None:
                    return RegistryResult(st)
                if not all(s in _TERMINAL_STEPS for s in tmp.step_states):
                    return RegistryResult(RegistryStatus.REJECTED)
            existing = dict(existing)
            existing["lifecycle"] = to_state.value
            existing["version"] = int(expected_version) + 1
            existing["updated_at"] = now
            if to_state is GoalLifecycle.STALE:
                existing["staleness_reason"] = reason_c
            if to_state is GoalLifecycle.ACTIVE:
                existing["last_active_at"] = now
                existing["staleness_reason"] = ""
            _TEST_ROWS[gid] = existing
            if to_state is not GoalLifecycle.ACTIVE:
                _purge_archive_test(owner)
            out, st = _snapshot_from_row(existing)
            return RegistryResult(st if out is None else RegistryStatus.OK, out)

    try:
        if not ensure_goal_registry_schema():
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT goal_id, owner_id, lifecycle, schema_version, title, plan_title, "
                    "step_titles_json, step_states_json, active_step_index, blocker_summary, "
                    "staleness_reason, durable_view, version, created_at, updated_at, last_active_at "
                    "FROM v8_active_goals WHERE goal_id=%s",
                    (gid,),
                )
                fetched = cur.fetchone()
                if not fetched:
                    return RegistryResult(RegistryStatus.NOT_FOUND)
                mapped = _pg_row_to_dict(fetched)
                if str(mapped["owner_id"]) != owner:
                    return RegistryResult(RegistryStatus.OWNER_MISMATCH)
                if str(mapped["lifecycle"]) != from_state.value:
                    return RegistryResult(RegistryStatus.CONFLICT)
                if int(mapped["version"]) != int(expected_version):
                    return RegistryResult(RegistryStatus.CONFLICT)

                if to_state is GoalLifecycle.COMPLETED:
                    tmp, st = _snapshot_from_row(mapped)
                    if tmp is None:
                        return RegistryResult(st)
                    if not all(s in _TERMINAL_STEPS for s in tmp.step_states):
                        return RegistryResult(RegistryStatus.REJECTED)

                if to_state is GoalLifecycle.ACTIVE:
                    cur.execute(
                        "SELECT goal_id FROM v8_active_goals "
                        "WHERE owner_id=%s AND lifecycle='ACTIVE' AND goal_id<>%s",
                        (owner, gid),
                    )
                    if cur.fetchone():
                        return RegistryResult(RegistryStatus.CONFLICT)

                new_ver = int(expected_version) + 1
                stale_reason = reason_c if to_state is GoalLifecycle.STALE else (
                    "" if to_state is GoalLifecycle.ACTIVE else str(mapped.get("staleness_reason") or "")
                )
                last_active = now if to_state is GoalLifecycle.ACTIVE else float(mapped["last_active_at"])
                cur.execute(
                    """
                    UPDATE v8_active_goals SET
                        lifecycle=%s, version=%s, updated_at=%s,
                        staleness_reason=%s, last_active_at=%s
                    WHERE goal_id=%s AND owner_id=%s AND version=%s AND lifecycle=%s
                    """,
                    (
                        to_state.value, new_ver, now, stale_reason, last_active,
                        gid, owner, int(expected_version), from_state.value,
                    ),
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    return RegistryResult(RegistryStatus.CONFLICT)
                if to_state is not GoalLifecycle.ACTIVE:
                    _purge_archive_pg(cur, owner)
            conn.commit()
            # Reload
            return _load_goal(owner, gid)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return RegistryResult(RegistryStatus.UNAVAILABLE)


def _load_goal(owner: str, gid: str) -> RegistryResult:
    if _USE_TEST_STORE:
        with _LOCK:
            row = _TEST_ROWS.get(gid)
            if row is None:
                return RegistryResult(RegistryStatus.NOT_FOUND)
            if str(row.get("owner_id")) != owner:
                return RegistryResult(RegistryStatus.OWNER_MISMATCH)
            out, st = _snapshot_from_row(row)
            return RegistryResult(st if out is None else RegistryStatus.OK, out)
    try:
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT goal_id, owner_id, lifecycle, schema_version, title, plan_title, "
                    "step_titles_json, step_states_json, active_step_index, blocker_summary, "
                    "staleness_reason, durable_view, version, created_at, updated_at, last_active_at "
                    "FROM v8_active_goals WHERE goal_id=%s",
                    (gid,),
                )
                fetched = cur.fetchone()
            if not fetched:
                return RegistryResult(RegistryStatus.NOT_FOUND)
            mapped = _pg_row_to_dict(fetched)
            if str(mapped["owner_id"]) != owner:
                return RegistryResult(RegistryStatus.OWNER_MISMATCH)
            out, st = _snapshot_from_row(mapped)
            return RegistryResult(st if out is None else RegistryStatus.OK, out)
        except Exception:
            return RegistryResult(RegistryStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return RegistryResult(RegistryStatus.UNAVAILABLE)


def archive_goal(owner_id: str, goal_id: str, expected_version: int) -> RegistryResult:
    """ACTIVE|STALE → COMPLETED (requires terminal steps). Idempotent if already COMPLETED."""
    if not _enabled():
        return RegistryResult(RegistryStatus.UNAVAILABLE)
    owner = _normalize_owner(owner_id)
    gid = str(goal_id or "").strip()[:MAX_GOAL_ID_CHARS]
    if not owner or not gid:
        return RegistryResult(RegistryStatus.REJECTED)

    loaded = _load_goal(owner, gid)
    if loaded.status is not RegistryStatus.OK or loaded.snapshot is None:
        return loaded
    snap = loaded.snapshot
    if snap.lifecycle is GoalLifecycle.COMPLETED:
        if int(snap.version) != int(expected_version):
            # Idempotent OK if already completed regardless of version mismatch? Spec: idempotent
            return RegistryResult(RegistryStatus.OK, snap)
        return RegistryResult(RegistryStatus.OK, snap)
    if snap.lifecycle is GoalLifecycle.ABANDONED:
        return RegistryResult(RegistryStatus.REJECTED)
    if int(snap.version) != int(expected_version):
        return RegistryResult(RegistryStatus.CONFLICT)
    return transition_goal(
        owner,
        gid,
        snap.lifecycle,
        GoalLifecycle.COMPLETED,
        "archived",
        expected_version,
    )


def abandon_goal(owner_id: str, goal_id: str, expected_version: int) -> RegistryResult:
    if not _enabled():
        return RegistryResult(RegistryStatus.UNAVAILABLE)
    owner = _normalize_owner(owner_id)
    gid = str(goal_id or "").strip()[:MAX_GOAL_ID_CHARS]
    if not owner or not gid:
        return RegistryResult(RegistryStatus.REJECTED)
    loaded = _load_goal(owner, gid)
    if loaded.status is not RegistryStatus.OK or loaded.snapshot is None:
        return loaded
    snap = loaded.snapshot
    if snap.lifecycle is GoalLifecycle.ABANDONED:
        return RegistryResult(RegistryStatus.OK, snap)
    if snap.lifecycle is GoalLifecycle.COMPLETED:
        return RegistryResult(RegistryStatus.REJECTED)
    if int(snap.version) != int(expected_version):
        return RegistryResult(RegistryStatus.CONFLICT)
    return transition_goal(
        owner,
        gid,
        snap.lifecycle,
        GoalLifecycle.ABANDONED,
        "abandoned",
        expected_version,
    )


def mark_stale(
    owner_id: str,
    goal_id: str,
    reason: str,
    expected_version: int,
) -> RegistryResult:
    if not _enabled():
        return RegistryResult(RegistryStatus.UNAVAILABLE)
    owner = _normalize_owner(owner_id)
    gid = str(goal_id or "").strip()[:MAX_GOAL_ID_CHARS]
    if not owner or not gid:
        return RegistryResult(RegistryStatus.REJECTED)
    return transition_goal(
        owner,
        gid,
        GoalLifecycle.ACTIVE,
        GoalLifecycle.STALE,
        reason,
        expected_version,
    )


def recover_goal(owner_id: str) -> RegistryResult:
    """Load ACTIVE goal and validate snapshot integrity (Phase 1: no TTL side effects)."""
    return get_active_goal(owner_id)
