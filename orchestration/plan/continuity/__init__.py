"""V8.25 Adaptive Goal Continuity package.

Pure, deterministic, bounded informational progress and continuity engine.
Zero execution authority.
"""

from __future__ import annotations

from orchestration.plan.continuity.detect import (
    detect_progress_event,
    sanitize_progress_evidence,
)
from orchestration.plan.continuity.engine import (
    build_continuity_state,
    has_continuity_anchor,
    needs_continuity_anchor,
    process_progress_update,
    recover_continuity_from_parse,
    rebuild_continuity_preserving_states,
    should_route_to_plan_with_anchor,
)
from orchestration.plan.continuity.state import (
    apply_progress_event,
    recompute_active_step,
)
from orchestration.plan.continuity.types import (
    MAX_CONTINUITY_STEPS,
    MAX_GOAL_TITLE_CHARS,
    MAX_RAW_EVIDENCE_CHARS,
    MAX_STALENESS_REASON_CHARS,
    MAX_STATE_DETAIL_CHARS,
    SUGGEST_CLARIFY,
    SUGGEST_PROCEED,
    SUGGEST_REPLAN,
    SUGGEST_RESOLVE_BLOCKER,
    ContinuityAnalysis,
    GoalState,
    PlanContinuityState,
    ProgressEvent,
    ProgressEventKind,
    StepState,
    StepStateRecord,
)

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
    "needs_continuity_anchor",
    "process_progress_update",
    "recover_continuity_from_parse",
    "rebuild_continuity_preserving_states",
    "sanitize_progress_evidence",
    "apply_progress_event",
    "recompute_active_step",
]
