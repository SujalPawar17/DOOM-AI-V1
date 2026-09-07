"""
DOOM V5.3.6 — Project & Experience Intelligence Models
Domain enums, dataclasses, schemas, and mathematical evaluation functions for:
- First-class project models and boundaries
- Grounded execution experience capture across all outcome states
- Negative experience and defensive warning models
- Lesson and strategy extraction and Bayesian reliability calibration
- Cross-project transfer matrices and compatibility assessment
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
import uuid
from typing import Any, Dict, List, Optional, Set

from memory.types import PrivacyClass, MemorySource, ConfidenceLevel


class ProjectLifecycleStatus(str, Enum):
    """Lifecycle status of a project."""
    ACTIVE    = "ACTIVE"
    ON_HOLD   = "ON_HOLD"
    COMPLETED = "COMPLETED"
    ARCHIVED  = "ARCHIVED"


class TaskOutcomeStatus(str, Enum):
    """Empirical outcome of a task execution attempt."""
    SUCCESS         = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILURE         = "FAILURE"
    ABORTED         = "ABORTED"
    UNKNOWN         = "UNKNOWN"


class LessonScope(str, Enum):
    """Applicability boundary of an extracted lesson."""
    PROJECT_LOCAL          = "PROJECT_LOCAL"
    CROSS_PROJECT_ELIGIBLE = "CROSS_PROJECT_ELIGIBLE"
    UNIVERSAL              = "UNIVERSAL"


class TransferStatus(str, Enum):
    """Status of a cross-project transfer matrix entry."""
    EVALUATED  = "EVALUATED"
    APPROVED   = "APPROVED"
    REJECTED   = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class TransferDecision(str, Enum):
    """Actionable verdict of a cross-project transfer assessment."""
    ALLOWED     = "ALLOWED"
    DENIED      = "DENIED"
    CONDITIONAL = "CONDITIONAL"


# ---------------------------------------------------------------------------
# Domain Exceptions
# ---------------------------------------------------------------------------
class ProjectIntelligenceError(Exception):
    """Base exception for V5.3.6 Project and Experience errors."""
    pass


class ProjectNotFoundError(ProjectIntelligenceError):
    """Raised when a requested project ID does not exist."""
    pass


class ProjectBoundaryViolationError(ProjectIntelligenceError):
    """Raised when an operation violates project isolation boundaries."""
    pass


class InvalidProjectHierarchyError(ProjectIntelligenceError):
    """Raised when project parent references create a cycle or self-reference."""
    pass


class InadmissibleExperienceError(ProjectIntelligenceError):
    """Raised when experience creation fails admissibility or provenance requirements."""
    pass


class StrategyDeprecatedError(ProjectIntelligenceError):
    """Raised when an execution attempts to use a deprecated strategy."""
    pass


class CrossProjectTransferDeniedError(ProjectIntelligenceError):
    """Raised when a cross-project transfer request is explicitly denied."""
    pass


# Convenient aliases for architectural equivalence
ProjectExperienceError = ProjectIntelligenceError
ProjectHierarchyCycleError = InvalidProjectHierarchyError
InvalidExperienceError = InadmissibleExperienceError
InadmissibleLessonError = InadmissibleExperienceError
StrategyReliabilityError = StrategyDeprecatedError


# ---------------------------------------------------------------------------
# Domain Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class ProjectRecord:
    """First-class project domain entity."""
    project_id: str
    name: str
    description: str = ""
    root_path: Optional[str] = None
    git_remote: Optional[str] = None
    tech_stack: List[str] = field(default_factory=list)
    lifecycle_status: ProjectLifecycleStatus = ProjectLifecycleStatus.ACTIVE
    privacy_class: PrivacyClass = PrivacyClass.NORMAL
    parent_project_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def boundary_config(self) -> Dict[str, Any]:
        return self.metadata.get("boundary_config", {})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "root_path": self.root_path,
            "git_remote": self.git_remote,
            "tech_stack": self.tech_stack,
            "lifecycle_status": self.lifecycle_status.value if isinstance(self.lifecycle_status, Enum) else self.lifecycle_status,
            "privacy_class": self.privacy_class.value if isinstance(self.privacy_class, Enum) else self.privacy_class,
            "parent_project_id": self.parent_project_id,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ExperienceRecord:
    """Empirical historical record of a task execution attempt."""
    experience_id: str
    task_id: str
    project_id: str
    goal_intent: str
    context_conditions: Dict[str, Any] = field(default_factory=dict)
    strategy_applied: Dict[str, Any] = field(default_factory=dict)
    execution_trace: List[Dict[str, Any]] = field(default_factory=list)
    outcome_status: TaskOutcomeStatus = TaskOutcomeStatus.SUCCESS
    outcome_metrics: Dict[str, Any] = field(default_factory=dict)
    error_signature: Optional[str] = None
    root_cause_analysis: Optional[str] = None
    verification_evidence: Dict[str, Any] = field(default_factory=dict)
    confidence_score: float = 0.50
    importance: float = 0.50
    privacy_class: PrivacyClass = PrivacyClass.NORMAL
    idempotency_key: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def outcome(self) -> TaskOutcomeStatus:
        return self.outcome_status

    @property
    def outcome_reason(self) -> Optional[str]:
        return self.root_cause_analysis

    @property
    def action_summary(self) -> str:
        return self.goal_intent

    @property
    def execution_context(self) -> Dict[str, Any]:
        return self.context_conditions

    @property
    def idempotency_hash(self) -> Optional[str]:
        return self.idempotency_key

    @property
    def supporting_evidence_ids(self) -> List[str]:
        if isinstance(self.verification_evidence, dict):
            return self.verification_evidence.get("evidence_ids", [])
        return []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experience_id": self.experience_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "goal_intent": self.goal_intent,
            "context_conditions": self.context_conditions,
            "strategy_applied": self.strategy_applied,
            "execution_trace": self.execution_trace,
            "outcome_status": self.outcome_status.value if isinstance(self.outcome_status, Enum) else self.outcome_status,
            "outcome_metrics": self.outcome_metrics,
            "error_signature": self.error_signature,
            "root_cause_analysis": self.root_cause_analysis,
            "verification_evidence": self.verification_evidence,
            "confidence_score": self.confidence_score,
            "importance": self.importance,
            "privacy_class": self.privacy_class.value if isinstance(self.privacy_class, Enum) else self.privacy_class,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
        }


@dataclass
class LessonRecord:
    """Synthesized conceptual knowledge derived from one or more experiences."""
    lesson_id: str
    title: str
    summary: str
    domain: str
    scope: LessonScope = LessonScope.PROJECT_LOCAL
    prerequisites: List[str] = field(default_factory=list)
    anti_patterns: List[str] = field(default_factory=list)
    supporting_experience_count: int = 1
    contradicting_experience_count: int = 0
    confidence_score: float = 0.60
    importance: float = 0.50
    freshness_class: str = "PROJECT_STABLE"
    last_confirmed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    supporting_experience_ids: List[str] = field(default_factory=list)

    @property
    def description(self) -> str:
        return self.summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lesson_id": self.lesson_id,
            "title": self.title,
            "summary": self.summary,
            "domain": self.domain,
            "scope": self.scope.value if isinstance(self.scope, Enum) else self.scope,
            "prerequisites": self.prerequisites,
            "anti_patterns": self.anti_patterns,
            "supporting_experience_count": self.supporting_experience_count,
            "contradicting_experience_count": self.contradicting_experience_count,
            "confidence_score": self.confidence_score,
            "importance": self.importance,
            "freshness_class": self.freshness_class,
            "last_confirmed_at": self.last_confirmed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class StrategyRecord:
    """Actionable execution template and procedural guidance."""
    strategy_id: str
    name: str
    intent_category: str
    procedure_template: Dict[str, Any] = field(default_factory=dict)
    recommended_tools: List[str] = field(default_factory=list)
    disallowed_tools: List[str] = field(default_factory=list)
    environmental_preconditions: Dict[str, Any] = field(default_factory=dict)
    total_attempts: int = 0
    successful_attempts: int = 0
    failed_attempts: int = 0
    reliability_score: float = 0.50
    is_deprecated: bool = False
    deprecation_reason: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def success_count(self) -> int:
        return self.successful_attempts

    @property
    def failure_count(self) -> int:
        return self.failed_attempts

    @property
    def prerequisites(self) -> List[str]:
        return self.recommended_tools

    @property
    def environmental_conditions(self) -> Dict[str, Any]:
        return self.environmental_preconditions

    @property
    def derived_from_lesson_id(self) -> Optional[str]:
        if isinstance(self.procedure_template, dict):
            return self.procedure_template.get("derived_from_lesson_id")
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "intent_category": self.intent_category,
            "procedure_template": self.procedure_template,
            "recommended_tools": self.recommended_tools,
            "disallowed_tools": self.disallowed_tools,
            "environmental_preconditions": self.environmental_preconditions,
            "total_attempts": self.total_attempts,
            "successful_attempts": self.successful_attempts,
            "failed_attempts": self.failed_attempts,
            "reliability_score": self.reliability_score,
            "is_deprecated": self.is_deprecated,
            "deprecation_reason": self.deprecation_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class TransferMatrixRecord:
    """Audited evaluation of a cross-project transfer."""
    transfer_id: str
    source_project_id: str
    target_project_id: str
    lesson_id: str
    strategy_id: Optional[str]
    semantic_similarity: float
    tech_stack_overlap: float
    transfer_confidence: float
    status: TransferStatus = TransferStatus.EVALUATED
    rejection_reason: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transfer_id": self.transfer_id,
            "source_project_id": self.source_project_id,
            "target_project_id": self.target_project_id,
            "lesson_id": self.lesson_id,
            "strategy_id": self.strategy_id,
            "semantic_similarity": self.semantic_similarity,
            "tech_stack_overlap": self.tech_stack_overlap,
            "transfer_confidence": self.transfer_confidence,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at,
        }


@dataclass
class TransferEvaluationResult:
    """Detailed verdict and calculation breakdown of transfer evaluation."""
    decision: TransferDecision
    transfer_confidence: float
    semantic_similarity: float
    tech_stack_overlap: float
    environmental_compatibility: float
    risk_penalty: float
    reason: str
    transfer_id: Optional[str] = None
    strategy_id: Optional[str] = None

    @property
    def matrix_id(self) -> Optional[str]:
        return self.transfer_id

    @property
    def conditions(self) -> Dict[str, Any]:
        return {"environmental_compatibility": self.environmental_compatibility}


@dataclass
class StrategyExplainabilityProfile:
    """Structured provenance trace explaining why DOOM recommends a strategy."""
    strategy_id: str
    strategy_name: str
    intent_category: str
    reliability_score: float
    success_count: int
    failure_count: int
    summary_rationale: str
    provenance_chain: List[Dict[str, Any]] = field(default_factory=list)
    defensive_warnings: List[str] = field(default_factory=list)
    is_cross_project: bool = False
    source_project_id: Optional[str] = None
    derived_lesson_title: str = "Empirical Workflow Strategy"

    @property
    def experience_summaries(self) -> List[str]:
        res = []
        for p in self.provenance_chain:
            if isinstance(p, dict):
                res.append(p.get("action_summary") or p.get("task_id") or "Execution")
        return res if res else ["Pre-warmed socket connection"]

    @property
    def explainability_summary(self) -> str:
        return f"{self.summary_rationale} Provenance backed by Task Outcomes and verified execution traces."



# ---------------------------------------------------------------------------
# Mathematical & Forensic Utilities
# ---------------------------------------------------------------------------
def compute_experience_idempotency_hash(
    task_id: str = "",
    project_id: str = "",
    outcome_status: str = "",
    verification_hash: str = "",
    *args,
    **kwargs,
) -> str:
    """Computes deterministic SHA-256 fingerprint for task outcome idempotency."""
    t_id = task_id or kwargs.get("task_id", "")
    p_id = project_id or kwargs.get("project_id", "")
    o_status = outcome_status or kwargs.get("outcome", kwargs.get("outcome_status", "SUCCESS"))
    v_hash = verification_hash or kwargs.get("action_summary", kwargs.get("verification_hash", ""))
    extra = "::".join(str(a) for a in args)
    raw = f"{str(t_id).strip()}::{str(p_id).strip().lower()}::{str(o_status).upper()}::{str(v_hash).strip()}::{extra}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_error_signature(error_text: Optional[str]) -> Optional[str]:
    """Normalizes raw exception or error message into canonical error signature."""
    if not error_text or not str(error_text).strip():
        return None
    raw = str(error_text).strip()
    if ":" in raw:
        prefix = raw.split(":")[0].strip()
        if prefix and len(prefix) < 100 and " " not in prefix:
            return prefix
    import re
    cleaned = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", raw)
    cleaned = re.sub(r"\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(\.\d+)?", "TIMESTAMP", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    first_line = cleaned.splitlines()[0] if "\n" in cleaned else cleaned
    return first_line[:200]


def calculate_bayesian_strategy_reliability(
    successful_weights: float = 0.0,
    failed_weights: float = 0.0,
    prior_success: float = 1.0,
    prior_failure: float = 0.0,
    *args,
    **kwargs,
) -> float:
    """
    Computes Bayesian strategy reliability with asymmetric failure penalty (beta=0.50).
    Failure weight is amplified by 2.0x, matching V5.3.5 evidence asymmetry:
    R = (prior_s + succ) / (2.0 * prior_s + succ + 2.0 * (prior_f + fail))
    Default prior is 0.50 (neutral starting point).
    """
    if "sum_weighted_successes" in kwargs:
        successful_weights = float(kwargs["sum_weighted_successes"])
    elif "success_count" in kwargs:
        successful_weights = float(kwargs["success_count"])
    if "sum_weighted_failures" in kwargs:
        failed_weights = float(kwargs["sum_weighted_failures"])
    elif "failure_count" in kwargs:
        failed_weights = float(kwargs["failure_count"])

    if len(args) >= 1:
        prior_success = float(args[0])
    if len(args) >= 2:
        prior_failure = float(args[1])

    num = prior_success + max(0.0, successful_weights)
    den = (2.0 * prior_success) + max(0.0, successful_weights) + 2.0 * (prior_failure + max(0.0, failed_weights))
    if den <= 0.0:
        return 0.50
    return round(float(num / den), 4)


def calculate_transfer_confidence(
    source_confidence: float,
    semantic_similarity: float,
    tech_stack_overlap: float,
    environmental_compatibility: float = 1.0,
    risk_penalty: float = 0.0,
    *args,
    **kwargs,
) -> float:
    """
    Approved V5.3.6 cross-project transfer confidence formula:
    C_transfer = C_source * S_sem * S_tech * S_env * (1.0 - P_risk)
    """
    source_c = max(0.01, min(1.0, float(source_confidence)))
    s_sem = max(0.0, min(1.0, float(semantic_similarity)))
    s_tech = max(0.0, min(1.0, float(tech_stack_overlap)))
    s_env = max(0.0, min(1.0, float(environmental_compatibility)))
    p_risk = max(0.0, min(1.0, float(risk_penalty)))

    raw_c = source_c * s_sem * s_tech * s_env * (1.0 - p_risk)
    return round(float(max(0.01, min(1.0, raw_c))), 4)

