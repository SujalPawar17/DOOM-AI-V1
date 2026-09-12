"""V8 production request seam. Fail closed. Does not invent identity or adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from orchestration.executor import ExecutionIdentity, execute_plan, validate_execution_identity
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.kernel import process_goal
from orchestration.goal.plan_types import GoalPlan
from orchestration.goal.planner import plan_goal
from orchestration.goal.planner_errors import PlannerStatus
from orchestration.goal.types import IntentClass
from proactive.config import is_v8_enabled


def _text(status: str, reason: str = "") -> str:
    body = str(status or "BLOCKED")
    extra = str(reason or "").strip()[:64]
    if extra and extra != body:
        return f"[V8] {body}: {extra}"
    return f"[V8] {body}"


@dataclass(frozen=True)
class V8PrepareResult:
    status: str
    reason: str
    plan: Optional[GoalPlan]
    intent: str


def prepare_v8_request(user_input: str, *, identity: Any = None) -> V8PrepareResult:
    """Classify and plan only. Does not authorize or execute."""
    if not is_v8_enabled():
        return V8PrepareResult(ExecutionStatus.V8_DISABLED.value, "", None, "")
    ident_status = validate_execution_identity(identity)
    if ident_status is not None:
        return V8PrepareResult(ident_status.value, "IDENTITY_INTEGRATION", None, "")
    if type(identity) is not ExecutionIdentity:
        return V8PrepareResult(ExecutionStatus.IDENTITY_REQUIRED.value, "IDENTITY_INTEGRATION", None, "")

    classified = process_goal(
        user_input,
        context={
            "owner_id": identity.owner_id,
            "session_id": identity.session_id,
            "computer_session_id": identity.computer_session_id,
        },
    )
    if classified.goal is None:
        return V8PrepareResult("BLOCKED", classified.reason_code, None, "")
    intent = classified.goal.normalized_intent.value
    if classified.goal.normalized_intent in (IntentClass.UNKNOWN, IntentClass.AMBIGUOUS):
        return V8PrepareResult(classified.reason_code or classified.status.value, "", None, intent)

    planner_context = None
    if classified.goal.normalized_intent is IntentClass.COMPUTER:
        raw = str(classified.goal.raw_intent or "").lower()
        if any(tok in raw for tok in ("click", "press", "type")):
            from orchestration.observation import computer_planner_context
            planner_context, obs_status = computer_planner_context(identity)
            if obs_status != "OK":
                return V8PrepareResult(obs_status, "COMPUTER_SESSION_INTEGRATION_BLOCKED", None, intent)

    proposal = plan_goal(classified.goal, planner_context)
    if proposal.plan is None or proposal.status is not PlannerStatus.SUCCESS:
        return V8PrepareResult(proposal.status.value, proposal.reason_code, None, intent)
    return V8PrepareResult("OK", "", proposal.plan, intent)


def handle_v8_enabled_request(
    user_input: str,
    *,
    identity: Any = None,
    authorized_plan_hash: str = "",
) -> str:
    """Run the V8 path only. Never falls back to CognitiveEngine or legacy tools.

    Identity must be supplied by the trusted caller. It is not read from user
    text, GoalSpec, memory, audit, ledger, or environment defaults.
    """
    prep = prepare_v8_request(user_input, identity=identity)
    if prep.plan is None:
        return _text(prep.status, prep.reason)

    result = execute_plan(
        prep.plan,
        identity=identity,
        authorized_plan_hash=str(authorized_plan_hash or ""),
    )
    extra = prep.intent if result.status is ExecutionStatus.SUCCESS else ""
    return _text(result.status.value, extra)
