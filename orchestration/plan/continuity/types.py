"""V8.25 bounded data models for adaptive goal continuity.

Informational only. Zero execution authority. No mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from orchestration.plan.types import PlanConfidence

MAX_CONTINUITY_STEPS = 6
MAX_GOAL_TITLE_CHARS = 80
MAX_RAW_EVIDENCE_CHARS = 80
MAX_STATE_DETAIL_CHARS = 80
MAX_STALENESS_REASON_CHARS = 120
MAX_CLARIFY_CHARS = 200

# Action suggestions
SUGGEST_PROCEED = "PROCEED"
SUGGEST_RESOLVE_BLOCKER = "RESOLVE_BLOCKER"
SUGGEST_REPLAN = "REPLAN"
SUGGEST_CLARIFY = "CLARIFY"


class StepState(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class GoalState(str, Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"
    ABANDONED = "ABANDONED"


class ProgressEventKind(str, Enum):
    STEP_COMPLETED = "STEP_COMPLETED"
    STEP_STARTED = "STEP_STARTED"
    STEP_BLOCKED = "STEP_BLOCKED"
    STEP_SKIPPED = "STEP_SKIPPED"
    GOAL_PIVOT = "GOAL_PIVOT"
    GOAL_RESET = "GOAL_RESET"
    NOOP = "NOOP"


@dataclass(frozen=True)
class ProgressEvent:
    kind: ProgressEventKind
    target_step_index: int = 0
    raw_evidence: str = ""
    confidence: PlanConfidence = PlanConfidence.LOW
    is_ambiguous: bool = False

    def __post_init__(self) -> None:
        idx = int(self.target_step_index)
        if idx < 0:
            idx = 0
        object.__setattr__(self, "target_step_index", idx)
        evidence = str(self.raw_evidence or "").strip()[:MAX_RAW_EVIDENCE_CHARS]
        object.__setattr__(self, "raw_evidence", evidence)


@dataclass(frozen=True)
class StepStateRecord:
    index: int
    state: StepState = StepState.PENDING
    detail: str = ""

    def __post_init__(self) -> None:
        idx = max(1, int(self.index))
        object.__setattr__(self, "index", idx)
        det = str(self.detail or "").strip()[:MAX_STATE_DETAIL_CHARS]
        object.__setattr__(self, "detail", det)


@dataclass(frozen=True)
class PlanContinuityState:
    goal_title: str = ""
    goal_state: GoalState = GoalState.ACTIVE
    step_records: Tuple[StepStateRecord, ...] = ()
    active_step_index: int = 1
    completed_count: int = 0
    total_count: int = 0
    staleness_reason: str = ""

    def __post_init__(self) -> None:
        title = str(self.goal_title or "").strip()[:MAX_GOAL_TITLE_CHARS]
        object.__setattr__(self, "goal_title", title)
        reason = str(self.staleness_reason or "").strip()[:MAX_STALENESS_REASON_CHARS]
        object.__setattr__(self, "staleness_reason", reason)

        records = tuple(self.step_records or ())
        if len(records) > MAX_CONTINUITY_STEPS:
            records = records[:MAX_CONTINUITY_STEPS]
        object.__setattr__(self, "step_records", records)

        tot = len(records)
        object.__setattr__(self, "total_count", tot)

        comp = sum(1 for r in records if r.state is StepState.COMPLETED)
        object.__setattr__(self, "completed_count", comp)

        act = int(self.active_step_index)
        if tot == 0:
            act = 0
        elif act < 0 or act > tot:
            act = 0
        object.__setattr__(self, "active_step_index", act)


@dataclass(frozen=True)
class ContinuityAnalysis:
    continuity_state: PlanContinuityState
    detected_event: ProgressEvent
    clarification_needed: str = ""
    suggested_action: str = ""

    def __post_init__(self) -> None:
        clar = str(self.clarification_needed or "").strip()[:MAX_CLARIFY_CHARS]
        object.__setattr__(self, "clarification_needed", clar)
        act = str(self.suggested_action or "").strip()
        if act not in (
            SUGGEST_PROCEED,
            SUGGEST_RESOLVE_BLOCKER,
            SUGGEST_REPLAN,
            SUGGEST_CLARIFY,
            "",
        ):
            act = SUGGEST_PROCEED
        object.__setattr__(self, "suggested_action", act)
