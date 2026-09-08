"""
DOOM V5.3.7.3 — Governance & Evidence Reconciliation Subsystem
Performs automated state audit, repairs Bayesian reliability score drift against empirical experiences,
and enforces privacy quarantine compliance across the project transfer matrix.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, List, Optional

from database.postgres_db import postgres_manager
from memory.project_models import (
    calculate_bayesian_strategy_reliability,
    TransferStatus,
)
from memory.types import PrivacyClass

logger = logging.getLogger("doom.memory.governance_reconciliation")


@dataclass
class GovernanceReconciliationReport:
    """Comprehensive diagnostic outcome of governance & evidence state reconciliation."""
    strategies_audited: int = 0
    counter_mismatches_fixed: int = 0
    reliability_drift_repaired: int = 0
    orphaned_matrix_rows_removed: int = 0
    quarantine_violations_fixed: int = 0
    superseded_count: int = 0
    duration_ms: float = 0.0
    is_healthy: bool = True
    details: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategies_audited": self.strategies_audited,
            "counter_mismatches_fixed": self.counter_mismatches_fixed,
            "reliability_drift_repaired": self.reliability_drift_repaired,
            "orphaned_matrix_rows_removed": self.orphaned_matrix_rows_removed,
            "quarantine_violations_fixed": self.quarantine_violations_fixed,
            "superseded_count": self.superseded_count,
            "duration_ms": self.duration_ms,
            "is_healthy": self.is_healthy,
            "details": self.details,
        }


class GovernanceReconciliationEngine:
    """
    V5.3.7.3: Verifies database consistency across experiences, strategies, and transfer matrix.
    """

    def __init__(self, db_manager=None):
        self._manager = db_manager or postgres_manager

    def reconcile_strategy_governance(self, dry_run: bool = False) -> GovernanceReconciliationReport:
        """
        Runs comprehensive audit and repair on strategy counters, bayesian reliability,
        and transfer matrix quarantine integrity.
        """
        t0 = time.time()
        report = GovernanceReconciliationReport()

        conn = self._manager.get_connection()
        if not conn:
            report.is_healthy = False
            report.details.append("Database connection unavailable")
            return report

        try:
            with conn.cursor() as cur:
                # 1. Audit Strategies vs Experiences
                cur.execute("""
                    SELECT s.strategy_id, s.total_attempts, s.successful_attempts, s.failed_attempts, s.reliability_score,
                           COALESCE(e.actual_total, 0), COALESCE(e.actual_succ, 0), COALESCE(e.actual_fail, 0)
                    FROM strategies s
                    LEFT JOIN (
                        SELECT COALESCE(strategy_id, strategy_applied->>'strategy_id') as sid,
                               COUNT(*) as actual_total,
                               COUNT(CASE WHEN outcome_status = 'SUCCESS' THEN 1 END) as actual_succ,
                               COUNT(CASE WHEN outcome_status IN ('FAILURE', 'PARTIAL_SUCCESS') THEN 1 END) as actual_fail
                        FROM experiences
                        WHERE COALESCE(strategy_id, strategy_applied->>'strategy_id') IS NOT NULL
                        GROUP BY COALESCE(strategy_id, strategy_applied->>'strategy_id')
                    ) e ON s.strategy_id = e.sid
                    WHERE s.is_deprecated = FALSE;
                """)
                strat_rows = cur.fetchall()
                report.strategies_audited = len(strat_rows)

                for r in strat_rows:
                    strat_id = r[0]
                    curr_tot, curr_succ, curr_fail, curr_rel = r[1], r[2], r[3], float(r[4])
                    act_tot, act_succ, act_fail = int(r[5]), int(r[6]), int(r[7])

                    # Note: experiences might only account for recorded experiences, but if actual > current
                    # or mismatch exists, we synchronize
                    counter_mismatch = (curr_tot != act_tot or curr_succ != act_succ or curr_fail != act_fail)
                    expected_rel = calculate_bayesian_strategy_reliability(
                        successful_attempts=act_succ,
                        total_attempts=act_tot,
                    )
                    rel_drift = abs(curr_rel - expected_rel) > 0.001

                    if counter_mismatch or rel_drift:
                        report.details.append(
                            f"Strategy {strat_id} drift: counters ({curr_tot}/{curr_succ}/{curr_fail} vs {act_tot}/{act_succ}/{act_fail}), "
                            f"rel ({curr_rel} vs {expected_rel})"
                        )
                        if not dry_run:
                            cur.execute("""
                                UPDATE strategies
                                SET total_attempts = %s,
                                    successful_attempts = %s,
                                    failed_attempts = %s,
                                    reliability_score = %s,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE strategy_id = %s;
                            """, (act_tot, act_succ, act_fail, expected_rel, strat_id))
                        if counter_mismatch:
                            report.counter_mismatches_fixed += 1
                        if rel_drift:
                            report.reliability_drift_repaired += 1

                # 2. Check Orphaned Transfer Matrix Rows
                cur.execute("""
                    SELECT ptm.transfer_id
                    FROM project_transfer_matrix ptm
                    LEFT JOIN projects p_src ON ptm.source_project_id = p_src.project_id
                    LEFT JOIN projects p_tgt ON ptm.target_project_id = p_tgt.project_id
                    WHERE p_src.project_id IS NULL OR p_tgt.project_id IS NULL;
                """)
                orphaned = [row[0] for row in cur.fetchall()]
                if orphaned:
                    report.orphaned_matrix_rows_removed = len(orphaned)
                    report.details.append(f"Found {len(orphaned)} orphaned transfer matrix records")
                    if not dry_run:
                        cur.execute("""
                            DELETE FROM project_transfer_matrix
                            WHERE transfer_id = ANY(%s);
                        """, (orphaned,))

                # 3. Check Quarantine Violations in Transfer Matrix
                cur.execute("""
                    SELECT ptm.transfer_id, ptm.source_project_id, p_src.privacy_class
                    FROM project_transfer_matrix ptm
                    JOIN projects p_src ON ptm.source_project_id = p_src.project_id
                    WHERE p_src.privacy_class = 'SENSITIVE'
                      AND ptm.status = 'APPROVED';
                """)
                quarantine_violations = cur.fetchall()
                if quarantine_violations:
                    report.quarantine_violations_fixed = len(quarantine_violations)
                    report.is_healthy = False
                    for v in quarantine_violations:
                        report.details.append(f"CRITICAL: Quarantine violation in transfer {v[0]} for sensitive project {v[1]}")
                    if not dry_run:
                        cur.execute("""
                            UPDATE project_transfer_matrix
                            SET status = 'REJECTED',
                                rejection_reason = 'QUARANTINE_ENFORCEMENT: Source project is SENSITIVE'
                            WHERE transfer_id = ANY(%s);
                        """, ([v[0] for v in quarantine_violations],))

            if not dry_run:
                conn.commit()
            else:
                conn.rollback()

        except Exception as e:
            conn.rollback()
            logger.error(f"[GOV RECON] Reconciliation error: {e}")
            report.is_healthy = False
            report.details.append(f"Reconciliation error: {e}")
        finally:
            self._manager.release_connection(conn)

        report.duration_ms = round((time.time() - t0) * 1000.0, 2)
        return report

    def reconcile_transfer_matrix(
        self,
        target_project_id: Optional[str] = None,
        dry_run: bool = False,
    ) -> GovernanceReconciliationReport:
        """
        Audits project_transfer_matrix records against current strategy lifecycle state
        (e.g., deprecated strategies, archived projects) and marks stale approvals as 'SUPERSEDED'.
        """
        t0 = time.time()
        report = GovernanceReconciliationReport()

        conn = self._manager.get_connection()
        if not conn:
            report.is_healthy = False
            report.details.append("Database connection unavailable")
            return report

        try:
            with conn.cursor() as cur:
                query = """
                    SELECT ptm.transfer_id, ptm.strategy_id
                    FROM project_transfer_matrix ptm
                    JOIN strategies s ON ptm.strategy_id = s.strategy_id
                    WHERE ptm.status IN ('APPROVED', 'EVALUATED')
                      AND (s.is_deprecated = TRUE);
                """
                if target_project_id:
                    query = """
                        SELECT ptm.transfer_id, ptm.strategy_id
                        FROM project_transfer_matrix ptm
                        JOIN strategies s ON ptm.strategy_id = s.strategy_id
                        WHERE ptm.status IN ('APPROVED', 'EVALUATED')
                          AND (s.is_deprecated = TRUE)
                          AND ptm.target_project_id = %s;
                    """
                    cur.execute(query, (target_project_id,))
                else:
                    cur.execute(query)

                stale_rows = cur.fetchall()
                if stale_rows:
                    transfer_ids = [r[0] for r in stale_rows]
                    report.superseded_count = len(transfer_ids)
                    report.details.append(f"Marking {len(transfer_ids)} stale transfer matrix records as SUPERSEDED")
                    if not dry_run:
                        cur.execute("""
                            UPDATE project_transfer_matrix
                            SET status = 'SUPERSEDED',
                                rejection_reason = 'GOVERNANCE_RECONCILIATION: Source strategy has been deprecated'
                            WHERE transfer_id = ANY(%s);
                        """, (transfer_ids,))

            if not dry_run:
                conn.commit()
            else:
                conn.rollback()
        except Exception as e:
            conn.rollback()
            logger.error(f"[GOV RECON] Error reconciling transfer matrix: {e}")
            report.is_healthy = False
            report.details.append(f"Error: {e}")
        finally:
            self._manager.release_connection(conn)

        report.duration_ms = round((time.time() - t0) * 1000.0, 2)
        return report


governance_reconciliation_engine = GovernanceReconciliationEngine()
