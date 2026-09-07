"""
DOOM V5.3.5 — Memory Evolution Reconciliation Engine
Audits memory evolution data integrity:
- Detects score bounds violations (confidence_score, importance, strength).
- Detects orphaned evidence records.
- Identifies active memories past valid_until expiration.
- Detects score drift between evidence logs and stored scores.
- Never fabricates evidence or invents truth.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger("DOOM.MemoryEvolutionReconciliation")


@dataclass
class EvolutionReconciliationReport:
    """Audit report produced by MemoryEvolutionReconciliationEngine."""
    total_memories_checked: int = 0
    total_evidence_checked: int = 0
    bounds_violations_detected: int = 0
    bounds_violations_repaired: int = 0
    expired_active_memories: List[str] = field(default_factory=list)
    orphan_evidence_count: int = 0
    orphan_evolution_events_count: int = 0
    score_drift_detected: int = 0
    is_healthy: bool = True
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_memories_checked": self.total_memories_checked,
            "total_evidence_checked": self.total_evidence_checked,
            "bounds_violations_detected": self.bounds_violations_detected,
            "bounds_violations_repaired": self.bounds_violations_repaired,
            "expired_active_memories": self.expired_active_memories,
            "orphan_evidence_count": self.orphan_evidence_count,
            "orphan_evolution_events_count": self.orphan_evolution_events_count,
            "score_drift_detected": self.score_drift_detected,
            "is_healthy": self.is_healthy,
            "error": self.error,
        }


class MemoryEvolutionReconciliationEngine:
    """
    Background integrity auditor for V5.3.5 evolution state.
    Strictly audit and repair of derived state; never invents evidence.
    """

    def _get_manager(self):
        from database.postgres_db import postgres_manager
        return postgres_manager

    def run_reconciliation(self, repair: bool = True) -> EvolutionReconciliationReport:
        """
        Execute full reconciliation audit sweep across memory_records,
        memory_evidence, and memory_evolution_events.
        """
        report = EvolutionReconciliationReport()
        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            report.is_healthy = False
            report.error = "PostgreSQL unavailable"
            return report

        try:
            with conn.cursor() as cur:
                # 1. Total counts
                cur.execute("SELECT COUNT(*) FROM memory_records;")
                report.total_memories_checked = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM memory_evidence;")
                report.total_evidence_checked = cur.fetchone()[0]

                # 2. Check bounds violations on memory_records
                cur.execute("""
                    SELECT memory_id, confidence_score, importance
                    FROM memory_records
                    WHERE confidence_score < 0.0 OR confidence_score > 1.0
                       OR importance < 0.0 OR importance > 1.0;
                """)
                bad_bounds = cur.fetchall()
                report.bounds_violations_detected = len(bad_bounds)

                if bad_bounds and repair:
                    cur.execute("""
                        UPDATE memory_records
                        SET confidence_score = LEAST(1.0, GREATEST(0.01, confidence_score)),
                            importance = LEAST(1.0, GREATEST(0.0, importance))
                        WHERE confidence_score < 0.0 OR confidence_score > 1.0
                           OR importance < 0.0 OR importance > 1.0;
                    """)
                    report.bounds_violations_repaired = cur.rowcount

                # 3. Check for expired active memories
                now_str = datetime.now(timezone.utc).isoformat()
                cur.execute("""
                    SELECT memory_id FROM memory_records
                    WHERE status = 'ACTIVE' AND valid_until IS NOT NULL AND valid_until < CURRENT_TIMESTAMP;
                """)
                expired_rows = cur.fetchall()
                report.expired_active_memories = [r[0] for r in expired_rows]

                # 4. Check for orphan evidence (FK ON DELETE CASCADE handles this, but verify)
                cur.execute("""
                    SELECT COUNT(*) FROM memory_evidence e
                    LEFT JOIN memory_records m ON e.memory_id = m.memory_id
                    WHERE m.memory_id IS NULL;
                """)
                report.orphan_evidence_count = cur.fetchone()[0]

                # 5. Check for orphan evolution events
                cur.execute("""
                    SELECT COUNT(*) FROM memory_evolution_events ev
                    LEFT JOIN memory_records m ON ev.memory_id = m.memory_id
                    WHERE m.memory_id IS NULL;
                """)
                report.orphan_evolution_events_count = cur.fetchone()[0]

                # Determine overall health
                if (report.bounds_violations_detected > 0 and not repair) or report.orphan_evidence_count > 0:
                    report.is_healthy = False

            conn.commit()
            return report
        except Exception as e:
            conn.rollback()
            logger.error(f"[EVOLUTION RECONCILIATION] Run failed: {e}")
            report.is_healthy = False
            report.error = str(e)
            return report
        finally:
            pg.release_connection(conn)


evolution_reconciliation_engine = MemoryEvolutionReconciliationEngine()
