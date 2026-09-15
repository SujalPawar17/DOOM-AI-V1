"""V8.24 deterministic prioritization. Scores never user-facing."""

from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple

from orchestration.plan.analysis import (
    MAX_NEXT_RATIONALE,
    MAX_NEXT_TITLE,
    PlanAnalysis,
    PlanDependency,
    PlanIssue,
    PlanIssueKind,
    ValidationStatus,
)
from orchestration.plan.modes import PlanMode
from orchestration.plan.types import PlanConfidence, PlanResult, PlanStepKind

_LEX = re.compile(r"(?i)\b(define|choose|target|scope|clarify)\b")


def _inbound(deps: Tuple[PlanDependency, ...]) -> Set[int]:
    return {d.to_index for d in deps}


def _score_step(
    index: int,
    kind: PlanStepKind,
    title: str,
    inbound: Set[int],
    blocked: Set[int],
) -> int:
    score = 0
    if index not in inbound:
        score += 40
    if kind in (PlanStepKind.PREPARE, PlanStepKind.DECIDE):
        score += 25
    if kind is PlanStepKind.CHECK:
        score += 15
    if _LEX.search(title or ""):
        score += 10
    if kind is PlanStepKind.VERIFY:
        score += 5
    if kind is PlanStepKind.OPTIONAL:
        score -= 20
    if index in blocked:
        score -= 30
    return score


def prioritize_plan(
    result: PlanResult,
    *,
    mode: PlanMode,
    dependencies: Tuple[PlanDependency, ...] = (),
    issues: Tuple[PlanIssue, ...] = (),
    validation_status: ValidationStatus = ValidationStatus.OK,
    confidence: PlanConfidence = PlanConfidence.MEDIUM,
) -> PlanAnalysis:
    steps = list(result.steps or ())
    if not steps:
        return PlanAnalysis(
            mode=mode,
            dependencies=dependencies,
            issues=issues,
            validation_status=validation_status,
            confidence=confidence,
        )

    inbound = _inbound(dependencies)
    blocked = {
        i.step_index
        for i in issues
        if i.kind is PlanIssueKind.MISSING_PREREQ and i.step_index > 0
    }

    scored: List[Tuple[int, int, int]] = []  # (-score, index, index)
    for s in steps:
        sc = _score_step(s.index, s.kind, s.title, inbound, blocked)
        scored.append((-sc, s.index, s.index))
    scored.sort()
    order = tuple(t[2] for t in scored)

    by_idx = {s.index: s for s in steps}
    next_idx = 0
    for idx in order:
        s = by_idx.get(idx)
        if s and s.kind is not PlanStepKind.OPTIONAL:
            next_idx = idx
            break
    if next_idx == 0 and order:
        next_idx = order[0]

    next_title = ""
    rationale = ""
    if next_idx and next_idx in by_idx:
        s = by_idx[next_idx]
        next_title = s.title[:MAX_NEXT_TITLE]
        if next_idx not in inbound:
            rationale = "This step has no prerequisites and unlocks later work."
        elif s.kind in (PlanStepKind.PREPARE, PlanStepKind.DECIDE):
            rationale = "Preparation or decision work should come before later steps."
        else:
            rationale = "This is the highest-value step given current dependencies."
        rationale = rationale[:MAX_NEXT_RATIONALE]

    return PlanAnalysis(
        mode=mode,
        dependencies=dependencies[:8],
        issues=issues[:6],
        next_step_index=next_idx,
        next_step_title=next_title,
        next_rationale=rationale,
        priority_order=order[:6],
        validation_status=validation_status,
        confidence=confidence if confidence else result.confidence,
    )
