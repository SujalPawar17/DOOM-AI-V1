"""V8.28 Goal Experience persistent store — Postgres + in-memory test double.

Owner-scoped historical experience records. Lazy schema. Zero execution authority.
Not Goal Registry. Not Continuity. Not User Model.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from orchestration.experience.config import is_v828_goal_experience_enabled
from orchestration.experience.policy import (
    is_sensitive_experience_content,
    is_valid_owner_id,
    is_valid_transition,
    normalize_owner_id,
    parse_outcome,
    parse_status,
    validate_experience_fields,
)
from orchestration.experience.types import (
    EXPERIENCE_ID_PREFIX,
    MAX_EXPERIENCES_PER_OWNER,
    MAX_LIST_RESULTS,
    SCHEMA_VERSION,
    ExperienceResult,
    ExperienceResultStatus,
    ExperienceStatus,
    GoalExperience,
    Outcome,
)

_LOCK = threading.Lock()
_USE_TEST_STORE = False
# owner_id -> list of row dicts
_TEST_ROWS: Dict[str, List[Dict[str, Any]]] = {}


def use_test_experience_store(enabled: bool = True) -> None:
    global _USE_TEST_STORE
    with _LOCK:
        _USE_TEST_STORE = bool(enabled)
        if enabled:
            _TEST_ROWS.clear()


def reset_experience_for_tests() -> None:
    with _LOCK:
        _TEST_ROWS.clear()


def _enabled() -> bool:
    return bool(is_v828_goal_experience_enabled())


def new_experience_id() -> str:
    return f"{EXPERIENCE_ID_PREFIX}{uuid.uuid4().hex}"


def ensure_goal_experience_schema() -> bool:
    """Lazy DDL for v8_goal_experiences. Never raises to callers."""
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
                    CREATE TABLE IF NOT EXISTS v8_goal_experiences (
                        experience_id VARCHAR(64) PRIMARY KEY,
                        owner_id VARCHAR(64) NOT NULL,
                        schema_version INTEGER NOT NULL,
                        source_goal_id VARCHAR(64),
                        title VARCHAR(160) NOT NULL,
                        outcome VARCHAR(16) NOT NULL
                            CHECK (outcome IN (
                                'COMPLETED','ABANDONED','STALE','PARTIAL')),
                        step_summary TEXT NOT NULL DEFAULT '[]',
                        blockers TEXT NOT NULL DEFAULT '[]',
                        user_note VARCHAR(200) NOT NULL DEFAULT '',
                        tags TEXT NOT NULL DEFAULT '[]',
                        status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE_RECORD'
                            CHECK (status IN (
                                'ACTIVE_RECORD','SUPERSEDED','FORGOTTEN')),
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at DOUBLE PRECISION NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL,
                        expires_at DOUBLE PRECISION
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_v8_ge_owner_source_active
                    ON v8_goal_experiences (owner_id, source_goal_id)
                    WHERE status = 'ACTIVE_RECORD'
                      AND source_goal_id IS NOT NULL
                      AND source_goal_id <> '';
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_v8_ge_owner_updated
                    ON v8_goal_experiences (owner_id, updated_at DESC);
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_v8_ge_owner_status
                    ON v8_goal_experiences (owner_id, status);
                    """
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


def _is_expired(row: Dict[str, Any], now: float) -> bool:
    exp = row.get("expires_at")
    if exp is None:
        return False
    try:
        return float(exp) <= float(now)
    except (TypeError, ValueError):
        return True


def _materialize_expired_test_rows(rows: List[Dict[str, Any]], now: float) -> None:
    for row in rows:
        if str(row.get("status")) != ExperienceStatus.ACTIVE_RECORD.value:
            continue
        if _is_expired(row, now):
            row["status"] = ExperienceStatus.FORGOTTEN.value


def _materialize_expired_pg(cur: Any, owner: str, now: float) -> None:
    try:
        cur.execute(
            """
            UPDATE v8_goal_experiences
            SET status = 'FORGOTTEN'
            WHERE owner_id = %s
              AND status = 'ACTIVE_RECORD'
              AND expires_at IS NOT NULL
              AND expires_at <= %s
            """,
            (owner, now),
        )
    except Exception:
        pass


def _dumps_list(items: Sequence[str]) -> str:
    return json.dumps(list(items or ()), ensure_ascii=False, separators=(",", ":"))


def _loads_list(raw: Any) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(x) for x in raw if str(x).strip())
    text = str(raw or "").strip()
    if not text:
        return ()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return tuple(str(x) for x in data if str(x).strip())
    except Exception:
        return ()
    return ()


def _row_to_experience(row: Dict[str, Any], *, now: Optional[float] = None) -> Optional[GoalExperience]:
    try:
        if int(row.get("schema_version") or 0) != SCHEMA_VERSION:
            return None
        outcome = parse_outcome(row.get("outcome"))
        st = parse_status(row.get("status"))
        if outcome is None or st is None:
            return None
        eid = str(row.get("experience_id") or "")
        owner = str(row.get("owner_id") or "")
        title = str(row.get("title") or "")
        if not eid.startswith(EXPERIENCE_ID_PREFIX):
            return None
        if not owner or not title:
            return None
        version = int(row.get("version") or 0)
        if version < 1:
            return None
        expires_at = row.get("expires_at")
        if expires_at is not None:
            expires_at = float(expires_at)
        status = st
        ts = float(now if now is not None else time.time())
        if status is ExperienceStatus.ACTIVE_RECORD and _is_expired(row, ts):
            status = ExperienceStatus.FORGOTTEN
        return GoalExperience(
            schema_version=SCHEMA_VERSION,
            experience_id=eid[:64],
            owner_id=owner[:64],
            source_goal_id=str(row.get("source_goal_id") or "")[:64],
            title=title[:160],
            outcome=outcome,
            step_summary=_loads_list(row.get("step_summary")),
            blockers=_loads_list(row.get("blockers")),
            user_note=str(row.get("user_note") or "")[:200],
            tags=_loads_list(row.get("tags")),
            status=status,
            version=version,
            created_at=float(row.get("created_at") or 0.0),
            updated_at=float(row.get("updated_at") or 0.0),
            expires_at=expires_at,
        )
    except Exception:
        return None


def _count_active(rows: List[Dict[str, Any]], now: float) -> int:
    n = 0
    for row in rows:
        if str(row.get("status")) != ExperienceStatus.ACTIVE_RECORD.value:
            continue
        if _is_expired(row, now):
            continue
        n += 1
    return n


def _enforce_retention_test(rows: List[Dict[str, Any]], now: float) -> None:
    """If at/over cap, SUPERSEDE oldest ACTIVE_RECORD to free room for one create."""
    active = [
        r
        for r in rows
        if str(r.get("status")) == ExperienceStatus.ACTIVE_RECORD.value
        and not _is_expired(r, now)
    ]
    if len(active) < MAX_EXPERIENCES_PER_OWNER:
        return
    active.sort(key=lambda r: float(r.get("updated_at") or 0.0))
    # Free enough slots so one new ACTIVE_RECORD can be inserted.
    overflow = len(active) - MAX_EXPERIENCES_PER_OWNER + 1
    for row in active[:overflow]:
        row["status"] = ExperienceStatus.SUPERSEDED.value
        row["updated_at"] = now
        row["version"] = int(row.get("version") or 1) + 1


def create_experience(owner_id: str, fields: Dict[str, Any]) -> ExperienceResult:
    """Create an ACTIVE_RECORD experience. Idempotent on (owner, source_goal_id)."""
    if not _enabled():
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
    if not isinstance(fields, dict):
        return ExperienceResult(ExperienceResultStatus.REJECTED)

    payload = dict(fields)
    payload.setdefault("owner_id", owner_id)
    now = time.time()
    normalized, st = validate_experience_fields(
        owner_id=payload.get("owner_id", owner_id),
        title=str(payload.get("title") or ""),
        outcome=payload.get("outcome"),
        step_summary=payload.get("step_summary", ()),
        blockers=payload.get("blockers", ()),
        user_note=str(payload.get("user_note") or ""),
        tags=payload.get("tags", ()),
        source_goal_id=str(payload.get("source_goal_id") or ""),
        schema_version=int(payload.get("schema_version") or SCHEMA_VERSION),
        experience_id=str(payload.get("experience_id") or ""),
        version=1,
        status=ExperienceStatus.ACTIVE_RECORD,
        expires_at=payload.get("expires_at"),
    )
    if st is not ExperienceResultStatus.OK or normalized is None:
        return ExperienceResult(st)

    owner = normalized["owner_id"]
    if normalize_owner_id(owner_id) != owner:
        return ExperienceResult(ExperienceResultStatus.REJECTED)

    if _USE_TEST_STORE:
        return _create_test(owner, normalized, now=now)
    return _create_pg(owner, normalized, now=now)


def get_experience(owner_id: str, experience_id: str) -> ExperienceResult:
    if not _enabled():
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
    if not is_valid_owner_id(owner_id):
        return ExperienceResult(ExperienceResultStatus.REJECTED)
    eid = str(experience_id or "").strip()[:64]
    if not eid.startswith(EXPERIENCE_ID_PREFIX):
        return ExperienceResult(ExperienceResultStatus.REJECTED)
    owner = normalize_owner_id(owner_id)
    now = time.time()

    if _USE_TEST_STORE:
        with _LOCK:
            rows = _TEST_ROWS.get(owner, [])
            _materialize_expired_test_rows(rows, now)
            for row in rows:
                if str(row.get("experience_id")) != eid:
                    continue
                if str(row.get("status")) != ExperienceStatus.ACTIVE_RECORD.value:
                    return ExperienceResult(ExperienceResultStatus.NOT_FOUND)
                exp = _row_to_experience(row, now=now)
                if exp is None:
                    return ExperienceResult(ExperienceResultStatus.REJECTED)
                if exp.status is not ExperienceStatus.ACTIVE_RECORD:
                    return ExperienceResult(ExperienceResultStatus.NOT_FOUND)
                return ExperienceResult(ExperienceResultStatus.OK, experience=exp)
        return ExperienceResult(ExperienceResultStatus.NOT_FOUND)

    try:
        if not ensure_goal_experience_schema():
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                _materialize_expired_pg(cur, owner, now)
                try:
                    conn.commit()
                except Exception:
                    pass
                cur.execute(
                    """
                    SELECT experience_id, owner_id, schema_version, source_goal_id, title,
                           outcome, step_summary, blockers, user_note, tags, status,
                           version, created_at, updated_at, expires_at
                    FROM v8_goal_experiences
                    WHERE owner_id = %s AND experience_id = %s AND status = 'ACTIVE_RECORD'
                    """,
                    (owner, eid),
                )
                fetched = cur.fetchone()
                if not fetched:
                    return ExperienceResult(ExperienceResultStatus.NOT_FOUND)
                row = _pg_row_to_dict(fetched)
                exp = _row_to_experience(row, now=now)
                if exp is None:
                    return ExperienceResult(ExperienceResultStatus.REJECTED)
                if exp.status is not ExperienceStatus.ACTIVE_RECORD:
                    return ExperienceResult(ExperienceResultStatus.NOT_FOUND)
                return ExperienceResult(ExperienceResultStatus.OK, experience=exp)
        except Exception:
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)


def list_experiences(
    owner_id: str,
    *,
    include_inactive: bool = False,
    limit: int = MAX_LIST_RESULTS,
) -> ExperienceResult:
    if not _enabled():
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
    if not is_valid_owner_id(owner_id):
        return ExperienceResult(ExperienceResultStatus.REJECTED)
    owner = normalize_owner_id(owner_id)
    lim = max(1, min(int(limit or MAX_LIST_RESULTS), MAX_LIST_RESULTS))
    now = time.time()

    if _USE_TEST_STORE:
        with _LOCK:
            rows = list(_TEST_ROWS.get(owner, []))
            _materialize_expired_test_rows(rows, now)
        rows.sort(key=lambda r: float(r.get("updated_at") or 0.0), reverse=True)
        out: List[GoalExperience] = []
        for row in rows:
            st = str(row.get("status") or "")
            if not include_inactive:
                if st != ExperienceStatus.ACTIVE_RECORD.value:
                    continue
                if _is_expired(row, now):
                    continue
            elif st == ExperienceStatus.FORGOTTEN.value and not include_inactive:
                continue
            exp = _row_to_experience(row, now=now)
            if exp is None:
                continue
            if not include_inactive and exp.status is not ExperienceStatus.ACTIVE_RECORD:
                continue
            out.append(exp)
            if len(out) >= lim:
                break
        return ExperienceResult(ExperienceResultStatus.OK, experiences=tuple(out))

    try:
        if not ensure_goal_experience_schema():
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                _materialize_expired_pg(cur, owner, now)
                try:
                    conn.commit()
                except Exception:
                    pass
                cur.execute(
                    """
                    SELECT experience_id, owner_id, schema_version, source_goal_id, title,
                           outcome, step_summary, blockers, user_note, tags, status,
                           version, created_at, updated_at, expires_at
                    FROM v8_goal_experiences
                    WHERE owner_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (owner, max(lim * 4, lim)),
                )
                out2: List[GoalExperience] = []
                for fetched in cur.fetchall() or ():
                    row = _pg_row_to_dict(fetched)
                    st = str(row.get("status") or "")
                    if not include_inactive:
                        if st != ExperienceStatus.ACTIVE_RECORD.value:
                            continue
                        if _is_expired(row, now):
                            continue
                    exp = _row_to_experience(row, now=now)
                    if exp is None:
                        continue
                    if not include_inactive and exp.status is not ExperienceStatus.ACTIVE_RECORD:
                        continue
                    out2.append(exp)
                    if len(out2) >= lim:
                        break
                return ExperienceResult(ExperienceResultStatus.OK, experiences=tuple(out2))
        except Exception:
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)


def update_experience(
    owner_id: str,
    experience_id: str,
    fields: Dict[str, Any],
    *,
    expected_version: int,
) -> ExperienceResult:
    """Update ACTIVE_RECORD fields with optimistic versioning."""
    if not _enabled():
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
    if not is_valid_owner_id(owner_id):
        return ExperienceResult(ExperienceResultStatus.REJECTED)
    if not isinstance(fields, dict):
        return ExperienceResult(ExperienceResultStatus.REJECTED)
    eid = str(experience_id or "").strip()[:64]
    if not eid.startswith(EXPERIENCE_ID_PREFIX):
        return ExperienceResult(ExperienceResultStatus.REJECTED)

    existing = get_experience(owner_id, eid)
    if existing.status is ExperienceResultStatus.UNAVAILABLE:
        return existing
    if existing.status is not ExperienceResultStatus.OK or existing.experience is None:
        return ExperienceResult(ExperienceResultStatus.NOT_FOUND)
    cur = existing.experience
    if int(expected_version) != int(cur.version):
        return ExperienceResult(ExperienceResultStatus.CONFLICT)

    merged = {
        "title": fields.get("title", cur.title),
        "outcome": fields.get("outcome", cur.outcome),
        "step_summary": fields.get("step_summary", cur.step_summary),
        "blockers": fields.get("blockers", cur.blockers),
        "user_note": fields.get("user_note", cur.user_note),
        "tags": fields.get("tags", cur.tags),
        "source_goal_id": fields.get("source_goal_id", cur.source_goal_id),
        "expires_at": fields.get("expires_at", cur.expires_at),
        "experience_id": eid,
        "status": ExperienceStatus.ACTIVE_RECORD,
        "version": cur.version,
    }
    normalized, st = validate_experience_fields(
        owner_id=owner_id,
        title=str(merged["title"] or ""),
        outcome=merged["outcome"],
        step_summary=merged["step_summary"],
        blockers=merged["blockers"],
        user_note=str(merged["user_note"] or ""),
        tags=merged["tags"],
        source_goal_id=str(merged["source_goal_id"] or ""),
        experience_id=eid,
        version=int(cur.version),
        status=ExperienceStatus.ACTIVE_RECORD,
        expires_at=merged.get("expires_at"),
    )
    if st is not ExperienceResultStatus.OK or normalized is None:
        return ExperienceResult(st)

    owner = normalize_owner_id(owner_id)
    now = time.time()
    if _USE_TEST_STORE:
        return _update_test(owner, eid, normalized, expected_version=int(expected_version), now=now)
    return _update_pg(owner, eid, normalized, expected_version=int(expected_version), now=now)


def transition_experience(
    owner_id: str,
    experience_id: str,
    to_status: Any,
    *,
    expected_version: int,
) -> ExperienceResult:
    """ACTIVE_RECORD → SUPERSEDED | FORGOTTEN with optimistic versioning."""
    if not _enabled():
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
    if not is_valid_owner_id(owner_id):
        return ExperienceResult(ExperienceResultStatus.REJECTED)
    target = parse_status(to_status)
    if target is None:
        return ExperienceResult(ExperienceResultStatus.REJECTED)
    eid = str(experience_id or "").strip()[:64]
    if not eid.startswith(EXPERIENCE_ID_PREFIX):
        return ExperienceResult(ExperienceResultStatus.REJECTED)

    existing = get_experience(owner_id, eid)
    if existing.status is ExperienceResultStatus.UNAVAILABLE:
        return existing
    if existing.status is not ExperienceResultStatus.OK or existing.experience is None:
        return ExperienceResult(ExperienceResultStatus.NOT_FOUND)
    cur = existing.experience
    if int(expected_version) != int(cur.version):
        return ExperienceResult(ExperienceResultStatus.CONFLICT)
    if not is_valid_transition(cur.status, target):
        return ExperienceResult(ExperienceResultStatus.REJECTED)

    owner = normalize_owner_id(owner_id)
    now = time.time()
    if _USE_TEST_STORE:
        return _transition_test(owner, eid, target, expected_version=int(expected_version), now=now)
    return _transition_pg(owner, eid, target, expected_version=int(expected_version), now=now)


def forget_experience(
    owner_id: str,
    experience_id: str,
    *,
    expected_version: int,
) -> ExperienceResult:
    """ACTIVE_RECORD → FORGOTTEN."""
    return transition_experience(
        owner_id,
        experience_id,
        ExperienceStatus.FORGOTTEN,
        expected_version=expected_version,
    )


def _pg_row_to_dict(fetched: Sequence[Any]) -> Dict[str, Any]:
    return {
        "experience_id": fetched[0],
        "owner_id": fetched[1],
        "schema_version": fetched[2],
        "source_goal_id": fetched[3] or "",
        "title": fetched[4],
        "outcome": fetched[5],
        "step_summary": fetched[6],
        "blockers": fetched[7],
        "user_note": fetched[8] or "",
        "tags": fetched[9],
        "status": fetched[10],
        "version": fetched[11],
        "created_at": fetched[12],
        "updated_at": fetched[13],
        "expires_at": fetched[14],
    }


def _create_test(owner: str, normalized: Dict[str, Any], *, now: float) -> ExperienceResult:
    with _LOCK:
        rows = _TEST_ROWS.setdefault(owner, [])
        _materialize_expired_test_rows(rows, now)
        src = str(normalized.get("source_goal_id") or "")
        if src:
            for row in rows:
                if (
                    str(row.get("source_goal_id") or "") == src
                    and str(row.get("status")) == ExperienceStatus.ACTIVE_RECORD.value
                ):
                    # Idempotent: return existing ACTIVE for same source_goal_id.
                    exp = _row_to_experience(row, now=now)
                    if exp is None:
                        return ExperienceResult(ExperienceResultStatus.REJECTED)
                    return ExperienceResult(ExperienceResultStatus.OK, experience=exp)

        if _count_active(rows, now) >= MAX_EXPERIENCES_PER_OWNER:
            _enforce_retention_test(rows, now)
        if _count_active(rows, now) >= MAX_EXPERIENCES_PER_OWNER:
            return ExperienceResult(ExperienceResultStatus.REJECTED)

        eid = normalized["experience_id"] or new_experience_id()
        row = {
            "experience_id": eid,
            "owner_id": owner,
            "schema_version": SCHEMA_VERSION,
            "source_goal_id": src,
            "title": normalized["title"],
            "outcome": normalized["outcome"].value,
            "step_summary": list(normalized["step_summary"]),
            "blockers": list(normalized["blockers"]),
            "user_note": normalized["user_note"],
            "tags": list(normalized["tags"]),
            "status": ExperienceStatus.ACTIVE_RECORD.value,
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "expires_at": normalized["expires_at"],
        }
        rows.append(row)
        exp = _row_to_experience(row, now=now)
        if exp is None:
            return ExperienceResult(ExperienceResultStatus.REJECTED)
        return ExperienceResult(ExperienceResultStatus.OK, experience=exp)


def _update_test(
    owner: str,
    eid: str,
    normalized: Dict[str, Any],
    *,
    expected_version: int,
    now: float,
) -> ExperienceResult:
    with _LOCK:
        rows = _TEST_ROWS.get(owner, [])
        _materialize_expired_test_rows(rows, now)
        for row in rows:
            if str(row.get("experience_id")) != eid:
                continue
            if str(row.get("status")) != ExperienceStatus.ACTIVE_RECORD.value:
                return ExperienceResult(ExperienceResultStatus.NOT_FOUND)
            cur_ver = int(row.get("version") or 0)
            if cur_ver != int(expected_version):
                return ExperienceResult(ExperienceResultStatus.CONFLICT)
            row["title"] = normalized["title"]
            row["outcome"] = normalized["outcome"].value
            row["step_summary"] = list(normalized["step_summary"])
            row["blockers"] = list(normalized["blockers"])
            row["user_note"] = normalized["user_note"]
            row["tags"] = list(normalized["tags"])
            row["source_goal_id"] = normalized["source_goal_id"]
            row["expires_at"] = normalized["expires_at"]
            row["version"] = cur_ver + 1
            row["updated_at"] = now
            exp = _row_to_experience(row, now=now)
            if exp is None:
                return ExperienceResult(ExperienceResultStatus.REJECTED)
            return ExperienceResult(ExperienceResultStatus.OK, experience=exp)
        return ExperienceResult(ExperienceResultStatus.NOT_FOUND)


def _transition_test(
    owner: str,
    eid: str,
    target: ExperienceStatus,
    *,
    expected_version: int,
    now: float,
) -> ExperienceResult:
    with _LOCK:
        rows = _TEST_ROWS.get(owner, [])
        for row in rows:
            if str(row.get("experience_id")) != eid:
                continue
            if str(row.get("status")) != ExperienceStatus.ACTIVE_RECORD.value:
                return ExperienceResult(ExperienceResultStatus.NOT_FOUND)
            cur_ver = int(row.get("version") or 0)
            if cur_ver != int(expected_version):
                return ExperienceResult(ExperienceResultStatus.CONFLICT)
            row["status"] = target.value
            row["version"] = cur_ver + 1
            row["updated_at"] = now
            exp = _row_to_experience(row, now=now)
            if exp is None:
                return ExperienceResult(ExperienceResultStatus.REJECTED)
            return ExperienceResult(ExperienceResultStatus.OK, experience=exp)
        return ExperienceResult(ExperienceResultStatus.NOT_FOUND)


def _create_pg(owner: str, normalized: Dict[str, Any], *, now: float) -> ExperienceResult:
    try:
        if not ensure_goal_experience_schema():
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                _materialize_expired_pg(cur, owner, now)
                src = str(normalized.get("source_goal_id") or "") or None
                if src:
                    cur.execute(
                        """
                        SELECT experience_id, owner_id, schema_version, source_goal_id, title,
                               outcome, step_summary, blockers, user_note, tags, status,
                               version, created_at, updated_at, expires_at
                        FROM v8_goal_experiences
                        WHERE owner_id = %s AND source_goal_id = %s AND status = 'ACTIVE_RECORD'
                        """,
                        (owner, src),
                    )
                    existing = cur.fetchone()
                    if existing:
                        conn.commit()
                        exp = _row_to_experience(_pg_row_to_dict(existing), now=now)
                        if exp is None:
                            return ExperienceResult(ExperienceResultStatus.REJECTED)
                        return ExperienceResult(ExperienceResultStatus.OK, experience=exp)

                cur.execute(
                    """
                    SELECT COUNT(*) FROM v8_goal_experiences
                    WHERE owner_id = %s AND status = 'ACTIVE_RECORD'
                      AND (expires_at IS NULL OR expires_at > %s)
                    """,
                    (owner, now),
                )
                count = int(cur.fetchone()[0] or 0)
                if count >= MAX_EXPERIENCES_PER_OWNER:
                    # Supersede oldest ACTIVE_RECORD to free a slot.
                    cur.execute(
                        """
                        UPDATE v8_goal_experiences
                        SET status = 'SUPERSEDED', version = version + 1, updated_at = %s
                        WHERE experience_id = (
                            SELECT experience_id FROM v8_goal_experiences
                            WHERE owner_id = %s AND status = 'ACTIVE_RECORD'
                              AND (expires_at IS NULL OR expires_at > %s)
                            ORDER BY updated_at ASC
                            LIMIT 1
                        )
                        """,
                        (now, owner, now),
                    )

                eid = normalized["experience_id"] or new_experience_id()
                cur.execute(
                    """
                    INSERT INTO v8_goal_experiences (
                        experience_id, owner_id, schema_version, source_goal_id, title,
                        outcome, step_summary, blockers, user_note, tags, status,
                        version, created_at, updated_at, expires_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, 'ACTIVE_RECORD',
                        1, %s, %s, %s
                    )
                    RETURNING experience_id, owner_id, schema_version, source_goal_id, title,
                              outcome, step_summary, blockers, user_note, tags, status,
                              version, created_at, updated_at, expires_at
                    """,
                    (
                        eid,
                        owner,
                        SCHEMA_VERSION,
                        src,
                        normalized["title"],
                        normalized["outcome"].value,
                        _dumps_list(normalized["step_summary"]),
                        _dumps_list(normalized["blockers"]),
                        normalized["user_note"],
                        _dumps_list(normalized["tags"]),
                        now,
                        now,
                        normalized["expires_at"],
                    ),
                )
                fetched = cur.fetchone()
            conn.commit()
            exp = _row_to_experience(_pg_row_to_dict(fetched), now=now)
            if exp is None:
                return ExperienceResult(ExperienceResultStatus.REJECTED)
            return ExperienceResult(ExperienceResultStatus.OK, experience=exp)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            msg = str(exc).lower()
            pgcode = str(getattr(exc, "pgcode", "") or "")
            if pgcode == "23505" or "unique" in msg or "duplicate" in msg:
                # Concurrent create for same source_goal_id — re-read.
                if normalized.get("source_goal_id"):
                    listed = list_experiences(owner, limit=MAX_LIST_RESULTS)
                    for e in listed.experiences or ():
                        if e.source_goal_id == normalized["source_goal_id"]:
                            return ExperienceResult(ExperienceResultStatus.OK, experience=e)
                return ExperienceResult(ExperienceResultStatus.CONFLICT)
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)


def _update_pg(
    owner: str,
    eid: str,
    normalized: Dict[str, Any],
    *,
    expected_version: int,
    now: float,
) -> ExperienceResult:
    try:
        if not ensure_goal_experience_schema():
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE v8_goal_experiences SET
                        title = %s, outcome = %s, step_summary = %s, blockers = %s,
                        user_note = %s, tags = %s, source_goal_id = %s,
                        expires_at = %s, version = %s, updated_at = %s
                    WHERE experience_id = %s AND owner_id = %s
                      AND version = %s AND status = 'ACTIVE_RECORD'
                    RETURNING experience_id, owner_id, schema_version, source_goal_id, title,
                              outcome, step_summary, blockers, user_note, tags, status,
                              version, created_at, updated_at, expires_at
                    """,
                    (
                        normalized["title"],
                        normalized["outcome"].value,
                        _dumps_list(normalized["step_summary"]),
                        _dumps_list(normalized["blockers"]),
                        normalized["user_note"],
                        _dumps_list(normalized["tags"]),
                        normalized["source_goal_id"] or None,
                        normalized["expires_at"],
                        int(expected_version) + 1,
                        now,
                        eid,
                        owner,
                        int(expected_version),
                    ),
                )
                fetched = cur.fetchone()
                if not fetched:
                    conn.rollback()
                    return ExperienceResult(ExperienceResultStatus.CONFLICT)
            conn.commit()
            exp = _row_to_experience(_pg_row_to_dict(fetched), now=now)
            if exp is None:
                return ExperienceResult(ExperienceResultStatus.REJECTED)
            return ExperienceResult(ExperienceResultStatus.OK, experience=exp)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)


def _transition_pg(
    owner: str,
    eid: str,
    target: ExperienceStatus,
    *,
    expected_version: int,
    now: float,
) -> ExperienceResult:
    try:
        if not ensure_goal_experience_schema():
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE v8_goal_experiences SET
                        status = %s, version = %s, updated_at = %s
                    WHERE experience_id = %s AND owner_id = %s
                      AND version = %s AND status = 'ACTIVE_RECORD'
                    RETURNING experience_id, owner_id, schema_version, source_goal_id, title,
                              outcome, step_summary, blockers, user_note, tags, status,
                              version, created_at, updated_at, expires_at
                    """,
                    (
                        target.value,
                        int(expected_version) + 1,
                        now,
                        eid,
                        owner,
                        int(expected_version),
                    ),
                )
                fetched = cur.fetchone()
                if not fetched:
                    conn.rollback()
                    return ExperienceResult(ExperienceResultStatus.CONFLICT)
            conn.commit()
            exp = _row_to_experience(_pg_row_to_dict(fetched), now=now)
            if exp is None:
                return ExperienceResult(ExperienceResultStatus.REJECTED)
            return ExperienceResult(ExperienceResultStatus.OK, experience=exp)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
