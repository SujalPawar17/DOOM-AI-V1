"""V8.25 pure deterministic state machine for adaptive goal continuity.

No database. No global mutable state. Pure functional transitions.
"""

from __future__ import annotations

from typing import Any, List, Sequence, Set, Tuple

from orchestration.plan.continuity.types import (
    MAX_CONTINUITY_STEPS,
    MAX_STALENESS_REASON_CHARS,
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


def _parse_dependencies(deps: Sequence[Any]) -> List[Tuple[int, int]]:
    """Normalize dependencies into a list of (from_index, to_index) tuples."""
    out: List[Tuple[int, int]] = []
    for d in deps or ():
        if hasattr(d, "from_index") and hasattr(d, "to_index"):
            out.append((int(d.from_index), int(d.to_index)))
        elif isinstance(d, (tuple, list)) and len(d) >= 2:
            out.append((int(d[0]), int(d[1])))
    return out


def recompute_active_step(
    continuity_state: PlanContinuityState,
    dependencies: Sequence[Any] = (),
) -> int:
    """Determine the next eligible step index (1-indexed) or 0 if none.

    Rules:
    - Ignore COMPLETED
    - Ignore SKIPPED
    - Avoid BLOCKED steps
    - Disqualify steps whose prerequisites are not COMPLETED (or are BLOCKED)
    - Prefer smallest eligible 1-indexed step
    - Zero execution or mutation side-effects
    """
    records = continuity_state.step_records or ()
    if not records:
        return 0

    deps = _parse_dependencies(dependencies)
    by_idx = {r.index: r for r in records}
    blocked_indices: Set[int] = {
        r.index for r in records if r.state is StepState.BLOCKED
    }

    # Inbound prerequisites for each step: step -> set of prerequisite steps
    prereqs: dict[int, Set[int]] = {r.index: set() for r in records}
    for frm, to in deps:
        if to in prereqs:
            prereqs[to].add(frm)

    for r in records:
        # Ignore completed, skipped, or blocked
        if r.state in (StepState.COMPLETED, StepState.SKIPPED, StepState.BLOCKED):
            continue

        # Check all prerequisites
        reqs = prereqs.get(r.index, set())
        if any(p in blocked_indices for p in reqs):
            # Prerequisite is blocked -> cannot proceed with this step
            continue

        all_met = True
        for p in reqs:
            p_rec = by_idx.get(p)
            if p_rec is None or p_rec.state is not StepState.COMPLETED:
                all_met = False
                break

        if all_met:
            return r.index

    return 0


def apply_progress_event(
    continuity_state: PlanContinuityState,
    event: ProgressEvent,
    dependencies: Sequence[Any] = (),
) -> ContinuityAnalysis:
    """Pure state transition mapping (State, Event) -> ContinuityAnalysis.

    Guarantees:
    - Input state is never mutated
    - Idempotent completions and skips
    - Fail-closed on ambiguity or out-of-bounds indices
    """
    records = list(continuity_state.step_records or ())
    tot = len(records)

    # 1. Guard against empty plan state
    if tot == 0:
        return ContinuityAnalysis(
            continuity_state=continuity_state,
            detected_event=event,
            clarification_needed="No active plan steps found.",
            suggested_action=SUGGEST_CLARIFY,
        )

    # 2. Handle ambiguous events
    if event.is_ambiguous:
        return ContinuityAnalysis(
            continuity_state=continuity_state,
            detected_event=event,
            clarification_needed="Which step did you mean? Please name the step number or title.",
            suggested_action=SUGGEST_CLARIFY,
        )

    # 3. Handle Goal Pivot
    if event.kind is ProgressEventKind.GOAL_PIVOT:
        reason = f"Goal pivoted: {event.raw_evidence}"[:MAX_STALENESS_REASON_CHARS]
        new_state = PlanContinuityState(
            goal_title=continuity_state.goal_title,
            goal_state=GoalState.STALE,
            step_records=continuity_state.step_records,
            active_step_index=0,
            completed_count=continuity_state.completed_count,
            total_count=tot,
            staleness_reason=reason,
        )
        return ContinuityAnalysis(
            continuity_state=new_state,
            detected_event=event,
            clarification_needed="",
            suggested_action=SUGGEST_REPLAN,
        )

    # 4. Handle Goal Reset
    if event.kind is ProgressEventKind.GOAL_RESET:
        new_state = PlanContinuityState(
            goal_title=continuity_state.goal_title,
            goal_state=GoalState.ABANDONED,
            step_records=continuity_state.step_records,
            active_step_index=0,
            completed_count=continuity_state.completed_count,
            total_count=tot,
            staleness_reason="Goal reset by user.",
        )
        return ContinuityAnalysis(
            continuity_state=new_state,
            detected_event=event,
            clarification_needed="",
            suggested_action=SUGGEST_REPLAN,
        )

    # 5. Handle NOOP
    if event.kind is ProgressEventKind.NOOP:
        return ContinuityAnalysis(
            continuity_state=continuity_state,
            detected_event=event,
            clarification_needed="",
            suggested_action=SUGGEST_PROCEED,
        )

    # 6. Validate target step index bounds for step-level events
    target_idx = event.target_step_index
    if target_idx < 1 or target_idx > tot:
        return ContinuityAnalysis(
            continuity_state=continuity_state,
            detected_event=event,
            clarification_needed=f"That plan only has {tot} steps. Which step did you mean?",
            suggested_action=SUGGEST_CLARIFY,
        )

    # Find target record index in list (0-based)
    target_pos = target_idx - 1
    current_rec = records[target_pos]

    # 7. Apply Step Transitions
    if event.kind is ProgressEventKind.STEP_COMPLETED:
        if current_rec.state is StepState.COMPLETED:
            # Idempotent completion
            new_rec = current_rec
        else:
            new_rec = StepStateRecord(
                index=target_idx,
                state=StepState.COMPLETED,
                detail=current_rec.detail,
            )
        records[target_pos] = new_rec

    elif event.kind is ProgressEventKind.STEP_BLOCKED:
        if current_rec.state in (StepState.COMPLETED, StepState.SKIPPED):
            # Do not mutate completed or skipped steps into blocked
            new_rec = current_rec
        else:
            new_rec = StepStateRecord(
                index=target_idx,
                state=StepState.BLOCKED,
                detail=event.raw_evidence or current_rec.detail,
            )
        records[target_pos] = new_rec

    elif event.kind is ProgressEventKind.STEP_SKIPPED:
        if current_rec.state is StepState.COMPLETED:
            # Do not overwrite completion with skip
            new_rec = current_rec
        else:
            new_rec = StepStateRecord(
                index=target_idx,
                state=StepState.SKIPPED,
                detail=current_rec.detail,
            )
        records[target_pos] = new_rec

    elif event.kind is ProgressEventKind.STEP_STARTED:
        if current_rec.state in (StepState.COMPLETED, StepState.SKIPPED):
            new_rec = current_rec
        else:
            new_rec = StepStateRecord(
                index=target_idx,
                state=StepState.IN_PROGRESS,
                detail=current_rec.detail,
            )
        records[target_pos] = new_rec

    # Build intermediate state for active step recomputation
    new_step_records = tuple(records)
    comp_count = sum(1 for r in new_step_records if r.state is StepState.COMPLETED)
    intermediate = PlanContinuityState(
        goal_title=continuity_state.goal_title,
        goal_state=continuity_state.goal_state,
        step_records=new_step_records,
        active_step_index=continuity_state.active_step_index,
        completed_count=comp_count,
        total_count=tot,
        staleness_reason=continuity_state.staleness_reason,
    )

    # Recompute next active step
    next_active = recompute_active_step(intermediate, dependencies=dependencies)

    # Determine resulting goal state and suggested action
    all_terminal = all(
        r.state in (StepState.COMPLETED, StepState.SKIPPED) for r in new_step_records
    )
    if all_terminal:
        res_goal_state = GoalState.COMPLETED
        next_active = 0
        action = SUGGEST_PROCEED
    elif next_active == 0:
        # Steps remain, but none are eligible (e.g. blockers halt all paths)
        res_goal_state = GoalState.BLOCKED
        action = SUGGEST_RESOLVE_BLOCKER
    else:
        res_goal_state = GoalState.ACTIVE
        action = SUGGEST_PROCEED

    final_state = PlanContinuityState(
        goal_title=continuity_state.goal_title,
        goal_state=res_goal_state,
        step_records=new_step_records,
        active_step_index=next_active,
        completed_count=comp_count,
        total_count=tot,
        staleness_reason=continuity_state.staleness_reason,
    )

    return ContinuityAnalysis(
        continuity_state=final_state,
        detected_event=event,
        clarification_needed="",
        suggested_action=action,
    )
