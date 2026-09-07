"""
DOOM V5.3.6 — Project & Experience Reconciliation Engine
Audits, detects, and safely reconciles structural anomalies in the
Project & Experience intelligence subsystem:
1. Strategy reliability counter drift vs. authoritative experiences.
2. Orphan experience records (unregistered project references).
3. Project hierarchy cycles and self-parenting anomalies.
4. Stale or invalid cross-project transfer matrix records.

Invariants:
- Never invents authoritative experiences from thin air.
- Preserves PostgreSQL transaction authority and atomicity.
- Logs all remediations with structured reconciliation reports.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from database.postgres_db import postgres_manager
from memory.project_models import (
    calculate_bayesian_strategy_reliability,
    TaskOutcomeStatus,
)

logger = logging.getLogger("DOOM.Memory.ProjectReconciliation")


@dataclass
class ProjectReconciliationReport:
    """Structured report produced by reconciliation audits."""
    status: str = "CLEAN"
    orphan_experiences_fixed: int = 0
    orphan_experiences_detected: int = 0
    strategy_counters_reconciled: int = 0
    hierarchy_cycles_broken: int = 0
    stale_transfers_purged: int = 0
    anomalies_detected: List[Any] = field(default_factory=list)
    remediations: List[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return (
            self.orphan_experiences_fixed == 0
            and self.orphan_experiences_detected == 0
            and self.strategy_counters_reconciled == 0
            and self.hierarchy_cycles_broken == 0
            and self.stale_transfers_purged == 0
            and len(self.anomalies_detected) == 0
        )


class ProjectReconciliationEngine:
    """
    Reconciliation engine for Project & Experience Intelligence.
    """

    def __init__(self, db=None):
        self._db = db or postgres_manager

    def run_reconciliation(self, dry_run: bool = False) -> ProjectReconciliationReport:
        """
        Executes a full reconciliation pass across projects, experiences,
        strategies, and cross-project transfer matrices.
        """
        report = ProjectReconciliationReport()
        conn = self._db.get_connection()
        if not conn:
            report.status = "ERROR"
            report.anomalies_detected.append("Database connection unavailable")
            return report

        try:
            with conn.cursor() as cur:
                # -------------------------------------------------------------
                # 1. Project Hierarchy Check (Self-parenting & immediate cycles)
                # -------------------------------------------------------------
                cur.execute("""
                    SELECT project_id, parent_project_id
                    FROM projects
                    WHERE parent_project_id IS NOT NULL;
                """)
                edges = cur.fetchall()
                parent_map = {p: par for p, par in edges}

                for proj_id, parent_id in edges:
                    if proj_id == parent_id:
                        report.anomalies_detected.append(f"Self-parenting detected in project '{proj_id}'")
                        if not dry_run:
                            cur.execute("UPDATE projects SET parent_project_id = NULL WHERE project_id = %s;", (proj_id,))
                            report.hierarchy_cycles_broken += 1
                            report.remediations.append(f"Cleared self-parenting on project '{proj_id}'")
                    else:
                        # Check transitive cycle (depth <= 20)
                        curr = parent_id
                        visited = {proj_id}
                        cycle_found = False
                        while curr in parent_map:
                            if curr in visited:
                                cycle_found = True
                                break
                            visited.add(curr)
                            curr = parent_map[curr]

                        if cycle_found:
                            report.anomalies_detected.append(f"Hierarchy cycle detected involving project '{proj_id}'")
                            if not dry_run:
                                cur.execute("UPDATE projects SET parent_project_id = NULL WHERE project_id = %s;", (proj_id,))
                                report.hierarchy_cycles_broken += 1
                                report.remediations.append(f"Severed cycle by resetting parent on project '{proj_id}'")
                                parent_map[proj_id] = None

                # -------------------------------------------------------------
                # 2. Orphan Experiences (referencing deleted or missing projects)
                # -------------------------------------------------------------
                cur.execute("""
                    SELECT e.experience_id, e.project_id
                    FROM experiences e
                    LEFT JOIN projects p ON e.project_id = p.project_id
                    WHERE p.project_id IS NULL;
                """)
                orphan_rows = cur.fetchall()
                for exp_id, proj_id in orphan_rows:
                    anomaly_entry = {
                        "type": "ORPHAN_EXPERIENCE",
                        "experience_id": exp_id,
                        "original_project_id": proj_id,
                        "reason": "PROJECT_NOT_FOUND",
                        "action": "REPORTED_UNRESOLVED",
                    }
                    report.anomalies_detected.append(anomaly_entry)
                    report.orphan_experiences_detected += 1
                    report.remediations.append(
                        f"Preserved orphan experience '{exp_id}' referencing missing project '{proj_id}'; historical provenance kept immutable without reassignment"
                    )

                # -------------------------------------------------------------
                # 3. Strategy Reliability Counter Drift
                # -------------------------------------------------------------
                cur.execute("""
                    SELECT s.strategy_id, s.successful_attempts, s.failed_attempts, s.reliability_score
                    FROM strategies s
                    WHERE s.is_deprecated = FALSE;
                """)
                strategies = cur.fetchall()
                for strat_id, s_cnt, f_cnt, rel_score in strategies:
                    # Query actual counts from experiences table where strategy was applied
                    cur.execute("""
                        SELECT outcome_status, confidence_score
                        FROM experiences
                        WHERE strategy_applied->>'strategy_id' = %s;
                    """, (strat_id,))
                    exp_rows = cur.fetchall()
                    if not exp_rows and s_cnt == 0 and f_cnt == 0:
                        continue

                    real_succ = 0
                    real_fail = 0
                    w_succ = 0.0
                    w_fail = 0.0

                    for outcome_status, conf in exp_rows:
                        c_val = float(conf) if conf is not None else 0.8
                        o_clean = str(outcome_status).upper()
                        if o_clean == "SUCCESS":
                            real_succ += 1
                            w_succ += c_val
                        elif o_clean == "PARTIAL_SUCCESS":
                            real_succ += 1
                            w_succ += 0.5 * c_val
                        elif o_clean in ("FAILURE", "ABORTED"):
                            real_fail += 1
                            w_fail += c_val

                    expected_reliability = calculate_bayesian_strategy_reliability(
                        successful_weights=w_succ,
                        failed_weights=w_fail,
                    )

                    drift_detected = (
                        real_succ != s_cnt
                        or real_fail != f_cnt
                        or abs(expected_reliability - float(rel_score)) > 0.001
                    )

                    if drift_detected:
                        report.anomalies_detected.append(
                            f"Strategy '{strat_id}' counter drift: stored ({s_cnt}s, {f_cnt}f, {rel_score:.4f}r) "
                            f"!= computed ({real_succ}s, {real_fail}f, {expected_reliability:.4f}r)"
                        )
                        if not dry_run:
                            cur.execute("""
                                UPDATE strategies
                                SET successful_attempts = %s,
                                    failed_attempts = %s,
                                    total_attempts = %s,
                                    reliability_score = %s,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE strategy_id = %s;
                            """, (real_succ, real_fail, real_succ + real_fail, expected_reliability, strat_id))
                            report.strategy_counters_reconciled += 1
                            report.remediations.append(f"Reconciled strategy '{strat_id}' to computed reliability")

                # -------------------------------------------------------------
                # 4. Stale Transfer Matrix Entries (referencing missing strategies or projects)
                # -------------------------------------------------------------
                cur.execute("""
                    SELECT m.transfer_id, m.strategy_id
                    FROM project_transfer_matrix m
                    LEFT JOIN strategies s ON m.strategy_id = s.strategy_id
                    WHERE m.strategy_id IS NOT NULL AND s.strategy_id IS NULL;
                """)
                stale_transfers = cur.fetchall()
                for transfer_id, strat_id in stale_transfers:
                    report.anomalies_detected.append(f"Transfer matrix entry '{transfer_id}' references missing strategy '{strat_id}'")
                    if not dry_run:
                        cur.execute("DELETE FROM project_transfer_matrix WHERE transfer_id = %s;", (transfer_id,))
                        report.stale_transfers_purged += 1
                        report.remediations.append(f"Purged stale transfer matrix record '{transfer_id}'")

                if not dry_run:
                    conn.commit()
                else:
                    conn.rollback()

            report.status = "CLEAN" if report.is_clean else "RECONCILED" if not dry_run else "DRIFT_DETECTED"

        except Exception as e:
            conn.rollback()
            report.status = "ERROR"
            report.anomalies_detected.append(f"Reconciliation error: {e}")
            logger.error(f"Error during project reconciliation: {e}", exc_info=True)
        finally:
            self._db.release_connection(conn)

        return report


project_reconciliation_engine = ProjectReconciliationEngine()


def run_project_reconciliation(dry_run: bool = False) -> ProjectReconciliationReport:
    """Convenience functional wrapper for reconciliation."""
    return project_reconciliation_engine.run_reconciliation(dry_run=dry_run)
