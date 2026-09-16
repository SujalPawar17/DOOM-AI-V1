"""V8.27 User Model persistent store — Postgres + in-memory test double.

Owner-scoped profile entries. Lazy schema. Zero execution authority.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from orchestration.user_model.config import is_v827_user_model_enabled
from orchestration.user_model.policy import (
    default_confidence_for_category,
    default_expiry_for_category,
    is_valid_owner_id,
    normalize_owner_id,
    parse_category,
    parse_confidence,
    parse_provenance,
    parse_status,
    validate_entry_fields,
)
from orchestration.user_model.types import (
    ENTRY_ID_PREFIX,
    MAX_ENTRIES_PER_CATEGORY,
    MAX_LIST_RESULTS,
    MAX_PROFILE_ENTRIES,
    SCHEMA_VERSION,
    Category,
    Confidence,
    ProfileEntry,
    ProfileResult,
    ProfileResultStatus,
    ProfileStatus,
    Provenance,
)

_LOCK = threading.Lock()
_USE_TEST_STORE = False
# owner_id -> list of row dicts
_TEST_ROWS: Dict[str, List[Dict[str, Any]]] = {}


def use_test_user_model_store(enabled: bool = True) -> None:
    global _USE_TEST_STORE
    with _LOCK:
        _USE_TEST_STORE = bool(enabled)
        if enabled:
            _TEST_ROWS.clear()


def reset_user_model_for_tests() -> None:
    with _LOCK:
        _TEST_ROWS.clear()


def _enabled() -> bool:
    return bool(is_v827_user_model_enabled())


def new_profile_entry_id() -> str:
    return f"{ENTRY_ID_PREFIX}{uuid.uuid4().hex}"


def ensure_user_profile_schema() -> bool:
    """Lazy DDL for v8_user_profile_entries. Never raises to callers."""
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
                    CREATE TABLE IF NOT EXISTS v8_user_profile_entries (
                        entry_id VARCHAR(64) PRIMARY KEY,
                        owner_id VARCHAR(64) NOT NULL,
                        schema_version INTEGER NOT NULL,
                        category VARCHAR(32) NOT NULL
                            CHECK (category IN (
                                'preference','project','constraint',
                                'communication','stable_fact','temporary_fact')),
                        key VARCHAR(96) NOT NULL,
                        value VARCHAR(160) NOT NULL,
                        confidence VARCHAR(16) NOT NULL
                            CHECK (confidence IN ('HIGH','MEDIUM','LOW')),
                        provenance VARCHAR(32) NOT NULL
                            CHECK (provenance IN (
                                'USER_EXPLICIT','PROJECTED_MEMORY','USER_CONFIRMED')),
                        source_memory_id VARCHAR(64),
                        version INTEGER NOT NULL DEFAULT 1,
                        status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'
                            CHECK (status IN ('ACTIVE','SUPERSEDED','EXPIRED')),
                        created_at DOUBLE PRECISION NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL,
                        confirmed_at DOUBLE PRECISION NOT NULL,
                        expires_at DOUBLE PRECISION
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_v8_up_owner_cat_key_active
                    ON v8_user_profile_entries (owner_id, category, key)
                    WHERE status = 'ACTIVE';
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_v8_up_owner_updated
                    ON v8_user_profile_entries (owner_id, updated_at DESC);
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_v8_up_owner_category
                    ON v8_user_profile_entries (owner_id, category, status);
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
    """Persist soft-expired ACTIVE → EXPIRED so unique slots and caps stay honest."""
    for row in rows:
        if str(row.get("status")) != ProfileStatus.ACTIVE.value:
            continue
        if _is_expired(row, now):
            row["status"] = ProfileStatus.EXPIRED.value


def _materialize_expired_pg(cur: Any, owner: str, now: float) -> None:
    """Flip soft-expired ACTIVE rows to EXPIRED (parameterized). Best-effort."""
    try:
        cur.execute(
            """
            UPDATE v8_user_profile_entries
            SET status = 'EXPIRED'
            WHERE owner_id = %s
              AND status = 'ACTIVE'
              AND expires_at IS NOT NULL
              AND expires_at <= %s
            """,
            (owner, now),
        )
    except Exception:
        pass


def _row_to_entry(row: Dict[str, Any], *, now: Optional[float] = None) -> Optional[ProfileEntry]:
    try:
        if int(row.get("schema_version") or 0) != SCHEMA_VERSION:
            return None
        cat = parse_category(row.get("category"))
        conf = parse_confidence(row.get("confidence"))
        prov = parse_provenance(row.get("provenance"))
        st = parse_status(row.get("status"))
        if cat is None or conf is None or prov is None or st is None:
            return None
        eid = str(row.get("entry_id") or "")
        owner = str(row.get("owner_id") or "")
        key = str(row.get("key") or "")
        value = str(row.get("value") or "")
        if not eid.startswith(ENTRY_ID_PREFIX):
            return None
        if not owner or not key or not value:
            return None
        version = int(row.get("version") or 0)
        if version < 1:
            return None
        expires_at = row.get("expires_at")
        if expires_at is not None:
            expires_at = float(expires_at)
        status = st
        ts = float(now if now is not None else time.time())
        if status is ProfileStatus.ACTIVE and _is_expired(row, ts):
            status = ProfileStatus.EXPIRED
        return ProfileEntry(
            schema_version=SCHEMA_VERSION,
            entry_id=eid[:64],
            owner_id=owner[:64],
            category=cat,
            key=key[:96],
            value=value[:160],
            confidence=conf,
            provenance=prov,
            source_memory_id=str(row.get("source_memory_id") or "")[:64],
            version=version,
            created_at=float(row.get("created_at") or 0.0),
            updated_at=float(row.get("updated_at") or 0.0),
            confirmed_at=float(row.get("confirmed_at") or 0.0),
            expires_at=expires_at,
            status=status,
        )
    except Exception:
        return None


def _count_active(rows: List[Dict[str, Any]], now: float, category: Optional[Category] = None) -> int:
    n = 0
    for row in rows:
        if str(row.get("status")) != ProfileStatus.ACTIVE.value:
            continue
        if _is_expired(row, now):
            continue
        if category is not None and str(row.get("category")) != category.value:
            continue
        n += 1
    return n


def upsert_profile_entry(
    owner_id: str,
    entry_fields: Dict[str, Any],
    *,
    expected_version: Optional[int] = None,
) -> ProfileResult:
    """Create or update an ACTIVE profile entry. Fail closed when flag OFF."""
    if not _enabled():
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)
    if not isinstance(entry_fields, dict):
        return ProfileResult(ProfileResultStatus.REJECTED)

    fields = dict(entry_fields)
    fields.setdefault("owner_id", owner_id)
    now = time.time()

    cat = parse_category(fields.get("category"))
    if cat is None:
        return ProfileResult(ProfileResultStatus.REJECTED)

    if "confidence" not in fields or fields.get("confidence") is None:
        fields["confidence"] = default_confidence_for_category(cat)
    if "expires_at" not in fields:
        fields["expires_at"] = default_expiry_for_category(cat, now)
    if fields.get("provenance") is None:
        fields["provenance"] = Provenance.USER_EXPLICIT

    normalized, st = validate_entry_fields(
        owner_id=fields.get("owner_id", owner_id),
        category=fields.get("category"),
        key=fields.get("key", ""),
        value=fields.get("value", ""),
        confidence=fields.get("confidence"),
        provenance=fields.get("provenance"),
        source_memory_id=fields.get("source_memory_id", ""),
        schema_version=int(fields.get("schema_version") or SCHEMA_VERSION),
        entry_id=str(fields.get("entry_id") or ""),
        version=int(fields.get("version") or 1),
        status=ProfileStatus.ACTIVE,
        expires_at=fields.get("expires_at"),
    )
    if st is not ProfileResultStatus.OK or normalized is None:
        return ProfileResult(st)

    owner = normalized["owner_id"]
    if normalize_owner_id(owner_id) != owner:
        return ProfileResult(ProfileResultStatus.REJECTED)

    if _USE_TEST_STORE:
        return _upsert_test(owner, normalized, expected_version=expected_version, now=now)
    return _upsert_pg(owner, normalized, expected_version=expected_version, now=now)


def get_profile_entry(owner_id: str, category: Any, key: str) -> ProfileResult:
    if not _enabled():
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)
    if not is_valid_owner_id(owner_id):
        return ProfileResult(ProfileResultStatus.REJECTED)
    cat = parse_category(category)
    if cat is None:
        return ProfileResult(ProfileResultStatus.REJECTED)
    from orchestration.user_model.policy import sanitize_profile_key

    clean_key = sanitize_profile_key(key)
    if not clean_key:
        return ProfileResult(ProfileResultStatus.REJECTED)
    owner = normalize_owner_id(owner_id)
    now = time.time()

    if _USE_TEST_STORE:
        with _LOCK:
            rows = _TEST_ROWS.get(owner, [])
            _materialize_expired_test_rows(rows, now)
            for row in rows:
                if (
                    str(row.get("category")) == cat.value
                    and str(row.get("key")) == clean_key
                    and str(row.get("status")) == ProfileStatus.ACTIVE.value
                ):
                    entry = _row_to_entry(row, now=now)
                    if entry is None:
                        return ProfileResult(ProfileResultStatus.REJECTED)
                    return ProfileResult(ProfileResultStatus.OK, entry=entry)
        return ProfileResult(ProfileResultStatus.NOT_FOUND)

    try:
        if not ensure_user_profile_schema():
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                _materialize_expired_pg(cur, owner, now)
                try:
                    conn.commit()
                except Exception:
                    pass
                cur.execute(
                    """
                    SELECT entry_id, owner_id, schema_version, category, key, value,
                           confidence, provenance, source_memory_id, version, status,
                           created_at, updated_at, confirmed_at, expires_at
                    FROM v8_user_profile_entries
                    WHERE owner_id = %s AND category = %s AND key = %s AND status = 'ACTIVE'
                    """,
                    (owner, cat.value, clean_key),
                )
                fetched = cur.fetchone()
                if not fetched:
                    return ProfileResult(ProfileResultStatus.NOT_FOUND)
                row = _pg_row_to_dict(fetched)
                entry = _row_to_entry(row, now=now)
                if entry is None:
                    return ProfileResult(ProfileResultStatus.REJECTED)
                if entry.status is ProfileStatus.EXPIRED:
                    return ProfileResult(ProfileResultStatus.NOT_FOUND)
                return ProfileResult(ProfileResultStatus.OK, entry=entry)
        except Exception:
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)


def list_profile_entries(
    owner_id: str,
    *,
    categories: Optional[Sequence[Any]] = None,
    include_expired: bool = False,
    limit: int = MAX_LIST_RESULTS,
) -> ProfileResult:
    if not _enabled():
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)
    if not is_valid_owner_id(owner_id):
        return ProfileResult(ProfileResultStatus.REJECTED)
    owner = normalize_owner_id(owner_id)
    lim = max(1, min(int(limit or MAX_LIST_RESULTS), MAX_LIST_RESULTS))
    wanted: Optional[set] = None
    if categories is not None:
        wanted = set()
        for c in categories:
            cat = parse_category(c)
            if cat is None:
                return ProfileResult(ProfileResultStatus.REJECTED)
            wanted.add(cat.value)
    now = time.time()

    if _USE_TEST_STORE:
        with _LOCK:
            rows = list(_TEST_ROWS.get(owner, []))
            _materialize_expired_test_rows(rows, now)
        rows.sort(key=lambda r: float(r.get("updated_at") or 0.0), reverse=True)
        out: List[ProfileEntry] = []
        for row in rows:
            st = str(row.get("status") or "")
            expired = st == ProfileStatus.EXPIRED.value or _is_expired(row, now)
            if wanted is not None and str(row.get("category")) not in wanted:
                continue
            if st == ProfileStatus.SUPERSEDED.value:
                continue
            if expired:
                if not include_expired:
                    continue
            elif st != ProfileStatus.ACTIVE.value:
                continue
            entry = _row_to_entry(row, now=now)
            if entry is None:
                continue
            out.append(entry)
            if len(out) >= lim:
                break
        return ProfileResult(ProfileResultStatus.OK, entries=tuple(out))

    try:
        if not ensure_user_profile_schema():
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                _materialize_expired_pg(cur, owner, now)
                try:
                    conn.commit()
                except Exception:
                    pass
                cur.execute(
                    """
                    SELECT entry_id, owner_id, schema_version, category, key, value,
                           confidence, provenance, source_memory_id, version, status,
                           created_at, updated_at, confirmed_at, expires_at
                    FROM v8_user_profile_entries
                    WHERE owner_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (owner, max(lim * 4, lim)),
                )
                out2: List[ProfileEntry] = []
                for fetched in cur.fetchall() or ():
                    row = _pg_row_to_dict(fetched)
                    st = str(row.get("status") or "")
                    expired = _is_expired(row, now)
                    if wanted is not None and str(row.get("category")) not in wanted:
                        continue
                    if st == ProfileStatus.SUPERSEDED.value:
                        continue
                    if expired and not include_expired:
                        continue
                    if not expired and st != ProfileStatus.ACTIVE.value and not (
                        include_expired and st == ProfileStatus.EXPIRED.value
                    ):
                        if not (expired and include_expired):
                            continue
                    entry = _row_to_entry(row, now=now)
                    if entry is None:
                        continue
                    out2.append(entry)
                    if len(out2) >= lim:
                        break
                return ProfileResult(ProfileResultStatus.OK, entries=tuple(out2))
        except Exception:
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)


def forget_profile_entry(
    owner_id: str,
    category: Any,
    key: str,
    expected_version: int,
) -> ProfileResult:
    """ACTIVE → SUPERSEDED when version matches."""
    if not _enabled():
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)
    if not is_valid_owner_id(owner_id):
        return ProfileResult(ProfileResultStatus.REJECTED)
    cat = parse_category(category)
    if cat is None:
        return ProfileResult(ProfileResultStatus.REJECTED)
    from orchestration.user_model.policy import sanitize_profile_key

    clean_key = sanitize_profile_key(key)
    if not clean_key:
        return ProfileResult(ProfileResultStatus.REJECTED)
    owner = normalize_owner_id(owner_id)
    now = time.time()

    if _USE_TEST_STORE:
        with _LOCK:
            rows = _TEST_ROWS.get(owner, [])
            for row in rows:
                if (
                    str(row.get("category")) == cat.value
                    and str(row.get("key")) == clean_key
                    and str(row.get("status")) == ProfileStatus.ACTIVE.value
                ):
                    if int(row.get("version") or 0) != int(expected_version):
                        return ProfileResult(ProfileResultStatus.CONFLICT)
                    row["status"] = ProfileStatus.SUPERSEDED.value
                    row["version"] = int(expected_version) + 1
                    row["updated_at"] = now
                    entry = _row_to_entry(row, now=now)
                    return ProfileResult(
                        ProfileResultStatus.OK if entry else ProfileResultStatus.REJECTED,
                        entry=entry,
                    )
        return ProfileResult(ProfileResultStatus.NOT_FOUND)

    try:
        if not ensure_user_profile_schema():
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE v8_user_profile_entries
                    SET status = 'SUPERSEDED', version = %s, updated_at = %s
                    WHERE owner_id = %s AND category = %s AND key = %s
                      AND status = 'ACTIVE' AND version = %s
                    RETURNING entry_id, owner_id, schema_version, category, key, value,
                              confidence, provenance, source_memory_id, version, status,
                              created_at, updated_at, confirmed_at, expires_at
                    """,
                    (int(expected_version) + 1, now, owner, cat.value, clean_key, int(expected_version)),
                )
                fetched = cur.fetchone()
                if not fetched:
                    conn.rollback()
                    # Distinguish NOT_FOUND vs CONFLICT
                    cur.execute(
                        """
                        SELECT version FROM v8_user_profile_entries
                        WHERE owner_id = %s AND category = %s AND key = %s AND status = 'ACTIVE'
                        """,
                        (owner, cat.value, clean_key),
                    )
                    existing = cur.fetchone()
                    if existing:
                        return ProfileResult(ProfileResultStatus.CONFLICT)
                    return ProfileResult(ProfileResultStatus.NOT_FOUND)
            conn.commit()
            entry = _row_to_entry(_pg_row_to_dict(fetched), now=now)
            if entry is None:
                return ProfileResult(ProfileResultStatus.REJECTED)
            return ProfileResult(ProfileResultStatus.OK, entry=entry)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)


def forget_profile_category(owner_id: str, category: Any) -> ProfileResult:
    """Supersede all ACTIVE entries in a category for owner."""
    if not _enabled():
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)
    if not is_valid_owner_id(owner_id):
        return ProfileResult(ProfileResultStatus.REJECTED)
    cat = parse_category(category)
    if cat is None:
        return ProfileResult(ProfileResultStatus.REJECTED)
    owner = normalize_owner_id(owner_id)
    now = time.time()

    if _USE_TEST_STORE:
        with _LOCK:
            rows = _TEST_ROWS.get(owner, [])
            changed: List[ProfileEntry] = []
            for row in rows:
                if (
                    str(row.get("category")) == cat.value
                    and str(row.get("status")) == ProfileStatus.ACTIVE.value
                ):
                    row["status"] = ProfileStatus.SUPERSEDED.value
                    row["version"] = int(row.get("version") or 1) + 1
                    row["updated_at"] = now
                    entry = _row_to_entry(row, now=now)
                    if entry is not None:
                        changed.append(entry)
            return ProfileResult(ProfileResultStatus.OK, entries=tuple(changed))

    try:
        if not ensure_user_profile_schema():
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE v8_user_profile_entries
                    SET status = 'SUPERSEDED',
                        version = version + 1,
                        updated_at = %s
                    WHERE owner_id = %s AND category = %s AND status = 'ACTIVE'
                    RETURNING entry_id, owner_id, schema_version, category, key, value,
                              confidence, provenance, source_memory_id, version, status,
                              created_at, updated_at, confirmed_at, expires_at
                    """,
                    (now, owner, cat.value),
                )
                fetched = cur.fetchall() or ()
            conn.commit()
            entries = []
            for row in fetched:
                entry = _row_to_entry(_pg_row_to_dict(row), now=now)
                if entry is not None:
                    entries.append(entry)
            return ProfileResult(ProfileResultStatus.OK, entries=tuple(entries))
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)


def clear_profile(owner_id: str) -> ProfileResult:
    """Supersede all ACTIVE entries for owner."""
    if not _enabled():
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)
    if not is_valid_owner_id(owner_id):
        return ProfileResult(ProfileResultStatus.REJECTED)
    owner = normalize_owner_id(owner_id)
    now = time.time()

    if _USE_TEST_STORE:
        with _LOCK:
            rows = _TEST_ROWS.get(owner, [])
            changed: List[ProfileEntry] = []
            for row in rows:
                if str(row.get("status")) == ProfileStatus.ACTIVE.value:
                    row["status"] = ProfileStatus.SUPERSEDED.value
                    row["version"] = int(row.get("version") or 1) + 1
                    row["updated_at"] = now
                    entry = _row_to_entry(row, now=now)
                    if entry is not None:
                        changed.append(entry)
            return ProfileResult(ProfileResultStatus.OK, entries=tuple(changed))

    try:
        if not ensure_user_profile_schema():
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE v8_user_profile_entries
                    SET status = 'SUPERSEDED',
                        version = version + 1,
                        updated_at = %s
                    WHERE owner_id = %s AND status = 'ACTIVE'
                    RETURNING entry_id, owner_id, schema_version, category, key, value,
                              confidence, provenance, source_memory_id, version, status,
                              created_at, updated_at, confirmed_at, expires_at
                    """,
                    (now, owner),
                )
                fetched = cur.fetchall() or ()
            conn.commit()
            entries = []
            for row in fetched:
                entry = _row_to_entry(_pg_row_to_dict(row), now=now)
                if entry is not None:
                    entries.append(entry)
            return ProfileResult(ProfileResultStatus.OK, entries=tuple(entries))
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)


def _pg_row_to_dict(fetched: Tuple[Any, ...]) -> Dict[str, Any]:
    return {
        "entry_id": fetched[0],
        "owner_id": fetched[1],
        "schema_version": fetched[2],
        "category": fetched[3],
        "key": fetched[4],
        "value": fetched[5],
        "confidence": fetched[6],
        "provenance": fetched[7],
        "source_memory_id": fetched[8] or "",
        "version": fetched[9],
        "status": fetched[10],
        "created_at": fetched[11],
        "updated_at": fetched[12],
        "confirmed_at": fetched[13],
        "expires_at": fetched[14],
    }


def _upsert_test(
    owner: str,
    normalized: Dict[str, Any],
    *,
    expected_version: Optional[int],
    now: float,
) -> ProfileResult:
    with _LOCK:
        rows = _TEST_ROWS.setdefault(owner, [])
        _materialize_expired_test_rows(rows, now)
        existing = None
        for row in rows:
            if (
                str(row.get("category")) == normalized["category"].value
                and str(row.get("key")) == normalized["key"]
                and str(row.get("status")) == ProfileStatus.ACTIVE.value
            ):
                existing = row
                break

        if existing is not None:
            cur_ver = int(existing.get("version") or 0)
            if expected_version is not None and int(expected_version) != cur_ver:
                return ProfileResult(ProfileResultStatus.CONFLICT)
            if expected_version is None:
                # Unconditional update of existing ACTIVE key (projection / latest-wins)
                pass
            existing["value"] = normalized["value"]
            existing["confidence"] = normalized["confidence"].value
            existing["provenance"] = normalized["provenance"].value
            existing["source_memory_id"] = normalized["source_memory_id"]
            existing["version"] = cur_ver + 1
            existing["updated_at"] = now
            existing["confirmed_at"] = now
            existing["expires_at"] = normalized["expires_at"]
            entry = _row_to_entry(existing, now=now)
            if entry is None:
                return ProfileResult(ProfileResultStatus.REJECTED)
            return ProfileResult(ProfileResultStatus.OK, entry=entry)

        # Create new
        if expected_version is not None:
            return ProfileResult(ProfileResultStatus.CONFLICT)
        if _count_active(rows, now) >= MAX_PROFILE_ENTRIES:
            return ProfileResult(ProfileResultStatus.REJECTED)
        if _count_active(rows, now, normalized["category"]) >= MAX_ENTRIES_PER_CATEGORY:
            return ProfileResult(ProfileResultStatus.REJECTED)

        eid = normalized["entry_id"] or new_profile_entry_id()
        row = {
            "entry_id": eid,
            "owner_id": owner,
            "schema_version": SCHEMA_VERSION,
            "category": normalized["category"].value,
            "key": normalized["key"],
            "value": normalized["value"],
            "confidence": normalized["confidence"].value,
            "provenance": normalized["provenance"].value,
            "source_memory_id": normalized["source_memory_id"],
            "version": 1,
            "status": ProfileStatus.ACTIVE.value,
            "created_at": now,
            "updated_at": now,
            "confirmed_at": now,
            "expires_at": normalized["expires_at"],
        }
        rows.append(row)
        entry = _row_to_entry(row, now=now)
        if entry is None:
            return ProfileResult(ProfileResultStatus.REJECTED)
        return ProfileResult(ProfileResultStatus.OK, entry=entry)


def _upsert_pg(
    owner: str,
    normalized: Dict[str, Any],
    *,
    expected_version: Optional[int],
    now: float,
) -> ProfileResult:
    try:
        if not ensure_user_profile_schema():
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        from database.postgres_db import postgres_manager

        conn = postgres_manager.get_connection()
        if not conn:
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        try:
            with conn.cursor() as cur:
                _materialize_expired_pg(cur, owner, now)
                cur.execute(
                    """
                    SELECT entry_id, owner_id, schema_version, category, key, value,
                           confidence, provenance, source_memory_id, version, status,
                           created_at, updated_at, confirmed_at, expires_at
                    FROM v8_user_profile_entries
                    WHERE owner_id = %s AND category = %s AND key = %s AND status = 'ACTIVE'
                    """,
                    (owner, normalized["category"].value, normalized["key"]),
                )
                existing = cur.fetchone()
                if existing:
                    row = _pg_row_to_dict(existing)
                    cur_ver = int(row["version"])
                    if expected_version is not None and int(expected_version) != cur_ver:
                        return ProfileResult(ProfileResultStatus.CONFLICT)
                    new_ver = cur_ver + 1
                    cur.execute(
                        """
                        UPDATE v8_user_profile_entries SET
                            value = %s, confidence = %s, provenance = %s,
                            source_memory_id = %s, version = %s,
                            updated_at = %s, confirmed_at = %s, expires_at = %s
                        WHERE entry_id = %s AND owner_id = %s AND version = %s AND status = 'ACTIVE'
                        RETURNING entry_id, owner_id, schema_version, category, key, value,
                                  confidence, provenance, source_memory_id, version, status,
                                  created_at, updated_at, confirmed_at, expires_at
                        """,
                        (
                            normalized["value"],
                            normalized["confidence"].value,
                            normalized["provenance"].value,
                            normalized["source_memory_id"] or None,
                            new_ver,
                            now,
                            now,
                            normalized["expires_at"],
                            row["entry_id"],
                            owner,
                            cur_ver,
                        ),
                    )
                    fetched = cur.fetchone()
                    if not fetched:
                        conn.rollback()
                        return ProfileResult(ProfileResultStatus.CONFLICT)
                    conn.commit()
                    entry = _row_to_entry(_pg_row_to_dict(fetched), now=now)
                    if entry is None:
                        return ProfileResult(ProfileResultStatus.REJECTED)
                    return ProfileResult(ProfileResultStatus.OK, entry=entry)

                if expected_version is not None:
                    return ProfileResult(ProfileResultStatus.CONFLICT)

                cur.execute(
                    """
                    SELECT COUNT(*) FROM v8_user_profile_entries
                    WHERE owner_id = %s AND status = 'ACTIVE'
                      AND (expires_at IS NULL OR expires_at > %s)
                    """,
                    (owner, now),
                )
                if int(cur.fetchone()[0] or 0) >= MAX_PROFILE_ENTRIES:
                    return ProfileResult(ProfileResultStatus.REJECTED)
                cur.execute(
                    """
                    SELECT COUNT(*) FROM v8_user_profile_entries
                    WHERE owner_id = %s AND category = %s AND status = 'ACTIVE'
                      AND (expires_at IS NULL OR expires_at > %s)
                    """,
                    (owner, normalized["category"].value, now),
                )
                if int(cur.fetchone()[0] or 0) >= MAX_ENTRIES_PER_CATEGORY:
                    return ProfileResult(ProfileResultStatus.REJECTED)

                eid = normalized["entry_id"] or new_profile_entry_id()
                cur.execute(
                    """
                    INSERT INTO v8_user_profile_entries (
                        entry_id, owner_id, schema_version, category, key, value,
                        confidence, provenance, source_memory_id, version, status,
                        created_at, updated_at, confirmed_at, expires_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, 1, 'ACTIVE',
                        %s, %s, %s, %s
                    )
                    RETURNING entry_id, owner_id, schema_version, category, key, value,
                              confidence, provenance, source_memory_id, version, status,
                              created_at, updated_at, confirmed_at, expires_at
                    """,
                    (
                        eid,
                        owner,
                        SCHEMA_VERSION,
                        normalized["category"].value,
                        normalized["key"],
                        normalized["value"],
                        normalized["confidence"].value,
                        normalized["provenance"].value,
                        normalized["source_memory_id"] or None,
                        now,
                        now,
                        now,
                        normalized["expires_at"],
                    ),
                )
                fetched = cur.fetchone()
            conn.commit()
            entry = _row_to_entry(_pg_row_to_dict(fetched), now=now)
            if entry is None:
                return ProfileResult(ProfileResultStatus.REJECTED)
            return ProfileResult(ProfileResultStatus.OK, entry=entry)
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            # Unique-violation / concurrent ACTIVE insert → CONFLICT (not UNAVAILABLE).
            msg = str(exc).lower()
            pgcode = str(getattr(exc, "pgcode", "") or "")
            if pgcode == "23505" or "unique" in msg or "duplicate" in msg:
                return ProfileResult(ProfileResultStatus.CONFLICT)
            return ProfileResult(ProfileResultStatus.UNAVAILABLE)
        finally:
            postgres_manager.release_connection(conn)
    except Exception:
        return ProfileResult(ProfileResultStatus.UNAVAILABLE)
