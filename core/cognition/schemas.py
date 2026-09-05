"""
DOOM V4 — Cognitive Core Data Models & Schemas
Defines structured dataclasses and enums for the 9-stage cognitive loop:
UNDERSTAND -> REASON -> DECIDE -> PLAN -> ACT -> OBSERVE -> EVALUATE -> REFLECT -> REPLAN
"""

from enum import Enum
from dataclasses import dataclass, field, asdict
import time
from typing import List, Dict, Any, Optional


class CognitiveIntent(str, Enum):
    QUERY = "QUERY"
    ACTION = "ACTION"
    MULTI_STEP = "MULTI_STEP"
    ANALYSIS = "ANALYSIS"
    CREATION = "CREATION"
    MODIFICATION = "MODIFICATION"
    SEARCH = "SEARCH"
    EXECUTION = "EXECUTION"
    AUTOMATION = "AUTOMATION"
    CONVERSATION = "CONVERSATION"
    SYSTEM_OPERATION = "SYSTEM_OPERATION"
    UNKNOWN = "UNKNOWN"


class CognitiveDecisionType(str, Enum):
    ANSWER_DIRECTLY = "ANSWER_DIRECTLY"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    SEARCH_WEB = "SEARCH_WEB"
    USE_MEMORY = "USE_MEMORY"
    EXECUTE_TOOL = "EXECUTE_TOOL"
    CREATE_PLAN = "CREATE_PLAN"
    CONTINUE_TASK = "CONTINUE_TASK"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"
    PAUSE_TASK = "PAUSE_TASK"
    FAIL_TASK = "FAIL_TASK"
    COMPLETE = "COMPLETE"


class EvaluationOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass
class CognitiveStep:
    step_id: int
    objective: str
    action: str
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = field(default_factory=dict)
    required_capability: str = "general"
    expected_outcome: str = ""
    risk_level: str = "SAFE"
    dependencies: List[int] = field(default_factory=list)
    verification_required: bool = True
    status: str = "pending"  # pending, running, succeeded, failed, blocked, skipped
    result: Optional[str] = None
    error: Optional[str] = None

    @property
    def description(self) -> str:
        return self.objective or self.action

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CognitiveObservation:
    action: str
    tool: str
    success: bool
    stdout: str = ""
    stderr: str = ""
    output: str = ""
    exit_code: Optional[int] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    verification_relevance: str = ""
    timestamp: str = field(default_factory=lambda: time.strftime("%H:%M:%S"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CognitiveReflection:
    cycle: int
    expected: str
    observed: str
    worked: bool
    failure_reason: Optional[str] = None
    assumption_fault: Optional[str] = None
    lesson: Optional[str] = None
    next_action: str = ""
    should_replan: bool = False
    timestamp: str = field(default_factory=lambda: time.strftime("%H:%M:%S"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CognitiveTelemetry:
    understanding_ms: float = 0.0
    reasoning_ms: float = 0.0
    decision_ms: float = 0.0
    planning_ms: float = 0.0
    execution_ms: float = 0.0
    observation_ms: float = 0.0
    evaluation_ms: float = 0.0
    reflection_ms: float = 0.0
    replanning_ms: float = 0.0
    checkpoint_ms: float = 0.0
    verification_ms: float = 0.0
    total_cognitive_ms: float = 0.0
    cognitive_cycles: int = 0
    replan_count: int = 0
    models_used: List[str] = field(default_factory=list)
    tools_executed: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CognitiveState:
    """Master structured state for DOOM V4 Cognitive Core."""
    user_request: str
    normalized_goal: str = ""
    intent: CognitiveIntent = CognitiveIntent.UNKNOWN
    task_type: str = "QUERY"  # Backward compatibility with V3.3
    constraints: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    entities: Dict[str, Any] = field(default_factory=dict)
    relevant_memory: Dict[str, Any] = field(default_factory=dict)
    required_capabilities: List[str] = field(default_factory=list)
    
    # Reasoning & Decision
    reasoning_summary: str = ""
    decision: CognitiveDecisionType = CognitiveDecisionType.ANSWER_DIRECTLY
    decision_basis: str = ""
    needs_clarification: bool = False
    clarification_prompt: Optional[str] = None
    confidence: float = 1.0  # Note: confidence never overrides empirical verification
    
    # Planning & Replanning
    plan_version: int = 1
    current_plan: List[CognitiveStep] = field(default_factory=list)
    completed_steps: List[CognitiveStep] = field(default_factory=list)
    current_step_id: Optional[int] = None
    
    # Empirical Observations & Reflections
    observations: List[CognitiveObservation] = field(default_factory=list)
    evaluation_outcomes: List[EvaluationOutcome] = field(default_factory=list)
    reflections: List[CognitiveReflection] = field(default_factory=list)
    replan_history: List[Dict[str, Any]] = field(default_factory=list)
    
    # Ground Truth Verification & Final Output
    verification_results: Dict[str, Any] = field(default_factory=dict)
    final_decision: str = ""
    final_response: str = ""
    final_response_status: str = "success"  # success, partial_success, blocked, failed
    is_terminal: bool = False
    termination_reason: str = "COMPLETED"
    
    # Telemetry
    telemetry: CognitiveTelemetry = field(default_factory=CognitiveTelemetry)

    def to_dict(self, safe: bool = True) -> Dict[str, Any]:
        """Safe serialization. Never exposes raw or private chain-of-thought."""
        return {
            "user_request": self.user_request,
            "normalized_goal": self.normalized_goal,
            "intent": self.intent.value,
            "task_type": self.task_type,
            "constraints": self.constraints,
            "required_capabilities": self.required_capabilities,
            "reasoning_summary": self.reasoning_summary,
            "decision": self.decision.value,
            "decision_basis": self.decision_basis,
            "needs_clarification": self.needs_clarification,
            "clarification_prompt": self.clarification_prompt,
            "confidence": self.confidence,
            "plan_version": self.plan_version,
            "current_plan": [s.to_dict() for s in self.current_plan],
            "completed_steps": [s.to_dict() for s in self.completed_steps],
            "current_step_id": self.current_step_id,
            "observations_count": len(self.observations),
            "reflections": [r.to_dict() for r in self.reflections],
            "replan_count": self.telemetry.replan_count,
            "verification_results": self.verification_results,
            "final_response_status": self.final_response_status,
            "is_terminal": self.is_terminal,
            "termination_reason": self.termination_reason,
            "telemetry": self.telemetry.to_dict()
        }
