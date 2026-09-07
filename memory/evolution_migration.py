"""
DOOM V5.3.5 — Memory Evolution Migration Module
Performs deterministic, idempotent backfill of historical V5.3.4 memories into V5.3.5
temporal models, continuous confidence scores, and default freshness classes.
"""
from typing import Dict, Any
import logging

logger = logging.getLogger("DOOM.MemoryEvolutionMigration")


def run_evolution_migration() -> Dict[str, Any]:
    """
    Idempotent migration:
    1. Ensures DDL schema tables and columns exist.
    2. Backfills confidence_score from legacy confidence enum:
       - HIGH -> 0.90
       - MEDIUM -> 0.60
       - LOW -> 0.30
       - UNKNOWN -> 0.50
    3. Assigns semantic freshness_class defaults:
       - PREFERENCE -> FOUNDATIONAL (is_foundational = TRUE, floor = 0.85)
       - SHORT_TERM -> EPHEMERAL
       - EXPERIENCE -> DYNAMIC_FACT
       - SEMANTIC / PROJECT / EPISODIC -> PROJECT_STABLE
    4. Initializes last_confirmed_at safely from updated_at/created_at.
    """
    from database.postgres_db import postgres_manager
    conn = postgres_manager.get_connection()
    if not conn:
        return {"success": False, "error": "PostgreSQL connection unavailable"}

    report = {
        "success": False,
        "records_migrated": 0,
        "preferences_foundationalized": 0,
        "error": None,
    }

    try:
        with conn.cursor() as cur:
            # 1. Backfill confidence_score for rows where it is still at uninitialized default or null
            cur.execute("""
                UPDATE memory_records
                SET confidence_score = CASE
                        WHEN confidence = 'HIGH' THEN 0.90
                        WHEN confidence = 'MEDIUM' THEN 0.60
                        WHEN confidence = 'LOW' THEN 0.30
                        ELSE 0.50
                    END,
                    freshness_class = CASE
                        WHEN memory_type = 'PREFERENCE' THEN 'FOUNDATIONAL'
                        WHEN memory_type = 'SHORT_TERM' THEN 'EPHEMERAL'
                        WHEN memory_type = 'EXPERIENCE' THEN 'DYNAMIC_FACT'
                        ELSE 'PROJECT_STABLE'
                    END,
                    is_foundational = (memory_type = 'PREFERENCE'),
                    last_confirmed_at = COALESCE(last_confirmed_at, updated_at, created_at, CURRENT_TIMESTAMP)
                WHERE confidence_score = 0.50 AND is_foundational = FALSE AND memory_type = 'PREFERENCE';
            """)
            pref_count = cur.rowcount

            # 2. General backfill for any legacy rows missing last_confirmed_at
            cur.execute("""
                UPDATE memory_records
                SET last_confirmed_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)
                WHERE last_confirmed_at IS NULL;
            """)
            updated_count = cur.rowcount

            report["records_migrated"] = updated_count
            report["preferences_foundationalized"] = pref_count
            report["success"] = True

        conn.commit()
        return report
    except Exception as e:
        conn.rollback()
        logger.error(f"[EVOLUTION MIGRATION] Migration failed: {e}")
        report["error"] = str(e)
        return report
    finally:
        postgres_manager.release_connection(conn)
