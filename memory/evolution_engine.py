"""
DOOM V5.3.5 — Memory Evolution Engine
Authoritative subsystem governing evidence validation, continuous confidence updates,
importance evolution, anti-feedback invariants, and transactional explainability.
"""
from datetime import datetime, timezone
import json
import logging
import math
import uuid
from typing import Any, Dict, List, Optional, Tuple

from memory.types import (
    MemoryStatus,
    MemorySource,
    ConfidenceLevel,
    VerificationStatus,
    PrivacyClass,
)
from memory.schemas import MemoryRecord
from memory.evolution_models import (
    FreshnessClass,
    FRESHNESS_CONFIG,
    EvidencePolarity,
    EvidenceType,
    EvolutionType,
    MemoryEvidence,
    MemoryEvolutionEvent,
    EvolutionResult,
    MemoryEpistemicProfile,
    MemoryEvolutionError,
    InadmissibleEvidenceError,
    InactiveMemoryEvolutionError,
    EvolutionValidationError,
    IdempotencyConflictError,
    SensitiveEvidencePolicyError,
    clamp_float,
    project_confidence_score_to_level,
    project_confidence_level_to_score,
    compute_observation_hash,
    compute_evidence_idempotency_key,
)

logger = logging.getLogger("DOOM.MemoryEvolutionEngine")


# ---------------------------------------------------------------------------
# Source Reliability Weights
# ---------------------------------------------------------------------------
SOURCE_RELIABILITY_WEIGHTS: Dict[MemorySource, float] = {
    MemorySource.USER_EXPLICIT:      1.00,  # User directly stated/confirmed ground truth
    MemorySource.VERIFIED_TASK:      0.95,  # Empirically verified task outcome
    MemorySource.TOOL_RESULT:        0.75,  # Tool output without ground-truth verifier
    MemorySource.SYSTEM_OBSERVATION: 0.70,  # Operating system/telemetry grounded state
    MemorySource.USER_CONVERSATION:  0.60,  # Conversationally inferred user statement
    MemorySource.IMPORTED_DATA:      0.50,  # External batch data
    MemorySource.DERIVED_CONTEXT:    0.20,  # Derived or synthesized context
}


class MemoryEvolutionEngine:
    """
    Authoritative transactional engine for memory evolution:
    - Enforces evidence admissibility and provenance rules.
    - Prevents self-confirmation and circular reasoning.
    - Executes atomic ACID updates using row-level locking (SELECT ... FOR UPDATE).
    - Preserves V5.3.3 vector monotonic generation invariants (no re-embedding).
    - Produces immutable audit records in memory_evolution_events.
    """

    def __init__(self):
        self._alpha = 0.25  # Learning rate for positive corroboration
        self._beta = 0.50   # Degradation rate for contradictory observations

    def _get_manager(self):
        from database.postgres_db import postgres_manager
        return postgres_manager

    # -----------------------------------------------------------------------
    # Evidence Admissibility Gate
    # -----------------------------------------------------------------------
    def validate_evidence_admissibility(
        self,
        source: MemorySource,
        actor: str,
        polarity: EvidencePolarity,
        strength: float,
        raw_payload: Any,
        task_verified: bool = False,
    ) -> Tuple[bool, str]:
        """
        Validates evidence against the V5.3.5 anti-hallucination and provenance policy:
        1. Model-generated text without external grounding CANNOT be supporting evidence.
        2. DERIVED_CONTEXT is inadmissible as positive factual confirmation.
        3. Actor must be non-empty.
        4. Strength must be finite in [0.0, 1.0].
        """
        if math.isnan(strength) or math.isinf(strength) or strength < 0.0 or strength > 1.0:
            return False, f"Evidence strength must be in [0.0, 1.0], got {strength}"

        # LLM inference / DERIVED_CONTEXT cannot be supporting evidence
        if source == MemorySource.DERIVED_CONTEXT and polarity == EvidencePolarity.SUPPORTING:
            return False, "DERIVED_CONTEXT and unverified model inferences cannot serve as supporting evidence"

        actor_clean = str(actor).strip().upper()
        if not actor_clean:
            return False, "Evidence actor must not be empty"

        # Model cannot be its own ground truth
        if actor_clean in ("MODEL", "LLM", "ASSISTANT") and not task_verified:
            if polarity == EvidencePolarity.SUPPORTING:
                return False, "Model self-generation without independent ground-truth verification is inadmissible"

        return True, "Admissible"

    # -----------------------------------------------------------------------
    # Core Evolution Mutation (Atomic Transaction)
    # -----------------------------------------------------------------------
    def record_evidence_and_evolve(
        self,
        memory_id: str,
        polarity: EvidencePolarity,
        strength: float,
        source: MemorySource,
        evidence_type: EvidenceType = EvidenceType.SYSTEM_ENVIRONMENT_STATE,
        actor: str = "SYSTEM",
        source_task_id: Optional[str] = None,
        raw_observation: Any = "",
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        task_verified: bool = False,
    ) -> EvolutionResult:
        """
        Record empirical evidence and atomically update confidence, importance,
        and confirmation timestamps under a PostgreSQL row lock.
        """
        # 1. Admissibility check
        is_admissible, reason = self.validate_evidence_admissibility(
            source=source,
            actor=actor,
            polarity=polarity,
            strength=strength,
            raw_payload=raw_observation,
            task_verified=task_verified,
        )
        if not is_admissible:
            raise InadmissibleEvidenceError(f"Evidence rejected: {reason}")

        obs_hash = compute_observation_hash(source.value, actor, raw_observation)
        effective_key = idempotency_key or compute_evidence_idempotency_key(
            memory_id=memory_id,
            source_task_id=source_task_id,
            observation_hash=obs_hash,
            polarity=polarity.value,
        )

        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            raise MemoryEvolutionError("PostgreSQL connection unavailable for memory evolution")

        try:
            with conn.cursor() as cur:
                # 2. Pessimistic row-level lock on target record
                cur.execute("""
                    SELECT memory_id, confidence_score, importance, status, generation, privacy_class, is_foundational
                    FROM memory_records
                    WHERE memory_id = %s
                    FOR UPDATE;
                """, (memory_id,))
                row = cur.fetchone()
                if not row:
                    raise InactiveMemoryEvolutionError(f"Memory record {memory_id} not found")

                mem_id, curr_conf, curr_imp, status, gen, pclass, is_found = row
                if status != MemoryStatus.ACTIVE.value:
                    raise InactiveMemoryEvolutionError(
                        f"Cannot evolve memory {memory_id}: lifecycle status is {status} (only ACTIVE records may evolve)"
                    )

                # 3. Privacy redaction check
                if pclass == PrivacyClass.SENSITIVE.value:
                    safe_summary = "[REDACTED_SENSITIVE_EVIDENCE]"
                    safe_meta = {"redacted": True}
                else:
                    safe_summary = (summary or f"{polarity.value} evidence from {source.value}")[:255]
                    safe_meta = metadata or {}

                # 4. Check for Idempotency replay
                cur.execute("SELECT evidence_id FROM memory_evidence WHERE idempotency_key = %s;", (effective_key,))
                existing_ev = cur.fetchone()
                if existing_ev:
                    # Idempotent replay: return existing state without reapplying deltas
                    return EvolutionResult(
                        success=True,
                        memory_id=memory_id,
                        confidence_before=float(curr_conf),
                        confidence_after=float(curr_conf),
                        importance_before=float(curr_imp),
                        importance_after=float(curr_imp),
                        evidence_id=existing_ev[0],
                        is_idempotent_replay=True,
                    )

                # 5. Check for Correlated / Duplicate Observation in the same task
                if source_task_id:
                    cur.execute("""
                        SELECT COUNT(*) FROM memory_evidence
                        WHERE memory_id = %s AND source_task_id = %s AND observation_hash = %s;
                    """, (memory_id, source_task_id, obs_hash))
                    count_row = cur.fetchone()
                    if count_row and count_row[0] > 0:
                        # Correlated duplicate: heavily damp strength to avoid artificial inflation
                        strength = strength * 0.10

                # 6. Calculate new confidence score
                curr_c = clamp_float(curr_conf, 0.01, 1.00, 0.50)
                rel_weight = SOURCE_RELIABILITY_WEIGHTS.get(source, 0.50)

                if polarity == EvidencePolarity.SUPPORTING:
                    if source == MemorySource.USER_EXPLICIT:
                        new_c = 1.00
                    else:
                        new_c = curr_c + self._alpha * strength * rel_weight * (1.0 - curr_c)
                elif polarity == EvidencePolarity.CONTRADICTING:
                    new_c = curr_c - self._beta * strength * rel_weight * curr_c
                else:
                    # AMBIGUOUS: leaves confidence untouched
                    new_c = curr_c

                new_c = clamp_float(new_c, 0.01, 1.00, curr_c)
                delta_c = new_c - curr_c
                projected_level = project_confidence_score_to_level(new_c)

                # 7. Importance adjustments (Task Criticality)
                curr_i = clamp_float(curr_imp, 0.0, 1.0, 0.50)
                delta_i = 0.0
                if task_verified and polarity == EvidencePolarity.SUPPORTING:
                    delta_i = min(0.10, strength * 0.05)
                new_i = clamp_float(curr_i + delta_i, 0.0, 1.0, curr_i)
                if is_found and new_i < 0.80:
                    new_i = 0.80

                # 8. Insert evidence row
                ev_id = f"ev_{uuid.uuid4().hex[:16]}"
                cur.execute("""
                    INSERT INTO memory_evidence (
                        evidence_id, memory_id, evidence_type, polarity, strength,
                        source, actor, source_task_id, observation_hash, idempotency_key,
                        summary, metadata, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, CURRENT_TIMESTAMP
                    );
                """, (
                    ev_id, memory_id, evidence_type.value, polarity.value, float(strength),
                    source.value, actor, source_task_id, obs_hash, effective_key,
                    safe_summary, json.dumps(safe_meta),
                ))

                # 9. Update memory_records
                # Note: generation is NOT incremented (vector sync is not enqueued)
                if polarity == EvidencePolarity.SUPPORTING:
                    cur.execute("""
                        UPDATE memory_records
                        SET confidence_score = %s,
                            confidence = %s,
                            importance = %s,
                            last_confirmed_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE memory_id = %s;
                    """, (new_c, projected_level.value, new_i, memory_id))
                else:
                    cur.execute("""
                        UPDATE memory_records
                        SET confidence_score = %s,
                            confidence = %s,
                            importance = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE memory_id = %s;
                    """, (new_c, projected_level.value, new_i, memory_id))

                # 10. Record immutable evolution event
                evo_id = f"evo_{uuid.uuid4().hex[:16]}"
                evo_type = EvolutionType.EVIDENCE_UPDATE if polarity == EvidencePolarity.SUPPORTING else EvolutionType.CONTRADICTION_DEGRADE
                evo_reason = f"{polarity.value} evidence ({evidence_type.value}) via {actor}"

                cur.execute("""
                    INSERT INTO memory_evolution_events (
                        event_id, memory_id, evidence_id, evolution_type,
                        confidence_before, confidence_after, importance_before, importance_after,
                        delta_confidence, delta_importance, reason, actor, idempotency_key,
                        metadata, created_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, CURRENT_TIMESTAMP
                    );
                """, (
                    evo_id, memory_id, ev_id, evo_type.value,
                    curr_c, new_c, curr_i, new_i,
                    delta_c, delta_i, evo_reason, actor, f"evo_{effective_key}",
                    json.dumps({"source": source.value, "task_id": source_task_id}),
                ))

            conn.commit()
            return EvolutionResult(
                success=True,
                memory_id=memory_id,
                confidence_before=curr_c,
                confidence_after=new_c,
                importance_before=curr_i,
                importance_after=new_i,
                evidence_id=ev_id,
                event_id=evo_id,
                is_idempotent_replay=False,
            )
        except Exception as e:
            conn.rollback()
            if isinstance(e, (MemoryEvolutionError, IdempotencyConflictError)):
                raise
            logger.error(f"[MEMORY EVOLUTION] Evolution failed for {memory_id}: {e}")
            return EvolutionResult(
                success=False,
                memory_id=memory_id,
                confidence_before=0.0,
                confidence_after=0.0,
                importance_before=0.0,
                importance_after=0.0,
                error=str(e),
            )
        finally:
            pg.release_connection(conn)

    # -----------------------------------------------------------------------
    # Structural Importance Evolution
    # -----------------------------------------------------------------------
    def update_structural_importance(self, memory_id: str, actor: str = "SYSTEM") -> float:
        """
        Evaluate V5.3.4 relationship graph centrality and apply structural importance delta:
        structural_delta = min(0.20, 0.05*N_supersedes + 0.02*N_derived_from + 0.01*N_related_to)
        """
        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            return 0.50

        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT memory_id, importance, is_foundational, status
                    FROM memory_records
                    WHERE memory_id = %s
                    FOR UPDATE;
                """, (memory_id,))
                row = cur.fetchone()
                if not row or row[3] != MemoryStatus.ACTIVE.value:
                    return 0.50

                mem_id, curr_imp, is_found, status = row
                curr_i = float(curr_imp)

                # Count distinct incoming/outgoing relationships
                cur.execute("""
                    SELECT relationship_type, COUNT(*)
                    FROM memory_relationships
                    WHERE source_memory_id = %s
                    GROUP BY relationship_type;
                """, (memory_id,))
                counts = dict(cur.fetchall())

                n_sup = counts.get("SUPERSEDES", 0)
                n_der = counts.get("DERIVED_FROM", 0)
                n_rel = counts.get("RELATED_TO", 0)

                s_delta = min(0.20, 0.05 * n_sup + 0.02 * n_der + 0.01 * n_rel)
                base_i = 0.80 if is_found else 0.50
                new_i = clamp_float(base_i + s_delta, 0.0, 1.0, curr_i)

                delta_imp = new_i - curr_i
                if abs(delta_imp) > 1e-4:
                    cur.execute("""
                        UPDATE memory_records
                        SET importance = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE memory_id = %s;
                    """, (new_i, memory_id))

                    evo_id = f"evo_{uuid.uuid4().hex[:16]}"
                    cur.execute("""
                        INSERT INTO memory_evolution_events (
                            event_id, memory_id, evolution_type,
                            confidence_before, confidence_after, importance_before, importance_after,
                            delta_confidence, delta_importance, reason, actor, idempotency_key,
                            created_at
                        ) VALUES (
                            %s, %s, 'IMPORTANCE_UPDATE',
                            0.0, 0.0, %s, %s,
                            0.0, %s, %s, %s, %s,
                            CURRENT_TIMESTAMP
                        );
                    """, (
                        evo_id, memory_id, curr_i, new_i,
                        delta_imp, f"Structural centrality ({n_sup} sup, {n_der} der, {n_rel} rel)", actor,
                        f"str_{memory_id}_{int(datetime.now(timezone.utc).timestamp())}",
                    ))

            conn.commit()
            return new_i
        except Exception as e:
            conn.rollback()
            logger.error(f"[MEMORY EVOLUTION] Structural importance update failed: {e}")
            return 0.50
        finally:
            pg.release_connection(conn)

    # -----------------------------------------------------------------------
    # Explainability & Epistemic Profiles
    # -----------------------------------------------------------------------
    def get_epistemic_profile(self, memory_id: str) -> Optional[MemoryEpistemicProfile]:
        """
        Produce a structured epistemic explainability profile for a memory record:
        - Confirms why DOOM trusts or doubts this record.
        - Provides counts of supporting and contradicting evidence.
        - Does NOT expose internal model chain-of-thought.
        """
        pg = self._get_manager()
        conn = pg.get_connection()
        if not conn:
            return None

        try:
            from psycopg2 import extras
            with conn.cursor(cursor_factory=extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM memory_records WHERE memory_id = %s;", (memory_id,))
                r_row = cur.fetchone()
                if not r_row:
                    return None

                from memory.repository import memory_repository
                rec = memory_repository._row_to_record(dict(r_row))

                # Query evidence counts and recent items
                cur.execute("""
                    SELECT polarity, COUNT(*) as cnt
                    FROM memory_evidence
                    WHERE memory_id = %s
                    GROUP BY polarity;
                """, (memory_id,))
                pol_rows = cur.fetchall()
                pol_counts = {pr["polarity"]: pr["cnt"] for pr in pol_rows}

                cur.execute("""
                    SELECT evidence_id, evidence_type, polarity, strength, source, actor, summary, created_at
                    FROM memory_evidence
                    WHERE memory_id = %s
                    ORDER BY created_at DESC
                    LIMIT 5;
                """, (memory_id,))
                recent_rows = [dict(r) for r in cur.fetchall()]

                from memory.ranking import memory_ranker
                f_score = memory_ranker.compute_freshness_score(rec)
                c_score = rec.confidence_score

                # Format human-readable summary reason
                sup_cnt = pol_counts.get("SUPPORTING", 0)
                con_cnt = pol_counts.get("CONTRADICTING", 0)
                reason_parts = [
                    f"Confidence: {round(c_score * 100)}% ({rec.confidence.value})",
                    f"Source: {rec.source.value}",
                    f"Freshness: {round(f_score * 100)}% ({rec.freshness_class})",
                    f"Evidence: {sup_cnt} supporting, {con_cnt} contradicting",
                ]
                if rec.is_foundational:
                    reason_parts.append("Status: Foundational Knowledge (decay protected)")

                return MemoryEpistemicProfile(
                    memory_id=rec.memory_id,
                    content=rec.content,
                    confidence_score=c_score,
                    confidence_level=rec.confidence.value,
                    freshness_score=f_score,
                    freshness_class=rec.freshness_class,
                    importance=rec.importance,
                    is_foundational=rec.is_foundational,
                    status=rec.status.value,
                    supporting_evidence_count=sup_cnt,
                    contradicting_evidence_count=con_cnt,
                    primary_source=rec.source.value,
                    last_confirmed_at=rec.last_confirmed_at or rec.created_at,
                    summary_reason=" | ".join(reason_parts),
                    recent_evidence=recent_rows,
                )
        except Exception as e:
            logger.error(f"[MEMORY EVOLUTION] Epistemic profile query failed: {e}")
            return None
        finally:
            pg.release_connection(conn)


evolution_engine = MemoryEvolutionEngine()
