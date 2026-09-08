"""
DOOM V5.3.7.3 — Governance & Knowledge Transfer Subsystem
Implements the deterministic Governance Engine, Policy Versioning,
and the typed GovernanceDecision contract.

Governance operates as an immutable gatekeeper:
- Evaluates the 14 Hard Governance Gates.
- Absolute Privacy Quarantine (SENSITIVE / PRIVATE).
- Target-Aware Failure Intelligence.
- Explicit Abstention (ABSTAIN).
- Strict Read-Only Guarantee (dI/dN_retrieval = 0).
- Pure DATA_ONLY output with ZERO tool execution authority.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from memory.types import PrivacyClass, MemorySource, MemoryStatus
from memory.project_models import (
    ProjectRecord,
    StrategyRecord,
    LessonRecord,
    TransferDecision,
    TransferEvaluationResult,
    calculate_transfer_confidence,
)
from memory.governance_gates import (
    GovernanceGateId,
    GovernanceGateEvaluator,
    GateResult,
)
from memory.conflict_engine import ConflictEngine

logger = logging.getLogger("doom.memory.governance")


# ---------------------------------------------------------------------------
# Governance Policy Contract
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GovernancePolicy:
    """Versioned parameterization of governance thresholds and rules."""
    policy_version: str = "GOV_POLICY_V1"
    min_transfer_confidence: float = 0.35
    allow_transfer_confidence: float = 0.60
    min_tech_overlap: float = 0.05
    min_reliability: float = 0.25
    max_consecutive_target_failures: int = 3
    max_target_failure_rate: float = 0.50
    target_failure_penalty_multiplier: float = 2.0
    enable_in_memory_cache: bool = True
    cache_ttl_seconds: int = 300


# Canonical default policy
DEFAULT_GOVERNANCE_POLICY = GovernancePolicy()


# ---------------------------------------------------------------------------
# Typed Governance Decision (DATA ONLY)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GovernanceDecision:
    """
    Actionable, typed, and explainable governance transfer verdict.
    CRITICAL: is_data_only is True; possesses ZERO tool execution authority.
    """
    decision: TransferDecision
    source_project_id: str
    target_project_id: str
    strategy_id: Optional[str]
    transfer_confidence: float
    risk_penalty: float
    semantic_similarity: float
    tech_stack_overlap: float
    environmental_compatibility: float
    evidence_weight: float
    gates_evaluated: List[str]
    failed_gate: Optional[str]
    decision_reason: str
    abstain_reason: Optional[str]
    defensive_warnings: List[str]
    required_preconditions: List[str]
    policy_version: str
    provenance_hash: str
    evaluated_at: str
    is_data_only: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Converts decision to safe dictionary without sensitive content."""
        return {
            "decision": self.decision.value,
            "source_project_id": self.source_project_id,
            "target_project_id": self.target_project_id,
            "strategy_id": self.strategy_id,
            "transfer_confidence": self.transfer_confidence,
            "risk_penalty": self.risk_penalty,
            "semantic_similarity": self.semantic_similarity,
            "tech_stack_overlap": self.tech_stack_overlap,
            "environmental_compatibility": self.environmental_compatibility,
            "evidence_weight": self.evidence_weight,
            "gates_evaluated": self.gates_evaluated,
            "failed_gate": self.failed_gate,
            "decision_reason": self.decision_reason,
            "abstain_reason": self.abstain_reason,
            "defensive_warnings": self.defensive_warnings,
            "required_preconditions": self.required_preconditions,
            "policy_version": self.policy_version,
            "provenance_hash": self.provenance_hash,
            "evaluated_at": self.evaluated_at,
            "is_data_only": self.is_data_only,
        }

    def to_legacy_result(self) -> TransferEvaluationResult:
        """Adapts to legacy V5.3.6 TransferEvaluationResult for backward compatibility."""
        legacy_dec = self.decision
        if self.decision == TransferDecision.CONDITIONAL and self.transfer_confidence >= 0.35:
            legacy_dec = TransferDecision.ALLOWED
        return TransferEvaluationResult(
            decision=legacy_dec,
            transfer_confidence=self.transfer_confidence,
            semantic_similarity=self.semantic_similarity,
            tech_stack_overlap=self.tech_stack_overlap,
            environmental_compatibility=self.environmental_compatibility,
            risk_penalty=self.risk_penalty,
            reason=self.decision_reason,
            strategy_id=self.strategy_id,
            policy_version=self.policy_version,
            gates_evaluated=self.gates_evaluated,
            failed_gate=self.failed_gate,
            defensive_warnings=self.defensive_warnings,
            provenance_hash=self.provenance_hash,
            is_data_only=self.is_data_only,
        )


# ---------------------------------------------------------------------------
# Governance Engine
# ---------------------------------------------------------------------------
class GovernanceEngine:
    """
    The central Deterministic Governance & Knowledge Transfer Authority.
    Orchestrates the 14 Hard Gates, Multi-Factor Scoring, Conflict Resolution,
    and Fail-Closed safety.
    """

    def __init__(self, policy: Optional[GovernancePolicy] = None):
        self.policy = policy or DEFAULT_GOVERNANCE_POLICY
        self._cache: Dict[str, Tuple[float, GovernanceDecision, int]] = {}  # key -> (expires_at, decision, source_gen)

    def evaluate_transfer(
        self,
        strategy: Optional[StrategyRecord],
        source_project: Optional[ProjectRecord],
        target_project: Optional[ProjectRecord],
        semantic_similarity: float = 0.50,
        target_environment: Optional[Dict[str, Any]] = None,
        target_failure_stats: Optional[Dict[str, Any]] = None,
        extra_context: Optional[Dict[str, Any]] = None,
        source_generation: int = 1,
    ) -> GovernanceDecision:
        """
        Main deterministic governance evaluation method.
        Strictly read-only ($dI/dN = 0$).
        """
        evaluated_at = datetime.now(timezone.utc).isoformat()
        src_id = source_project.project_id if source_project else "unknown"
        tgt_id = target_project.project_id if target_project else "unknown"
        strat_id = strategy.strategy_id if strategy else None

        # Check in-memory cache if enabled
        cache_key = None
        tf_gen = int(target_failure_stats.get("failures", 0) + target_failure_stats.get("consecutive_failures", 0)) if target_failure_stats else 0
        if self.policy.enable_in_memory_cache and strategy and source_project and target_project:
            cache_key = f"{self.policy.policy_version}::{src_id}::{tgt_id}::{strat_id}::{source_generation}::{tf_gen}"
            cached = self._cache.get(cache_key)
            if cached:
                expires_at, cached_decision, cached_gen, cached_tf = cached
                # Validate TTL, generation freshness, and target failure state
                if time.time() < expires_at and cached_gen >= source_generation and cached_tf == tf_gen:
                    return cached_decision
                else:
                    self._cache.pop(cache_key, None)

        gates_evaluated: List[str] = []
        defensive_warnings: List[str] = []
        required_preconditions: List[str] = []

        try:
            # -----------------------------------------------------------------
            # Stage 1: The 14 Hard Governance Gates
            # -----------------------------------------------------------------

            # Gate 1: Source Exists
            g01 = GovernanceGateEvaluator.evaluate_gate_01_source_exists(strategy, source_project)
            gates_evaluated.append(g01.gate_id)
            if not g01.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g01, gates_evaluated, evaluated_at,
                    decision=TransferDecision.DENIED,
                )

            # Gate 2: Lifecycle Active
            g02 = GovernanceGateEvaluator.evaluate_gate_02_lifecycle_active(strategy, source_project, extra_context)
            gates_evaluated.append(g02.gate_id)
            if not g02.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g02, gates_evaluated, evaluated_at,
                    decision=TransferDecision.DENIED,
                )

            # Gate 3: Hard Privacy Quarantine
            strat_priv = extra_context.get("privacy_class") if extra_context else None
            g03 = GovernanceGateEvaluator.evaluate_gate_03_privacy_quarantine(
                source_project, target_project, strategy_privacy=strat_priv, extra_context=extra_context
            )
            gates_evaluated.append(g03.gate_id)
            if not g03.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g03, gates_evaluated, evaluated_at,
                    decision=TransferDecision.DENIED,
                )

            # Gate 4: Provenance Verified
            g04 = GovernanceGateEvaluator.evaluate_gate_04_provenance_verified(strategy, extra_context)
            gates_evaluated.append(g04.gate_id)
            if not g04.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g04, gates_evaluated, evaluated_at,
                    decision=TransferDecision.ABSTAIN,
                    abstain_reason="INSUFFICIENT_EVIDENCE",
                )

            # Gate 5: Target Project Valid
            g05 = GovernanceGateEvaluator.evaluate_gate_05_target_project_valid(target_project, tgt_id)
            gates_evaluated.append(g05.gate_id)
            if not g05.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g05, gates_evaluated, evaluated_at,
                    decision=TransferDecision.DENIED,
                )

            # Same Project Shortcut
            if src_id == tgt_id:
                return self._build_same_project_decision(
                    strategy, source_project, evaluated_at, gates_evaluated
                )

            # Gate 6: Scope Eligible
            g06 = GovernanceGateEvaluator.evaluate_gate_06_scope_eligible(strategy, extra_context)
            gates_evaluated.append(g06.gate_id)
            if not g06.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g06, gates_evaluated, evaluated_at,
                    decision=TransferDecision.DENIED,
                )

            # Gate 7: Prerequisites Met
            g07 = GovernanceGateEvaluator.evaluate_gate_07_prerequisites_met(strategy, target_project)
            gates_evaluated.append(g07.gate_id)
            if not g07.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g07, gates_evaluated, evaluated_at,
                    decision=TransferDecision.DENIED,
                )

            # Gate 8: Target Failure History
            tf_stats = target_failure_stats or {}
            target_failures = int(tf_stats.get("failures", 0))
            target_successes = int(tf_stats.get("successes", 0))
            consecutive_failures = int(tf_stats.get("consecutive_failures", 0))

            g08 = GovernanceGateEvaluator.evaluate_gate_08_target_failure_history(
                strategy,
                target_failures=target_failures,
                target_successes=target_successes,
                consecutive_target_failures=consecutive_failures,
                max_consecutive_failures=self.policy.max_consecutive_target_failures,
                max_failure_rate=self.policy.max_target_failure_rate,
            )
            gates_evaluated.append(g08.gate_id)
            if not g08.passed:
                defensive_warnings.append(g08.reason)
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g08, gates_evaluated, evaluated_at,
                    decision=TransferDecision.DENIED,
                    defensive_warnings=defensive_warnings,
                )

            # Gate 9: Tech Stack Overlap
            scope_val = (
                (extra_context or {}).get("scope")
                or (strategy.procedure_template or {}).get("scope")
                or "CROSS_PROJECT_ELIGIBLE"
            )
            g09, tech_overlap = GovernanceGateEvaluator.evaluate_gate_09_tech_stack_overlap(
                source_project, target_project, strategy_scope=scope_val, min_overlap=self.policy.min_tech_overlap
            )
            gates_evaluated.append(g09.gate_id)
            if not g09.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g09, gates_evaluated, evaluated_at,
                    decision=TransferDecision.ABSTAIN,
                    abstain_reason="CONTEXT_AMBIGUOUS",
                )

            # Gate 10: Environment Compatible
            g10, env_compat = GovernanceGateEvaluator.evaluate_gate_10_environment_compatible(
                strategy, target_environment
            )
            gates_evaluated.append(g10.gate_id)
            if not g10.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g10, gates_evaluated, evaluated_at,
                    decision=TransferDecision.DENIED,
                )

            # Gate 11: Minimum Reliability
            g11 = GovernanceGateEvaluator.evaluate_gate_11_minimum_reliability(
                strategy, min_reliability_threshold=self.policy.min_reliability
            )
            gates_evaluated.append(g11.gate_id)
            if not g11.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g11, gates_evaluated, evaluated_at,
                    decision=TransferDecision.DENIED,
                )

            # Gate 12: Evidence Admissible
            g12, evidence_weight = GovernanceGateEvaluator.evaluate_gate_12_evidence_admissible(extra_context)
            gates_evaluated.append(g12.gate_id)
            if not g12.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g12, gates_evaluated, evaluated_at,
                    decision=TransferDecision.ABSTAIN,
                    abstain_reason="INSUFFICIENT_EVIDENCE",
                )

            # Gate 13: Template Safety
            g13 = GovernanceGateEvaluator.evaluate_gate_13_template_safety(strategy)
            gates_evaluated.append(g13.gate_id)
            if not g13.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g13, gates_evaluated, evaluated_at,
                    decision=TransferDecision.DENIED,
                )

            # Gate 14: Explicit Policy
            g14 = GovernanceGateEvaluator.evaluate_gate_14_explicit_policy(
                source_project, target_project, extra_context
            )
            gates_evaluated.append(g14.gate_id)
            if not g14.passed:
                return self._build_blocked_decision(
                    src_id, tgt_id, strat_id, g14, gates_evaluated, evaluated_at,
                    decision=TransferDecision.DENIED,
                )

            # -----------------------------------------------------------------
            # Stage 2: Multi-Factor Transfer Confidence & Risk Scoring
            # -----------------------------------------------------------------
            # Target risk penalty calculation
            tot_target = target_successes + target_failures
            if tot_target > 0:
                p_risk = min(1.0, float(self.policy.target_failure_penalty_multiplier * target_failures / (tot_target + 1)))
            else:
                p_risk = 0.0

            if target_failures > 0:
                defensive_warnings.append(
                    f"Strategy has {target_failures} previous failure(s) in target project '{tgt_id}'"
                )

            # Multi-factor source confidence resolution
            source_c = (
                float(extra_context["source_confidence"])
                if (extra_context and "source_confidence" in extra_context)
                else strategy.reliability_score
            )

            eff_evidence_weight = (
                float(extra_context["evidence_weight"])
                if (extra_context and "evidence_weight" in extra_context)
                else (evidence_weight if (extra_context and extra_context.get("apply_evidence_weight")) else 1.0)
            )

            transfer_confidence = calculate_transfer_confidence(
                source_confidence=source_c,
                semantic_similarity=semantic_similarity,
                tech_stack_overlap=tech_overlap,
                environmental_compatibility=env_compat,
                risk_penalty=p_risk,
                evidence_weight=eff_evidence_weight,
            )

            # Determine final decision
            if transfer_confidence >= self.policy.allow_transfer_confidence:
                decision = TransferDecision.ALLOWED
                reason = (
                    f"High-confidence transfer ({transfer_confidence:.2f} >= {self.policy.allow_transfer_confidence:.2f}) "
                    f"verified across 14 Hard Governance Gates."
                )
            elif transfer_confidence >= self.policy.min_transfer_confidence:
                decision = TransferDecision.CONDITIONAL
                reason = (
                    f"Conditional transfer ({transfer_confidence:.2f}); environmental compatibility "
                    f"and preconditions must be verified."
                )
                required_preconditions.extend(list(strategy.recommended_tools))
            else:
                decision = TransferDecision.ABSTAIN
                reason = (
                    f"Transfer confidence ({transfer_confidence:.2f}) below threshold "
                    f"({self.policy.min_transfer_confidence:.2f}); DOOM abstains from unverified transfer."
                )

            abstain_reason = "INSUFFICIENT_EVIDENCE" if decision == TransferDecision.ABSTAIN else None
            provenance_hash = self._compute_provenance_hash(
                src_id, tgt_id, strat_id, decision.value, transfer_confidence, self.policy.policy_version
            )

            gov_decision = GovernanceDecision(
                decision=decision,
                source_project_id=src_id,
                target_project_id=tgt_id,
                strategy_id=strat_id,
                transfer_confidence=transfer_confidence,
                risk_penalty=round(p_risk, 4),
                semantic_similarity=round(semantic_similarity, 4),
                tech_stack_overlap=round(tech_overlap, 4),
                environmental_compatibility=round(env_compat, 4),
                evidence_weight=round(evidence_weight, 4),
                gates_evaluated=gates_evaluated,
                failed_gate=None,
                decision_reason=reason,
                abstain_reason=abstain_reason,
                defensive_warnings=defensive_warnings,
                required_preconditions=required_preconditions,
                policy_version=self.policy.policy_version,
                provenance_hash=provenance_hash,
                evaluated_at=evaluated_at,
                is_data_only=True,
            )

            # Cache decision with authoritative generation and target failure state
            if cache_key:
                expires_at = time.time() + self.policy.cache_ttl_seconds
                self._cache[cache_key] = (expires_at, gov_decision, source_generation, tf_gen)

            return gov_decision

        except Exception as ex:
            logger.error(f"[GOVERNANCE FAIL-CLOSED] Unexpected error during transfer evaluation: {ex}")
            # Fail closed to ABSTAIN
            return self._build_fail_closed_decision(
                src_id, tgt_id, strat_id, str(ex), gates_evaluated, evaluated_at
            )

    def invalidate_cache(self, strategy_id: Optional[str] = None, project_id: Optional[str] = None) -> int:
        """Explicitly purges in-memory cached governance decisions."""
        if not strategy_id and not project_id:
            count = len(self._cache)
            self._cache.clear()
            return count

        keys_to_remove = []
        for k in self._cache.keys():
            if strategy_id and strategy_id in k:
                keys_to_remove.append(k)
            elif project_id and project_id in k:
                keys_to_remove.append(k)

        for k in keys_to_remove:
            self._cache.pop(k, None)
        return len(keys_to_remove)

    # -------------------------------------------------------------------------
    # Internal Decision Builders
    # -------------------------------------------------------------------------
    def _build_blocked_decision(
        self,
        src_id: str,
        tgt_id: str,
        strat_id: Optional[str],
        gate_result: GateResult,
        gates_evaluated: List[str],
        evaluated_at: str,
        decision: TransferDecision = TransferDecision.DENIED,
        abstain_reason: Optional[str] = None,
        defensive_warnings: Optional[List[str]] = None,
    ) -> GovernanceDecision:
        """Constructs a deterministic blocked decision with 0.0 confidence."""
        prov_hash = self._compute_provenance_hash(
            src_id, tgt_id, strat_id, decision.value, 0.0, self.policy.policy_version
        )
        return GovernanceDecision(
            decision=decision,
            source_project_id=src_id,
            target_project_id=tgt_id,
            strategy_id=strat_id,
            transfer_confidence=0.0,
            risk_penalty=1.0 if decision == TransferDecision.DENIED else 0.0,
            semantic_similarity=0.0,
            tech_stack_overlap=0.0,
            environmental_compatibility=0.0,
            evidence_weight=0.0,
            gates_evaluated=gates_evaluated,
            failed_gate=gate_result.gate_id,
            decision_reason=f"Hard Governance Gate [{gate_result.gate_id}] BLOCKED: {gate_result.reason}",
            abstain_reason=abstain_reason,
            defensive_warnings=defensive_warnings or [gate_result.reason],
            required_preconditions=[],
            policy_version=self.policy.policy_version,
            provenance_hash=prov_hash,
            evaluated_at=evaluated_at,
            is_data_only=True,
        )

    def _build_same_project_decision(
        self,
        strategy: StrategyRecord,
        project: ProjectRecord,
        evaluated_at: str,
        gates_evaluated: List[str],
    ) -> GovernanceDecision:
        """Same project transfer shortcut: no cross-project degradation."""
        prov_hash = self._compute_provenance_hash(
            project.project_id, project.project_id, strategy.strategy_id,
            TransferDecision.ALLOWED.value, strategy.reliability_score, self.policy.policy_version
        )
        return GovernanceDecision(
            decision=TransferDecision.ALLOWED,
            source_project_id=project.project_id,
            target_project_id=project.project_id,
            strategy_id=strategy.strategy_id,
            transfer_confidence=round(strategy.reliability_score, 4),
            risk_penalty=0.0,
            semantic_similarity=1.0,
            tech_stack_overlap=1.0,
            environmental_compatibility=1.0,
            evidence_weight=1.0,
            gates_evaluated=gates_evaluated,
            failed_gate=None,
            decision_reason="Same project evaluation: full local reliability applies without cross-project degradation.",
            abstain_reason=None,
            defensive_warnings=[],
            required_preconditions=[],
            policy_version=self.policy.policy_version,
            provenance_hash=prov_hash,
            evaluated_at=evaluated_at,
            is_data_only=True,
        )

    def _build_fail_closed_decision(
        self,
        src_id: str,
        tgt_id: str,
        strat_id: Optional[str],
        error_msg: str,
        gates_evaluated: List[str],
        evaluated_at: str,
    ) -> GovernanceDecision:
        """Fail-closed safety fallback."""
        prov_hash = self._compute_provenance_hash(
            src_id, tgt_id, strat_id, TransferDecision.ABSTAIN.value, 0.0, self.policy.policy_version
        )
        return GovernanceDecision(
            decision=TransferDecision.ABSTAIN,
            source_project_id=src_id,
            target_project_id=tgt_id,
            strategy_id=strat_id,
            transfer_confidence=0.0,
            risk_penalty=1.0,
            semantic_similarity=0.0,
            tech_stack_overlap=0.0,
            environmental_compatibility=0.0,
            evidence_weight=0.0,
            gates_evaluated=gates_evaluated,
            failed_gate="FAIL_CLOSED_EXCEPTION",
            decision_reason=f"Governance evaluation failed closed due to internal error: {error_msg}",
            abstain_reason="GOVERNANCE_EVALUATION_ERROR",
            defensive_warnings=[f"Fail-closed safety triggered: {error_msg}"],
            required_preconditions=[],
            policy_version=self.policy.policy_version,
            provenance_hash=prov_hash,
            evaluated_at=evaluated_at,
            is_data_only=True,
        )

    @staticmethod
    def _compute_provenance_hash(
        src: str,
        tgt: str,
        strat_id: Optional[str],
        verdict: str,
        conf: float,
        policy: str,
    ) -> str:
        """Produces a deterministic SHA-256 fingerprint of the governance decision."""
        raw = f"{src}::{tgt}::{strat_id or 'none'}::{verdict}::{conf:.4f}::{policy}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Canonical singleton instance
governance_engine = GovernanceEngine()
