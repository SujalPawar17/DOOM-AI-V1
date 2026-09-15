"""V8.23 deterministic plan synthesis. No LLM."""

from __future__ import annotations

import re
from typing import List, Tuple

from orchestration.plan.types import (
    MAX_ASSUMPTION_CHARS,
    MAX_ASSUMPTIONS,
    MAX_BLOCKER_CHARS,
    MAX_BLOCKERS,
    MAX_REASON_CHARS,
    MAX_REASONS,
    MAX_STEP_DETAIL_CHARS,
    MAX_STEP_TITLE_CHARS,
    MAX_STEPS,
    MAX_TITLE_CHARS,
    GoalClass,
    PlanConfidence,
    PlanInput,
    PlanResult,
    PlanStatus,
    PlanStepItem,
    PlanStepKind,
)

CLARIFY_GOAL = (
    "To build a useful plan, I need to know what you want to accomplish. "
    "Please describe the goal in a sentence or two."
)


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def classify_goal(inp: PlanInput) -> GoalClass:
    if inp.source_flags.decision_seed or extract_rec_hint(inp):
        return GoalClass.DECIDE_FOLLOWUP
    blob = _norm(inp.question + " " + inp.goal_summary)
    ram = any(
        str(v).upper() in ("ELEVATED", "HIGH")
        for k, v in inp.situation_factors
        if k == "RAM_PRESSURE"
    )
    cpu = any(
        str(v).upper() in ("ELEVATED", "HIGH")
        for k, v in inp.situation_factors
        if k == "CPU_LOAD"
    )
    if ram or cpu or re.search(r"\b(memory|ram|free memory|heavy ai)\b", blob):
        return GoalClass.RESOURCE
    if re.search(
        r"\b(implement|improve|build|add|create|integrate|develop|local stt|stt)\b",
        blob,
    ):
        return GoalClass.IMPLEMENT
    return GoalClass.GENERAL


def extract_rec_hint(inp: PlanInput) -> bool:
    return bool(re.search(r"(?i)\brecommendation:\b", inp.goal_summary))


def _step(
    index: int,
    title: str,
    detail: str,
    kind: PlanStepKind,
) -> PlanStepItem:
    return PlanStepItem(
        index=index,
        title=title[:MAX_STEP_TITLE_CHARS],
        detail=detail[:MAX_STEP_DETAIL_CHARS],
        kind=kind,
    )


def _templates(goal_class: GoalClass, goal: str) -> Tuple[str, List[PlanStepItem], List[str]]:
    g = goal[:80] if goal else "your goal"
    reasons: List[str] = []
    if goal_class is GoalClass.DECIDE_FOLLOWUP:
        title = f"Plan for: {goal[:100]}" if goal else "Follow-up plan"
        steps = [
            _step(1, "Verify the selected choice", "Confirm the recommendation still fits your constraints.", PlanStepKind.CHECK),
            _step(2, "Identify the smallest next step", "Pick one concrete action you can finish soon.", PlanStepKind.PREPARE),
            _step(3, "Implement the change", "Apply the smallest useful change toward the goal.", PlanStepKind.PREPARE),
            _step(4, "Verify the result", "Check that the outcome matches what you expected.", PlanStepKind.VERIFY),
            _step(5, "Document the outcome", "Note what worked for future reference.", PlanStepKind.OPTIONAL),
        ]
        reasons.append("Structured follow-up after a decision recommendation.")
        return title, steps, reasons
    if goal_class is GoalClass.RESOURCE:
        title = "Plan to reduce resource pressure"
        steps = [
            _step(1, "Close unused applications", "Free memory and CPU by closing apps you do not need.", PlanStepKind.PREPARE),
            _step(2, "Re-check system status", "Review current memory and CPU in Dashboard or system status.", PlanStepKind.CHECK),
            _step(3, "Confirm available resources", "Ensure pressure has dropped before heavy work.", PlanStepKind.VERIFY),
            _step(4, "Defer heavy work if needed", "Postpone heavy AI tasks until pressure is normal.", PlanStepKind.OPTIONAL),
        ]
        reasons.append("Resource pressure suggests preparing the system first.")
        return title, steps, reasons
    if goal_class is GoalClass.IMPLEMENT:
        title = f"Plan for: {g}"
        steps = [
            _step(1, "Clarify scope", "Define the smallest useful version of the change.", PlanStepKind.PREPARE),
            _step(2, "Identify the smallest change", "Choose one incremental step to implement first.", PlanStepKind.DECIDE),
            _step(3, "Implement it", "Make the change in a controlled, reversible way.", PlanStepKind.PREPARE),
            _step(4, "Test it", "Verify behavior with a focused check.", PlanStepKind.VERIFY),
            _step(5, "Review the result", "Decide whether to continue or adjust scope.", PlanStepKind.VERIFY),
        ]
        reasons.append("Implementation goals benefit from incremental steps.")
        return title, steps, reasons
    title = f"Plan for: {g}" if goal else "General plan"
    steps = [
        _step(1, "Clarify the outcome", "State what success looks like in one sentence.", PlanStepKind.PREPARE),
        _step(2, "List prerequisites", "Note anything you need before starting.", PlanStepKind.CHECK),
        _step(3, "Take the first small step", "Begin with the lowest-risk action.", PlanStepKind.PREPARE),
        _step(4, "Review progress", "Check whether you are closer to the outcome.", PlanStepKind.VERIFY),
    ]
    reasons.append("General goals use a simple prepare-check-act-review pattern.")
    return title, steps, reasons


def synthesize_plan(inp: PlanInput) -> PlanResult:
    goal = (inp.goal_summary or "").strip()
    if len(goal) < 8:
        return PlanResult(
            status=PlanStatus.CLARIFY,
            clarification=CLARIFY_GOAL,
            confidence=PlanConfidence.LOW,
        )
    goal_class = classify_goal(inp)
    title, steps, reasons = _templates(goal_class, goal)
    steps = tuple(steps[:MAX_STEPS])
    blockers: List[str] = []
    for c in inp.constraints:
        if "memory" in c.lower() or "cpu" in c.lower() or "pressure" in c.lower():
            blockers.append(c[:MAX_BLOCKER_CHARS])
    for f in inp.facts:
        if "pressure" in f.lower() or "unavailable" in f.lower():
            blockers.append(f[:MAX_BLOCKER_CHARS])
    blockers = list(dict.fromkeys(blockers))[:MAX_BLOCKERS]
    assumptions_list: List[str] = []
    if not inp.source_flags.system:
        assumptions_list.append("Current system measurements were unavailable.")
    if goal_class is GoalClass.DECIDE_FOLLOWUP and not inp.source_flags.decision_seed:
        assumptions_list.append("Decision recommendation context was inferred from the thread.")
    assumptions_list = assumptions_list[:MAX_ASSUMPTIONS]
    has_context = bool(inp.facts or inp.constraints or inp.preferences or inp.source_flags.decision_seed)
    if has_context and len(steps) >= 4:
        conf = PlanConfidence.HIGH
    elif len(steps) >= 3:
        conf = PlanConfidence.MEDIUM
    else:
        conf = PlanConfidence.LOW
    if blockers and conf is PlanConfidence.HIGH:
        reasons.append("System or resource watch-outs were included.")
    return PlanResult(
        status=PlanStatus.OK,
        title=title[:MAX_TITLE_CHARS],
        steps=steps,
        reasons=tuple(r[:MAX_REASON_CHARS] for r in reasons[:MAX_REASONS]),
        blockers=tuple(blockers),
        confidence=conf,
        assumptions=tuple(a[:MAX_ASSUMPTION_CHARS] for a in assumptions_list),
    )
