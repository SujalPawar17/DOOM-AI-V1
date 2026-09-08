"""
DOOM V5.3.7.3 — Governance Gates Subsystem
Defines and executes the 14 Deterministic Hard Governance Gates.
Each gate evaluates independently:
- Returns (passed: bool, gate_id: str, reason: str, is_hard_block: bool)
- Any failing hard gate halts transfer evaluation immediately with zero score.
- No mathematical score, similarity, or confidence can override a hard block.
"""

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from memory.types import PrivacyClass, MemorySource, MemoryStatus
from memory.project_models import (
    ProjectRecord,
    StrategyRecord,
    LessonRecord,
    ProjectLifecycleStatus,
    LessonScope,
    TransferDecision,
)


class GovernanceGateId(str, Enum):
    """The 14 canonical Hard Governance Gate identifiers."""
    GATE_01_SOURCE_EXISTS          = "GATE_01_SOURCE_EXISTS"
    GATE_02_LIFECYCLE_ACTIVE       = "GATE_02_LIFECYCLE_ACTIVE"
    GATE_03_PRIVACY_QUARANTINE     = "GATE_03_PRIVACY_QUARANTINE"
    GATE_04_PROVENANCE_VERIFIED    = "GATE_04_PROVENANCE_VERIFIED"
    GATE_05_TARGET_PROJECT_VALID   = "GATE_05_TARGET_PROJECT_VALID"
    GATE_06_SCOPE_ELIGIBLE         = "GATE_06_SCOPE_ELIGIBLE"
    GATE_07_PREREQUISITES_MET      = "GATE_07_PREREQUISITES_MET"
    GATE_08_TARGET_FAILURE_HISTORY = "GATE_08_TARGET_FAILURE_HISTORY"
    GATE_09_TECH_STACK_OVERLAP     = "GATE_09_TECH_STACK_OVERLAP"
    GATE_10_ENVIRONMENT_COMPATIBLE = "GATE_10_ENVIRONMENT_COMPATIBLE"
    GATE_11_MINIMUM_RELIABILITY    = "GATE_11_MINIMUM_RELIABILITY"
    GATE_12_EVIDENCE_ADMISSIBLE    = "GATE_12_EVIDENCE_ADMISSIBLE"
    GATE_13_TEMPLATE_SAFETY        = "GATE_13_TEMPLATE_SAFETY"
    GATE_14_EXPLICIT_POLICY        = "GATE_14_EXPLICIT_POLICY"


@dataclass(frozen=True)
class GateResult:
    """Immutable result of a single governance gate evaluation."""
    gate_id: str
    passed: bool
    reason: str
    is_hard_block: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


# Common OS/runtime system tools that are globally available
GLOBAL_SYSTEM_TOOLS = {
    "git", "curl", "python", "pip", "bash", "powershell", "sh", "echo",
    "cat", "grep", "sed", "awk", "find", "ls", "dir", "mkdir", "rm"
}

# Regex to detect hardcoded foreign user home paths (POSIX and Windows)
FOREIGN_PATH_RE = re.compile(
    r"(?:/home/[a-zA-Z0-9_-]+|/Users/[a-zA-Z0-9_-]+|[a-zA-Z]:[\\/]+[Uu]sers[\\/]+[a-zA-Z0-9_-]+)",
    re.IGNORECASE
)


class GovernanceGateEvaluator:
    """
    Pure deterministic evaluator for the 14 Hard Governance Gates.
    Strictly read-only; performs zero database mutations.
    """

    @staticmethod
    def evaluate_gate_01_source_exists(
        strategy: Optional[StrategyRecord],
        source_project: Optional[ProjectRecord],
    ) -> GateResult:
        """Gate 1: Verifies source knowledge and originating project exist."""
        if not strategy:
            return GateResult(
                gate_id=GovernanceGateId.GATE_01_SOURCE_EXISTS.value,
                passed=False,
                reason="Source strategy not found or null",
                is_hard_block=True,
            )
        if not source_project:
            return GateResult(
                gate_id=GovernanceGateId.GATE_01_SOURCE_EXISTS.value,
                passed=False,
                reason="Source project not found or null",
                is_hard_block=True,
            )
        return GateResult(
            gate_id=GovernanceGateId.GATE_01_SOURCE_EXISTS.value,
            passed=True,
            reason="Source strategy and project exist",
        )

    @staticmethod
    def evaluate_gate_02_lifecycle_active(
        strategy: StrategyRecord,
        source_project: ProjectRecord,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> GateResult:
        """Gate 2: Source strategy and project must be in active lifecycle states."""
        if strategy.is_deprecated:
            reason = strategy.deprecation_reason or "Strategy is deprecated"
            return GateResult(
                gate_id=GovernanceGateId.GATE_02_LIFECYCLE_ACTIVE.value,
                passed=False,
                reason=f"Source strategy is deprecated: {reason}",
                is_hard_block=True,
            )

        src_status = source_project.lifecycle_status
        if isinstance(src_status, Enum):
            src_status = src_status.value
        if src_status == "ARCHIVED":
            return GateResult(
                gate_id=GovernanceGateId.GATE_02_LIFECYCLE_ACTIVE.value,
                passed=False,
                reason=f"Source project '{source_project.project_id}' is ARCHIVED",
                is_hard_block=True,
            )

        if extra_context and extra_context.get("memory_status") in ("SUPERSEDED", "ARCHIVED", "TOMBSTONE"):
            return GateResult(
                gate_id=GovernanceGateId.GATE_02_LIFECYCLE_ACTIVE.value,
                passed=False,
                reason=f"Underlying memory record is {extra_context.get('memory_status')}",
                is_hard_block=True,
            )

        return GateResult(
            gate_id=GovernanceGateId.GATE_02_LIFECYCLE_ACTIVE.value,
            passed=True,
            reason="Source strategy and project lifecycle active",
        )

    @staticmethod
    def evaluate_gate_03_privacy_quarantine(
        source_project: ProjectRecord,
        target_project: ProjectRecord,
        strategy_privacy: Optional[PrivacyClass] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> GateResult:
        """
        Gate 3: Hard Privacy Quarantine Authority.
        SENSITIVE: permanently quarantined to originating project.
        PRIVATE: cannot transfer to public/NORMAL project.
        """
        src_priv = source_project.privacy_class
        if isinstance(src_priv, Enum):
            src_priv = src_priv.value
        tgt_priv = target_project.privacy_class
        if isinstance(tgt_priv, Enum):
            tgt_priv = tgt_priv.value

        # Check effective privacy class (max of project and record)
        effective_src = src_priv
        if strategy_privacy:
            sp_val = strategy_privacy.value if isinstance(strategy_privacy, Enum) else strategy_privacy
            if sp_val == PrivacyClass.SENSITIVE.value or effective_src == PrivacyClass.SENSITIVE.value:
                effective_src = PrivacyClass.SENSITIVE.value
            elif sp_val == PrivacyClass.PRIVATE.value and effective_src == PrivacyClass.NORMAL.value:
                effective_src = PrivacyClass.PRIVATE.value

        if extra_context and extra_context.get("privacy_class") == PrivacyClass.SENSITIVE.value:
            effective_src = PrivacyClass.SENSITIVE.value

        if effective_src == PrivacyClass.SENSITIVE.value:
            return GateResult(
                gate_id=GovernanceGateId.GATE_03_PRIVACY_QUARANTINE.value,
                passed=False,
                reason="Source has SENSITIVE privacy class; transfer is strictly quarantined",
                is_hard_block=True,
            )

        if effective_src == PrivacyClass.PRIVATE.value and tgt_priv == PrivacyClass.NORMAL.value:
            return GateResult(
                gate_id=GovernanceGateId.GATE_03_PRIVACY_QUARANTINE.value,
                passed=False,
                reason="PRIVATE source cannot transfer into public/NORMAL target project",
                is_hard_block=True,
            )

        if effective_src == PrivacyClass.PRIVATE.value and tgt_priv == PrivacyClass.PRIVATE.value:
            # Check hierarchy / ownership or explicit authorization
            same_hierarchy = (
                source_project.project_id == target_project.project_id or
                (source_project.parent_project_id and source_project.parent_project_id == target_project.project_id) or
                (target_project.parent_project_id and target_project.parent_project_id == source_project.project_id) or
                (source_project.parent_project_id and target_project.parent_project_id and source_project.parent_project_id == target_project.parent_project_id)
            )
            authorized = (
                same_hierarchy or
                bool(extra_context and extra_context.get("is_authorized_private_transfer")) or
                bool(extra_context and extra_context.get("authorized_target_project_id") == target_project.project_id)
            )
            if not authorized:
                return GateResult(
                    gate_id=GovernanceGateId.GATE_03_PRIVACY_QUARANTINE.value,
                    passed=False,
                    reason="PRIVATE to PRIVATE transfer requires verified hierarchy or explicit authorization",
                    is_hard_block=True,
                )

        return GateResult(
            gate_id=GovernanceGateId.GATE_03_PRIVACY_QUARANTINE.value,
            passed=True,
            reason="Privacy requirements satisfied",
        )

    @staticmethod
    def evaluate_gate_04_provenance_verified(
        strategy: StrategyRecord,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> GateResult:
        """
        Gate 4: Source strategy provenance verification.
        Model self-generation without real-world execution trace is inadmissible.
        """
        ctx = extra_context or {}
        actor = str(ctx.get("actor", "")).strip().upper()
        task_verified = ctx.get("task_verified", True)

        if actor in ("MODEL", "LLM", "ASSISTANT") and not task_verified:
            return GateResult(
                gate_id=GovernanceGateId.GATE_04_PROVENANCE_VERIFIED.value,
                passed=False,
                reason="Model self-generation without task verification fails provenance",
                is_hard_block=True,
            )

        # Strategy must have non-empty procedure template
        if not strategy.procedure_template or not isinstance(strategy.procedure_template, dict):
            return GateResult(
                gate_id=GovernanceGateId.GATE_04_PROVENANCE_VERIFIED.value,
                passed=False,
                reason="Strategy procedure template is empty or invalid",
                is_hard_block=True,
            )

        return GateResult(
            gate_id=GovernanceGateId.GATE_04_PROVENANCE_VERIFIED.value,
            passed=True,
            reason="Strategy provenance verified",
        )

    @staticmethod
    def evaluate_gate_05_target_project_valid(
        target_project: Optional[ProjectRecord],
        target_project_id: str,
    ) -> GateResult:
        """Gate 5: Target project must exist, be non-empty, and be ACTIVE."""
        if not target_project:
            return GateResult(
                gate_id=GovernanceGateId.GATE_05_TARGET_PROJECT_VALID.value,
                passed=False,
                reason=f"Target project '{target_project_id}' not found",
                is_hard_block=True,
            )

        tgt_status = target_project.lifecycle_status
        if isinstance(tgt_status, Enum):
            tgt_status = tgt_status.value
        if tgt_status == "ARCHIVED":
            return GateResult(
                gate_id=GovernanceGateId.GATE_05_TARGET_PROJECT_VALID.value,
                passed=False,
                reason=f"Target project '{target_project.project_id}' is ARCHIVED",
                is_hard_block=True,
            )

        return GateResult(
            gate_id=GovernanceGateId.GATE_05_TARGET_PROJECT_VALID.value,
            passed=True,
            reason="Target project valid and active",
        )

    @staticmethod
    def evaluate_gate_06_scope_eligible(
        strategy: StrategyRecord,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> GateResult:
        """Gate 6: Strategy must be CROSS_PROJECT_ELIGIBLE or UNIVERSAL."""
        ctx = extra_context or {}
        strat_scope = (
            ctx.get("scope")
            or (strategy.procedure_template or {}).get("scope")
            or "CROSS_PROJECT_ELIGIBLE"
        )
        if isinstance(strat_scope, Enum):
            strat_scope = strat_scope.value

        if strat_scope in ("PROJECT_LOCAL", LessonScope.PROJECT_LOCAL.value):
            return GateResult(
                gate_id=GovernanceGateId.GATE_06_SCOPE_ELIGIBLE.value,
                passed=False,
                reason="Strategy scope is PROJECT_LOCAL; cross-project transfer prohibited",
                is_hard_block=True,
            )

        return GateResult(
            gate_id=GovernanceGateId.GATE_06_SCOPE_ELIGIBLE.value,
            passed=True,
            reason=f"Strategy scope '{strat_scope}' is cross-project eligible",
        )

    @staticmethod
    def evaluate_gate_07_prerequisites_met(
        strategy: StrategyRecord,
        target_project: ProjectRecord,
    ) -> GateResult:
        """
        Gate 7: Mandatory prerequisite tools and disallowed tool validation.
        Missing prerequisites or having disallowed tools triggers hard block.
        """
        target_tools = set(str(t).strip().lower() for t in target_project.tech_stack)
        recommended = [str(r).strip().lower() for r in strategy.recommended_tools]
        disallowed = [str(d).strip().lower() for d in strategy.disallowed_tools]

        # Check disallowed tools
        conflicts = [d for d in disallowed if d in target_tools]
        if conflicts:
            return GateResult(
                gate_id=GovernanceGateId.GATE_07_PREREQUISITES_MET.value,
                passed=False,
                reason=f"Target tech stack contains disallowed tools: {conflicts}",
                is_hard_block=True,
            )

        # Check recommended/prerequisite tools (excluding global system tools)
        missing = [
            req for req in recommended
            if req not in target_tools and req not in GLOBAL_SYSTEM_TOOLS
        ]
        if missing:
            return GateResult(
                gate_id=GovernanceGateId.GATE_07_PREREQUISITES_MET.value,
                passed=False,
                reason=f"Prerequisites not satisfied: missing {missing}",
                is_hard_block=True,
            )

        return GateResult(
            gate_id=GovernanceGateId.GATE_07_PREREQUISITES_MET.value,
            passed=True,
            reason="All prerequisite tools satisfied",
        )

    @staticmethod
    def evaluate_gate_08_target_failure_history(
        strategy: StrategyRecord,
        target_failures: int,
        target_successes: int,
        consecutive_target_failures: int = 0,
        max_consecutive_failures: int = 3,
        max_failure_rate: float = 0.50,
    ) -> GateResult:
        """
        Gate 8: Target-environment failure intelligence.
        >= 3 consecutive failures or > 50% failure rate in target context triggers hard block.
        """
        if consecutive_target_failures >= max_consecutive_failures:
            return GateResult(
                gate_id=GovernanceGateId.GATE_08_TARGET_FAILURE_HISTORY.value,
                passed=False,
                reason=f"Strategy accumulated {consecutive_target_failures} consecutive failures in target project",
                is_hard_block=True,
                metadata={
                    "consecutive_failures": consecutive_target_failures,
                    "target_failures": target_failures,
                },
            )

        total_target_attempts = target_successes + target_failures
        if total_target_attempts >= 2:
            failure_rate = float(target_failures / total_target_attempts)
            if failure_rate > max_failure_rate:
                return GateResult(
                    gate_id=GovernanceGateId.GATE_08_TARGET_FAILURE_HISTORY.value,
                    passed=False,
                    reason=f"Strategy target failure rate ({failure_rate:.1%}) exceeds threshold ({max_failure_rate:.1%})",
                    is_hard_block=True,
                    metadata={"failure_rate": failure_rate, "total_attempts": total_target_attempts},
                )

        return GateResult(
            gate_id=GovernanceGateId.GATE_08_TARGET_FAILURE_HISTORY.value,
            passed=True,
            reason="Target failure history within acceptable thresholds",
            metadata={"target_failures": target_failures, "target_successes": target_successes},
        )

    @staticmethod
    def evaluate_gate_09_tech_stack_overlap(
        source_project: ProjectRecord,
        target_project: ProjectRecord,
        strategy_scope: str = "CROSS_PROJECT_ELIGIBLE",
        min_overlap: float = 0.05,
    ) -> Tuple[GateResult, float]:
        """
        Gate 9: Technology Stack Overlap evaluation.
        Empty tech stacks evaluate to 0.0 (no assumption).
        Returns (GateResult, tech_overlap_score).
        """
        src_set = set(str(s).strip().lower() for s in source_project.tech_stack)
        tgt_set = set(str(t).strip().lower() for t in target_project.tech_stack)

        if not src_set or not tgt_set:
            # If scope is UNIVERSAL, allow zero overlap with soft score
            if strategy_scope == "UNIVERSAL":
                return GateResult(
                    gate_id=GovernanceGateId.GATE_09_TECH_STACK_OVERLAP.value,
                    passed=True,
                    reason="Universal scope permits transfer with default baseline overlap",
                ), 0.50
            return GateResult(
                gate_id=GovernanceGateId.GATE_09_TECH_STACK_OVERLAP.value,
                passed=False,
                reason="Empty tech stack in source or target project (overlap = 0.0)",
                is_hard_block=True,
            ), 0.0

        intersection = len(src_set.intersection(tgt_set))
        total = len(src_set) + len(tgt_set)
        tech_overlap = float(2.0 * intersection / total) if total > 0 else 0.0

        if tech_overlap < min_overlap and strategy_scope != "UNIVERSAL":
            return GateResult(
                gate_id=GovernanceGateId.GATE_09_TECH_STACK_OVERLAP.value,
                passed=False,
                reason=f"Tech stack overlap ({tech_overlap:.2f}) below minimum threshold ({min_overlap:.2f})",
                is_hard_block=True,
                metadata={"tech_overlap": tech_overlap},
            ), tech_overlap

        return GateResult(
            gate_id=GovernanceGateId.GATE_09_TECH_STACK_OVERLAP.value,
            passed=True,
            reason=f"Tech stack overlap ({tech_overlap:.2f}) acceptable",
            metadata={"tech_overlap": tech_overlap},
        ), tech_overlap

    @staticmethod
    def evaluate_gate_10_environment_compatible(
        strategy: StrategyRecord,
        target_environment: Optional[Dict[str, Any]] = None,
    ) -> Tuple[GateResult, float]:
        """
        Gate 10: Environmental precondition validation (OS, python version, runtime).
        Returns (GateResult, environmental_compatibility_score).
        """
        preconds = strategy.environmental_preconditions or {}
        if not preconds:
            return GateResult(
                gate_id=GovernanceGateId.GATE_10_ENVIRONMENT_COMPATIBLE.value,
                passed=True,
                reason="No environmental preconditions specified",
            ), 1.0

        target_env = target_environment or {}
        # Check required OS
        req_os = preconds.get("os")
        if req_os and target_env.get("os"):
            if str(req_os).lower() != str(target_env.get("os")).lower():
                # Cross-OS mismatch
                if preconds.get("containerized", False) or preconds.get("abstracted", False):
                    return GateResult(
                        gate_id=GovernanceGateId.GATE_10_ENVIRONMENT_COMPATIBLE.value,
                        passed=True,
                        reason="OS mismatch mitigated by containerization",
                    ), 0.70
                return GateResult(
                    gate_id=GovernanceGateId.GATE_10_ENVIRONMENT_COMPATIBLE.value,
                    passed=False,
                    reason=f"OS incompatible: requires {req_os}, target has {target_env.get('os')}",
                    is_hard_block=True,
                ), 0.0

        # Check required python_version
        req_py = preconds.get("python_version")
        if req_py and target_env.get("python_version"):
            target_py_str = str(target_env.get("python_version")).strip()
            m_req = re.search(r"(\d+(?:\.\d+)?)", str(req_py))
            m_tgt = re.search(r"(\d+(?:\.\d+)?)", target_py_str)
            if m_req and m_tgt:
                req_ver = tuple(int(x) for x in m_req.group(1).split("."))
                tgt_ver = tuple(int(x) for x in m_tgt.group(1).split("."))
                if ">=" in str(req_py):
                    if tgt_ver < req_ver:
                        return GateResult(
                            gate_id=GovernanceGateId.GATE_10_ENVIRONMENT_COMPATIBLE.value,
                            passed=False,
                            reason=f"Python runtime incompatible: requires {req_py}, target has {target_py_str}",
                            is_hard_block=True,
                        ), 0.0
                elif "==" in str(req_py) or str(req_py).startswith("3."):
                    if tgt_ver != req_ver:
                        return GateResult(
                            gate_id=GovernanceGateId.GATE_10_ENVIRONMENT_COMPATIBLE.value,
                            passed=False,
                            reason=f"Python runtime incompatible: requires {req_py}, target has {target_py_str}",
                            is_hard_block=True,
                        ), 0.0

        return GateResult(
            gate_id=GovernanceGateId.GATE_10_ENVIRONMENT_COMPATIBLE.value,
            passed=True,
            reason="Environmental conditions compatible",
        ), 1.0

    @staticmethod
    def evaluate_gate_11_minimum_reliability(
        strategy: StrategyRecord,
        min_reliability_threshold: float = 0.25,
    ) -> GateResult:
        """Gate 11: Minimum strategy Bayesian reliability score threshold."""
        rel = strategy.reliability_score
        if rel < min_reliability_threshold:
            return GateResult(
                gate_id=GovernanceGateId.GATE_11_MINIMUM_RELIABILITY.value,
                passed=False,
                reason=f"Strategy reliability ({rel:.2f}) below threshold ({min_reliability_threshold:.2f})",
                is_hard_block=True,
                metadata={"reliability_score": rel},
            )
        return GateResult(
            gate_id=GovernanceGateId.GATE_11_MINIMUM_RELIABILITY.value,
            passed=True,
            reason=f"Strategy reliability ({rel:.2f}) acceptable",
            metadata={"reliability_score": rel},
        )

    @staticmethod
    def evaluate_gate_12_evidence_admissible(
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[GateResult, float]:
        """
        Gate 12: Evidence admissibility and quality weight calculation.
        Returns (GateResult, evidence_weight).
        """
        ctx = extra_context or {}
        evidence_list = ctx.get("evidence", [])
        verified_count = int(ctx.get("verified_experience_count", 1))

        # Check for ungrounded LLM inference as sole evidence
        if ctx.get("source") == MemorySource.DERIVED_CONTEXT.value and verified_count <= 0:
            return GateResult(
                gate_id=GovernanceGateId.GATE_12_EVIDENCE_ADMISSIBLE.value,
                passed=False,
                reason="Unverified derived context cannot serve as transfer evidence",
                is_hard_block=True,
            ), 0.0

        # Calculate evidence weight based on sample size
        # W_evidence in [0.5, 1.0]
        import math
        w_evidence = round(float(1.0 - 0.5 * math.exp(-max(0, verified_count) / 2.0)), 4)
        return GateResult(
            gate_id=GovernanceGateId.GATE_12_EVIDENCE_ADMISSIBLE.value,
            passed=True,
            reason="Evidence admissible",
            metadata={"evidence_weight": w_evidence, "verified_count": verified_count},
        ), w_evidence

    @staticmethod
    def evaluate_gate_13_template_safety(
        strategy: StrategyRecord,
    ) -> GateResult:
        """
        Gate 13: Template Parameterization and Foreign Path Sanitization.
        Strategy templates containing hardcoded user absolute paths fail safety.
        """
        template = strategy.procedure_template or {}
        raw_text = str(template)

        # Check foreign path regex
        foreign_match = FOREIGN_PATH_RE.search(raw_text)
        if foreign_match:
            return GateResult(
                gate_id=GovernanceGateId.GATE_13_TEMPLATE_SAFETY.value,
                passed=False,
                reason=f"Strategy template contains unparameterized foreign path: {foreign_match.group(0)}",
                is_hard_block=True,
            )

        # Check prompt injection markers
        injection_markers = ["<system>", "[prompt_override]", "ignore previous instructions", "system prompt", "[system_override]"]
        lower_text = raw_text.lower()
        for marker in injection_markers:
            if marker in lower_text:
                return GateResult(
                    gate_id=GovernanceGateId.GATE_13_TEMPLATE_SAFETY.value,
                    passed=False,
                    reason=f"Strategy template contains unsafe prompt injection marker: {marker}",
                    is_hard_block=True,
                )

        return GateResult(
            gate_id=GovernanceGateId.GATE_13_TEMPLATE_SAFETY.value,
            passed=True,
            reason="Strategy template parameterization safe",
        )

    @staticmethod
    def evaluate_gate_14_explicit_policy(
        source_project: ProjectRecord,
        target_project: ProjectRecord,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> GateResult:
        """
        Gate 14: Explicit project boundary exclusion policies.
        Checks target project's boundary_config for denied source projects.
        """
        boundary = target_project.boundary_config or {}
        denied_sources = [str(s).lower() for s in boundary.get("denied_sources", [])]
        src_id = str(source_project.project_id).strip().lower()

        if src_id in denied_sources:
            return GateResult(
                gate_id=GovernanceGateId.GATE_14_EXPLICIT_POLICY.value,
                passed=False,
                reason=f"Source project '{src_id}' is explicitly denied by target project boundary policy",
                is_hard_block=True,
            )

        # Self-transfer is same-project, handled outside cross-project gates
        if src_id == str(target_project.project_id).strip().lower():
            return GateResult(
                gate_id=GovernanceGateId.GATE_14_EXPLICIT_POLICY.value,
                passed=True,
                reason="Same project evaluation",
            )

        return GateResult(
            gate_id=GovernanceGateId.GATE_14_EXPLICIT_POLICY.value,
            passed=True,
            reason="No explicit policy prohibition",
        )
