"""V8.23/V8.24 Bounded Informational Next-Step Planner. Never authorizes execution.

Eager imports intentionally avoided so submodule imports such as
``orchestration.plan.goal_registry`` do not load execute/continuity graphs.
Public symbols resolve lazily via ``__getattr__``.
"""

from __future__ import annotations

from typing import Any

__all__ = (
    "GoalClass",
    "PlanAnalysis",
    "PlanConfidence",
    "PlanDependency",
    "PlanInput",
    "PlanIssue",
    "PlanIssueKind",
    "PlanMode",
    "PlanResult",
    "PlanStatus",
    "PlanStepItem",
    "PlanStepKind",
    "ValidationStatus",
    "detect_plan_mode",
    "execute_plan_steps",
    "last_plan_ollama_calls",
    "plan_continuation",
    "plan_relevant",
    "reset_plan_provider_for_tests",
    "use_plan_provider_for_tests",
)

# name -> (module, attribute)
_LAZY_EXPORTS = {
    "GoalClass": ("orchestration.plan.types", "GoalClass"),
    "PlanAnalysis": ("orchestration.plan.analysis", "PlanAnalysis"),
    "PlanConfidence": ("orchestration.plan.types", "PlanConfidence"),
    "PlanDependency": ("orchestration.plan.analysis", "PlanDependency"),
    "PlanInput": ("orchestration.plan.types", "PlanInput"),
    "PlanIssue": ("orchestration.plan.analysis", "PlanIssue"),
    "PlanIssueKind": ("orchestration.plan.analysis", "PlanIssueKind"),
    "PlanMode": ("orchestration.plan.modes", "PlanMode"),
    "PlanResult": ("orchestration.plan.types", "PlanResult"),
    "PlanStatus": ("orchestration.plan.types", "PlanStatus"),
    "PlanStepItem": ("orchestration.plan.types", "PlanStepItem"),
    "PlanStepKind": ("orchestration.plan.types", "PlanStepKind"),
    "ValidationStatus": ("orchestration.plan.analysis", "ValidationStatus"),
    "detect_plan_mode": ("orchestration.plan.modes", "detect_plan_mode"),
    "execute_plan_steps": ("orchestration.plan.execute", "execute_plan_steps"),
    "last_plan_ollama_calls": ("orchestration.plan.execute", "last_plan_ollama_calls"),
    "plan_continuation": ("orchestration.plan.relevance", "plan_continuation"),
    "plan_relevant": ("orchestration.plan.relevance", "plan_relevant"),
    "reset_plan_provider_for_tests": (
        "orchestration.plan.execute",
        "reset_plan_provider_for_tests",
    ),
    "use_plan_provider_for_tests": (
        "orchestration.plan.execute",
        "use_plan_provider_for_tests",
    ),
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
