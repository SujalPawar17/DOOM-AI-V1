"""V8.24 PlanMode detection. System-derived; never trust user as control metadata."""

from __future__ import annotations

import re
from enum import Enum

from orchestration.decision.relevance import decision_relevant


class PlanMode(str, Enum):
    CREATE = "CREATE"
    REFINE = "REFINE"
    NEXT = "NEXT"
    VALIDATE = "VALIDATE"
    DEPEND = "DEPEND"


_REFINE = re.compile(
    r"(?i)^(improve (this|that) plan|refine (the |this |that )?plan|"
    r"make (this|that) plan (better|more practical)|"
    r"reorder (these|the) steps)[\s?.!]*$"
)

_NEXT = re.compile(
    r"(?i)^(what should i do first|what is the most important next step|"
    r"which step comes first|most important (next )?step)[\s?.!]*$"
)

_VALIDATE = re.compile(
    r"(?i)^(does this plan make sense|are there any blockers|"
    r"validate (this |the )?plan|any blockers)[\s?.!]*$"
)

_DEPEND = re.compile(
    r"(?i)^(which step depends on another|what are the dependencies|"
    r"show (me )?dependencies|any dependencies)[\s?.!]*$"
)

# Continuations that need a prior plan (V8.24 + V8.23 next-steps).
_NEEDS_PRIOR = re.compile(
    r"(?i)^(next steps|what next|how do i proceed|"
    r"what should i do next|what should i do|"
    r"improve (this|that) plan|refine (the |this |that )?plan|"
    r"make (this|that) plan (better|more practical)|"
    r"reorder (these|the) steps|"
    r"what should i do first|what is the most important next step|"
    r"which step comes first|most important (next )?step|"
    r"does this plan make sense|are there any blockers|"
    r"validate (this |the )?plan|any blockers|"
    r"which step depends on another|what are the dependencies|"
    r"show (me )?dependencies|any dependencies)[\s?.!]*$"
)


def detect_plan_mode(query: str) -> PlanMode:
    q = " ".join(str(query or "").strip().split())
    if not q:
        return PlanMode.CREATE
    if decision_relevant(q):
        return PlanMode.CREATE
    if _REFINE.match(q):
        return PlanMode.REFINE
    if _NEXT.match(q):
        return PlanMode.NEXT
    if _VALIDATE.match(q):
        return PlanMode.VALIDATE
    if _DEPEND.match(q):
        return PlanMode.DEPEND
    return PlanMode.CREATE


def needs_prior_plan(query: str) -> bool:
    """True when utterance is a continuation that requires a parseable anchor plan."""
    q = " ".join(str(query or "").strip().split())
    if not q:
        return False
    mode = detect_plan_mode(q)
    if mode in (PlanMode.REFINE, PlanMode.NEXT, PlanMode.VALIDATE, PlanMode.DEPEND):
        return True
    return bool(_NEEDS_PRIOR.match(q))


def plan_mode_continuation(query: str) -> bool:
    """Whether this utterance should resolve V8.21 anchor (includes V8.23 continuations)."""
    return needs_prior_plan(query)
