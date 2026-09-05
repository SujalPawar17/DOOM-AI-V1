"""
DOOM V5.1 — Memory Repository
PostgreSQL persistence layer for MemoryRecord objects.
All database operations go through this class.
This is the ONLY code that reads/writes the memory_records table.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from memory.schemas import MemoryRecord
from memory.types import (
    MemoryType, MemoryStatus, MemorySource,
    ConfidenceLevel, VerificationStatus, PrivacyClass,
)


def _serialize_list(value: List) -> str:
    return json.dumps(value or [], default=str)


def _serialize_dict(value: Dict) -> str:
    return json.dumps(value or {}, default=str)


class MemoryRepository:
    """
    PostgreSQL CRUD for the memory_records table.
    Uses the existing postgres_manager connection pool.
    Never raises — returns empty/None on failure and logs errors.
    """

    def _get_manager(self):
        """Deferred import to avoid circular dependency at module load."""
        from database.postgres_db import postgres_manager
        return postgres_manager

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def store(self, record: MemoryRecord) -> bool:
        """Insert or update a MemoryRecord. Returns True on success."""
        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO memory_records (
                        memory_id, memory_type, content, source, confidence,
                        importance, status, project_id, task_id, entity_ids, tags,
                        supersedes_memory_id, source_event_id, verification_status,
                        privacy_class, metadata, created_at, updated_at, last_accessed_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (memory_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        confidence = EXCLUDED.confidence,
                        importance = EXCLUDED.importance,
                        status = EXCLUDED.status,
                        verification_status = EXCLUDED.verification_status,
                        tags = EXCLUDED.tags,
                        metadata = EXCLUDED.metadata,
                        updated_at = CURRENT_TIMESTAMP;
                """, (
                    record.memory_id,
                    record.memory_type.value,
                    record.content,
                    record.source.value,
                    record.confidence.value,
                    record.importance,
                    record.status.value,
                    record.project_id,
                    record.task_id,
                    _serialize_list(record.entity_ids),
                    _serialize_list(record.tags),
                    record.supersedes_memory_id,
                    record.source_event_id,
                    record.verification_status.value,
                    record.privacy_class.value,
                    _serialize_dict(record.metadata),
                    record.created_at,
                    record.updated_at,
                    record.last_accessed_at,
                ))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            print(f"[MEMORY REPO] Store failed for {record.memory_id}: {e}")
            return False
        finally:
            pg.release_connection(conn)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_by_id(self, memory_id: str) -> Optional[MemoryRecord]:
        """Fetch a single MemoryRecord by ID."""
        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            return None
        try:
            from psycopg2 import extras
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM memory_records WHERE memory_id = %s;",
                    (memory_id,)
                )
                row = cur.fetchone()
                if row:
                    return self._row_to_record(dict(row))
            return None
        except Exception as e:
            print(f"[MEMORY REPO] get_by_id failed: {e}")
            return None
        finally:
            pg.release_connection(conn)

    def search(
        self,
        query: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        status: Optional[MemoryStatus] = None,
        project_id: Optional[str] = None,
        task_id: Optional[str] = None,
        min_importance: float = 0.0,
        privacy_classes: Optional[List[PrivacyClass]] = None,
        limit: int = 50,
    ) -> List[MemoryRecord]:
        """
        Flexible search. Returns bounded list of MemoryRecords.
        Defaults to ACTIVE status only when status not specified.
        """
        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            return []
        try:
            from psycopg2 import extras
            conditions = []
            params: List[Any] = []

            # Default: only ACTIVE records unless caller specifies
            if status is not None:
                conditions.append("status = %s")
                params.append(status.value)
            else:
                conditions.append("status = 'ACTIVE'")

            if memory_type:
                conditions.append("memory_type = %s")
                params.append(memory_type.value)

            if project_id:
                conditions.append("project_id = %s")
                params.append(project_id)

            if task_id:
                conditions.append("task_id = %s")
                params.append(task_id)

            if min_importance > 0.0:
                conditions.append("importance >= %s")
                params.append(min_importance)

            if privacy_classes:
                pc_values = [pc.value for pc in privacy_classes]
                conditions.append(f"privacy_class = ANY(%s)")
                params.append(pc_values)

            if query:
                conditions.append("content ILIKE %s")
                params.append(f"%{query}%")

            where_clause = " AND ".join(conditions) if conditions else "TRUE"
            sql = f"""
                SELECT * FROM memory_records
                WHERE {where_clause}
                ORDER BY importance DESC, created_at DESC
                LIMIT %s;
            """
            params.append(limit)

            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [self._row_to_record(dict(r)) for r in rows]
        except Exception as e:
            print(f"[MEMORY REPO] Search failed: {e}")
            return []
        finally:
            pg.release_connection(conn)

    def get_active_by_type(self, memory_type: MemoryType, limit: int = 20) -> List[MemoryRecord]:
        """Get all ACTIVE records of a given type."""
        return self.search(memory_type=memory_type, limit=limit)

    # ------------------------------------------------------------------
    # Update / Status transitions
    # ------------------------------------------------------------------

    def update_status(self, memory_id: str, new_status: MemoryStatus) -> bool:
        """Update only the status field of a memory record."""
        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE memory_records
                    SET status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE memory_id = %s;
                """, (new_status.value, memory_id))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            print(f"[MEMORY REPO] update_status failed for {memory_id}: {e}")
            return False
        finally:
            pg.release_connection(conn)

    def update_content(self, memory_id: str, new_content: str) -> bool:
        """Update the content of an existing memory record."""
        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE memory_records
                    SET content = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE memory_id = %s AND status = 'ACTIVE';
                """, (new_content, memory_id))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            print(f"[MEMORY REPO] update_content failed for {memory_id}: {e}")
            return False
        finally:
            pg.release_connection(conn)

    def touch_accessed(self, memory_id: str) -> None:
        """Update last_accessed_at for a memory record (non-blocking)."""
        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE memory_records
                    SET last_accessed_at = CURRENT_TIMESTAMP
                    WHERE memory_id = %s;
                """, (memory_id,))
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            pg.release_connection(conn)

    def find_conflicting_active(
        self,
        memory_type: MemoryType,
        content_keywords: List[str],
        project_id: Optional[str] = None,
    ) -> List[MemoryRecord]:
        """
        Find existing ACTIVE memories of the same type that may conflict
        with a new memory (for supersession logic).
        """
        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            return []
        try:
            from psycopg2 import extras
            conditions = ["status = 'ACTIVE'", "memory_type = %s"]
            params: List[Any] = [memory_type.value]
            if project_id:
                conditions.append("project_id = %s")
                params.append(project_id)
            # Keyword content overlap
            keyword_conditions = " OR ".join(["content ILIKE %s"] * len(content_keywords))
            if keyword_conditions:
                conditions.append(f"({keyword_conditions})")
                for kw in content_keywords:
                    params.append(f"%{kw}%")

            where = " AND ".join(conditions)
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM memory_records WHERE {where} ORDER BY created_at DESC LIMIT 10;",
                    params
                )
                rows = cur.fetchall()
                return [self._row_to_record(dict(r)) for r in rows]
        except Exception as e:
            print(f"[MEMORY REPO] find_conflicting_active failed: {e}")
            return []
        finally:
            pg.release_connection(conn)

    def count_active(self) -> int:
        """Return count of ACTIVE memory records."""
        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            return 0
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM memory_records WHERE status = 'ACTIVE';")
                row = cur.fetchone()
                return row[0] if row else 0
        except Exception:
            return 0
        finally:
            pg.release_connection(conn)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_record(self, row: Dict[str, Any]) -> MemoryRecord:
        """Convert a database row dict to a MemoryRecord."""

        def safe_list(val) -> List:
            if isinstance(val, list):
                return val
            if isinstance(val, str):
                try:
                    result = json.loads(val)
                    return result if isinstance(result, list) else []
                except Exception:
                    return []
            return []

        def safe_dict(val) -> Dict:
            if isinstance(val, dict):
                return val
            if isinstance(val, str):
                try:
                    result = json.loads(val)
                    return result if isinstance(result, dict) else {}
                except Exception:
                    return {}
            return {}

        def iso_str(val) -> Optional[str]:
            if val is None:
                return None
            if isinstance(val, str):
                return val
            if hasattr(val, "isoformat"):
                return val.isoformat()
            return str(val)

        return MemoryRecord(
            memory_id=row.get("memory_id", ""),
            memory_type=MemoryType(row.get("memory_type", MemoryType.SEMANTIC.value)),
            content=row.get("content", ""),
            source=MemorySource(row.get("source", MemorySource.DERIVED_CONTEXT.value)),
            confidence=ConfidenceLevel(row.get("confidence", ConfidenceLevel.MEDIUM.value)),
            importance=float(row.get("importance", 0.5)),
            status=MemoryStatus(row.get("status", MemoryStatus.ACTIVE.value)),
            project_id=row.get("project_id"),
            task_id=row.get("task_id"),
            entity_ids=safe_list(row.get("entity_ids")),
            tags=safe_list(row.get("tags")),
            supersedes_memory_id=row.get("supersedes_memory_id"),
            source_event_id=row.get("source_event_id"),
            verification_status=VerificationStatus(row.get("verification_status", VerificationStatus.UNVERIFIED.value)),
            privacy_class=PrivacyClass(row.get("privacy_class", PrivacyClass.NORMAL.value)),
            metadata=safe_dict(row.get("metadata")),
            created_at=iso_str(row.get("created_at")) or "",
            updated_at=iso_str(row.get("updated_at")) or "",
            last_accessed_at=iso_str(row.get("last_accessed_at")),
        )


memory_repository = MemoryRepository()
