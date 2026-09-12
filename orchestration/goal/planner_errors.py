"""V8.3 planner errors. Fail closed. Not execution or authorization."""

from __future__ import annotations

from enum import Enum


class PlannerStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PLANNING_UNAVAILABLE = "PLANNING_UNAVAILABLE"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    UNSUPPORTED_INTENT = "UNSUPPORTED_INTENT"
    INVALID_GOAL = "INVALID_GOAL"
    PLAN_VALIDATION_FAILED = "PLAN_VALIDATION_FAILED"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    V8_DISABLED = "V8_DISABLED"
