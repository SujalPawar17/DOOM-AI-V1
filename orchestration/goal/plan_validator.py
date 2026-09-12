"""V8.2 GoalPlan validator and factory. Validation is not execution."""

from __future__ import annotations

import math
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from orchestration.goal.plan_errors import PlanValidationError
from orchestration.goal.plan_hashing import plan_hash
from orchestration.goal.plan_registry import (
    ALLOWED_ACTIONS,
    ALLOWED_CAPABILITIES,
    FORBIDDEN_PARAM_KEYS,
    IDEMPOTENT_RETRY,
    MAX_DEPENDENCY_DEPTH,
    MAX_IDEMPOTENT_RETRIES,
    MAX_PARAM_CHARS,
    MAX_STEPS,
    MAX_TIMEOUT_MS,
    MIN_TIMEOUT_MS,
    MUTATION_ACTIONS,
    PLAN_SCHEMA_VERSION,
    RISK_RANK,
    VERIFY_ACTIONS,
    aggregate_risk,
    allowed_params,
    policy_risk,
)
from orchestration.goal.plan_types import GoalPlan, ParamValue, PlanStep
from orchestration.goal.types import GoalSpec, IntentClass


def _fail(code: str) -> None:
    raise PlanValidationError(code)


def _as_timeout(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        _fail("INVALID_TIMEOUT")
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("INVALID_TIMEOUT")
        value = int(value)
    if not isinstance(value, int):
        _fail("INVALID_TIMEOUT")
    if value < MIN_TIMEOUT_MS or value > MAX_TIMEOUT_MS:
        _fail("INVALID_TIMEOUT")
    return int(value)


def _as_retry(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("INVALID_RETRY")
        value = int(value)
    if not isinstance(value, int):
        _fail("INVALID_RETRY")
    if value < 0 or value > MAX_IDEMPOTENT_RETRIES:
        _fail("INVALID_RETRY")
    return int(value)


def _freeze_params(raw: Any, capability: str, action: str) -> Tuple[Tuple[str, ParamValue], ...]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        _fail("INVALID_PARAMETERS")
    allowed = allowed_params(capability, action)
    items: List[Tuple[str, ParamValue]] = []
    for key, value in raw.items():
        name = str(key)
        low = name.lower()
        if low in FORBIDDEN_PARAM_KEYS:
            _fail("FORBIDDEN_PARAMETER")
        if name not in allowed:
            _fail("UNKNOWN_PARAMETER")
        if callable(value):
            _fail("FORBIDDEN_PARAMETER")
        if isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
            _fail("FORBIDDEN_PARAMETER")
        if isinstance(value, bool):
            items.append((name, bool(value)))
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            items.append((name, int(value)))
            continue
        if isinstance(value, float):
            _fail("FORBIDDEN_PARAMETER")
        text = str(value)
        if len(text) > MAX_PARAM_CHARS:
            _fail("PARAMETER_TOO_LONG")
        items.append((name, text))
    items.sort(key=lambda kv: kv[0])
    return tuple(items)


def _longest_path(ids: Sequence[str], deps: Mapping[str, Tuple[str, ...]]) -> int:
    memo: Dict[str, int] = {}
    visiting = set()

    def depth(sid: str) -> int:
        if sid in visiting:
            _fail("CYCLIC_DEPENDENCY")
        if sid in memo:
            return memo[sid]
        visiting.add(sid)
        kids = deps.get(sid, ())
        best = 0
        for kid in kids:
            best = max(best, 1 + depth(kid))
        visiting.remove(sid)
        memo[sid] = best
        return best

    longest = 0
    for sid in ids:
        longest = max(longest, depth(sid))
    return longest


def _normalize_step(raw: Mapping[str, Any], known_ids: Sequence[str]) -> PlanStep:
    sid = str(raw.get("step_id") or "").strip()
    if not sid or len(sid) > 64:
        _fail("INVALID_STEP_ID")
    cap = str(raw.get("capability_id") or "").strip()
    if cap not in ALLOWED_CAPABILITIES:
        _fail("UNKNOWN_CAPABILITY")
    action = str(raw.get("action") or "").strip()
    if action not in ALLOWED_ACTIONS.get(cap, frozenset()):
        _fail("UNKNOWN_ACTION")
    params = _freeze_params(raw.get("parameters"), cap, action)
    deps_raw = raw.get("dependencies") or ()
    if not isinstance(deps_raw, (list, tuple)):
        _fail("INVALID_DEPENDENCIES")
    deps = tuple(str(d) for d in deps_raw)
    if sid in deps:
        _fail("SELF_DEPENDENCY")
    known = set(known_ids)
    for d in deps:
        if d not in known:
            _fail("UNKNOWN_DEPENDENCY")
    vreq = bool(raw.get("verification_required", False))
    vtype = str(raw.get("verification_type") or "")
    if vtype and vtype not in VERIFY_ACTIONS:
        _fail("INVALID_VERIFICATION")
    if cap == "verification" and action not in VERIFY_ACTIONS:
        _fail("UNKNOWN_ACTION")
    policy = policy_risk(cap, action)
    claimed = str(raw.get("risk") or policy).upper()
    if claimed not in RISK_RANK:
        claimed = policy
    if RISK_RANK[claimed] < RISK_RANK[policy]:
        risk = policy
    else:
        risk = claimed if claimed in RISK_RANK else policy
    approval_step = bool(raw.get("approval_required", False)) or risk in ("MEDIUM", "HIGH", "CRITICAL")
    retry = _as_retry(raw.get("retry_count", 0))
    pair = (cap, action)
    if pair in MUTATION_ACTIONS and retry != 0:
        _fail("MUTATION_RETRY_FORBIDDEN")
    if pair not in IDEMPOTENT_RETRY and retry != 0:
        _fail("RETRY_FORBIDDEN")
    timeout = _as_timeout(raw.get("timeout_ms", 8000))
    return PlanStep(
        step_id=sid,
        capability_id=cap,
        action=action,
        parameters=params,
        dependencies=deps,
        verification_required=vreq,
        verification_type=vtype,
        risk=risk,
        approval_required=approval_step,
        retry_count=retry,
        timeout_ms=timeout,
    )


def _fields_for_hash(goal: GoalSpec, steps: Sequence[PlanStep], plan_risk: str, approval_required: bool) -> Dict[str, Any]:
    return {
        "approval_required": approval_required,
        "computer_session_id": goal.computer_session_id,
        "goal_id": goal.goal_id,
        "owner_id": goal.owner_id,
        "plan_risk": plan_risk,
        "provenance": "GOAL_PLAN",
        "schema_version": PLAN_SCHEMA_VERSION,
        "session_id": goal.session_id,
        "steps": [
            {
                "action": s.action,
                "approval_required": s.approval_required,
                "capability_id": s.capability_id,
                "dependencies": s.dependencies,
                "parameters": s.parameters,
                "retry_count": s.retry_count,
                "risk": s.risk,
                "step_id": s.step_id,
                "timeout_ms": s.timeout_ms,
                "verification_required": s.verification_required,
                "verification_type": s.verification_type,
            }
            for s in steps
        ],
    }


def build_goal_plan(
    goal: GoalSpec,
    steps: Iterable[Mapping[str, Any]],
    *,
    plan_id: str = "",
    claimed_plan_risk: str = "",
) -> GoalPlan:
    """Construct a validated immutable GoalPlan. Does not execute and does not authorize."""
    if not isinstance(goal, GoalSpec):
        _fail("INVALID_GOAL")
    if goal.normalized_intent in (IntentClass.UNKNOWN, IntentClass.AMBIGUOUS):
        _fail("INVALID_GOAL_INTENT")
    raw_steps = list(steps)
    if not raw_steps:
        _fail("EMPTY_PLAN")
    if len(raw_steps) > MAX_STEPS:
        _fail("TOO_MANY_STEPS")
    ids = [str(s.get("step_id") or "").strip() for s in raw_steps]
    if len(set(ids)) != len(ids):
        _fail("DUPLICATE_STEP_ID")
    built: List[PlanStep] = []
    for item in raw_steps:
        if not isinstance(item, Mapping):
            _fail("INVALID_STEP")
        built.append(_normalize_step(item, ids))
    dep_map = {s.step_id: s.dependencies for s in built}
    depth = _longest_path([s.step_id for s in built], dep_map)
    if depth > MAX_DEPENDENCY_DEPTH:
        _fail("DEPENDENCY_TOO_DEEP")
    risks = tuple(s.risk for s in built)
    plan_risk = aggregate_risk(risks)
    if claimed_plan_risk:
        claimed = str(claimed_plan_risk).upper()
        if claimed in RISK_RANK and RISK_RANK[claimed] > RISK_RANK[plan_risk]:
            plan_risk = claimed
    approval_required = plan_risk in ("MEDIUM", "HIGH", "CRITICAL") or any(s.approval_required for s in built)
    digest = plan_hash(_fields_for_hash(goal, built, plan_risk, approval_required))
    pid = str(plan_id or uuid.uuid4())[:64]
    return GoalPlan(
        plan_id=pid,
        goal_id=goal.goal_id,
        schema_version=PLAN_SCHEMA_VERSION,
        owner_id=goal.owner_id,
        session_id=goal.session_id,
        computer_session_id=goal.computer_session_id,
        steps=tuple(built),
        plan_risk=plan_risk,
        approval_required=approval_required,
        provenance="GOAL_PLAN",
        plan_hash=digest,
        execution_permitted=False,
        approved=False,
    )


def validate_plan_hash(plan: GoalPlan, goal: GoalSpec) -> None:
    digest = plan_hash(_fields_for_hash(goal, plan.steps, plan.plan_risk, plan.approval_required))
    if digest != plan.plan_hash:
        _fail("PLAN_HASH_MISMATCH")


def hash_goal_plan(plan: GoalPlan) -> str:
    """Same canonical hash as V8.2; plan_id is not semantic."""
    holder = type("GoalLike", (), {
        "goal_id": plan.goal_id,
        "owner_id": plan.owner_id,
        "session_id": plan.session_id,
        "computer_session_id": plan.computer_session_id,
    })()
    return plan_hash(_fields_for_hash(holder, plan.steps, plan.plan_risk, plan.approval_required))
