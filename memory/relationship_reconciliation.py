"""
DOOM V5.3.4 — Memory Relationship Reconciliation Engine
Independent auditor for memory knowledge graph integrity:
- Scans for orphan edges
- Verifies acyclicity of the entire supersession subgraph
- Reports on state inconsistency
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from database.postgres_db import postgres_manager


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RelationshipReconciliationReport:
    """Audit report produced by RelationshipReconciliationEngine."""
    scanned_at: str = field(default_factory=_utcnow)
    total_relationships: int = 0
    orphan_edges_detected: int = 0
    cycles_detected: int = 0
    inconsistencies_detected: int = 0
    details: List[str] = field(default_factory=list)
    healthy: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scanned_at": self.scanned_at,
            "total_relationships": self.total_relationships,
            "orphan_edges_detected": self.orphan_edges_detected,
            "cycles_detected": self.cycles_detected,
            "inconsistencies_detected": self.inconsistencies_detected,
            "details": self.details,
            "healthy": self.healthy,
        }


class RelationshipReconciliationEngine:
    """
    Audits and validates the global health of the memory relationship graph.
    """

    def run_reconciliation(self) -> RelationshipReconciliationReport:
        """Execute a full health audit of the relationship graph."""
        report = RelationshipReconciliationReport()
        conn = postgres_manager.get_connection()
        if not conn:
            report.healthy = False
            report.details.append("Database connection unavailable")
            return report

        try:
            with conn.cursor() as cur:
                # 1. Total relationship count
                cur.execute("SELECT COUNT(*) FROM memory_relationships;")
                report.total_relationships = cur.fetchone()[0]

                # 2. Orphan edge detection
                cur.execute("""
                    SELECT r.relationship_id, r.source_memory_id, r.target_memory_id
                    FROM memory_relationships r
                    LEFT JOIN memory_records s ON r.source_memory_id = s.memory_id
                    LEFT JOIN memory_records t ON r.target_memory_id = t.memory_id
                    WHERE s.memory_id IS NULL OR t.memory_id IS NULL;
                """)
                orphans = cur.fetchall()
                report.orphan_edges_detected = len(orphans)
                if orphans:
                    report.healthy = False
                    for o in orphans[:5]:
                        report.details.append(f"Orphan edge detected: {o[0]} ({o[1]} -> {o[2]})")

                # 3. Global Cycle Audit on SUPERSEDES edges
                # Find any node reachable from itself via SUPERSEDES edges (bounded to 15 hops)
                cur.execute("""
                    WITH RECURSIVE supersession_paths AS (
                        SELECT source_memory_id AS start_node, target_memory_id AS current_node, 1 AS depth
                        FROM memory_relationships
                        WHERE relationship_type = 'SUPERSEDES'
                        UNION ALL
                        SELECT sp.start_node, r.target_memory_id AS current_node, sp.depth + 1
                        FROM memory_relationships r
                        JOIN supersession_paths sp ON r.source_memory_id = sp.current_node
                        WHERE r.relationship_type = 'SUPERSEDES' AND sp.depth < 15
                    )
                    SELECT start_node, current_node
                    FROM supersession_paths
                    WHERE start_node = current_node;
                """)
                cycle_rows = cur.fetchall()
                report.cycles_detected = len(cycle_rows)
                if cycle_rows:
                    report.healthy = False
                    for c in cycle_rows[:5]:
                        report.details.append(f"Cycle detected involving node: {c[0]}")

                # 4. State Inconsistency Check:
                # Active records that are the TARGET of a SUPERSEDES edge
                cur.execute("""
                    SELECT r.target_memory_id, r.source_memory_id
                    FROM memory_relationships r
                    JOIN memory_records m ON r.target_memory_id = m.memory_id
                    WHERE r.relationship_type = 'SUPERSEDES' AND m.status = 'ACTIVE';
                """)
                inconsistencies = cur.fetchall()
                report.inconsistencies_detected = len(inconsistencies)
                if inconsistencies:
                    report.healthy = False
                    for inc in inconsistencies[:5]:
                        report.details.append(f"Inconsistency: target memory '{inc[0]}' is ACTIVE despite being superseded by '{inc[1]}'")

        except Exception as e:
            report.healthy = False
            report.details.append(f"Reconciliation query error: {e}")
        finally:
            postgres_manager.release_connection(conn)

        return report


relationship_reconciliation_engine = RelationshipReconciliationEngine()
