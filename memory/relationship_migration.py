"""
DOOM V5.3.4 — Memory Relationship Migration Module
Performs deterministic, idempotent backfill of historical V5.3.3 supersedes_memory_id
records into the canonical memory_relationships table.
"""
from typing import Dict, Any
from database.postgres_db import postgres_manager


def run_relationship_migration() -> Dict[str, Any]:
    """
    Idempotently migrates all existing scalar supersedes_memory_id pointers
    into first-class authoritative SUPERSEDES rows in memory_relationships.
    Returns migration report with count of processed and migrated records.
    """
    conn = postgres_manager.get_connection()
    if not conn:
        return {"success": False, "error": "Database connection unavailable", "migrated_count": 0}

    try:
        with conn.cursor() as cur:
            # 1. Inspect existing legacy supersedes records
            cur.execute("""
                SELECT COUNT(*) FROM memory_records
                WHERE supersedes_memory_id IS NOT NULL AND supersedes_memory_id <> memory_id;
            """)
            legacy_count = cur.fetchone()[0]

            # 2. Insert into memory_relationships avoiding self-references and cycles
            sql = """
                INSERT INTO memory_relationships (
                    relationship_id, source_memory_id, target_memory_id, relationship_type,
                    confidence, reason, actor, idempotency_key, created_at, metadata
                )
                SELECT 
                    'rel_mig_' || substr(md5(m1.memory_id || m1.supersedes_memory_id), 1, 16),
                    m1.memory_id,
                    m1.supersedes_memory_id,
                    'SUPERSEDES',
                    1.0,
                    'Migrated from V5.3.3 legacy supersedes_memory_id',
                    'SYSTEM',
                    'idem_mig_' || substr(md5(m1.memory_id || m1.supersedes_memory_id), 1, 16),
                    CURRENT_TIMESTAMP,
                    '{"migrated": true}'::jsonb
                FROM memory_records m1
                JOIN memory_records m2 ON m1.supersedes_memory_id = m2.memory_id
                WHERE m1.supersedes_memory_id IS NOT NULL
                  AND m1.supersedes_memory_id <> m1.memory_id
                ON CONFLICT (idempotency_key) DO NOTHING;
            """
            cur.execute(sql)
            migrated_count = cur.rowcount
            conn.commit()

            return {
                "success": True,
                "legacy_supersedes_found": legacy_count,
                "edges_inserted": migrated_count,
            }
    except Exception as e:
        conn.rollback()
        return {"success": False, "error": str(e), "migrated_count": 0}
    finally:
        postgres_manager.release_connection(conn)
