"""ASK-bound MEDIUM computer plan authorization. Not identity. Not V7 execution."""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional, Tuple

from orchestration.executor import ExecutionIdentity, validate_execution_identity
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.plan_registry import RISK_RANK, policy_risk
from orchestration.goal.plan_types import GoalPlan
from orchestration.goal.plan_validator import hash_goal_plan
from orchestration.identity import verified_computer_session_id

AUTHORIZATION_TTL_SECONDS = 120
APPROVABLE = frozenset({("computer", "CLICK"), ("computer", "TYPE")})
_SECRET_NAME = re.compile(r"password|secret|token|cookie|csrf|session", re.I)

_LOCK = threading.Lock()
_PENDING: Dict[str, "_Pending"] = {}


@dataclass
class _Pending:
    pending_id: str
    plan: GoalPlan
    owner_id: str
    ask_session_id: str
    computer_session_id: str
    plan_hash: str
    created_at: float
    expires_at: float
    state: str
    authorization_id: str


@dataclass(frozen=True)
class AuthorizationClaim:
    plan: GoalPlan
    identity: ExecutionIdentity
    authorized_plan_hash: str
    authorization_id: str


def reset_authorization_store_for_tests() -> None:
    with _LOCK:
        _PENDING.clear()


def replace_pending_plan_for_tests(pending_id: str, plan: GoalPlan) -> None:
    with _LOCK:
        rec = _PENDING.get(pending_id)
        if rec is None:
            return
        rec.plan = plan


def expire_pending_for_tests(pending_id: str, *, now: float = 0.0) -> None:
    with _LOCK:
        rec = _PENDING.get(pending_id)
        if rec is None:
            return
        rec.expires_at = float(now)


def safe_plan_display(plan: GoalPlan) -> Dict[str, str]:
    if type(plan) is not GoalPlan or not plan.steps:
        return {"action": "", "control_type": "", "name": "", "text": "", "risk": ""}
    step = plan.steps[0]
    params = dict(step.parameters)
    name = str(params.get("name") or "")[:80]
    control = str(params.get("control_type") or "")[:40]
    text = ""
    if step.action == "TYPE":
        raw = str(params.get("text") or "")[:256]
        if _SECRET_NAME.search(name) or _SECRET_NAME.search(control):
            text = ""
        else:
            text = raw
    return {
        "action": str(step.action or "")[:16],
        "control_type": control,
        "name": name,
        "text": text,
        "risk": str(plan.plan_risk or "")[:16],
    }


def medium_computer_approvable(plan: Any) -> Tuple[bool, str]:
    if type(plan) is not GoalPlan:
        return False, ExecutionStatus.INVALID_PLAN.value
    if plan.plan_risk in ("HIGH", "CRITICAL"):
        return False, ExecutionStatus.RISK_NOT_APPROVABLE.value
    if plan.plan_risk != "MEDIUM":
        return False, ExecutionStatus.RISK_NOT_APPROVABLE.value
    if len(plan.steps) != 1:
        return False, ExecutionStatus.RISK_NOT_APPROVABLE.value
    step = plan.steps[0]
    if (step.capability_id, step.action) not in APPROVABLE:
        return False, ExecutionStatus.RISK_NOT_APPROVABLE.value
    if policy_risk(step.capability_id, step.action) != "MEDIUM":
        return False, ExecutionStatus.RISK_NOT_APPROVABLE.value
    if RISK_RANK.get(step.risk, 4) != RISK_RANK["MEDIUM"]:
        return False, ExecutionStatus.RISK_NOT_APPROVABLE.value
    if not str(plan.computer_session_id or "").strip():
        return False, ExecutionStatus.COMPUTER_SESSION_REQUIRED.value
    return True, "OK"


def stash_pending_plan(plan: GoalPlan, identity: ExecutionIdentity) -> Tuple[Optional[str], str]:
    bad = validate_execution_identity(identity)
    if bad is not None:
        return None, bad.value
    ok, code = medium_computer_approvable(plan)
    if not ok:
        return None, code
    try:
        digest = hash_goal_plan(plan)
    except Exception:
        return None, ExecutionStatus.INVALID_PLAN.value
    if digest != plan.plan_hash:
        return None, ExecutionStatus.PLAN_HASH_MISMATCH.value
    if identity.owner_id != plan.owner_id or identity.session_id != plan.session_id:
        return None, ExecutionStatus.AUTHORIZATION_INVALID.value
    if identity.computer_session_id != plan.computer_session_id:
        return None, ExecutionStatus.SESSION_UNAVAILABLE.value
    live, live_err = verified_computer_session_id(identity.owner_id, plan.computer_session_id)
    if live is None:
        return None, live_err
    plan = replace(plan, approved=False, execution_permitted=False)
    now = time.time()
    pending_id = str(uuid.uuid4())
    rec = _Pending(
        pending_id=pending_id,
        plan=plan,
        owner_id=identity.owner_id,
        ask_session_id=identity.session_id,
        computer_session_id=live,
        plan_hash=digest,
        created_at=now,
        expires_at=now + AUTHORIZATION_TTL_SECONDS,
        state="PENDING",
        authorization_id="",
    )
    with _LOCK:
        _PENDING[pending_id] = rec
    return pending_id, "OK"


def pending_display(pending_id: str, identity: ExecutionIdentity) -> Tuple[Optional[Dict[str, str]], str]:
    bad = validate_execution_identity(identity)
    if bad is not None:
        return None, bad.value
    with _LOCK:
        rec = _PENDING.get(str(pending_id or ""))
        if rec is None:
            return None, ExecutionStatus.PLAN_NOT_FOUND.value
        if rec.owner_id != identity.owner_id or rec.ask_session_id != identity.session_id:
            return None, ExecutionStatus.AUTHORIZATION_INVALID.value
        if rec.state == "REVOKED":
            return None, ExecutionStatus.AUTHORIZATION_REVOKED.value
        if rec.state == "CONSUMED":
            return None, ExecutionStatus.AUTHORIZATION_CONSUMED.value
        if rec.expires_at <= time.time() or rec.state == "EXPIRED":
            rec.state = "EXPIRED"
            return None, ExecutionStatus.AUTHORIZATION_EXPIRED.value
        display = safe_plan_display(rec.plan)
    return display, "OK"


def revoke_pending(pending_id: str, identity: ExecutionIdentity) -> str:
    bad = validate_execution_identity(identity)
    if bad is not None:
        return bad.value
    with _LOCK:
        rec = _PENDING.get(str(pending_id or ""))
        if rec is None:
            return ExecutionStatus.PLAN_NOT_FOUND.value
        if rec.owner_id != identity.owner_id or rec.ask_session_id != identity.session_id:
            return ExecutionStatus.AUTHORIZATION_INVALID.value
        if rec.state == "CONSUMED":
            return ExecutionStatus.AUTHORIZATION_CONSUMED.value
        rec.state = "REVOKED"
    return "OK"


def claim_authorization(
    pending_id: str,
    identity: ExecutionIdentity,
    *,
    requested_computer_session_id: str = "",
) -> Tuple[Optional[AuthorizationClaim], str]:
    """Atomically consume a pending MEDIUM computer authorization. Does not execute."""
    bad = validate_execution_identity(identity)
    if bad is not None:
        return None, bad.value
    pid = str(pending_id or "").strip()
    if not pid:
        return None, ExecutionStatus.PLAN_NOT_FOUND.value
    supplied_cs = str(requested_computer_session_id or "").strip()
    with _LOCK:
        rec = _PENDING.get(pid)
        if rec is None:
            return None, ExecutionStatus.PLAN_NOT_FOUND.value
        now = time.time()
        if rec.state == "CONSUMED":
            return None, ExecutionStatus.AUTHORIZATION_CONSUMED.value
        if rec.state == "REVOKED":
            return None, ExecutionStatus.AUTHORIZATION_REVOKED.value
        if rec.expires_at <= now or rec.state == "EXPIRED":
            rec.state = "EXPIRED"
            return None, ExecutionStatus.AUTHORIZATION_EXPIRED.value
        if rec.owner_id != identity.owner_id or rec.ask_session_id != identity.session_id:
            return None, ExecutionStatus.AUTHORIZATION_INVALID.value
        if identity.computer_session_id and identity.computer_session_id != rec.computer_session_id:
            return None, ExecutionStatus.AUTHORIZATION_INVALID.value
        if supplied_cs and supplied_cs != rec.computer_session_id:
            return None, ExecutionStatus.AUTHORIZATION_INVALID.value
        live, live_err = verified_computer_session_id(rec.owner_id, rec.computer_session_id)
        if live is None:
            return None, live_err
        if live != rec.computer_session_id:
            return None, ExecutionStatus.SESSION_UNAVAILABLE.value
        plan = rec.plan
        try:
            digest = hash_goal_plan(plan)
        except Exception:
            return None, ExecutionStatus.INVALID_PLAN.value
        if digest != plan.plan_hash or digest != rec.plan_hash:
            return None, ExecutionStatus.PLAN_HASH_MISMATCH.value
        ok, code = medium_computer_approvable(plan)
        if not ok:
            return None, code
        if plan.owner_id != rec.owner_id or plan.session_id != rec.ask_session_id:
            return None, ExecutionStatus.AUTHORIZATION_INVALID.value
        if plan.computer_session_id != rec.computer_session_id:
            return None, ExecutionStatus.SESSION_UNAVAILABLE.value
        rec.state = "CONSUMED"
        rec.authorization_id = str(uuid.uuid4())
        bound = ExecutionIdentity(
            owner_id=rec.owner_id,
            session_id=rec.ask_session_id,
            computer_session_id=live,
        )
        claim = AuthorizationClaim(
            plan=plan,
            identity=bound,
            authorized_plan_hash=plan.plan_hash,
            authorization_id=rec.authorization_id,
        )
    return claim, "OK"
