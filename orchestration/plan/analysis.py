"""V8.24 analysis models + dependency generation. Informational only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Set, Tuple

from orchestration.plan.modes import PlanMode
from orchestration.plan.types import (
    PlanConfidence,
    PlanResult,
    PlanStepItem,
    PlanStepKind,
)

MAX_DEPENDENCIES = 8
MAX_DEP_REASON_CHARS = 120
MAX_ISSUES = 6
MAX_ISSUE_CHARS = 160
MAX_NEXT_TITLE = 80
MAX_NEXT_RATIONALE = 200


class PlanIssueKind(str, Enum):
    INCOMPLETE = "INCOMPLETE"
    AMBIGUOUS = "AMBIGUOUS"
    CONTRADICTORY = "CONTRADICTORY"
    MISSING_PREREQ = "MISSING_PREREQ"
    DUPLICATE = "DUPLICATE"
    BAD_ORDER = "BAD_ORDER"
    EXCESSIVE_SCOPE = "EXCESSIVE_SCOPE"
    INSUFFICIENT_INFO = "INSUFFICIENT_INFO"


class ValidationStatus(str, Enum):
    OK = "OK"
    NEEDS_CLARIFY = "NEEDS_CLARIFY"
    WEAK = "WEAK"
    INVALID = "INVALID"


@dataclass(frozen=True)
class PlanDependency:
    from_index: int
    to_index: int
    reason: str = ""


@dataclass(frozen=True)
class PlanIssue:
    kind: PlanIssueKind
    message: str = ""
    step_index: int = 0


@dataclass(frozen=True)
class PlanAnalysis:
    mode: PlanMode = PlanMode.CREATE
    dependencies: Tuple[PlanDependency, ...] = ()
    issues: Tuple[PlanIssue, ...] = ()
    next_step_index: int = 0
    next_step_title: str = ""
    next_rationale: str = ""
    priority_order: Tuple[int, ...] = ()
    validation_status: ValidationStatus = ValidationStatus.OK
    confidence: PlanConfidence = PlanConfidence.LOW


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _would_cycle(edges: List[Tuple[int, int]], frm: int, to: int) -> bool:
    """True if adding frm→to (to depends on frm) creates a cycle."""
    graph: dict = {}
    for a, b in edges:
        graph.setdefault(a, []).append(b)
    graph.setdefault(frm, []).append(to)
    # DFS from `to` looking for `frm` following dependency direction (dependee → depender).
    seen: Set[int] = set()
    stack = [to]
    while stack:
        n = stack.pop()
        if n == frm:
            return True
        if n in seen:
            continue
        seen.add(n)
        stack.extend(graph.get(n, []))
    return False


def _title_has(step: PlanStepItem, *needles: str) -> bool:
    blob = _norm(step.title + " " + step.detail)
    return any(n in blob for n in needles)


def analyze_dependencies(
    result: PlanResult,
    *,
    extra_issues: Tuple[PlanIssue, ...] = (),
) -> Tuple[Tuple[PlanDependency, ...], List[PlanIssue]]:
    """Informational edges: to_index depends on from_index. Max 8. No cycles."""
    steps = list(result.steps or ())
    if not steps:
        return (), list(extra_issues)

    edges: List[Tuple[int, int]] = []
    deps: List[PlanDependency] = []
    issues: List[PlanIssue] = list(extra_issues)

    def try_add(frm: int, to: int, reason: str) -> None:
        if len(deps) >= MAX_DEPENDENCIES:
            return
        if frm == to or frm < 1 or to < 1:
            return
        if any(d.from_index == frm and d.to_index == to for d in deps):
            return
        if _would_cycle(edges, frm, to):
            if len(issues) < MAX_ISSUES:
                issues.append(
                    PlanIssue(
                        kind=PlanIssueKind.BAD_ORDER,
                        message="Skipped a dependency that would create a cycle.",
                        step_index=to,
                    )
                )
            return
        edges.append((frm, to))
        deps.append(
            PlanDependency(
                from_index=frm,
                to_index=to,
                reason=reason[:MAX_DEP_REASON_CHARS],
            )
        )

    # Adjacent kind chain PREPARE/DECIDE → CHECK → VERIFY
    for i in range(len(steps) - 1):
        a, b = steps[i], steps[i + 1]
        if a.kind in (PlanStepKind.PREPARE, PlanStepKind.DECIDE, PlanStepKind.CHECK) and b.kind in (
            PlanStepKind.CHECK,
            PlanStepKind.VERIFY,
            PlanStepKind.PREPARE,
            PlanStepKind.DECIDE,
        ):
            if a.kind != PlanStepKind.OPTIONAL and b.kind != PlanStepKind.OPTIONAL:
                try_add(
                    a.index,
                    b.index,
                    "Later work builds on the earlier preparation step.",
                )

    # Lexical: demo depends on customer/problem/scope
    by_idx = {s.index: s for s in steps}
    demo_idxs = [s.index for s in steps if _title_has(s, "demo")]
    prereq_demo = [
        s.index
        for s in steps
        if _title_has(s, "customer", "problem", "scope", "target")
    ]
    for d in demo_idxs:
        for p in prereq_demo:
            if p < d:
                try_add(p, d, "A focused demo needs a defined customer or problem first.")

    contact_idxs = [
        s.index
        for s in steps
        if _title_has(s, "contact", "prospect", "outreach")
    ]
    prereq_contact = [
        s.index
        for s in steps
        if _title_has(s, "demo", "pricing", "qualif")
    ]
    for c in contact_idxs:
        for p in prereq_contact:
            if p < c:
                try_add(p, c, "Outreach works better after demo or qualification prep.")

    # Implement depends on clarify/scope
    for s in steps:
        if _title_has(s, "implement", "build", "create"):
            for p in steps:
                if p.index < s.index and _title_has(p, "scope", "clarify", "smallest", "define"):
                    try_add(p.index, s.index, "Implementation follows a clarified scope.")

    _ = by_idx  # reserved for future lexical maps
    return tuple(deps[:MAX_DEPENDENCIES]), issues[:MAX_ISSUES]
