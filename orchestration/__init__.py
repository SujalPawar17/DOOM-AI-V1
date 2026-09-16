"""V8 orchestration layer. V8.1 is classification-only. No execution.

Package initialization is intentionally lightweight. Public symbols resolve
lazily so submodule imports (e.g. orchestration.plan.goal_registry) do not
eagerly load executor, task ledger, or database clients.
"""

from __future__ import annotations

from typing import Any

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

# name -> (module, attribute)
_LAZY_EXPORTS = {
    "AuditEvent": ("orchestration.audit", "AuditEvent"),
    "AvailabilityStatus": ("orchestration.goal", "AvailabilityStatus"),
    "CapabilityClass": ("orchestration.goal", "CapabilityClass"),
    "ExecutionIdentity": ("orchestration.executor", "ExecutionIdentity"),
    "ExecutionResult": ("orchestration.executor", "ExecutionResult"),
    "ExecutionStatus": ("orchestration.executor_errors", "ExecutionStatus"),
    "ContextRequest": ("orchestration.context", "ContextRequest"),
    "ContextResult": ("orchestration.context", "ContextResult"),
    "GoalClassificationResult": ("orchestration.goal", "GoalClassificationResult"),
    "GoalPlan": ("orchestration.goal", "GoalPlan"),
    "GoalSpec": ("orchestration.goal", "GoalSpec"),
    "IntentClass": ("orchestration.goal", "IntentClass"),
    "PlanProposal": ("orchestration.goal", "PlanProposal"),
    "PlanStep": ("orchestration.goal", "PlanStep"),
    "PlannerStatus": ("orchestration.goal", "PlannerStatus"),
    "RecoveryExecutionResult": ("orchestration.recovery", "RecoveryExecutionResult"),
    "TaskSnapshot": ("orchestration.task", "TaskSnapshot"),
    "TaskState": ("orchestration.task", "TaskState"),
    "build_goal_plan": ("orchestration.goal", "build_goal_plan"),
    "cancel_task": ("orchestration.task", "cancel_task"),
    "classify_failure": ("orchestration.recovery", "classify_failure"),
    "create_task": ("orchestration.task", "create_task"),
    "execute_plan": ("orchestration.executor", "execute_plan"),
    "list_events": ("orchestration.audit", "list_events"),
    "plan_goal": ("orchestration.goal", "plan_goal"),
    "poll_task": ("orchestration.task", "poll_task"),
    "process_goal": ("orchestration.goal", "process_goal"),
    "propose_recovery": ("orchestration.recovery", "propose_recovery"),
    "record_event": ("orchestration.audit", "record_event"),
    "recover_execution": ("orchestration.recovery", "recover_execution"),
    "retrieve_context": ("orchestration.context", "retrieve_context"),
    "start_task": ("orchestration.task", "start_task"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(target[0]), target[1])
    globals()[name] = value
    return value


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))
