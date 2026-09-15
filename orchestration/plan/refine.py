"""V8.24 deterministic plan refinement. No LLM. Informational only."""

from __future__ import annotations

import re
from typing import List, Tuple

from orchestration.plan.types import (
    MAX_STEP_DETAIL_CHARS,
    MAX_STEP_TITLE_CHARS,
    MAX_STEPS,
    MAX_TITLE_CHARS,
    PlanResult,
    PlanStatus,
    PlanStepItem,
    PlanStepKind,
)

# Closed synonym map: vague title substring → (title, detail, kind)
_VAGUE_MAP: Tuple[Tuple[re.Pattern, str, str, PlanStepKind], ...] = (
    (
        re.compile(r"(?i)\bprepare product\b"),
        "Prepare a focused demo",
        "Build the smallest demo that shows the core value.",
        PlanStepKind.PREPARE,
    ),
    (
        re.compile(r"(?i)\bfind customers\b"),
        "Identify qualified prospects",
        "List prospects that match the target customer profile.",
        PlanStepKind.PREPARE,
    ),
    (
        re.compile(r"(?i)\bdo marketing\b"),
        "Prepare outreach materials",
        "Draft a short message and one-pager for prospects.",
        PlanStepKind.PREPARE,
    ),
    (
        re.compile(r"(?i)\bdefine target market\b"),
        "Define target customer",
        "Describe who benefits most from the product in one sentence.",
        PlanStepKind.PREPARE,
    ),
    (
        re.compile(r"(?i)\bcontact prospects\b"),
        "Contact qualified prospects",
        "Reach out with a clear ask and gather responses.",
        PlanStepKind.PREPARE,
    ),
    (
        re.compile(r"(?i)\breview feedback\b"),
        "Review prospect responses",
        "Note what resonated and what needs adjustment.",
        PlanStepKind.VERIFY,
    ),
    (
        re.compile(r"(?i)\bcreate (a )?demo\b"),
        "Prepare a focused demo",
        "Show one concrete problem and how the product solves it.",
        PlanStepKind.PREPARE,
    ),
)

_OVERSIZE = re.compile(
    r"(?i)\b(everything|all modules|entire (product|system)|whole (product|system))\b"
)


def _norm_title(title: str) -> str:
    t = re.sub(r"[^\w\s]", "", str(title or "").lower())
    return " ".join(t.split())


def _apply_vague(step: PlanStepItem, goal: str) -> PlanStepItem:
    title = step.title
    detail = step.detail
    kind = step.kind
    for pat, new_title, new_detail, new_kind in _VAGUE_MAP:
        if pat.search(title):
            # Prefer goal-aware detail when ERP/launch context present.
            g = (goal or "").lower()
            d = new_detail
            if "erp" in g and "demo" in new_title.lower():
                d = "Show one ERP workflow problem and the proposed fix."
            return PlanStepItem(
                index=step.index,
                title=new_title[:MAX_STEP_TITLE_CHARS],
                detail=d[:MAX_STEP_DETAIL_CHARS],
                kind=new_kind,
            )
    return step


def _dedupe(steps: List[PlanStepItem]) -> List[PlanStepItem]:
    seen = set()
    out: List[PlanStepItem] = []
    for s in steps:
        key = _norm_title(s.title)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _optional_last(steps: List[PlanStepItem]) -> List[PlanStepItem]:
    required = [s for s in steps if s.kind is not PlanStepKind.OPTIONAL]
    optional = [s for s in steps if s.kind is PlanStepKind.OPTIONAL]
    return required + optional


def _maybe_split(step: PlanStepItem, room: int) -> List[PlanStepItem]:
    if room < 1:
        return [step]
    if not _OVERSIZE.search(step.title + " " + step.detail):
        return [step]
    a = PlanStepItem(
        index=step.index,
        title=("Clarify scope of: " + step.title)[:MAX_STEP_TITLE_CHARS],
        detail="Define the smallest useful slice first.",
        kind=PlanStepKind.PREPARE,
    )
    b = PlanStepItem(
        index=step.index,
        title=("Deliver the first slice: " + step.title)[:MAX_STEP_TITLE_CHARS],
        detail="Implement only the clarified slice.",
        kind=PlanStepKind.PREPARE,
    )
    return [a, b]


def refine_plan(
    result: PlanResult,
    *,
    goal_summary: str = "",
    aggressive: bool = False,
) -> PlanResult:
    """Return a new PlanResult. Light refine for CREATE; fuller for REFINE."""
    if result.status is not PlanStatus.OK or not result.steps:
        return result

    steps = list(result.steps)
    if aggressive:
        steps = [_apply_vague(s, goal_summary) for s in steps]
        # At most one oversized split across the plan.
        split_done = False
        rebuilt: List[PlanStepItem] = []
        remaining_after = len(steps)
        for s in steps:
            remaining_after -= 1
            room = MAX_STEPS - (len(rebuilt) + remaining_after)
            if not split_done and room >= 1 and _OVERSIZE.search(s.title + " " + s.detail):
                parts = _maybe_split(s, room)
                if len(parts) > 1:
                    split_done = True
                rebuilt.extend(parts)
            else:
                rebuilt.append(s)
        steps = rebuilt

    steps = _dedupe(steps)
    steps = _optional_last(steps)
    steps = steps[:MAX_STEPS]
    steps = [
        PlanStepItem(
            index=i,
            title=s.title[:MAX_STEP_TITLE_CHARS],
            detail=s.detail[:MAX_STEP_DETAIL_CHARS],
            kind=s.kind,
        )
        for i, s in enumerate(steps, start=1)
    ]

    return PlanResult(
        status=result.status,
        title=(result.title or "")[:MAX_TITLE_CHARS],
        steps=tuple(steps),
        reasons=result.reasons,
        blockers=result.blockers,
        confidence=result.confidence,
        assumptions=result.assumptions,
        clarification=result.clarification,
    )
