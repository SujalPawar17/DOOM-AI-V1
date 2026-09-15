"""V8.22 DECIDE executor. Advice only. No tools. No side effects."""

from __future__ import annotations

import threading
from typing import Any, Tuple

from orchestration.decision.assemble import assemble_decision_input
from orchestration.decision.format import format_decision_template, maybe_polish_with_ollama
from orchestration.decision.score import score_decision
from orchestration.decision.types import DecisionStatus
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.plan_types import GoalPlan, PlanStep

_PROVIDER_LOCK = threading.Lock()
_TEST_PROVIDER: Any = None
_LAST_OLLAMA_CALLS = 0


def use_decide_provider_for_tests(provider: Any) -> None:
    global _TEST_PROVIDER
    with _PROVIDER_LOCK:
        _TEST_PROVIDER = provider


def reset_decide_provider_for_tests() -> None:
    global _TEST_PROVIDER, _LAST_OLLAMA_CALLS
    with _PROVIDER_LOCK:
        _TEST_PROVIDER = None
    _LAST_OLLAMA_CALLS = 0


def last_decide_ollama_calls() -> int:
    return int(_LAST_OLLAMA_CALLS)


def _test_provider() -> Any:
    with _PROVIDER_LOCK:
        return _TEST_PROVIDER


def _resolve_question(plan: GoalPlan, user_text: str) -> Tuple[str, str, str]:
    """Return (effective_question, anchor_assistant_text, clarify_message).

    Reuses V8.21 current-anchor / TTL resolution. Does not invent options.
    """
    owner = str(plan.owner_id or "")
    session = str(plan.session_id or "")
    effective = user_text
    anchor_asst = ""
    try:
        from orchestration.conversation.resolve import (
            is_continuation_utterance,
            resolve_conversation_reference,
        )
        from orchestration.conversation.thread import (
            current_anchor_turn,
            get_conversation_thread,
        )
        from orchestration.decision.assemble import options_from_user_text
        from orchestration.decision.relevance import decision_relevant

        continuation = is_continuation_utterance(user_text)
        needs_anchor = continuation or (
            decision_relevant(user_text)
            and len(options_from_user_text(user_text)) < 2
        )
        if needs_anchor or continuation:
            thread = get_conversation_thread(
                owner, session, for_continuation=True
            )
            anchor = current_anchor_turn(thread)
            if anchor and anchor.assistant_text:
                anchor_asst = anchor.assistant_text
            if continuation:
                resolution = resolve_conversation_reference(user_text, thread)
                if resolution.status == "CLARIFY" and resolution.clarification:
                    return "", "", resolution.clarification
                if resolution.status == "RESOLVED" and resolution.effective_text:
                    effective = resolution.effective_text
                elif not thread:
                    return "", "", (
                        "Which options should I compare? "
                        "Please name at least two alternatives."
                    )
    except Exception:
        return user_text, "", ""
    return effective, anchor_asst, ""


def execute_decide(step: PlanStep, plan: GoalPlan) -> Tuple[str, str]:
    """Return (ExecutionStatus.value, response_text). Never executes tools."""
    global _LAST_OLLAMA_CALLS
    _LAST_OLLAMA_CALLS = 0
    if step.capability_id != "conversation" or step.action != "DECIDE":
        return ExecutionStatus.ACTION_UNAVAILABLE.value, ""
    user_text = str(dict(step.parameters).get("text") or "")
    if not user_text.strip():
        return ExecutionStatus.LOCAL_MODEL_ERROR.value, ""

    effective, anchor_asst, clarify = _resolve_question(plan, user_text)
    if clarify:
        text = clarify.strip()[:2048]
        _record_turn(plan, user_text, text)
        return ExecutionStatus.SUCCESS.value, text

    try:
        from orchestration.conversation.resolve import is_new_standalone_topic
        from orchestration.conversation.context import clear_conversation_thread
        # Standalone decision with its own options: drop stale thread.
        if is_new_standalone_topic(user_text):
            from orchestration.decision.assemble import options_from_user_text
            if len(options_from_user_text(user_text)) >= 2:
                clear_conversation_thread(
                    str(plan.owner_id or ""), str(plan.session_id or "")
                )
                anchor_asst = ""
    except Exception:
        pass

    decision_input = assemble_decision_input(
        plan, effective or user_text, anchor_assistant_text=anchor_asst
    )
    result = score_decision(decision_input)

    # Template-first. Optional polish only via test provider (or future LOW OK).
    # Prefer deterministic template — do not auto-invoke live Ollama.
    provider = _test_provider()
    text, calls = maybe_polish_with_ollama(result, provider=provider)
    _LAST_OLLAMA_CALLS = calls
    if result.status is DecisionStatus.CLARIFY or (
        result.status is DecisionStatus.LOW_CONFIDENCE and not result.recommendation
    ):
        text = format_decision_template(result)

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
