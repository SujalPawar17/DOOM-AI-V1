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


# ---------------------------------------------------------------------------
# V8.26 Phase 4 — lifecycle UX (thin orchestration; ephemeral confirmation)
# ---------------------------------------------------------------------------

from enum import Enum


class LifecycleIntent(str, Enum):
    NONE = "NONE"
    CONFIRM_YES = "CONFIRM_YES"
    CONFIRM_NO = "CONFIRM_NO"
    CONFIRM_AMBIGUOUS = "CONFIRM_AMBIGUOUS"
    RESUME = "RESUME"
    RESUME_NAMED = "RESUME_NAMED"
    ABANDON = "ABANDON"
    REPLACE = "REPLACE"
    QUERY_PREVIOUS = "QUERY_PREVIOUS"
    QUERY_COMPLETED = "QUERY_COMPLETED"
    QUERY_ABANDONED = "QUERY_ABANDONED"
    RESTART = "RESTART"
    RESUME_COMPLETED = "RESUME_COMPLETED"
    RESUME_ABANDONED = "RESUME_ABANDONED"


_LIFECYCLE_YES = re.compile(
    r"(?i)^(yes|y|yeah|yep|confirm|do it|please do|ok|okay|sure)[\s?.!]*$"
)
_LIFECYCLE_NO = re.compile(
    r"(?i)^(no|n|nope|cancel|never mind|nevermind|don'?t|do not)[\s?.!]*$"
)
_LIFECYCLE_ABANDON = re.compile(
    r"(?i)^(?:please\s+)?(?:abandon|forget)\s+"
    r"(?:this|my|the|that)?\s*(?:current\s+)?(?:plan|goal)[\s?.!]*$"
)
_LIFECYCLE_REPLACE = re.compile(
    r"(?i)^(?:please\s+)?(?:replace\s+(?:this|my|the|that)\s+(?:plan|goal)|"
    r"start a new plan instead)[\s?.!]*$"
)
_LIFECYCLE_PREVIOUS = re.compile(
    r"(?i)^(?:what was my previous goal|show (?:me )?(?:my )?previous goal|"
    r"previous goal)[\s?.!]*$"
)
_LIFECYCLE_FINISHED = re.compile(
    r"(?i)^(?:what did i finish|what have i (?:finished|completed)|"
    r"show (?:me )?completed (?:plans|goals))[\s?.!]*$"
)
_LIFECYCLE_ABANDONED_Q = re.compile(
    r"(?i)^(?:show (?:me )?(?:what i )?abandoned|what did i abandon|"
    r"show abandoned (?:plans|goals))[\s?.!]*$"
)
_LIFECYCLE_RESUME_COMPLETED = re.compile(
    r"(?i)^(?:please\s+)?resume\s+(?:my\s+)?(?:the\s+)?completed\s+(?:plan|goal)[\s?.!]*$"
)
_LIFECYCLE_RESUME_ABANDONED = re.compile(
    r"(?i)^(?:please\s+)?resume\s+(?:my\s+)?(?:the\s+)?abandoned\s+(?:plan|goal)[\s?.!]*$"
)
_LIFECYCLE_RESTART = re.compile(
    r"(?i)^(?:please\s+)?(?:restart|start again|start over)\s+"
    r"(?:my\s+)?(?:the\s+)?(.+?)(?:\s+plan)?[\s?.!]*$"
    r"|^start that plan again[\s?.!]*$"
)
_LIFECYCLE_RESUME_NAMED = re.compile(
    r"(?i)^(?:please\s+)?resume\s+(?:the\s+)?(.+?)(?:\s+plan)?[\s?.!]*$"
)
_LIFECYCLE_RESUME_GENERIC = re.compile(
    r"(?i)^(?:please\s+)?resume(?:\s+(?:my|the|that)\s+plan)?[\s?.!]*$"
)


def _lifecycle_enabled() -> bool:
    try:
        from orchestration.plan.goal_registry import is_goal_registry_sync_enabled

        return bool(is_goal_registry_sync_enabled())
    except Exception:
        return False


def detect_lifecycle_intent(query: str) -> Tuple[LifecycleIntent, str]:
    """Bounded lifecycle intent. Returns (intent, optional title/topic)."""
    q = " ".join(str(query or "").strip().split())
    if not q:
        return LifecycleIntent.NONE, ""
    if _LIFECYCLE_YES.match(q):
        return LifecycleIntent.CONFIRM_YES, ""
    if _LIFECYCLE_NO.match(q):
        return LifecycleIntent.CONFIRM_NO, ""
    if _LIFECYCLE_RESUME_COMPLETED.match(q):
        return LifecycleIntent.RESUME_COMPLETED, ""
    if _LIFECYCLE_RESUME_ABANDONED.match(q):
        return LifecycleIntent.RESUME_ABANDONED, ""
    if _LIFECYCLE_ABANDON.match(q):
        return LifecycleIntent.ABANDON, ""
    if _LIFECYCLE_REPLACE.match(q):
        return LifecycleIntent.REPLACE, ""
    if _LIFECYCLE_PREVIOUS.match(q):
        return LifecycleIntent.QUERY_PREVIOUS, ""
    if _LIFECYCLE_FINISHED.match(q):
        return LifecycleIntent.QUERY_COMPLETED, ""
    if _LIFECYCLE_ABANDONED_Q.match(q):
        return LifecycleIntent.QUERY_ABANDONED, ""
    m = _LIFECYCLE_RESTART.match(q)
    if m:
        topic = (m.group(1) or "").strip() if m.lastindex else ""
        topic = re.sub(r"(?i)\bplan\b", "", topic).strip(" .!?")
        return LifecycleIntent.RESTART, topic[:80]
    # Named resume before generic so "Resume the Python plan" is not lost.
    m3 = _LIFECYCLE_RESUME_NAMED.match(q)
    if m3 and q.casefold().startswith("resume"):
        title = (m3.group(1) or "").strip()
        title = re.sub(r"(?i)^(my|the|that)\s+", "", title).strip()
        title = re.sub(r"(?i)\bplan\b", "", title).strip(" .!?")
        if title and title.casefold() not in (
            "my",
            "the",
            "that",
            "completed",
            "abandoned",
            "",
        ):
            return LifecycleIntent.RESUME_NAMED, title[:80]
    if _LIFECYCLE_RESUME_GENERIC.match(q):
        return LifecycleIntent.RESUME, ""
    if is_explicit_resume(q) and not is_natural_continuation(q):
        return LifecycleIntent.RESUME, ""
    if len(q) <= 24 and re.match(
        r"(?i)^(maybe|not sure|idk|i don't know)[\s?.!]*$", q
    ):
        return LifecycleIntent.CONFIRM_AMBIGUOUS, ""
    return LifecycleIntent.NONE, ""


def is_lifecycle_utterance(query: str) -> bool:
    intent, _ = detect_lifecycle_intent(query)
    return intent not in (
        LifecycleIntent.NONE,
        LifecycleIntent.CONFIRM_YES,
        LifecycleIntent.CONFIRM_NO,
        LifecycleIntent.CONFIRM_AMBIGUOUS,
    )


def should_route_lifecycle_to_plan(
    query: str,
    owner_id: str,
    session_id: str,
) -> bool:
    """Route lifecycle / pending-confirmation utterances to PLAN when flag on."""
    if not _lifecycle_enabled():
        return False
    owner = str(owner_id or "").strip()
    session = str(session_id or "").strip()
    if not owner or not session:
        return False
    try:
        from orchestration.plan.goal_registry import get_lifecycle_confirm

        pending = get_lifecycle_confirm(owner, session)
        intent, _ = detect_lifecycle_intent(query)
        if pending is not None and intent in (
            LifecycleIntent.CONFIRM_YES,
            LifecycleIntent.CONFIRM_NO,
            LifecycleIntent.CONFIRM_AMBIGUOUS,
        ):
            return True
        if is_lifecycle_utterance(query):
            return True
        if is_explicit_resume(query) or is_natural_continuation(query):
            if has_continuity_context(owner, session):
                return False
            from orchestration.plan.goal_registry import (
                GoalLifecycle,
                RegistryStatus,
                list_recent_goals,
            )

            st, snaps = list_recent_goals(
                owner, lifecycles=(GoalLifecycle.STALE,), limit=5
            )
            return st is RegistryStatus.OK and len(snaps) > 0
    except Exception:
        return False
    return False


def _safe_title(snap: Any) -> str:
    title = str(getattr(snap, "title", "") or getattr(snap, "plan_title", "") or "plan")
    title = title.strip()[:80] or "plan"
    try:
        from orchestration.plan.goal_registry import is_sensitive_goal_content

        if is_sensitive_goal_content(title):
            return "plan"
    except Exception:
        pass
    return title


def _format_history_lines(snaps: Sequence[Any], *, label: str) -> str:
    if not snaps:
        return f"No recent {label} goals found."[:2048]
    lines = [f"Recent {label} goals (up to 5):", ""]
    for i, snap in enumerate(snaps[:5], 1):
        lc = getattr(getattr(snap, "lifecycle", None), "value", "") or ""
        lines.append(f"{i}. {_safe_title(snap)} ({lc})")
    return "\n".join(lines).strip()[:2048]


def _match_title(snaps: Sequence[Any], title: str) -> Tuple[Any, ...]:
    needle = " ".join(str(title or "").strip().split()).casefold()
    if not needle:
        return ()
    hits = []
    for snap in snaps:
        t = " ".join(_safe_title(snap).split()).casefold()
        pt = " ".join(str(getattr(snap, "plan_title", "") or "").split()).casefold()
        pt_stripped = re.sub(r"(?i)^plan for:\s*", "", pt).strip()
        if needle in (t, pt, pt_stripped) or t == needle or pt_stripped == needle:
            hits.append(snap)
    return tuple(hits)


def _next_step_text_from_snapshot(snap: Any) -> str:
    prior = plan_result_from_registry_snapshot(snap)
    cont = rehydrate_continuity_from_registry_snapshot(snap)
    if prior is None or cont is None:
        return f"Resumed plan: {_safe_title(snap)}."[:2048]
    active = cont.active_step_index
    if active > 0:
        title = step_title_for_index(prior, active)
        return (
            f"Resumed plan: {_safe_title(snap)}.\n\n"
            f"Next step: {active}. {title}"
        ).strip()[:2048]
    return f"Resumed plan: {_safe_title(snap)}."[:2048]


def begin_pivot_replacement_confirm(
    owner_id: str,
    session_id: str,
    *,
    active_snap: Any,
    proposed_topic: str,
) -> str:
    """Start explicit abandon+replace confirmation for GOAL_PIVOT. No mutation yet."""
    from orchestration.plan.goal_registry import (
        CONFIRM_REPLACE_ACTIVE,
        make_lifecycle_confirm,
        set_lifecycle_confirm,
    )

    topic = " ".join(str(proposed_topic or "").strip().split())[:80]
    conf = make_lifecycle_confirm(
        owner_id=owner_id,
        session_id=session_id,
        action=CONFIRM_REPLACE_ACTIVE,
        goal_id=str(getattr(active_snap, "goal_id", "")),
        expected_version=int(getattr(active_snap, "version", 0)),
        title=_safe_title(active_snap),
        secondary_title=topic,
    )
    if conf is None:
        return (
            f"Your active goal may have changed to: {topic}. "
            "If you want to replan, ask me for a new plan explicitly."
        )[:2048]
    set_lifecycle_confirm(conf)
    return (
        f"\"{_safe_title(active_snap)}\" is currently active. "
        f"To start a new plan for \"{topic or 'the new goal'}\", "
        "I need to abandon the current goal first. Reply YES to abandon it, "
        "or NO to keep it."
    )[:2048]


def handle_lifecycle_request(
    owner_id: str,
    session_id: str,
    user_text: str,
) -> Optional[Tuple[str, Optional[Any], Optional[PlanContinuityState]]]:
    """Handle Phase 4 lifecycle UX.

    Returns (response_text, plan_result_or_None, continuity_or_None) when handled,
    or None when the normal V8.25/Phase 3 path should continue.
    """
    if not _lifecycle_enabled():
        return None
    owner = str(owner_id or "").strip()
    session = str(session_id or "").strip()
    if not owner or not session:
        return None

    from orchestration.plan.goal_registry import (
        CONFIRM_ABANDON_ACTIVE,
        CONFIRM_ABANDON_THEN_RESUME,
        CONFIRM_REPLACE_ACTIVE,
        CONFIRM_RESUME_STALE,
        GoalLifecycle,
        RegistryStatus,
        clear_lifecycle_confirm,
        get_active_goal,
        get_lifecycle_confirm,
        list_recent_goals,
        make_lifecycle_confirm,
        set_lifecycle_confirm,
    )

    intent, topic = detect_lifecycle_intent(user_text)
    pending = get_lifecycle_confirm(owner, session)

    if pending is not None and intent in (
        LifecycleIntent.CONFIRM_YES,
        LifecycleIntent.CONFIRM_NO,
        LifecycleIntent.CONFIRM_AMBIGUOUS,
    ):
        if intent is LifecycleIntent.CONFIRM_AMBIGUOUS:
            return (
                "Please reply YES to confirm or NO to cancel.",
                None,
                None,
            )
        if intent is LifecycleIntent.CONFIRM_NO:
            clear_lifecycle_confirm(owner, session)
            return ("Okay — cancelled. No goals were changed.", None, None)
        return _apply_lifecycle_confirm(owner, session, pending)

    if pending is not None and intent is LifecycleIntent.NONE:
        if len(" ".join(str(user_text or "").split())) <= 40:
            return (
                "I still need a clear YES or NO for the pending goal change.",
                None,
                None,
            )

    if intent is LifecycleIntent.QUERY_PREVIOUS:
        st, snaps = list_recent_goals(owner, limit=5)
        if st is not RegistryStatus.OK or not snaps:
            return ("I don't have a previous goal on record.", None, None)
        prev = snaps[0]
        return (
            (
                f"Your previous goal was \"{_safe_title(prev)}\" "
                f"({getattr(prev.lifecycle, 'value', '')})."
            )[:2048],
            None,
            None,
        )

    if intent is LifecycleIntent.QUERY_COMPLETED:
        st, snaps = list_recent_goals(
            owner, lifecycles=(GoalLifecycle.COMPLETED,), limit=5
        )
        return (
            _format_history_lines(
                snaps if st is RegistryStatus.OK else (), label="completed"
            ),
            None,
            None,
        )

    if intent is LifecycleIntent.QUERY_ABANDONED:
        st, snaps = list_recent_goals(
            owner, lifecycles=(GoalLifecycle.ABANDONED,), limit=5
        )
        return (
            _format_history_lines(
                snaps if st is RegistryStatus.OK else (), label="abandoned"
            ),
            None,
            None,
        )

    if intent is LifecycleIntent.RESUME_COMPLETED:
        return (
            "Completed goals cannot be resumed. "
            "Ask me to start a new plan for that topic instead.",
            None,
            None,
        )

    if intent is LifecycleIntent.RESUME_ABANDONED:
        return (
            "Abandoned goals cannot be reactivated. "
            "Ask me to start a new plan for that topic instead.",
            None,
            None,
        )

    if intent in (LifecycleIntent.ABANDON, LifecycleIntent.REPLACE):
        active = get_active_goal(owner)
        if active.status is not RegistryStatus.OK or active.snapshot is None:
            return ("There is no active plan to abandon.", None, None)
        snap = active.snapshot
        action = (
            CONFIRM_REPLACE_ACTIVE
            if intent is LifecycleIntent.REPLACE
            else CONFIRM_ABANDON_ACTIVE
        )
        conf = make_lifecycle_confirm(
            owner_id=owner,
            session_id=session,
            action=action,
            goal_id=snap.goal_id,
            expected_version=int(snap.version),
            title=_safe_title(snap),
        )
        if conf is None:
            return ("I couldn't start confirmation for that change.", None, None)
        set_lifecycle_confirm(conf)
        verb = "replace" if intent is LifecycleIntent.REPLACE else "abandon"
        return (
            (
                f"\"{_safe_title(snap)}\" is currently active. "
                f"Reply YES to {verb} it, or NO to keep it."
            ),
            None,
            None,
        )

    if intent is LifecycleIntent.RESTART:
        return _handle_restart(owner, session, topic)

    if intent in (LifecycleIntent.RESUME, LifecycleIntent.RESUME_NAMED):
        active = get_active_goal(owner)
        if (
            intent is LifecycleIntent.RESUME
            and active.status is RegistryStatus.OK
            and active.snapshot is not None
        ):
            return None
        st, stale_snaps = list_recent_goals(
            owner, lifecycles=(GoalLifecycle.STALE,), limit=5
        )
        if st is not RegistryStatus.OK:
            return ("I couldn't look up saved plans right now.", None, None)
        candidates: Tuple[Any, ...] = stale_snaps
        if intent is LifecycleIntent.RESUME_NAMED and topic:
            matched = _match_title(stale_snaps, topic)
            if len(matched) == 1:
                candidates = matched
            elif len(matched) > 1:
                return (
                    (
                        "Several stale plans match that title:\n"
                        + "\n".join(f"- {_safe_title(s)}" for s in matched[:5])
                        + "\nPlease name the exact plan to resume."
                    ),
                    None,
                    None,
                )
            else:
                return (
                    f"I couldn't find a stale plan matching \"{topic[:80]}\".",
                    None,
                    None,
                )
        else:
            if len(stale_snaps) == 0:
                return (
                    "There is no stale plan to resume. "
                    "Ask me to make a new plan if you want to start fresh.",
                    None,
                    None,
                )
            if len(stale_snaps) > 1:
                return (
                    (
                        "You have more than one stale plan:\n"
                        + "\n".join(f"- {_safe_title(s)}" for s in stale_snaps[:5])
                        + "\nName the plan to resume "
                        "(for example: Resume the Python plan)."
                    ),
                    None,
                    None,
                )
            candidates = (stale_snaps[0],)

        target = candidates[0]
        if active.status is RegistryStatus.OK and active.snapshot is not None:
            a = active.snapshot
            conf = make_lifecycle_confirm(
                owner_id=owner,
                session_id=session,
                action=CONFIRM_ABANDON_THEN_RESUME,
                goal_id=a.goal_id,
                expected_version=int(a.version),
                title=_safe_title(a),
                secondary_goal_id=target.goal_id,
                secondary_version=int(target.version),
                secondary_title=_safe_title(target),
            )
            if conf is None:
                return ("I couldn't start confirmation for that change.", None, None)
            set_lifecycle_confirm(conf)
            return (
                (
                    f"\"{_safe_title(a)}\" is currently active. "
                    f"To resume stale \"{_safe_title(target)}\", do you want to "
                    f"abandon \"{_safe_title(a)}\" first? Reply YES or NO."
                ),
                None,
                None,
            )
        conf = make_lifecycle_confirm(
            owner_id=owner,
            session_id=session,
            action=CONFIRM_RESUME_STALE,
            goal_id=target.goal_id,
            expected_version=int(target.version),
            title=_safe_title(target),
        )
        if conf is None:
            return ("I couldn't start confirmation for that change.", None, None)
        set_lifecycle_confirm(conf)
        return (
            f"Resume stale plan \"{_safe_title(target)}\"? Reply YES or NO.",
            None,
            None,
        )

    if (
        intent is LifecycleIntent.NONE
        and (is_explicit_resume(user_text) or is_natural_continuation(user_text))
        and not has_continuity_context(owner, session)
    ):
        return handle_lifecycle_request(owner, session, "Resume my plan")

    return None


def _apply_lifecycle_confirm(
    owner: str,
    session: str,
    pending: Any,
) -> Tuple[str, Optional[Any], Optional[PlanContinuityState]]:
    from orchestration.plan.goal_registry import (
        CONFIRM_ABANDON_ACTIVE,
        CONFIRM_ABANDON_THEN_RESUME,
        CONFIRM_REPLACE_ACTIVE,
        CONFIRM_RESUME_STALE,
        RegistryStatus,
        abandon_goal,
        clear_lifecycle_confirm,
        make_lifecycle_confirm,
        resume_stale_goal,
        set_lifecycle_confirm,
    )

    if pending.owner_id != owner or pending.session_id != session:
        clear_lifecycle_confirm(owner, session)
        return (
            "Confirmation does not match this session. Please request again.",
            None,
            None,
        )

    action = str(pending.action or "")

    if action == CONFIRM_RESUME_STALE:
        res = resume_stale_goal(owner, pending.goal_id, int(pending.expected_version))
        clear_lifecycle_confirm(owner, session)
        if res.status is RegistryStatus.CONFLICT:
            return (
                "That plan changed since confirmation. Please ask to resume again.",
                None,
                None,
            )
        if res.status is not RegistryStatus.OK or res.snapshot is None:
            return ("I couldn't resume that plan.", None, None)
        snap = res.snapshot
        prior = plan_result_from_registry_snapshot(snap)
        cont = rehydrate_continuity_from_registry_snapshot(snap)
        text = _next_step_text_from_snapshot(snap)
        return text, prior, cont

    if action in (CONFIRM_ABANDON_ACTIVE, CONFIRM_REPLACE_ACTIVE):
        res = abandon_goal(owner, pending.goal_id, int(pending.expected_version))
        clear_lifecycle_confirm(owner, session)
        if res.status is RegistryStatus.CONFLICT:
            return (
                "The active goal changed since confirmation. Please try again.",
                None,
                None,
            )
        if res.status is not RegistryStatus.OK:
            return ("I couldn't abandon that plan.", None, None)
        if action == CONFIRM_REPLACE_ACTIVE and pending.secondary_title:
            topic = pending.secondary_title
            return (
                (
                    f"Abandoned \"{pending.title or 'the active plan'}\". "
                    f"Ask me to make a plan for: {topic}"
                ),
                None,
                None,
            )
        return (f"Abandoned \"{pending.title or 'the active plan'}\".", None, None)

    if action == CONFIRM_ABANDON_THEN_RESUME:
        res = abandon_goal(owner, pending.goal_id, int(pending.expected_version))
        if res.status is RegistryStatus.CONFLICT:
            clear_lifecycle_confirm(owner, session)
            return (
                "The active goal changed since confirmation. Please try again.",
                None,
                None,
            )
        if res.status is not RegistryStatus.OK:
            clear_lifecycle_confirm(owner, session)
            return ("I couldn't abandon the active plan.", None, None)
        conf2 = make_lifecycle_confirm(
            owner_id=owner,
            session_id=session,
            action=CONFIRM_RESUME_STALE,
            goal_id=pending.secondary_goal_id,
            expected_version=int(pending.secondary_version),
            title=pending.secondary_title,
        )
        if conf2 is None:
            clear_lifecycle_confirm(owner, session)
            return (
                (
                    f"Abandoned \"{pending.title}\". "
                    "Ask me again to resume the stale plan."
                ),
                None,
                None,
            )
        set_lifecycle_confirm(conf2)
        return (
            (
                f"Abandoned \"{pending.title}\". "
                f"Resume stale \"{pending.secondary_title}\"? Reply YES or NO."
            ),
            None,
            None,
        )

    clear_lifecycle_confirm(owner, session)
    return ("That confirmation is no longer valid.", None, None)


def _handle_restart(
    owner: str,
    session: str,
    topic: str,
) -> Tuple[str, Optional[Any], Optional[PlanContinuityState]]:
    from orchestration.plan.goal_registry import (
        CONFIRM_REPLACE_ACTIVE,
        GoalLifecycle,
        RegistryStatus,
        get_active_goal,
        list_recent_goals,
        make_lifecycle_confirm,
        set_lifecycle_confirm,
    )

    topic_c = " ".join(str(topic or "").strip().split())[:80]
    st, snaps = list_recent_goals(
        owner,
        lifecycles=(GoalLifecycle.COMPLETED, GoalLifecycle.ABANDONED),
        limit=5,
    )
    source = None
    if st is RegistryStatus.OK and topic_c:
        matched = _match_title(snaps, topic_c)
        if len(matched) == 1:
            source = matched[0]
        elif len(matched) > 1:
            return (
                (
                    "Several past plans match that title:\n"
                    + "\n".join(f"- {_safe_title(s)}" for s in matched[:5])
                    + "\nName the exact plan to restart."
                ),
                None,
                None,
            )
    label = _safe_title(source) if source is not None else (topic_c or "that plan")
    active = get_active_goal(owner)
    if active.status is RegistryStatus.OK and active.snapshot is not None:
        a = active.snapshot
        conf = make_lifecycle_confirm(
            owner_id=owner,
            session_id=session,
            action=CONFIRM_REPLACE_ACTIVE,
            goal_id=a.goal_id,
            expected_version=int(a.version),
            title=_safe_title(a),
            secondary_title=label,
        )
        if conf is None:
            return ("I couldn't start confirmation for that change.", None, None)
        set_lifecycle_confirm(conf)
        return (
            (
                f"\"{_safe_title(a)}\" is currently active. "
                f"To restart \"{label}\" as a new plan, abandon the current goal "
                "first? Reply YES or NO."
            ),
            None,
            None,
        )
    # No ACTIVE: create a NEW goal_id (never revive COMPLETED/ABANDONED).
    if source is None and not topic_c:
        return (
            "Name the completed or abandoned plan to restart "
            "(for example: Restart my Python plan).",
            None,
            None,
        )
    try:
        from orchestration.plan.goal_registry import (
            StepState as RegStepState,
            build_snapshot,
            create_active_goal,
            is_sensitive_goal_content,
        )

        title = label[:80]
        if is_sensitive_goal_content(title):
            return ("That plan title isn't safe to restart.", None, None)
        if source is not None:
            titles = tuple(str(t)[:80] for t in (source.step_titles or ()))[:6]
            if not titles:
                titles = ("Clarify the goal", "Take the first step", "Review progress")
            plan_title = str(source.plan_title or f"Plan for: {title}")[:80]
            durable = str(source.durable_view or "")[:300]
        else:
            titles = ("Clarify the goal", "Take the first step", "Review progress")
            plan_title = f"Plan for: {title}"[:80]
            durable = (
                f"Plan: {plan_title}\n\nSteps:\n"
                + "\n".join(f"{i}. [ ] {t}" for i, t in enumerate(titles, 1))
                + "\n"
            )[:300]
        states = tuple(RegStepState.PENDING for _ in titles)
        snap, st = build_snapshot(
            owner_id=owner,
            title=title,
            plan_title=plan_title,
            step_titles=titles,
            step_states=states,
            active_step_index=1,
            durable_view=durable,
        )
        if st is not RegistryStatus.OK or snap is None:
            return ("I couldn't start a new plan from that archive.", None, None)
        old_id = getattr(source, "goal_id", "") if source is not None else ""
        res = create_active_goal(owner, snap)
        if res.status is not RegistryStatus.OK or res.snapshot is None:
            return ("I couldn't create a new plan right now.", None, None)
        new_snap = res.snapshot
        if old_id and new_snap.goal_id == old_id:
            return ("Restart failed to allocate a new goal.", None, None)
        prior = plan_result_from_registry_snapshot(new_snap)
        cont = rehydrate_continuity_from_registry_snapshot(new_snap)
        return (
            (
                f"Started a new plan for \"{_safe_title(new_snap)}\" "
                "(previous archive left unchanged)."
            ),
            prior,
            cont,
        )
    except Exception:
        return (
            (
                f"Completed and abandoned goals stay archived. "
                f"Ask me to make a new plan for \"{label}\"."
            ),
            None,
            None,
        )
