"""
DOOM V5.3.6 — Project & Experience Intelligence Migration Module
Safely and idempotently backfills legacy V5.1-V5.3.5 EXPERIENCE memory records
into the normalized V5.3.6 `experiences` table under project 'doom'.

Invariants:
- Preserves original created_at timestamps, privacy classes, and confidence scores.
- Computes deterministic idempotency hashes (no duplicate records).
- Strictly non-destructive: never deletes or mutates source memory_records.
- Zero tool authority: purely database-level transactional migration.
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from database.postgres_db import postgres_manager
from memory.types import ConfidenceLevel, PrivacyClass
from memory.project_models import (
    compute_experience_idempotency_hash,
    TaskOutcomeStatus,
)

try:
    from memory.evolution_models import project_confidence_level_to_score
except ImportError:
    project_confidence_level_to_score = None

logger = logging.getLogger("DOOM.Memory.ProjectMigration")

# Authoritative canonical enum-to-score mapping derived from existing DOOM semantics
# (memory.evolution_models.project_confidence_level_to_score)
CANONICAL_CONFIDENCE_MAP: Dict[str, float] = {
    ConfidenceLevel.HIGH.value: (
        project_confidence_level_to_score(ConfidenceLevel.HIGH)
        if project_confidence_level_to_score
        else 0.90
    ),
    ConfidenceLevel.MEDIUM.value: (
        project_confidence_level_to_score(ConfidenceLevel.MEDIUM)
        if project_confidence_level_to_score
        else 0.60
    ),
    ConfidenceLevel.LOW.value: (
        project_confidence_level_to_score(ConfidenceLevel.LOW)
        if project_confidence_level_to_score
        else 0.30
    ),
    ConfidenceLevel.UNKNOWN.value: (
        project_confidence_level_to_score(ConfidenceLevel.UNKNOWN)
        if project_confidence_level_to_score
        else 0.50
    ),
}

DEFAULT_NONE_CONFIDENCE = 0.80
SAFE_FALLBACK_CONFIDENCE = 0.50
MIN_CONFIDENCE_BOUND = 0.01  # Aligned with PostgreSQL DB CHECK constraint (confidence_score >= 0.01 AND confidence_score <= 1.00)
MAX_CONFIDENCE_BOUND = 1.00


def parse_legacy_confidence(conf_val: Any, return_anomaly: bool = False) -> Any:
    """
    Safely and canonically normalizes a legacy confidence representation into a
    valid continuous score within [0.01, 1.00].

    Supported formats:
    - None -> Default documented migration confidence (0.80)
    - Legacy enum or string ('HIGH', 'MEDIUM', 'LOW', 'UNKNOWN') -> Canonical score
    - Numeric string ("0.85", "0.5") -> Float score
    - Numeric value (0.85, 1) -> Float score
    - Out-of-range numeric -> Clamped with anomaly recorded
    - Unrecognized string -> Safe fallback (0.50) with anomaly recorded

    Returns:
        If return_anomaly is True: Tuple[float, Optional[str]]
        Else: float
    """
    anomaly: Optional[str] = None
    score: float

    if conf_val is None:
        score = DEFAULT_NONE_CONFIDENCE
    elif isinstance(conf_val, (int, float)):
        val = float(conf_val)
        if val < 0.0 or val > 1.0:
            anomaly = f"Numeric confidence {val} outside valid range [0.0, 1.0]; safely clamped"
            score = max(MIN_CONFIDENCE_BOUND, min(MAX_CONFIDENCE_BOUND, val))
        elif val < MIN_CONFIDENCE_BOUND:
            anomaly = f"Numeric confidence {val} below minimum schema bound {MIN_CONFIDENCE_BOUND}; adjusted to {MIN_CONFIDENCE_BOUND}"
            score = MIN_CONFIDENCE_BOUND
        else:
            score = min(MAX_CONFIDENCE_BOUND, val)
    elif isinstance(conf_val, ConfidenceLevel):
        score = CANONICAL_CONFIDENCE_MAP.get(conf_val.value, SAFE_FALLBACK_CONFIDENCE)
    elif isinstance(conf_val, str):
        cleaned = conf_val.strip()
        cleaned_upper = cleaned.upper()
        if cleaned_upper in CANONICAL_CONFIDENCE_MAP:
            score = CANONICAL_CONFIDENCE_MAP[cleaned_upper]
        else:
            try:
                val = float(cleaned)
                if val < 0.0 or val > 1.0:
                    anomaly = f"Numeric string confidence '{conf_val}' outside valid range [0.0, 1.0]; safely clamped"
                    score = max(MIN_CONFIDENCE_BOUND, min(MAX_CONFIDENCE_BOUND, val))
                elif val < MIN_CONFIDENCE_BOUND:
                    anomaly = f"Numeric string confidence '{conf_val}' below minimum schema bound {MIN_CONFIDENCE_BOUND}; adjusted to {MIN_CONFIDENCE_BOUND}"
                    score = MIN_CONFIDENCE_BOUND
                else:
                    score = min(MAX_CONFIDENCE_BOUND, val)
            except (ValueError, TypeError):
                anomaly = f"Unrecognized legacy confidence string '{conf_val}'; defaulted to safe fallback {SAFE_FALLBACK_CONFIDENCE}"
                score = SAFE_FALLBACK_CONFIDENCE
    else:
        anomaly = f"Unsupported legacy confidence type {type(conf_val).__name__}; defaulted to safe fallback {SAFE_FALLBACK_CONFIDENCE}"
        score = SAFE_FALLBACK_CONFIDENCE

    if return_anomaly:
        return score, anomaly
    return score


class ProjectExperienceMigrationEngine:
    """
    Idempotent migration utility to backfill historical experience records.
    """

    def __init__(self, db=None):
        self._db = db or postgres_manager

    def run_migration(self, default_project_id: str = "doom", batch_size: int = 500) -> Dict[str, Any]:
        """
        Scans memory_records with memory_type='EXPERIENCE' and ingests them into `experiences`.
        Returns a migration summary dictionary.
        """
        summary = {
            "total_legacy_experiences": 0,
            "migrated_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "errors": [],
            "anomalies": [],
        }

        conn = self._db.get_connection()
        if not conn:
            summary["errors"].append("Database connection unavailable")
            return summary

        try:
            with conn.cursor() as cur:
                # 1. Verify default project exists
                cur.execute("SELECT project_id FROM projects WHERE project_id = %s;", (default_project_id,))
                if not cur.fetchone():
                    cur.execute("""
                        INSERT INTO projects (project_id, name, description, privacy_class, lifecycle_status)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (project_id) DO NOTHING;
                    """, (
                        default_project_id,
                        "DOOM Primary Operating System",
                        "Default root project for sovereign AI operations",
                        "NORMAL",
                        "ACTIVE",
                    ))
                    conn.commit()

                # 2. Fetch candidate legacy experiences
                cur.execute("""
                    SELECT memory_id, content, project_id, privacy_class, confidence, created_at, metadata
                    FROM memory_records
                    WHERE memory_type = 'EXPERIENCE'
                      AND status = 'ACTIVE'
                    ORDER BY created_at ASC
                    LIMIT %s;
                """, (batch_size,))
                rows = cur.fetchall()
                summary["total_legacy_experiences"] = len(rows)

                for row in rows:
                    mem_id = row[0]
                    try:
                        mem_id, content, proj_id, priv, conf, created_at, meta = row
                        target_proj = proj_id if proj_id else default_project_id

                        # Preserve historical project provenance (F-01 compliance)
                        if proj_id:
                            cur.execute("SELECT 1 FROM projects WHERE project_id = %s;", (proj_id,))
                            if not cur.fetchone():
                                cur.execute("""
                                    INSERT INTO projects (project_id, name, description, privacy_class, lifecycle_status)
                                    VALUES (%s, %s, %s, %s, %s)
                                    ON CONFLICT (project_id) DO NOTHING;
                                """, (proj_id, f"Project {proj_id}", "Auto-migrated legacy project", "NORMAL", "ACTIVE"))

                        priv_str = priv if priv in [p.value for p in PrivacyClass] else PrivacyClass.NORMAL.value
                        conf_float, conf_anomaly = parse_legacy_confidence(conf, return_anomaly=True)
                        if conf_anomaly:
                            summary["anomalies"].append(f"{mem_id}: {conf_anomaly}")
                            logger.warning(f"Migration anomaly for {mem_id}: {conf_anomaly}")

                        task_id = f"legacy_task_{mem_id[:12]}"
                        action_summary = content[:200] if content else "Legacy execution"
                        outcome = TaskOutcomeStatus.SUCCESS.value

                        # Check metadata if available
                        if isinstance(meta, dict):
                            task_id = meta.get("task_id", task_id)
                            action_summary = meta.get("action_summary", action_summary)
                            outcome = meta.get("outcome", outcome)

                        idem_hash = compute_experience_idempotency_hash(
                            task_id=task_id,
                            project_id=target_proj,
                            outcome=outcome,
                            action_summary=action_summary,
                            timestamp=created_at,
                        )

                        # Check if experience already migrated
                        cur.execute("SELECT experience_id FROM experiences WHERE idempotency_key = %s;", (idem_hash,))
                        if cur.fetchone():
                            summary["skipped_count"] += 1
                            continue

                        exp_id = f"exp_mig_{mem_id[:16]}"
                        import json
                        cur.execute("""
                            INSERT INTO experiences (
                                experience_id, task_id, project_id, goal_intent,
                                context_conditions, strategy_applied, execution_trace,
                                outcome_status, outcome_metrics, error_signature,
                                root_cause_analysis, verification_evidence,
                                confidence_score, importance, privacy_class,
                                idempotency_key, created_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (idempotency_key) DO NOTHING;
                        """, (
                            exp_id,
                            task_id,
                            target_proj,
                            action_summary,
                            json.dumps({"source_memory_id": mem_id, "migrated": True, "raw_legacy_confidence": str(conf)}),
                            json.dumps({}),
                            json.dumps([]),
                            outcome,
                            json.dumps({}),
                            None,
                            "Migrated from historical memory record",
                            json.dumps({}),
                            conf_float,
                            0.50,
                            priv_str,
                            idem_hash,
                            created_at,
                        ))
                        if cur.rowcount > 0:
                            summary["migrated_count"] += 1
                        else:
                            summary["skipped_count"] += 1
                    except Exception as ins_err:
                        summary["error_count"] += 1
                        summary["errors"].append(f"Failed to migrate {mem_id}: {ins_err}")

                conn.commit()

        except Exception as e:
            conn.rollback()
            summary["error_count"] += 1
            summary["errors"].append(str(e))
        finally:
            self._db.release_connection(conn)

        logger.info(f"Project experience migration complete: {summary}")
        return summary


project_migration_engine = ProjectExperienceMigrationEngine()


def run_project_experience_migration(default_project_id: str = "doom") -> Dict[str, Any]:
    """Convenience functional wrapper for migration."""
    return project_migration_engine.run_migration(default_project_id=default_project_id)
