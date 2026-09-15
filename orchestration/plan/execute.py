"""V8.23 PLAN_STEPS executor. Informational only."""

from __future__ import annotations

import threading
from typing import Any, Tuple

from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.plan_types import GoalPlan, PlanStep
from orchestration.plan.assemble import assemble_plan_input
from orchestration.plan.format import format_plan_template, maybe_polish_with_ollama
from orchestration.plan.relevance import plan_continuation
from orchestration.plan.synthesize import synthesize_plan
from orchestration.plan.types import PlanStatus

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
                return "", "", "", CLARIFY_NEED_GOAL
            anchor = current_anchor_turn(thread)
            if not anchor:
                return "", "", "", CLARIFY_NEED_GOAL
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


def execute_plan_steps(step: PlanStep, plan: GoalPlan) -> Tuple[str, str]:
    global _LAST_OLLAMA_CALLS
    _LAST_OLLAMA_CALLS = 0
    if step.capability_id != "conversation" or step.action != "PLAN_STEPS":
        return ExecutionStatus.ACTION_UNAVAILABLE.value, ""
    user_text = str(dict(step.parameters).get("text") or "")
    if not user_text.strip():
        return ExecutionStatus.LOCAL_MODEL_ERROR.value, ""

    effective, anchor_asst, anchor_user, clarify = _resolve_context(plan, user_text)
    if clarify:
        text = clarify.strip()[:2048]
        _record_turn(plan, user_text, text)
        return ExecutionStatus.SUCCESS.value, text

    plan_input = assemble_plan_input(
        plan,
        effective or user_text,
        anchor_assistant_text=anchor_asst,
        anchor_user_text=anchor_user,
    )
    result = synthesize_plan(plan_input)
    provider = _test_provider()
    text, calls = maybe_polish_with_ollama(result, provider=provider)
    _LAST_OLLAMA_CALLS = calls
    if result.status is PlanStatus.CLARIFY or (
        result.status is PlanStatus.LOW_CONFIDENCE and not result.steps
    ):
        text = format_plan_template(result)

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
