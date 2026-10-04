"""V12.1 Response Intelligence.

Turns the facts of a cognitive cycle into a natural, truthful user-facing response.

    cycle facts -> response intent -> answer generation -> response validation

- Informational turns are answered through the existing V8 RESPOND path
  (orchestration.conversation.respond.execute_respond): direct User Model / personal
  memory answers, conversation-thread resolution, Cost Guard (LOCAL_FREE only) and the
  local Ollama model, with V8 secret redaction and internal-marker scrubbing.
- Action outcomes (success, failure, blocked, pending authorization, planning failure)
  are described deterministically from execution facts only.
- Every response is validated: no internal reasoning summaries, no implementation
  markers or secrets, and no claim that an action happened unless the V8 executor
  reported a verified success.
- If the local model is unavailable the response is a truthful deterministic fallback;
  no external or paid provider is ever used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from orchestration.executor_errors import ExecutionStatus

MAX_RESPONSE_CHARS = 2048


class ResponseIntent(str, Enum):
    GREETING = "GREETING"
    FAREWELL = "FAREWELL"
    ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"
    ANSWER = "ANSWER"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    COST_BLOCKED = "COST_BLOCKED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    PENDING_AUTHORIZATION = "PENDING_AUTHORIZATION"
    PLANNING_FAILED = "PLANNING_FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class ResponseSource(str, Enum):
    DETERMINISTIC = "deterministic"
    LOCAL_RESPONDER = "v8_local_responder"
    EXECUTOR = "executor"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class ComposedResponse:
    text: str
    intent: ResponseIntent
    source: ResponseSource
    corrections: Tuple[str, ...] = ()


# --- deterministic conversational intents -----------------------------------

_GREETING = re.compile(
    r"^\s*(hi|hello|hey|hiya|greetings|good\s+(morning|afternoon|evening))"
    r"(\s+(there|doom))?[\s!.,]*$", re.IGNORECASE)
_FAREWELL = re.compile(
    r"^\s*(bye|goodbye|good\s*night|see\s+you(\s+later)?|that'?s\s+all)(\s+doom)?[\s!.,]*$",
    re.IGNORECASE)
_THANKS = re.compile(r"^\s*(thanks|thank\s+you|cheers)(\s+(doom|a\s+lot|so\s+much))?[\s!.,]*$",
                     re.IGNORECASE)

# --- validation patterns -----------------------------------------------------

# First-person claims of having acted on the computer / files / services.
_ACTION_CLAIM = re.compile(
    r"(?i)\b(I|we)(?:'ve|\s+have|\s+just|\s+already)*\s+"
    r"(executed|ran|clicked|typed|opened|closed|deleted|removed|moved|renamed|copied|"
    r"installed|uninstalled|sent|emailed|saved|launched|started|stopped|restarted|"
    r"shut\s+down|downloaded|uploaded|turned\s+(?:on|off)|enabled|disabled|"
    r"backed\s+up|scheduled|booked|purchased|paid|submitted|executed\s+the\s+plan)\b"
)
_EXECUTION_MARKERS = re.compile(r"(?i)\b(successfully\s+executed|execution\s+id\b|plan\s+id\b)")

_STATUS_PHRASES = {
    ExecutionStatus.STEP_FAILED: "a step failed",
    ExecutionStatus.TIMEOUT: "it timed out",
    ExecutionStatus.SESSION_UNAVAILABLE: "no usable session was available",
    ExecutionStatus.PLAN_HASH_MISMATCH: "the plan failed an integrity check",
    ExecutionStatus.INVALID_PLAN: "the plan was not valid",
    ExecutionStatus.CAPABILITY_UNAVAILABLE: "that capability is not available",
    ExecutionStatus.ACTION_UNAVAILABLE: "that action is not available",
    ExecutionStatus.EMERGENCY_STOPPED: "an emergency stop is active",
    ExecutionStatus.ABORTED: "it was aborted",
    ExecutionStatus.CANCELLED: "it was cancelled",
    ExecutionStatus.PRECONDITION_FAILED: "a precondition was not met",
    ExecutionStatus.STALE_OBSERVATION_HASH: "the screen changed before I could act",
    ExecutionStatus.IDENTITY_REQUIRED: "the request was missing a valid identity",
    ExecutionStatus.V8_DISABLED: "execution is disabled",
    ExecutionStatus.LOCAL_MODEL_UNAVAILABLE: "the local model was unavailable",
    ExecutionStatus.LOCAL_MODEL_TIMEOUT: "the local model did not respond in time",
    ExecutionStatus.LOCAL_MODEL_ERROR: "the local model returned an error",
    ExecutionStatus.AUTHORIZATION_INVALID: "the authorization was not valid",
    ExecutionStatus.AUTHORIZATION_EXPIRED: "the authorization expired",
    ExecutionStatus.AUTHORIZATION_REVOKED: "the authorization was revoked",
    ExecutionStatus.AUTHORIZATION_CONSUMED: "the authorization was already used",
    ExecutionStatus.RISK_NOT_APPROVABLE: "that action is too risky to approve",
}

_RESPONDER_FALLBACKS = {
    ExecutionStatus.LOCAL_MODEL_UNAVAILABLE.value:
        "I can't answer that right now because my local language model isn't available.",
    ExecutionStatus.LOCAL_MODEL_TIMEOUT.value:
        "My local language model didn't respond in time. Please try again.",
    ExecutionStatus.INPUT_TOO_LARGE.value:
        "That message is too long for me to answer in one go. Could you shorten it?",
    ExecutionStatus.OUTPUT_LIMIT.value:
        "I couldn't produce a concise enough answer to that. Could you narrow the question?",
}
_GENERIC_UNAVAILABLE = "I couldn't produce a reliable answer to that just now."
_NO_ACTION_TAKEN = "I can explain how to do that, but I haven't taken any action."


def _status(execution_result: Any) -> Optional[ExecutionStatus]:
    status = getattr(execution_result, "status", None)
    return status if isinstance(status, ExecutionStatus) else None


def _sanitize(text: str) -> str:
    from orchestration.conversation.respond import _redact, scrub_internal_markers
    return scrub_internal_markers(_redact(str(text or ""))).strip()


class ResponseIntelligence:
    """Composes and validates the user-facing response for one cognitive cycle."""

    def compose(self, facts: Dict[str, Any]) -> ComposedResponse:
        draft = self._draft(facts)
        return self.validate(draft, facts)

    # -- drafting -------------------------------------------------------------

    def _draft(self, facts: Dict[str, Any]) -> ComposedResponse:
        planning_result = facts.get("planning_result")
        execution_result = facts.get("execution_result")
        status = _status(execution_result)

        if not getattr(planning_result, "planning_required", False):
            return self._informational(facts)
        if facts.get("planning_failed"):
            return ComposedResponse(
                "I couldn't work out a safe plan for that, so I haven't done anything.",
                ResponseIntent.PLANNING_FAILED, ResponseSource.DETERMINISTIC)
        if facts.get("skip_execution"):
            return ComposedResponse(
                "I can't do that: it would need a resource that isn't allowed under the "
                "zero-cost policy. Nothing was done.",
                ResponseIntent.COST_BLOCKED, ResponseSource.DETERMINISTIC)
        if status == ExecutionStatus.SUCCESS and facts.get("executed"):
            executor_text = str(getattr(execution_result, "response_text", "") or "").strip()
            if executor_text:
                return ComposedResponse(executor_text, ResponseIntent.SUCCESS, ResponseSource.EXECUTOR)
            verification = facts.get("verification_result") or {}
            suffix = " and was verified" if verification.get("status") == "VERIFIED" else ""
            return ComposedResponse(f"Done. The plan completed{suffix}.",
                                    ResponseIntent.SUCCESS, ResponseSource.DETERMINISTIC)
        if status == ExecutionStatus.APPROVAL_REQUIRED:
            pending_id = facts.get("pending_id")
            if pending_id:
                text = ("This needs your approval before I can do it. Nothing has been done yet. "
                        f"Approval reference: {pending_id}.")
            else:
                reason = str(facts.get("stash_error") or "unavailable")
                text = ("This needs your approval, but I couldn't hold the request for approval "
                        f"({reason}). Nothing was done.")
            return ComposedResponse(text, ResponseIntent.PENDING_AUTHORIZATION, ResponseSource.DETERMINISTIC)
        if status == ExecutionStatus.BLOCKED:
            return ComposedResponse("That was blocked by a safety policy, so it wasn't completed.",
                                    ResponseIntent.POLICY_BLOCKED, ResponseSource.DETERMINISTIC)
        if status in (ExecutionStatus.VERIFICATION_FAILED, ExecutionStatus.NOT_VERIFIED):
            return ComposedResponse(
                "I attempted it, but I couldn't verify that it worked, so I'm not treating it as done.",
                ResponseIntent.VERIFICATION_FAILED, ResponseSource.DETERMINISTIC)
        phrase = _STATUS_PHRASES.get(status, "something went wrong") if status else "something went wrong"
        return ComposedResponse(f"I couldn't complete that because {phrase}.",
                                ResponseIntent.FAILURE, ResponseSource.DETERMINISTIC)

    def _informational(self, facts: Dict[str, Any]) -> ComposedResponse:
        user_input = str(facts.get("user_input") or "")
        if _GREETING.match(user_input):
            return ComposedResponse("Hello. What can I do for you?",
                                    ResponseIntent.GREETING, ResponseSource.DETERMINISTIC)
        if _FAREWELL.match(user_input):
            return ComposedResponse("Goodbye. I'll be here when you need me.",
                                    ResponseIntent.FAREWELL, ResponseSource.DETERMINISTIC)
        if _THANKS.match(user_input):
            return ComposedResponse("You're welcome.",
                                    ResponseIntent.ACKNOWLEDGEMENT, ResponseSource.DETERMINISTIC)
        status, text = self.generate_answer(
            user_input, str(facts.get("owner_id") or ""), str(facts.get("session_id") or ""))
        if status == ExecutionStatus.SUCCESS.value and text.strip():
            return ComposedResponse(text, ResponseIntent.ANSWER, ResponseSource.LOCAL_RESPONDER)
        fallback = (text.strip() if status == ExecutionStatus.LOCAL_MODEL_TIMEOUT.value and text.strip()
                    else _RESPONDER_FALLBACKS.get(status, _GENERIC_UNAVAILABLE))
        return ComposedResponse(fallback, ResponseIntent.UNAVAILABLE, ResponseSource.FALLBACK)

    @staticmethod
    def generate_answer(user_input: str, owner_id: str, session_id: str) -> Tuple[str, str]:
        """Answer via the V8 local RESPOND path. Returns (ExecutionStatus value, text).

        RESPOND is information-only (never executes tools); the V8 responder enforces
        Cost Guard LOCAL_FREE and the local Ollama provider itself.
        """
        from orchestration.conversation.respond import execute_respond
        from orchestration.goal.plan_types import GoalPlan, PlanStep
        from orchestration.goal.plan_registry import CONVERSATION_MAX_TEXT
        from orchestration.goal.plan_validator import hash_goal_plan

        if len(user_input) > CONVERSATION_MAX_TEXT:
            return ExecutionStatus.INPUT_TOO_LARGE.value, ""
        step = PlanStep(
            step_id="respond", capability_id="conversation", action="RESPOND",
            parameters=(("text", user_input),), dependencies=(), verification_required=False,
            verification_type="", risk="LOW", approval_required=False, retry_count=0,
            timeout_ms=60000,
        )
        unsigned = GoalPlan(
            plan_id="v12_respond", goal_id="v12_respond", schema_version="v82.1",
            owner_id=owner_id, session_id=session_id, computer_session_id="",
            steps=(step,), plan_risk="LOW", approval_required=False,
            provenance="v12_response_intelligence", plan_hash="",
        )
        plan = replace(unsigned, plan_hash=hash_goal_plan(unsigned))
        try:
            status, text = execute_respond(step, plan)
        except Exception:
            return ExecutionStatus.LOCAL_MODEL_ERROR.value, ""
        return str(status or ""), str(text or "")

    # -- validation -----------------------------------------------------------

    def validate(self, draft: ComposedResponse, facts: Dict[str, Any]) -> ComposedResponse:
        corrections = list(draft.corrections)
        text = _sanitize(draft.text)
        if text != draft.text.strip():
            corrections.append("sanitized")

        verified_action = (draft.intent == ResponseIntent.SUCCESS and bool(facts.get("executed"))
                           and _status(facts.get("execution_result")) == ExecutionStatus.SUCCESS)

        reasoning = getattr(facts.get("reasoning_result"), "reasoning_summary", "") or ""
        if reasoning and text and text.strip() == str(reasoning).strip():
            corrections.append("internal_reasoning_blocked")
            text = _GENERIC_UNAVAILABLE

        if not verified_action and (_ACTION_CLAIM.search(text) or _EXECUTION_MARKERS.search(text)):
            if draft.intent in (ResponseIntent.ANSWER, ResponseIntent.UNAVAILABLE):
                corrections.append("unverified_action_claim_removed")
                text = _NO_ACTION_TAKEN
            elif _EXECUTION_MARKERS.search(text):
                corrections.append("execution_marker_removed")
                text = _EXECUTION_MARKERS.sub("", text).strip()

        if len(text) > MAX_RESPONSE_CHARS:
            corrections.append("truncated")
            text = text[:MAX_RESPONSE_CHARS - 1].rstrip() + "…"
        if not text:
            corrections.append("empty_replaced")
            text = _GENERIC_UNAVAILABLE
        return ComposedResponse(text, draft.intent, draft.source, tuple(corrections))
