"""V8.25 Phase 3 adaptive goal continuity engine integration.

Deterministic, bounded, informational only. Consumes Phase 1 state/detect
and Phase 2 durable/parser contracts. Zero execution authority.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence, Tuple

from orchestration.plan.continuity.detect import detect_progress_event
from orchestration.plan.continuity.state import apply_progress_event, recompute_active_step
from orchestration.plan.continuity.types import (
    ContinuityAnalysis,
    GoalState,
    PlanContinuityState,
    ProgressEventKind,
    StepState,
    StepStateRecord,
    SUGGEST_CLARIFY,
    SUGGEST_REPLAN,
    SUGGEST_RESOLVE_BLOCKER,
)
from orchestration.plan.modes import PlanMode, detect_plan_mode
from orchestration.plan.parse import ParseOutcome
from orchestration.plan.relevance import plan_continuation
from orchestration.plan.types import PlanResult, PlanStepItem

_NATURAL_CONTINUATION = re.compile(
    r"(?i)^(?:let'?s\s+)?(?:continue|go on|keep going|carry on)[\s?.!]*$"
)
_GENERIC_NEXT = re.compile(
    r"(?i)^(what next|what should i do next|how do i proceed)[\s?.!]*$"
)
_EXPLICIT_CREATE = re.compile(
    r"(?i)^(make me a plan|make a plan|create a plan|plan how to|plan for)\b"
)
_EXPLICIT_RESUME = re.compile(
    r"(?i)^(?:"
    r"(?:please\s+)?resume(?:\s+(?:my|the|that)\s+plan)?|"
    r"continue(?:\s+(?:my|the|that)\s+plan)?|"
    r"where was i|"
    r"what(?:'s| is) left|"
    r"what remains"
    r")[\s?.!]*$"
)


def is_natural_continuation(query: str) -> bool:
    """True for bare continuation phrases that may resume an active plan."""
    q = " ".join(str(query or "").strip().split())
    if not q:
        return False
    return bool(_NATURAL_CONTINUATION.match(q))


def is_explicit_resume(query: str) -> bool:
    """True for clear cross-session resume / where-was-I phrasing."""
    q = " ".join(str(query or "").strip().split())
    if not q:
        return False
    if is_natural_continuation(q):
        return True
    return bool(_EXPLICIT_RESUME.match(q))


def needs_continuity_anchor(query: str) -> bool:
    """True when resolving an anchor plan may be required for continuity."""
    q = " ".join(str(query or "").strip().split())
    if not q:
        return False
    if plan_continuation(q):
        return True
    if _GENERIC_NEXT.match(q):
        return True
    if is_natural_continuation(q) or is_explicit_resume(q):
        return True
    event = detect_progress_event(q)
    return event.kind is not ProgressEventKind.NOOP


def has_continuity_anchor(owner_id: str, session_id: str) -> bool:
    """True when a parseable durable plan anchor exists for this session."""
    owner = str(owner_id or "").strip()
    session = str(session_id or "").strip()
    if not owner or not session:
        return False
    try:
        from orchestration.conversation.thread import (
            current_anchor_turn,
            get_conversation_thread,
        )

        thread = get_conversation_thread(owner, session, for_continuation=True)
        if not thread:
            return False
        anchor = current_anchor_turn(thread)
        return bool(anchor and str(anchor.assistant_text or "").strip())
    except Exception:
        return False


def has_registry_goal_anchor(owner_id: str) -> bool:
    """True when an ACTIVE registry goal is recoverable for the trusted owner."""
    owner = str(owner_id or "").strip()
    if not owner:
        return False
    try:
        from orchestration.plan.goal_registry import (
            RegistryStatus,
            is_goal_registry_sync_enabled,
            recover_active_goal_for_owner,
        )

        if not is_goal_registry_sync_enabled():
            return False
        res = recover_active_goal_for_owner(owner)
        return res.status is RegistryStatus.OK and res.snapshot is not None
    except Exception:
        return False


def has_continuity_context(owner_id: str, session_id: str) -> bool:
    """Conversation anchor first; ACTIVE registry is fallback only."""
    if has_continuity_anchor(owner_id, session_id):
        return True
    return has_registry_goal_anchor(owner_id)


def should_route_to_plan_with_anchor(
    query: str,
    owner_id: str,
    session_id: str,
) -> bool:
    """Route progress utterances to PLAN only when a continuity source exists."""
    if not needs_continuity_anchor(query):
        return False
    return has_continuity_context(owner_id, session_id)


def should_route_natural_continuation_to_plan(
    query: str,
    owner_id: str,
    session_id: str,
) -> bool:
    """Route natural continuation / explicit resume to PLAN when a source exists."""
    if not (is_natural_continuation(query) or is_explicit_resume(query)):
        return False
    return has_continuity_context(owner_id, session_id)


def normalize_plan_mode_for_continuity(
    query: str,
    mode: PlanMode,
    *,
    had_prior: bool,
) -> PlanMode:
    """Map generic continuation phrasing to NEXT when an active plan exists."""
    if not had_prior:
        return mode
    q = " ".join(str(query or "").strip().split())
    if mode is not PlanMode.CREATE:
        return mode
    if is_natural_continuation(q) or is_explicit_resume(q) or _GENERIC_NEXT.match(q):
        return PlanMode.NEXT
    return mode


def is_explicit_plan_create(query: str) -> bool:
    """True for utterances that establish a new plan rather than progress."""
    q = " ".join(str(query or "").strip().split())
    if not q:
        return False
    if _EXPLICIT_CREATE.search(q):
        return True
    if detect_plan_mode(q) is not PlanMode.CREATE:
        return False
    return bool(re.search(r"(?i)\bplan\b", q) and len(q) >= 20)


def rebuild_continuity_preserving_states(
    prior_continuity: Optional[PlanContinuityState],
    result: PlanResult,
    *,
    dependencies: Sequence = (),
) -> PlanContinuityState:
    """Rebuild continuity for an updated plan while preserving states by step index."""
    old_states: Tuple[StepState, ...] = ()
    if prior_continuity is not None:
        old_states = step_states_from_continuity(prior_continuity)
    return build_continuity_state(result, old_states, dependencies=dependencies)


def build_continuity_state(
    result: PlanResult,
    step_states: Sequence[StepState] = (),
    *,
    dependencies: Sequence = (),
) -> PlanContinuityState:
    """Initialize continuity from a PlanResult and optional parsed step states."""
    steps = list(result.steps or ())
    records: list[StepStateRecord] = []
    for i, step in enumerate(steps, start=1):
        st = StepState.PENDING
        if i - 1 < len(step_states):
            st = step_states[i - 1]
        records.append(
            StepStateRecord(
                index=i,
                state=st,
                detail=(step.title or "")[:80],
            )
        )
    provisional = PlanContinuityState(
        goal_title=(result.title or "Plan")[:80],
        goal_state=GoalState.ACTIVE,
        step_records=tuple(records),
        active_step_index=1,
    )
    active = recompute_active_step(provisional, dependencies=dependencies)
    goal_state = GoalState.ACTIVE
    if active == 0 and records:
        if all(r.state in (StepState.COMPLETED, StepState.SKIPPED) for r in records):
            goal_state = GoalState.COMPLETED
        elif any(r.state is StepState.BLOCKED for r in records):
            goal_state = GoalState.BLOCKED
    return PlanContinuityState(
        goal_title=provisional.goal_title,
        goal_state=goal_state,
        step_records=provisional.step_records,
        active_step_index=active,
    )


def plan_result_from_registry_snapshot(snapshot: Any) -> Optional[PlanResult]:
    """Build a bounded PlanResult from a validated registry snapshot (no history)."""
    if snapshot is None:
        return None
    try:
        from orchestration.plan.types import (
            PlanConfidence,
            PlanStatus,
            PlanStepItem,
            PlanStepKind,
        )

        titles = tuple(str(t or "")[:80] for t in (getattr(snapshot, "step_titles", ()) or ()))
        if not titles:
            return None
        steps = tuple(
            PlanStepItem(i, title, "", PlanStepKind.PREPARE)
            for i, title in enumerate(titles, start=1)
        )
        plan_title = str(
            getattr(snapshot, "plan_title", "") or getattr(snapshot, "title", "") or "Plan"
        )[:80]
        blocker = str(getattr(snapshot, "blocker_summary", "") or "").strip()[:120]
        return PlanResult(
            status=PlanStatus.OK,
            title=plan_title,
            steps=steps,
            confidence=PlanConfidence.MEDIUM,
            blockers=(blocker,) if blocker else (),
        )
    except Exception:
        return None


def rehydrate_continuity_from_registry_snapshot(
    snapshot: Any,
    *,
    dependencies: Sequence = (),
) -> Optional[PlanContinuityState]:
    """Rehydrate PlanContinuityState from registry snapshot without mutating it.

    Registry remains cross-session persistence; returned state is runtime FSM input.
    """
    if snapshot is None:
        return None
    try:
        result = plan_result_from_registry_snapshot(snapshot)
        if result is None:
            return None
        raw_states = tuple(getattr(snapshot, "step_states", ()) or ())
        states: list[StepState] = []
        for i, _step in enumerate(result.steps):
            if i < len(raw_states):
                val = getattr(raw_states[i], "value", raw_states[i])
                states.append(StepState(str(val)))
            else:
                states.append(StepState.PENDING)

        records: list[StepStateRecord] = []
        blocker = str(getattr(snapshot, "blocker_summary", "") or "").strip()[:80]
        for i, step in enumerate(result.steps, start=1):
            st = states[i - 1]
            detail = blocker if st is StepState.BLOCKED and blocker else (step.title or "")[:80]
            records.append(StepStateRecord(index=i, state=st, detail=detail))

        goal_title = str(
            getattr(snapshot, "title", "") or getattr(snapshot, "plan_title", "") or result.title
        )[:80]
        provisional = PlanContinuityState(
            goal_title=goal_title,
            goal_state=GoalState.ACTIVE,
            step_records=tuple(records),
            active_step_index=int(getattr(snapshot, "active_step_index", 1) or 1),
        )
        recomputed = recompute_active_step(provisional, dependencies=dependencies)
        preferred = int(getattr(snapshot, "active_step_index", 0) or 0)
        active = recomputed
        if 1 <= preferred <= len(records):
            pref_state = records[preferred - 1].state
            if pref_state not in (
                StepState.COMPLETED,
                StepState.SKIPPED,
                StepState.BLOCKED,
            ):
                active = preferred

        goal_state = GoalState.ACTIVE
        if active == 0 and records:
            if all(r.state in (StepState.COMPLETED, StepState.SKIPPED) for r in records):
                goal_state = GoalState.COMPLETED
            elif any(r.state is StepState.BLOCKED for r in records):
                goal_state = GoalState.BLOCKED

        stale_reason = str(getattr(snapshot, "staleness_reason", "") or "").strip()[:120]
        return PlanContinuityState(
            goal_title=goal_title,
            goal_state=goal_state,
            step_records=tuple(records),
            active_step_index=active,
            staleness_reason=stale_reason,
        )
    except Exception:
        return None


def recover_continuity_from_parse(
    parsed: ParseOutcome,
    *,
    dependencies: Sequence = (),
) -> Optional[PlanContinuityState]:
    """Build continuity state from a parsed durable plan anchor."""
    if not parsed.ok or not parsed.result:
        return None
    return build_continuity_state(
        parsed.result,
        parsed.step_states,
        dependencies=dependencies,
    )


def step_states_from_continuity(
    continuity: Optional[PlanContinuityState],
) -> Tuple[StepState, ...]:
    if continuity is None:
        return ()
    return tuple(r.state for r in continuity.step_records)


def step_titles_from_result(result: PlanResult) -> Tuple[Tuple[int, str], ...]:
    return tuple((s.index, s.title or "") for s in (result.steps or ()))


def is_plan_mode_operation(mode: PlanMode) -> bool:
    return mode in (
        PlanMode.REFINE,
        PlanMode.NEXT,
        PlanMode.VALIDATE,
        PlanMode.DEPEND,
    )


def should_handle_as_progress(
    user_text: str,
    *,
    mode: PlanMode,
    had_prior: bool,
    continuity: Optional[PlanContinuityState],
) -> bool:
    """True when utterance should mutate continuity rather than plan modes."""
    if not had_prior or continuity is None:
        return False
    if is_plan_mode_operation(mode):
        return False
    if is_explicit_plan_create(user_text):
        return False
    if continuity.goal_state in (GoalState.COMPLETED, GoalState.ABANDONED):
        return False
    event = detect_progress_event(
        user_text,
        continuity,
        step_titles=step_titles_from_continuity(continuity),
    )
    return event.kind is not ProgressEventKind.NOOP


def step_titles_from_continuity(
    continuity: PlanContinuityState,
) -> Tuple[Tuple[int, str], ...]:
    return tuple((r.index, r.detail or "") for r in continuity.step_records)


def process_progress_update(
    user_text: str,
    continuity: PlanContinuityState,
    *,
    dependencies: Sequence = (),
) -> ContinuityAnalysis:
    """Detect and apply a progress event against existing continuity."""
    event = detect_progress_event(
        user_text,
        continuity,
        step_titles=step_titles_from_continuity(continuity),
    )
    return apply_progress_event(continuity, event, dependencies=dependencies)


def continuity_blockers(continuity: PlanContinuityState) -> Tuple[str, ...]:
    """Return human-readable blocker labels from continuity state."""
    out: list[str] = []
    for rec in continuity.step_records:
        if rec.state is StepState.BLOCKED:
            label = rec.detail or f"Step {rec.index}"
            out.append(f"Step {rec.index}: {label} is blocked")
    return tuple(out[:6])


def is_stale_or_replan(continuity: Optional[PlanContinuityState]) -> bool:
    if continuity is None:
        return False
    return continuity.goal_state in (GoalState.STALE, GoalState.ABANDONED)


def next_eligible_step_index(
    continuity: PlanContinuityState,
    *,
    dependencies: Sequence = (),
) -> int:
    return recompute_active_step(continuity, dependencies=dependencies)


def step_title_for_index(result: PlanResult, index: int) -> str:
    for step in result.steps or ():
        if step.index == index:
            return step.title or ""
    return ""


def apply_continuity_to_plan_result(
    result: PlanResult,
    continuity: PlanContinuityState,
) -> PlanResult:
    """Attach continuity-derived blockers without mutating step titles."""
    blockers = continuity_blockers(continuity)
    if not blockers:
        return result
    merged = tuple(dict.fromkeys(tuple(result.blockers or ()) + blockers))[:6]
    return PlanResult(
        status=result.status,
        title=result.title,
        steps=result.steps,
        reasons=result.reasons,
        blockers=merged,
        confidence=result.confidence,
        assumptions=result.assumptions,
        clarification=result.clarification,
    )


def format_progress_clarify(analysis: ContinuityAnalysis) -> str:
    return (analysis.clarification_needed or "Which step did you mean?")[:2048]


def format_replan_notice(analysis: ContinuityAnalysis) -> str:
    reason = analysis.continuity_state.staleness_reason or analysis.detected_event.raw_evidence
    topic = reason.replace("Goal pivoted:", "").strip()
    if topic:
        return (
            f"Your active goal may have changed to: {topic}. "
            "If you want to replan for that goal, ask me for a new plan explicitly."
        )[:2048]
    return (
        "Your active goal may have changed. "
        "If you want to replan, ask me for a new plan explicitly."
    )[:2048]


def format_progress_acknowledgement(
    analysis: ContinuityAnalysis,
    result: PlanResult,
    *,
    dependencies: Sequence = (),
) -> str:
    """Informational response after a progress state transition."""
    cont = analysis.continuity_state
    event = analysis.detected_event

    if cont.goal_state is GoalState.COMPLETED:
        return (
            f"Goal complete: {cont.goal_title}. "
            "All required steps are finished or skipped."
        )[:2048]

    if analysis.suggested_action == SUGGEST_RESOLVE_BLOCKER:
        blockers = continuity_blockers(cont)
        lines = ["Progress noted.", ""]
        if blockers:
            lines.append("Blockers:")
            for b in blockers:
                lines.append(f"- {b}")
        else:
            lines.append("Blockers:")
            lines.append("- None")
        active = next_eligible_step_index(cont, dependencies=dependencies)
        if active > 0:
            title = step_title_for_index(result, active)
            lines.append("")
            lines.append(f"Next eligible step: {active}. {title}")
        return "\n".join(lines).strip()[:2048]

    if event.kind is ProgressEventKind.STEP_COMPLETED:
        idx = event.target_step_index
        title = step_title_for_index(result, idx) if idx else ""
        lines = [f"Marked step {idx} complete: {title}".strip()]
    elif event.kind is ProgressEventKind.STEP_BLOCKED:
        idx = event.target_step_index
        title = step_title_for_index(result, idx) if idx else ""
        lines = [f"Noted blocker on step {idx}: {title}".strip()]
    elif event.kind is ProgressEventKind.STEP_SKIPPED:
        idx = event.target_step_index
        title = step_title_for_index(result, idx) if idx else ""
        lines = [f"Skipped step {idx}: {title}".strip()]
    elif event.kind is ProgressEventKind.STEP_STARTED:
        idx = event.target_step_index
        title = step_title_for_index(result, idx) if idx else ""
        lines = [f"In progress: step {idx}: {title}".strip()]
    else:
        lines = ["Progress noted."]

    active = next_eligible_step_index(cont, dependencies=dependencies)
    if active > 0:
        ntitle = step_title_for_index(result, active)
        lines.append("")
        lines.append(f"Next eligible step: {active}. {ntitle}")
    elif cont.goal_state is GoalState.BLOCKED:
        lines.append("")
        lines.append("No eligible next step until blockers are resolved.")

    return "\n".join(lines).strip()[:2048]
