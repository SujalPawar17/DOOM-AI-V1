"""V8.25 Adaptive Goal Continuity package.

Pure, deterministic, bounded informational progress and continuity engine.
Zero execution authority.

Package initialization is lightweight. Submodule imports such as
``continuity.types`` must not eagerly load the continuity engine (which would
reintroduce the durable ↔ parse circular import when plan/__init__ is lazy).
Public symbols resolve lazily via ``__getattr__``.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "MAX_CONTINUITY_STEPS",
    "MAX_GOAL_TITLE_CHARS",
    "MAX_RAW_EVIDENCE_CHARS",
    "MAX_STALENESS_REASON_CHARS",
    "MAX_STATE_DETAIL_CHARS",
    "SUGGEST_CLARIFY",
    "SUGGEST_PROCEED",
    "SUGGEST_REPLAN",
    "SUGGEST_RESOLVE_BLOCKER",
    "ContinuityAnalysis",
    "GoalState",
    "PlanContinuityState",
    "ProgressEvent",
    "ProgressEventKind",
    "StepState",
    "StepStateRecord",
    "build_continuity_state",
    "detect_progress_event",
    "has_continuity_anchor",
    "needs_continuity_anchor",
    "process_progress_update",
    "recover_continuity_from_parse",
    "rebuild_continuity_preserving_states",
    "sanitize_progress_evidence",
    "should_route_to_plan_with_anchor",
    "apply_progress_event",
    "recompute_active_step",
]

# Previously imported at package init (including names historically bound but
# omitted from __all__). Keep them resolvable via lazy getattr.
_LAZY_EXPORTS = {
    "MAX_CONTINUITY_STEPS": ("orchestration.plan.continuity.types", "MAX_CONTINUITY_STEPS"),
    "MAX_GOAL_TITLE_CHARS": ("orchestration.plan.continuity.types", "MAX_GOAL_TITLE_CHARS"),
    "MAX_RAW_EVIDENCE_CHARS": ("orchestration.plan.continuity.types", "MAX_RAW_EVIDENCE_CHARS"),
    "MAX_STALENESS_REASON_CHARS": (
        "orchestration.plan.continuity.types",
        "MAX_STALENESS_REASON_CHARS",
    ),
    "MAX_STATE_DETAIL_CHARS": ("orchestration.plan.continuity.types", "MAX_STATE_DETAIL_CHARS"),
    "SUGGEST_CLARIFY": ("orchestration.plan.continuity.types", "SUGGEST_CLARIFY"),
    "SUGGEST_PROCEED": ("orchestration.plan.continuity.types", "SUGGEST_PROCEED"),
    "SUGGEST_REPLAN": ("orchestration.plan.continuity.types", "SUGGEST_REPLAN"),
    "SUGGEST_RESOLVE_BLOCKER": (
        "orchestration.plan.continuity.types",
        "SUGGEST_RESOLVE_BLOCKER",
    ),
    "ContinuityAnalysis": ("orchestration.plan.continuity.types", "ContinuityAnalysis"),
    "GoalState": ("orchestration.plan.continuity.types", "GoalState"),
    "PlanContinuityState": ("orchestration.plan.continuity.types", "PlanContinuityState"),
    "ProgressEvent": ("orchestration.plan.continuity.types", "ProgressEvent"),
    "ProgressEventKind": ("orchestration.plan.continuity.types", "ProgressEventKind"),
    "StepState": ("orchestration.plan.continuity.types", "StepState"),
    "StepStateRecord": ("orchestration.plan.continuity.types", "StepStateRecord"),
    "build_continuity_state": ("orchestration.plan.continuity.engine", "build_continuity_state"),
    "detect_progress_event": ("orchestration.plan.continuity.detect", "detect_progress_event"),
    "has_continuity_anchor": ("orchestration.plan.continuity.engine", "has_continuity_anchor"),
    "needs_continuity_anchor": (
        "orchestration.plan.continuity.engine",
        "needs_continuity_anchor",
    ),
    "process_progress_update": (
        "orchestration.plan.continuity.engine",
        "process_progress_update",
    ),
    "recover_continuity_from_parse": (
        "orchestration.plan.continuity.engine",
        "recover_continuity_from_parse",
    ),
    "rebuild_continuity_preserving_states": (
        "orchestration.plan.continuity.engine",
        "rebuild_continuity_preserving_states",
    ),
    "sanitize_progress_evidence": (
        "orchestration.plan.continuity.detect",
        "sanitize_progress_evidence",
    ),
    "should_route_to_plan_with_anchor": (
        "orchestration.plan.continuity.engine",
        "should_route_to_plan_with_anchor",
    ),
    "apply_progress_event": ("orchestration.plan.continuity.state", "apply_progress_event"),
    "recompute_active_step": ("orchestration.plan.continuity.state", "recompute_active_step"),
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
