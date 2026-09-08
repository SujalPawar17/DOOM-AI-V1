"""
DOOM V5.3.7.3 — Atomic Evidence Transaction Engine
Provides strictly atomic, transactional logging of experiences and bayesian updates
to strategy reliability scores with deterministic concurrency control (SELECT ... FOR UPDATE).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid

from memory.project_models import (
    TaskOutcomeStatus,
    calculate_bayesian_strategy_reliability,
    compute_experience_idempotency_hash,
)
from memory.types import PrivacyClass
from database.postgres_db import postgres_manager

logger = logging.getLogger("doom.memory.evidence_transaction")


class EvidenceTransactionStatus(str, Enum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"


class EvidenceTransactionError(Exception):
    """Base exception for evidence transaction failures."""
    pass


class EvidenceValidationError(EvidenceTransactionError):
    """Raised when evidence parameters fail validation."""
    pass


class EvidenceIdempotencyConflictError(EvidenceTransactionError):
    """Raised when an idempotency collision is detected with mismatching payload."""
    pass


class EvidenceTransaction:
    """
    V5.3.7.3: Manages strictly atomic database transactions for recording evidence
    and mutating strategy reliability and counters.
    """

    def __init__(self, db_manager=None):
        self._manager = db_manager or postgres_manager

    def record_evidence(
        self,
        project_id: str,
        outcome_status: Any,
        task_context: Dict[str, Any],
        strategy_applied: Optional[Dict[str, Any]] = None,
        experience_id: Optional[str] = None,
        execution_time_ms: Optional[int] = None,
        tools_used: Optional[List[str]] = None,
        error_details: Optional[Dict[str, Any]] = None,
        lesson_learned: Optional[str] = None,
        client_transaction_id: Optional[str] = None,
        confidence_score: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Atomically records task evidence in `experiences` and updates `strategies` reliability.
        - Validates project and strategy existence.
        - Uses SELECT ... FOR UPDATE on strategies row to serialize concurrent Bayesian updates.
        - Guarantees atomicity: if strategy update fails, experience record is rolled back.
        - Preserves privacy quarantine for SENSITIVE projects.
        """
        # Normalize and validate outcome_status
        if isinstance(outcome_status, Enum):
            status_val = outcome_status.value
        else:
            status_val = str(outcome_status).upper()

        if status_val not in ("SUCCESS", "FAILURE", "PARTIAL_SUCCESS"):
            raise EvidenceValidationError(f"Invalid outcome_status: {status_val}")

        project_id_clean = str(project_id).strip().lower()
        if not project_id_clean:
            raise EvidenceValidationError("project_id cannot be empty")

        task_context = task_context or {}
        tools_used = tools_used or []

        # Strategy resolution
        strat_id = None
        if strategy_applied and isinstance(strategy_applied, dict):
            strat_id = strategy_applied.get("strategy_id")

        # Compute idempotency hash
        idempotency_hash = compute_experience_idempotency_hash(
            project_id=project_id_clean,
            task_context=task_context,
            strategy_applied=strategy_applied,
            tools_used=tools_used,
            execution_time_ms=execution_time_ms,
            outcome_status=status_val,
        )

        exp_id = experience_id or f"exp_{uuid.uuid4().hex[:12]}"
        client_tx_id = client_transaction_id or f"etx_{uuid.uuid4().hex[:12]}"

        conn = self._manager.get_connection()
        if not conn:
            raise EvidenceTransactionError("PostgreSQL connection unavailable for evidence transaction")

        try:
            with conn.cursor() as cur:
                # 1. Validate project existence & privacy class
                cur.execute("SELECT project_id, privacy_class FROM projects WHERE project_id = %s;", (project_id_clean,))
                proj_row = cur.fetchone()
                if not proj_row:
                    raise EvidenceValidationError(f"Target project '{project_id_clean}' does not exist")
                proj_privacy = proj_row[1]

                # 2. Check Idempotency collision
                cur.execute("""
                    SELECT experience_id, idempotency_hash, outcome_status, client_transaction_id
                    FROM experiences
                    WHERE idempotency_hash = %s OR client_transaction_id = %s;
                """, (idempotency_hash, client_tx_id))
                existing = cur.fetchone()
                if existing:
                    existing_id = existing[0]
                    existing_hash = existing[1]
                    existing_status = existing[2]
                    existing_tx = existing[3]
                    if existing_hash == idempotency_hash:
                        logger.info(f"[EVIDENCE TX] Idempotent replay detected for {existing_id}")
                        conn.commit()
                        return {
                            "status": EvidenceTransactionStatus.COMMITTED.value,
                            "experience_id": existing_id,
                            "idempotent_replay": True,
                            "outcome_status": existing_status,
                            "strategy_id": strat_id,
                            "reliability_score": None,
                        }
                    else:
                        raise EvidenceIdempotencyConflictError(
                            f"Client transaction '{client_tx_id}' previously used with different evidence hash"
                        )

                # 3. If strategy applied, lock strategy row deterministically
                new_reliability = None
                tot_att, succ_att, fail_att = 0, 0, 0
                if strat_id:
                    cur.execute("""
                        SELECT strategy_id, total_attempts, successful_attempts, failed_attempts, reliability_score
                        FROM strategies
                        WHERE strategy_id = %s
                        FOR UPDATE;
                    """, (strat_id,))
                    strat_row = cur.fetchone()
                    if not strat_row:
                        raise EvidenceValidationError(f"Strategy '{strat_id}' does not exist")

                    tot_att = strat_row[1] + 1
                    succ_att = strat_row[2] + (1 if status_val == "SUCCESS" else 0)
                    fail_att = strat_row[3] + (1 if status_val in ("FAILURE", "PARTIAL_SUCCESS") else 0)

                    new_reliability = calculate_bayesian_strategy_reliability(
                        successful_attempts=succ_att,
                        total_attempts=tot_att,
                    )

                    cur.execute("""
                        UPDATE strategies
                        SET total_attempts = %s,
                            successful_attempts = %s,
                            failed_attempts = %s,
                            reliability_score = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE strategy_id = %s;
                    """, (tot_att, succ_att, fail_att, new_reliability, strat_id))

                # 4. Insert Experience record
                strat_applied_dict = dict(strategy_applied) if strategy_applied else {}
                if strat_id and "strategy_id" not in strat_applied_dict:
                    strat_applied_dict["strategy_id"] = strat_id

                goal_intent = "Evidence transaction"
                if isinstance(task_context, dict):
                    goal_intent = task_context.get("goal_intent") or task_context.get("objective") or json.dumps(task_context)
                elif task_context:
                    goal_intent = str(task_context)

                err_sig = None
                rca = None
                if isinstance(error_details, dict):
                    err_sig = error_details.get("error_signature")
                    rca = error_details.get("root_cause")

                cur.execute("""
                    INSERT INTO experiences (
                        experience_id, task_id, project_id, goal_intent,
                        context_conditions, strategy_applied, execution_trace,
                        outcome_status, outcome_metrics, error_signature,
                        root_cause_analysis, verification_evidence,
                        confidence_score, importance, privacy_class,
                        idempotency_key, strategy_id, idempotency_hash,
                        client_transaction_id, created_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, CURRENT_TIMESTAMP
                    );
                """, (
                    exp_id,
                    f"task_{uuid.uuid4().hex[:12]}",
                    project_id_clean,
                    goal_intent,
                    json.dumps(task_context or {}),
                    json.dumps(strat_applied_dict),
                    json.dumps(tools_used or []),
                    status_val,
                    json.dumps({"execution_time_ms": execution_time_ms, "tools_used": tools_used or []}),
                    err_sig,
                    rca,
                    json.dumps({"lesson_learned": lesson_learned, "tools_used": tools_used or []}),
                    confidence_score,
                    0.50,
                    proj_privacy,
                    idempotency_hash,
                    strat_id,
                    idempotency_hash,
                    client_tx_id,
                ))

            # Commit the atomic transaction
            conn.commit()
            logger.info(f"[EVIDENCE TX] Committed experience {exp_id} for project {project_id_clean}, strategy={strat_id}")

            return {
                "status": EvidenceTransactionStatus.COMMITTED.value,
                "experience_id": exp_id,
                "idempotent_replay": False,
                "outcome_status": status_val,
                "strategy_id": strat_id,
                "total_attempts": tot_att if strat_id else None,
                "successful_attempts": succ_att if strat_id else None,
                "failed_attempts": fail_att if strat_id else None,
                "reliability_score": new_reliability,
                "client_transaction_id": client_tx_id,
            }

        except Exception as e:
            conn.rollback()
            logger.error(f"[EVIDENCE TX] Transaction aborted and rolled back: {e}")
            if isinstance(e, EvidenceTransactionError):
                raise
            raise EvidenceTransactionError(f"Atomic evidence transaction failed: {e}") from e
        finally:
            self._manager.release_connection(conn)
