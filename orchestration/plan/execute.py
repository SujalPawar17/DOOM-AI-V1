"""V8.23 PLAN_STEPS executor + V8.24 refine/validate/prioritize. Informational only."""

from __future__ import annotations

import threading
from typing import Any, Optional, Tuple

from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.plan_types import GoalPlan, PlanStep
from orchestration.plan.analysis import PlanAnalysis, analyze_dependencies
from orchestration.plan.assemble import assemble_plan_input
from orchestration.plan.format import format_plan_with_analysis, maybe_polish_with_ollama
from orchestration.plan.modes import PlanMode, detect_plan_mode
from orchestration.plan.parse import CLARIFY_NEED_PRIOR, parse_plan_from_assistant_text
from orchestration.plan.prioritize import prioritize_plan
from orchestration.plan.refine import refine_plan
from orchestration.plan.relevance import plan_continuation
from orchestration.plan.synthesize import synthesize_plan
from orchestration.plan.types import PlanConfidence, PlanResult, PlanStatus
from orchestration.plan.validate import validate_plan

_PROVIDER_LOCK = threading.Lock()
_TEST_PROVIDER: Any = None
_LAST_OLLAMA_CALLS = 0

CLARIFY_NEED_GOAL = (
    "To build a useful plan, I need to know what you want to accomplish. "
    "Please describe the goal in a sentence or two."
)


def use_plan_provider_for_tests(provider: Any) -> None:
    global _TEST_PROVIDER
    with _PROVIDER_LOCK:
        _TEST_PROVIDER = provider


def reset_plan_provider_for_tests() -> None:
    global _TEST_PROVIDER, _LAST_OLLAMA_CALLS
    with _PROVIDER_LOCK:
        _TEST_PROVIDER = None
    _LAST_OLLAMA_CALLS = 0


def last_plan_ollama_calls() -> int:
    return int(_LAST_OLLAMA_CALLS)


def _test_provider() -> Any:
    with _PROVIDER_LOCK:
        return _TEST_PROVIDER


def _resolve_context(plan: GoalPlan, user_text: str) -> Tuple[str, str, str, str]:
    owner = str(plan.owner_id or "")
    session = str(plan.session_id or "")
    effective = user_text
    anchor_asst = ""
    anchor_user = ""
    try:
        from orchestration.conversation.thread import (
            current_anchor_turn,
            get_conversation_thread,
        )

        continuation = plan_continuation(user_text)
        if continuation:
            thread = get_conversation_thread(owner, session, for_continuation=True)
            if not thread:
                return "", "", "", CLARIFY_NEED_PRIOR
            anchor = current_anchor_turn(thread)
            if not anchor:
                return "", "", "", CLARIFY_NEED_PRIOR
            anchor_asst = anchor.assistant_text or ""
            anchor_user = anchor.user_text or ""
            effective = user_text
        else:
            from orchestration.conversation.resolve import is_new_standalone_topic
            from orchestration.conversation.context import clear_conversation_thread

            if is_new_standalone_topic(user_text):
                clear_conversation_thread(owner, session)
    except Exception:
        pass
    return effective, anchor_asst, anchor_user, ""


def _empty_analysis(mode: PlanMode, conf: PlanConfidence = PlanConfidence.LOW) -> PlanAnalysis:
    from orchestration.plan.analysis import ValidationStatus

    return PlanAnalysis(mode=mode, confidence=conf, validation_status=ValidationStatus.NEEDS_CLARIFY)


def execute_plan_steps(step: PlanStep, plan: GoalPlan) -> Tuple[str, str]:
    global _LAST_OLLAMA_CALLS
    _LAST_OLLAMA_CALLS = 0
    if step.capability_id != "conversation" or step.action != "PLAN_STEPS":
        return ExecutionStatus.ACTION_UNAVAILABLE.value, ""
    user_text = str(dict(step.parameters).get("text") or "")
    if not user_text.strip():
        return ExecutionStatus.LOCAL_MODEL_ERROR.value, ""

    mode = detect_plan_mode(user_text)
    effective, anchor_asst, anchor_user, clarify = _resolve_context(plan, user_text)

    # Continuations without a valid TTL anchor — fail closed with mode-aware clarify.
    if clarify:
        if mode in (PlanMode.REFINE, PlanMode.NEXT, PlanMode.VALIDATE, PlanMode.DEPEND):
            text = CLARIFY_NEED_PRIOR.strip()[:2048]
        else:
            # V8.23 CREATE continuations ("next steps?") without context.
            text = CLARIFY_NEED_GOAL.strip()[:2048]
        _record_turn(plan, user_text, text)
        return ExecutionStatus.SUCCESS.value, text

    plan_input = assemble_plan_input(
        plan,
        effective or user_text,
        anchor_assistant_text=anchor_asst,
        anchor_user_text=anchor_user,
    )

    prior: Optional[PlanResult] = None
    had_prior = False
    if mode in (PlanMode.REFINE, PlanMode.NEXT, PlanMode.VALIDATE, PlanMode.DEPEND) or (
        plan_continuation(user_text) and anchor_asst
    ):
        parsed = parse_plan_from_assistant_text(anchor_asst)
        if not parsed.ok or not parsed.result:
            msg = parsed.clarify or CLARIFY_NEED_PRIOR
            # For CREATE continuation without parseable plan, fall through to synthesize
            # only when mode is CREATE; otherwise clarify.
            if mode is not PlanMode.CREATE:
                text = msg.strip()[:2048]
                _record_turn(plan, user_text, text)
                return ExecutionStatus.SUCCESS.value, text
        else:
            prior = parsed.result
            had_prior = True

    analysis: PlanAnalysis
    result: PlanResult

    if mode is PlanMode.CREATE and not (had_prior and plan_continuation(user_text)):
        result = synthesize_plan(plan_input)
        result = refine_plan(result, goal_summary=plan_input.goal_summary, aggressive=False)
        vstatus, issues, result = validate_plan(
            result, plan_input, mode=mode, had_prior_plan=True
        )
        deps, dep_issues = analyze_dependencies(result, extra_issues=issues)
        analysis = prioritize_plan(
            result,
            mode=mode,
            dependencies=deps,
            issues=tuple(list(issues) + list(dep_issues))[:6],
            validation_status=vstatus,
            confidence=result.confidence,
        )
    elif mode is PlanMode.REFINE:
        assert prior is not None
        result = refine_plan(prior, goal_summary=plan_input.goal_summary or prior.title, aggressive=True)
        # Carry goal from prior title if assemble lacked one
        if len((plan_input.goal_summary or "").strip()) < 8:
            from orchestration.plan.types import PlanInput, PlanSourceFlags

            plan_input = PlanInput(
                question=plan_input.question,
                goal_summary=(prior.title or "prior plan")[:300],
                facts=plan_input.facts,
                constraints=plan_input.constraints,
                preferences=plan_input.preferences,
                seed_steps=plan_input.seed_steps,
                situation_factors=plan_input.situation_factors,
                source_flags=plan_input.source_flags,
            )
        vstatus, issues, result = validate_plan(
            result, plan_input, mode=mode, had_prior_plan=True
        )
        deps, dep_issues = analyze_dependencies(result, extra_issues=issues)
        analysis = prioritize_plan(
            result,
            mode=mode,
            dependencies=deps,
            issues=tuple(list(issues) + list(dep_issues))[:6],
            validation_status=vstatus,
            confidence=result.confidence,
        )
    elif mode is PlanMode.NEXT:
        assert prior is not None
        result = refine_plan(prior, aggressive=False)
        vstatus, issues, result = validate_plan(
            result, plan_input, mode=mode, had_prior_plan=True
        )
        deps, dep_issues = analyze_dependencies(result, extra_issues=issues)
        analysis = prioritize_plan(
            result,
            mode=mode,
            dependencies=deps,
            issues=tuple(list(issues) + list(dep_issues))[:6],
            validation_status=vstatus,
            confidence=result.confidence,
        )
    elif mode is PlanMode.VALIDATE:
        assert prior is not None
        result = prior
        vstatus, issues, result = validate_plan(
            result, plan_input, mode=mode, had_prior_plan=True
        )
        deps, dep_issues = analyze_dependencies(result, extra_issues=())
        analysis = prioritize_plan(
            result,
            mode=mode,
            dependencies=deps,
            issues=tuple(list(issues) + list(dep_issues))[:6],
            validation_status=vstatus,
            confidence=result.confidence,
        )
    elif mode is PlanMode.DEPEND:
        assert prior is not None
        result = prior
        vstatus, issues, result = validate_plan(
            result, plan_input, mode=mode, had_prior_plan=True
        )
        deps, dep_issues = analyze_dependencies(result, extra_issues=issues)
        analysis = prioritize_plan(
            result,
            mode=mode,
            dependencies=deps,
            issues=tuple(list(issues) + list(dep_issues))[:6],
            validation_status=vstatus,
            confidence=result.confidence,
        )
    else:
        # CREATE-like with prior from "next steps?" — treat as NEXT if prior exists
        if had_prior and prior is not None:
            result = refine_plan(prior, aggressive=False)
            vstatus, issues, result = validate_plan(
                result, plan_input, mode=PlanMode.NEXT, had_prior_plan=True
            )
            deps, dep_issues = analyze_dependencies(result, extra_issues=issues)
            analysis = prioritize_plan(
                result,
                mode=PlanMode.NEXT,
                dependencies=deps,
                issues=tuple(list(issues) + list(dep_issues))[:6],
                validation_status=vstatus,
                confidence=result.confidence,
            )
        else:
            result = synthesize_plan(plan_input)
            result = refine_plan(result, goal_summary=plan_input.goal_summary, aggressive=False)
            vstatus, issues, result = validate_plan(
                result, plan_input, mode=PlanMode.CREATE, had_prior_plan=True
            )
            deps, dep_issues = analyze_dependencies(result, extra_issues=issues)
            analysis = prioritize_plan(
                result,
                mode=PlanMode.CREATE,
                dependencies=deps,
                issues=tuple(list(issues) + list(dep_issues))[:6],
                validation_status=vstatus,
                confidence=result.confidence,
            )

    provider = _test_provider()
    text, calls = maybe_polish_with_ollama(result, analysis=analysis, provider=provider)
    _LAST_OLLAMA_CALLS = calls
    if result.status is PlanStatus.CLARIFY or (
        result.status is PlanStatus.LOW_CONFIDENCE and not result.steps
    ):
        text = format_plan_with_analysis(result, analysis)

    text = str(text or "").strip()[:2048]
    if not text:
        return ExecutionStatus.LOCAL_MODEL_ERROR.value, ""
    try:
        from orchestration.conversation.respond import scrub_internal_markers

        text = scrub_internal_markers(text)
    except Exception:
        pass
    _record_turn(plan, user_text, text)
    return ExecutionStatus.SUCCESS.value, text


def _record_turn(plan: GoalPlan, user_text: str, assistant_text: str) -> None:
    try:
        from orchestration.conversation.context import record_conversation_turn

        record_conversation_turn(
            str(plan.owner_id or ""),
            str(plan.session_id or ""),
            user_text=user_text,
            assistant_text=assistant_text,
        )
    except Exception:
        pass
