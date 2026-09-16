"""V8.12 RESPOND: Cost Guard → local Ollama only → plain text. No tools."""

from __future__ import annotations

import re
import threading
from typing import Any, Optional, Tuple

from core.cost_guard.invoke import authorize_llm_provider
from core.cost_guard.types import CostClass, CostGuardBlockedError
from models.base_provider import (
    LLMResponse,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from models.ollama_provider import OllamaProvider
from orchestration.conversation.prompt import SYSTEM_PROMPT
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.plan_registry import CONVERSATION_MAX_TEXT
from orchestration.goal.plan_types import GoalPlan, PlanStep

MAX_INPUT_CHARS = CONVERSATION_MAX_TEXT
MAX_OUTPUT_CHARS = 2048
MAX_CONTEXT_CHARS = 512
MAX_SYSTEM_PROMPT_CHARS = 3600
MODEL_TIMEOUT_SEC = 50
MAX_GENERATION_TOKENS = 256

TIMEOUT_FOLLOWUP_BASE = (
    "I couldn't finish that follow-up with the local model in time. Please try again."
)

_PROVIDER_LOCK = threading.Lock()
_TEST_PROVIDER: Any = None

_SECRETISH = re.compile(
    r"(?i)(api[_-]?key|authorization|password|secret|token|cookie)\s*[=:]\s*\S+"
)
# Strip accidental exposure of implementation markers from user-visible text.
_INTERNAL_MARKERS = re.compile(
    r"(?i)"
    r"</?safe_context\b[^>]*>|"
    r"</?situation\b[^>]*>|"
    r"</?conversation_context\b[^>]*>|"
    r"\bsafe[_ ]?context\b|"
    r"\bsituation[_ ]?model\b|"
    r"\bconversation[_ ]?context\b|"
    r"\bcontext (assembler|injection|wrapper|block)\b|"
    r"\buntrusted contextual data\b|"
    r"\bexecutionidentity\b|"
    r"\bauthorized_plan_hash\b|"
    r"\bplan_hash\b|"
    r"\bsession_id_hash\b|"
    r"\bcsrf_token\b|"
    r"\bsystem prompt\b|"
    r"\bsecurity instructions\b"
)
# Narrow meta-preambles only (not a generic content filter).
# Require punctuation after source nouns where needed so ordinary prose survives.
_META_CONTEXT_PREAMBLE = re.compile(
    r"(?i)"
    # according to / based on + internal source + comma/colon
    r"\b(according to|based on)\s+"
    r"(the\s+|my\s+|your\s+)?"
    r"("
    r"untrusted\s+contextual\s+data|"
    r"contextual\s+data|"
    r"safe[_\s-]?context|"
    r"</?safe_context\b[^>]*>|"
    r"supporting\s+notes|"
    r"personal\s+memory|"
    r"memory|"
    r"notes|"
    r"context"
    r")\s*[,:]\s*"
    r"(it\s+appears\s+that\s+)?"
    r"|"
    # "X indicates/says/shows/suggests/mention …" for known internal sources
    r"\b("
    r"my\s+(personal\s+)?memory|"
    r"(your\s+)?supporting\s+notes|"
    r"the\s+notes|"
    r"my\s+notes|"
    r"the\s+(untrusted\s+)?contextual\s+data|"
    r"the\s+safe[_\s-]?context|"
    r"the\s+context"
    r")\s+(indicates?|says?|shows?|suggests?|mentions?)\b[,:]?\s*"
)

# Model refusal / meta-leak when trusted personal facts were already available.
_MEMORY_META_REFUSAL = re.compile(
    r"(?i)\b("
    r"can(?:not|'t)\s+access\s+your\s+(?:personal\s+)?(?:preferences?|memory)|"
    r"don(?:ot|'t)\s+have\s+access\s+to\s+your\s+(?:personal\s+)?memory|"
    r"supporting\s+notes\s+mention|"
    r"notes\s+mention\s+that|"
    r"personal\s+memory\s+that\s+says|"
    r"have\s+a\s+personal\s+memory|"
    r"memory\s+(?:store|database)|"
    r"injected\s+context|"
    r"internal\s+context|"
    r"retrieved\s+context|"
    r"context\s+block"
    r")\b"
)


def use_respond_provider_for_tests(provider: Any) -> None:
    global _TEST_PROVIDER
    with _PROVIDER_LOCK:
        _TEST_PROVIDER = provider


def reset_respond_provider_for_tests() -> None:
    global _TEST_PROVIDER
    with _PROVIDER_LOCK:
        _TEST_PROVIDER = None
    try:
        from orchestration.conversation.context import reset_conversation_context_for_tests
        reset_conversation_context_for_tests()
    except Exception:
        pass


def _provider() -> Any:
    with _PROVIDER_LOCK:
        if _TEST_PROVIDER is not None:
            return _TEST_PROVIDER
    return OllamaProvider()


def _redact(text: str) -> str:
    cleaned = _SECRETISH.sub("[REDACTED]", str(text or ""))
    return cleaned.replace("\x00", "")


def scrub_internal_markers(text: str) -> str:
    """Remove implementation/security marker leaks from user-visible replies."""
    original = str(text or "")
    cleaned, n_meta = _META_CONTEXT_PREAMBLE.subn("", original)
    cleaned = _INTERNAL_MARKERS.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip(" ,;:")
    # Only re-case when a meta preamble was stripped (avoid mutating normal replies).
    if n_meta and cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned.strip()


def _param_text(step: PlanStep) -> str:
    return str(dict(step.parameters).get("text") or "")


def _finish_respond(plan: GoalPlan, user_text: str, body: str) -> Tuple[str, str]:
    text = scrub_internal_markers(_redact(body))
    if len(text) > MAX_OUTPUT_CHARS:
        return ExecutionStatus.OUTPUT_LIMIT.value, ""
    text = text.strip()
    if not text:
        return ExecutionStatus.LOCAL_MODEL_ERROR.value, ""
    try:
        from orchestration.conversation.context import record_conversation_turn
        record_conversation_turn(
            str(plan.owner_id or ""),
            str(plan.session_id or ""),
            user_text=user_text,
            assistant_text=text,
        )
    except Exception:
        pass
    return ExecutionStatus.SUCCESS.value, text


def _try_direct_memory_answer(plan: GoalPlan, user_text: str) -> Optional[str]:
    owner = str(plan.owner_id or "")
    # V8.27 Phase 4: User Model first, then personal-memory fallback.
    try:
        from orchestration.user_model.config import is_v827_user_model_enabled
        from orchestration.user_model.resolve import try_direct_profile_fact_answer

        if is_v827_user_model_enabled():
            profile_ans = try_direct_profile_fact_answer(owner, user_text)
            if profile_ans:
                return profile_ans
    except Exception:
        pass
    try:
        from orchestration.conversation.personal_memory import (
            try_direct_personal_fact_answer,
        )
        return try_direct_personal_fact_answer(owner, user_text)
    except Exception:
        return None


def execute_respond(step: PlanStep, plan: GoalPlan) -> Tuple[str, str]:
    """Return (ExecutionStatus.value, response_text). Never executes tools."""
    if step.capability_id != "conversation" or step.action != "RESPOND":
        return ExecutionStatus.ACTION_UNAVAILABLE.value, ""
    user_text = _param_text(step)
    if len(user_text) > MAX_INPUT_CHARS:
        return ExecutionStatus.INPUT_TOO_LARGE.value, ""
    if not user_text.strip():
        return ExecutionStatus.LOCAL_MODEL_ERROR.value, ""

    # V8.27 Phase 2: explicit User Model UX (confirm / forget / transparency).
    try:
        from orchestration.user_model.intent import handle_user_model_request

        um = handle_user_model_request(
            str(plan.owner_id or ""),
            str(plan.session_id or ""),
            user_text,
        )
        if um is not None:
            text = str(um or "").strip()[:MAX_OUTPUT_CHARS]
            return _finish_respond(plan, user_text, text)
    except Exception:
        pass

    owner = str(plan.owner_id or "")
    session = str(plan.session_id or "")
    continuation = False
    anchor_topic = ""
    try:
        from orchestration.conversation.resolve import (
            is_continuation_utterance,
            is_new_standalone_topic,
        )
        continuation = is_continuation_utterance(user_text)
        if is_new_standalone_topic(user_text):
            from orchestration.conversation.context import clear_conversation_thread
            clear_conversation_thread(owner, session)
    except Exception:
        continuation = False

    # V8.21: deterministic thread reference resolution (no extra Ollama call).
    effective_text = user_text
    thread_block = ""
    try:
        from orchestration.conversation.resolve import resolve_conversation_reference
        from orchestration.conversation.thread import (
            current_anchor_turn,
            format_conversation_context_block,
            get_conversation_thread,
        )
        thread = get_conversation_thread(
            owner,
            session,
            for_continuation=continuation,
        )
        resolution = resolve_conversation_reference(user_text, thread)
        if resolution.status == "CLARIFY" and resolution.clarification:
            return _finish_respond(plan, user_text, resolution.clarification)
        if resolution.status == "RESOLVED" and resolution.effective_text:
            effective_text = resolution.effective_text
        anchor = current_anchor_turn(thread)
        if anchor and anchor.user_text:
            anchor_topic = anchor.user_text[:120]
        if continuation and thread:
            thread_block = format_conversation_context_block(thread)
    except Exception:
        effective_text = user_text
        thread_block = ""

    # Narrow deterministic path for exact personal-fact questions with a hit.
    # Prefer the original utterance; fall back to resolved text for references.
    direct = _try_direct_memory_answer(plan, user_text)
    if not direct and effective_text != user_text:
        direct = _try_direct_memory_answer(plan, effective_text)
    if direct:
        return _finish_respond(plan, user_text, direct)

    provider = _provider()
    name = str(getattr(provider, "name", "") or "").strip().lower()
    if name != "ollama":
        return ExecutionStatus.LOCAL_MODEL_UNAVAILABLE.value, ""

    try:
        decision = authorize_llm_provider(provider, capability="v8_respond")
    except Exception:
        return ExecutionStatus.LOCAL_MODEL_UNAVAILABLE.value, ""
    if (not decision.is_allow) or decision.cost_class != CostClass.LOCAL_FREE:
        return ExecutionStatus.LOCAL_MODEL_UNAVAILABLE.value, ""

    system = SYSTEM_PROMPT[:MAX_CONTEXT_CHARS]
    block = ""
    try:
        from orchestration.situation.assemble import assemble_situation_block
        from orchestration.situation.relevance import situation_relevant
        # Situation relevance uses the resolved request when available.
        if situation_relevant(effective_text):
            block, _st = assemble_situation_block(plan, effective_text)
    except Exception:
        block = ""
    if not block:
        try:
            from orchestration.conversation.safe_context import load_respond_context
            block, _status = load_respond_context(plan, effective_text)
        except Exception:
            block = ""
    # Inject bounded conversation thread as an explicit untrusted block.
    # Avoid duplicating the same material when situation/safe_context already
    # embedded "Recent conversation" for this turn.
    extras = []
    if thread_block and "<conversation_context>" not in (block or ""):
        if "Recent conversation:" not in (block or ""):
            extras.append(thread_block)
        else:
            # Situation/safe_context already carries recent turns; still attach
            # the tagged block when this is an explicit continuation resolve.
            if effective_text != user_text:
                extras.append(thread_block)
    parts = [system]
    if extras:
        parts.extend(extras)
    if block:
        parts.append(str(block))
    system = "\n\n".join(parts)[:MAX_SYSTEM_PROMPT_CHARS]
    try:
        out = provider.generate(
            effective_text,
            system_prompt=system,
            tools=None,
            temperature=0.4,
            timeout=MODEL_TIMEOUT_SEC,
            num_predict=MAX_GENERATION_TOKENS,
            capability="v8_respond",
        )
    except CostGuardBlockedError:
        return ExecutionStatus.LOCAL_MODEL_UNAVAILABLE.value, ""
    except ProviderTimeoutError:
        if effective_text != user_text:
            hint = anchor_topic.strip()
            msg = TIMEOUT_FOLLOWUP_BASE
            if hint:
                msg = f"{msg} (about: {hint})"
            return ExecutionStatus.LOCAL_MODEL_TIMEOUT.value, msg[:512]
        return ExecutionStatus.LOCAL_MODEL_TIMEOUT.value, ""
    except ProviderUnavailableError:
        return ExecutionStatus.LOCAL_MODEL_UNAVAILABLE.value, ""
    except Exception:
        return ExecutionStatus.LOCAL_MODEL_ERROR.value, ""

    if not isinstance(out, LLMResponse):
        return ExecutionStatus.LOCAL_MODEL_ERROR.value, ""
    raw = scrub_internal_markers(_redact(str(out.text or "")))
    # If the model meta-refuses despite a matching personal fact, use the fact.
    if _MEMORY_META_REFUSAL.search(raw):
        rescue = _try_direct_memory_answer(plan, user_text)
        if not rescue and effective_text != user_text:
            rescue = _try_direct_memory_answer(plan, effective_text)
        if rescue:
            return _finish_respond(plan, user_text, rescue)
    return _finish_respond(plan, user_text, raw)
