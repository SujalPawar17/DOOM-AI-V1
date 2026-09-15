"""V8.23 bounded informational plan models. Never authorize execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Tuple


class PlanStepKind(str, Enum):
    PREPARE = "PREPARE"
    CHECK = "CHECK"
    DECIDE = "DECIDE"
    VERIFY = "VERIFY"
    OPTIONAL = "OPTIONAL"


class PlanConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class PlanStatus(str, Enum):
    OK = "OK"
    CLARIFY = "CLARIFY"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UNAVAILABLE = "UNAVAILABLE"


class GoalClass(str, Enum):
    RESOURCE = "RESOURCE"
    IMPLEMENT = "IMPLEMENT"
    DECIDE_FOLLOWUP = "DECIDE_FOLLOWUP"
    GENERAL = "GENERAL"


MAX_QUESTION_CHARS = 500
MAX_GOAL_SUMMARY_CHARS = 300
MAX_FACTS = 6
MAX_FACT_CHARS = 200
MAX_CONSTRAINTS = 6
MAX_CONSTRAINT_CHARS = 160
MAX_PREFERENCES = 4
MAX_PREFERENCE_CHARS = 160
MAX_SEED_STEPS = 4
MAX_SEED_STEP_CHARS = 120
MAX_STEPS = 6
MAX_STEP_TITLE_CHARS = 80
MAX_STEP_DETAIL_CHARS = 200
MAX_TITLE_CHARS = 120
MAX_REASONS = 6
MAX_REASON_CHARS = 200
MAX_BLOCKERS = 4
MAX_BLOCKER_CHARS = 160
MAX_ASSUMPTIONS = 4
MAX_ASSUMPTION_CHARS = 160
MAX_CLARIFY_CHARS = 300


@dataclass(frozen=True)
class PlanSourceFlags:
    conversation: bool = False
    memory: bool = False
    system: bool = False
    situation: bool = False
    decision_seed: bool = False


@dataclass(frozen=True)
class PlanInput:
    question: str = ""
    goal_summary: str = ""
    facts: Tuple[str, ...] = ()
    constraints: Tuple[str, ...] = ()
    preferences: Tuple[str, ...] = ()
    seed_steps: Tuple[str, ...] = ()
    situation_factors: Tuple[Tuple[str, str], ...] = ()
    source_flags: PlanSourceFlags = field(default_factory=PlanSourceFlags)


@dataclass(frozen=True)
class PlanStepItem:
    index: int = 1
    title: str = ""
    detail: str = ""
    kind: PlanStepKind = PlanStepKind.PREPARE


@dataclass(frozen=True)
class PlanResult:
    status: PlanStatus = PlanStatus.UNAVAILABLE
    title: str = ""
    steps: Tuple[PlanStepItem, ...] = ()
    reasons: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    confidence: PlanConfidence = PlanConfidence.LOW
    assumptions: Tuple[str, ...] = ()
    clarification: str = ""
