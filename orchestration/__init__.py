"""V8 orchestration layer. V8.1 is classification-only. No execution."""

from orchestration.goal import (
    AvailabilityStatus,
    CapabilityClass,
    GoalClassificationResult,
    GoalPlan,
    GoalSpec,
    IntentClass,
    PlanProposal,
    PlanStep,
    PlannerStatus,
    build_goal_plan,
    plan_goal,
    process_goal,
)
from orchestration.audit import AuditEvent, list_events, record_event
from orchestration.context import (
    ContextRequest,
    ContextResult,
    retrieve_context,
)
from orchestration.executor import ExecutionIdentity, ExecutionResult, execute_plan
from orchestration.executor_errors import ExecutionStatus
from orchestration.recovery import (
    RecoveryExecutionResult,
    classify_failure,
    propose_recovery,
    recover_execution,
)
from orchestration.task import (
    TaskSnapshot,
    TaskState,
    cancel_task,
    create_task,
    poll_task,
    start_task,
)

__all__ = [
    "AuditEvent",
    "AvailabilityStatus",
    "CapabilityClass",
    "ExecutionIdentity",
    "ExecutionResult",
    "ExecutionStatus",
    "ContextRequest",
    "ContextResult",
    "GoalClassificationResult",
    "GoalPlan",
    "GoalSpec",
    "IntentClass",
    "PlanProposal",
    "PlanStep",
    "PlannerStatus",
    "RecoveryExecutionResult",
    "TaskSnapshot",
    "TaskState",
    "build_goal_plan",
    "cancel_task",
    "classify_failure",
    "create_task",
    "execute_plan",
    "list_events",
    "plan_goal",
    "poll_task",
    "process_goal",
    "propose_recovery",
    "record_event",
    "recover_execution",
    "retrieve_context",
    "start_task",
]
