"""V8.24 deterministic plan validation. Fail closed. Informational only."""

from __future__ import annotations

import re
from typing import List, Tuple

from orchestration.plan.analysis import (
    MAX_ISSUE_CHARS,
    MAX_ISSUES,
    PlanIssue,
    PlanIssueKind,
    ValidationStatus,
)
from orchestration.plan.modes import PlanMode
from orchestration.plan.refine import _norm_title
from orchestration.plan.types import (
    MAX_STEPS,
    PlanInput,
    PlanResult,
    PlanStatus,
    PlanStepKind,
)

_CONTRA_TODAY = re.compile(r"(?i)\b(today|right now|immediately|asap)\b")
_CONTRA_NO_TIME = re.compile(
    r"(?i)\b(no time|do not spend (any )?time|don't spend (any )?time|"
    r"zero time|cannot spend time)\b"
)


def validate_plan(
    result: PlanResult,
    inp: PlanInput,
    *,
    mode: PlanMode = PlanMode.CREATE,
    had_prior_plan: bool = True,
    clarify_message: str = "",
) -> Tuple[ValidationStatus, Tuple[PlanIssue, ...], PlanResult]:
    """Return (status, issues, possibly clarified PlanResult)."""
    issues: List[PlanIssue] = []

    def add(kind: PlanIssueKind, message: str, step_index: int = 0) -> None:
        if len(issues) >= MAX_ISSUES:
            return
        issues.append(
            PlanIssue(
                kind=kind,
                message=message[:MAX_ISSUE_CHARS],
                step_index=step_index,
            )
        )

    goal = (inp.goal_summary or "").strip()
    if mode is PlanMode.CREATE and len(goal) < 8 and result.status is PlanStatus.CLARIFY:
        add(PlanIssueKind.INSUFFICIENT_INFO, "Goal is too vague to build a useful plan.")
        return ValidationStatus.NEEDS_CLARIFY, tuple(issues), result

    if mode is PlanMode.CREATE and len(goal) < 8 and not result.steps:
        add(PlanIssueKind.INSUFFICIENT_INFO, "Goal is too vague to build a useful plan.")
        clarified = PlanResult(
            status=PlanStatus.CLARIFY,
            clarification=result.clarification
            or "To build a useful plan, I need to know what you want to accomplish.",
            confidence=result.confidence,
        )
        return ValidationStatus.NEEDS_CLARIFY, tuple(issues), clarified

    if mode in (PlanMode.REFINE, PlanMode.NEXT, PlanMode.VALIDATE, PlanMode.DEPEND):
        if not had_prior_plan or not result.steps:
            add(PlanIssueKind.INCOMPLETE, "No recent parseable plan is available.")
            msg = clarify_message or (
                "To refine or prioritize a plan, I need a recent plan in this conversation."
            )
            clarified = PlanResult(
                status=PlanStatus.CLARIFY,
                clarification=msg[:300],
                confidence=result.confidence,
            )
            return ValidationStatus.NEEDS_CLARIFY, tuple(issues), clarified

    if not result.steps and result.status is not PlanStatus.CLARIFY:
        add(PlanIssueKind.INCOMPLETE, "Plan has no steps.")
        clarified = PlanResult(
            status=PlanStatus.CLARIFY,
            clarification="I could not find usable steps in that plan.",
            confidence=result.confidence,
        )
        return ValidationStatus.INVALID, tuple(issues), clarified

    if len(result.steps) > MAX_STEPS:
        add(PlanIssueKind.EXCESSIVE_SCOPE, "Plan exceeds the maximum of 6 steps.")

    # Duplicates
    seen = set()
    for s in result.steps:
        key = _norm_title(s.title)
        if key in seen:
            add(PlanIssueKind.DUPLICATE, f"Duplicate step: {s.title[:80]}", s.index)
        seen.add(key)

    # OPTIONAL before required
    seen_optional = False
    for s in result.steps:
        if s.kind is PlanStepKind.OPTIONAL:
            seen_optional = True
        elif seen_optional and s.kind is not PlanStepKind.OPTIONAL:
            add(
                PlanIssueKind.BAD_ORDER,
                "Optional steps appear before required steps.",
                s.index,
            )
            break

    # Contradictory constraints — report both; do not pick a side.
    blob = " ".join(inp.constraints) + " " + inp.question
    if _CONTRA_TODAY.search(blob) and _CONTRA_NO_TIME.search(blob):
        add(
            PlanIssueKind.CONTRADICTORY,
            "Constraints conflict: urgent timing versus no time available.",
        )

    if any(i.kind is PlanIssueKind.CONTRADICTORY for i in issues):
        status = ValidationStatus.WEAK
    elif any(i.kind in (PlanIssueKind.DUPLICATE, PlanIssueKind.BAD_ORDER) for i in issues):
        status = ValidationStatus.WEAK
    elif any(i.kind is PlanIssueKind.EXCESSIVE_SCOPE for i in issues):
        status = ValidationStatus.WEAK
    else:
        status = ValidationStatus.OK

    # Merge issue messages into blockers for PlanResult display (bounded).
    blocker_msgs = list(result.blockers)
    for iss in issues:
        if iss.kind in (
            PlanIssueKind.CONTRADICTORY,
            PlanIssueKind.MISSING_PREREQ,
            PlanIssueKind.INCOMPLETE,
        ):
            if iss.message and iss.message not in blocker_msgs:
                blocker_msgs.append(iss.message)
    blocker_msgs = blocker_msgs[:4]

    updated = PlanResult(
        status=result.status,
        title=result.title,
        steps=result.steps,
        reasons=result.reasons,
        blockers=tuple(blocker_msgs),
        confidence=result.confidence,
        assumptions=result.assumptions,
        clarification=result.clarification,
    )
    return status, tuple(issues[:MAX_ISSUES]), updated
