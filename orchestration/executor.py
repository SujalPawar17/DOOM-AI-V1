"""V8.4 execution orchestrator. Routes GoalPlan to public capability boundaries. Not a kernel."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.plan_registry import (
    ALLOWED_ACTIONS,
    ALLOWED_CAPABILITIES,
    FORBIDDEN_PARAM_KEYS,
    MAX_TIMEOUT_MS,
    MIN_TIMEOUT_MS,
    MUTATION_ACTIONS,
    PLAN_SCHEMA_VERSION,
    RISK_RANK,
    VERIFY_ACTIONS,
    allowed_params,
    policy_risk,
)
from orchestration.goal.plan_types import GoalPlan, PlanStep
from orchestration.goal.plan_validator import hash_goal_plan
from proactive.config import is_v8_enabled

_LOCK = threading.Lock()
_HOOK_LOCK = threading.Lock()
_LEDGER: Dict[str, str] = {}
_MAX_LEDGER = 256
IDENTITY_MAX_LEN = 64

Adapter = Callable[[PlanStep, GoalPlan], str]


@dataclass(frozen=True)
class ExecutionIdentity:
    """Trusted caller identity. Not derived from plans, context, audit, or hashes."""

    owner_id: str
    session_id: str
    computer_session_id: str = ""

    def as_public(self) -> Dict[str, Any]:
        return {
            "owner_id": self.owner_id,
            "session_id": self.session_id,
            "computer_session_id": self.computer_session_id,
            "authorizes_execution": False,
            "approved": False,
        }


@dataclass
class _TestHookState:
    adapters: Optional[Dict[str, Adapter]] = None
    emergency_stop_fn: Optional[Callable[[str, str], bool]] = None
    cancelled_fn: Optional[Callable[[], bool]] = None


_TEST_HOOKS = _TestHookState()


def reset_test_execution_hooks() -> None:
    """Clear process-local test hooks. Not a production execution API."""
    with _HOOK_LOCK:
        _TEST_HOOKS.adapters = None
        _TEST_HOOKS.emergency_stop_fn = None
        _TEST_HOOKS.cancelled_fn = None


class use_test_execution_hooks:
    """Isolated test spies. Not exported from orchestration.__init__. Not callable via execute_plan()."""

    def __init__(
        self,
        *,
        adapters: Optional[Dict[str, Adapter]] = None,
        emergency_stop_fn: Optional[Callable[[str, str], bool]] = None,
        cancelled_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._new = _TestHookState(
            dict(adapters) if adapters is not None else None,
            emergency_stop_fn,
            cancelled_fn,
        )
        self._old: Optional[_TestHookState] = None

    def __enter__(self) -> "use_test_execution_hooks":
        with _HOOK_LOCK:
            self._old = _TestHookState(
                _TEST_HOOKS.adapters,
                _TEST_HOOKS.emergency_stop_fn,
                _TEST_HOOKS.cancelled_fn,
            )
            _TEST_HOOKS.adapters = self._new.adapters
            _TEST_HOOKS.emergency_stop_fn = self._new.emergency_stop_fn
            _TEST_HOOKS.cancelled_fn = self._new.cancelled_fn
        return self

    def __exit__(self, *exc: Any) -> bool:
        with _HOOK_LOCK:
            old = self._old or _TestHookState()
            _TEST_HOOKS.adapters = old.adapters
            _TEST_HOOKS.emergency_stop_fn = old.emergency_stop_fn
            _TEST_HOOKS.cancelled_fn = old.cancelled_fn
        return False


def validate_execution_identity(identity: Any) -> Optional[ExecutionStatus]:
    if type(identity) is not ExecutionIdentity:
        return ExecutionStatus.IDENTITY_REQUIRED
    for value in (identity.owner_id, identity.session_id):
        if type(value) is not str:
            return ExecutionStatus.IDENTITY_REQUIRED
        text = value.strip()
        if not text or text != value or "\x00" in value or len(value) > IDENTITY_MAX_LEN:
            return ExecutionStatus.IDENTITY_REQUIRED
    cs = identity.computer_session_id
    if type(cs) is not str or "\x00" in cs or len(cs) > IDENTITY_MAX_LEN:
        return ExecutionStatus.IDENTITY_REQUIRED
    if cs != cs.strip():
        return ExecutionStatus.IDENTITY_REQUIRED
    return None


def bind_execution_identity(identity: ExecutionIdentity, plan: GoalPlan) -> Optional[ExecutionStatus]:
    if identity.owner_id != plan.owner_id:
        return ExecutionStatus.SESSION_UNAVAILABLE
    if identity.session_id != plan.session_id:
        return ExecutionStatus.SESSION_UNAVAILABLE
    needs_computer = any(_needs_computer_session(s) for s in plan.steps)
    if needs_computer:
        if not identity.computer_session_id or identity.computer_session_id != plan.computer_session_id:
            return ExecutionStatus.SESSION_UNAVAILABLE
    elif identity.computer_session_id and plan.computer_session_id:
        if identity.computer_session_id != plan.computer_session_id:
            return ExecutionStatus.SESSION_UNAVAILABLE
    return None


@dataclass(frozen=True)
class StepExecutionRecord:
    step_id: str
    capability_id: str
    action: str
    status: str
    attempts: int
    verification_status: str

    def as_public(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "capability_id": self.capability_id,
            "action": self.action,
            "status": self.status,
            "attempts": self.attempts,
            "verification_status": self.verification_status,
        }


@dataclass(frozen=True)
class ExecutionResult:
    execution_id: str
    goal_id: str
    plan_hash: str
    status: ExecutionStatus
    plan_risk: str
    approval_required: bool
    approval_granted: bool
    completed_step_ids: Tuple[str, ...]
    failed_step_id: str
    steps: Tuple[StepExecutionRecord, ...]
    verification_state: str
    started_unix_ms: int
    ended_unix_ms: int

    def as_public(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "goal_id": self.goal_id,
            "plan_hash": self.plan_hash,
            "status": self.status.value,
            "plan_risk": self.plan_risk,
            "approval_required": self.approval_required,
            "approval_granted": self.approval_granted,
            "completed_step_ids": list(self.completed_step_ids),
            "failed_step_id": self.failed_step_id,
            "steps": [s.as_public() for s in self.steps],
            "verification_state": self.verification_state,
            "started_unix_ms": self.started_unix_ms,
            "ended_unix_ms": self.ended_unix_ms,
            "execution_permitted_field_ignored": True,
        }


def reset_execution_ledger_for_tests() -> None:
    with _LOCK:
        _LEDGER.clear()


def _ledger_key(plan: GoalPlan, step_id: str) -> str:
    return "|".join((plan.owner_id, plan.goal_id, plan.plan_hash, step_id))


def _result(
    plan: Optional[GoalPlan],
    status: ExecutionStatus,
    *,
    eid: str = "",
    completed: Tuple[str, ...] = (),
    failed: str = "",
    steps: Tuple[StepExecutionRecord, ...] = (),
    verification: str = "",
    approval_granted: bool = False,
    start: int = 0,
    end: int = 0,
) -> ExecutionResult:
    now = int(time.time() * 1000)
    return ExecutionResult(
        execution_id=eid or str(uuid.uuid4()),
        goal_id="" if plan is None else plan.goal_id,
        plan_hash="" if plan is None else plan.plan_hash,
        status=status,
        plan_risk="" if plan is None else plan.plan_risk,
        approval_required=False if plan is None else plan.approval_required,
        approval_granted=approval_granted,
        completed_step_ids=completed,
        failed_step_id=failed,
        steps=steps,
        verification_state=verification,
        started_unix_ms=start or now,
        ended_unix_ms=end or now,
    )


def _needs_computer_session(step: PlanStep) -> bool:
    return step.capability_id in (
        "computer", "browser", "filesystem", "sequence", "verification",
    )


def _policy_needs_approval(plan: GoalPlan) -> bool:
    if plan.approval_required:
        return True
    for step in plan.steps:
        if step.approval_required:
            return True
        rank = RISK_RANK.get(policy_risk(step.capability_id, step.action), 4)
        if rank >= RISK_RANK["MEDIUM"]:
            return True
    return False


def _revalidate(plan: GoalPlan) -> Optional[ExecutionStatus]:
    if plan.schema_version != PLAN_SCHEMA_VERSION:
        return ExecutionStatus.INVALID_PLAN
    if not plan.steps:
        return ExecutionStatus.INVALID_PLAN
    ids = [s.step_id for s in plan.steps]
    if len(set(ids)) != len(ids):
        return ExecutionStatus.INVALID_PLAN
    known = set(ids)
    for step in plan.steps:
        if step.capability_id not in ALLOWED_CAPABILITIES:
            return ExecutionStatus.CAPABILITY_UNAVAILABLE
        if step.action not in ALLOWED_ACTIONS.get(step.capability_id, frozenset()):
            return ExecutionStatus.ACTION_UNAVAILABLE
        allowed = allowed_params(step.capability_id, step.action)
        for key, _value in step.parameters:
            if str(key).lower() in FORBIDDEN_PARAM_KEYS:
                return ExecutionStatus.INVALID_PLAN
            if key not in allowed:
                return ExecutionStatus.INVALID_PLAN
        if step.verification_required:
            vtype = str(step.verification_type or "")
            if vtype and vtype not in VERIFY_ACTIONS:
                return ExecutionStatus.INVALID_PLAN
        pair = (step.capability_id, step.action)
        if pair in MUTATION_ACTIONS and int(step.retry_count) != 0:
            return ExecutionStatus.INVALID_PLAN
        if int(step.timeout_ms) < MIN_TIMEOUT_MS or int(step.timeout_ms) > MAX_TIMEOUT_MS:
            return ExecutionStatus.INVALID_PLAN
        policy = policy_risk(step.capability_id, step.action)
        if RISK_RANK.get(step.risk, 0) < RISK_RANK.get(policy, 4):
            return ExecutionStatus.INVALID_PLAN
        for dep in step.dependencies:
            if dep not in known:
                return ExecutionStatus.INVALID_PLAN
    return None


def _default_estop(owner: str, session: str) -> bool:
    if not session:
        return False
    from proactive.store import proactive_store
    row = proactive_store.get_computer_session(session[:64], owner[:64])
    return bool(row and (row.get("emergency_stop") or str(row.get("status") or "") == "STOPPED"))


def _default_computer(step: PlanStep, plan: GoalPlan) -> str:
    from proactive.computer.actions.kernel import execute_computer_action
    from proactive.computer.actions.types import ActionType, ApprovalState, ComputerActionRequest, TargetIdentity
    params = dict(step.parameters)
    if step.action == "OBSERVE":
        from proactive.computer.observe import capture_observation
        from proactive.computer.session import get_session
        sid = str(params.get("session_id") or plan.computer_session_id)[:64]
        row = get_session(sid, plan.owner_id)
        if not row:
            return ExecutionStatus.SESSION_UNAVAILABLE.value
        status = str(row.get("status") or "").upper()
        if status in ("STOPPED", "EXPIRED", "REVOKED", "CANCELLED"):
            return ExecutionStatus.SESSION_UNAVAILABLE.value
        obs, _code = capture_observation(plan.owner_id, computer_session_id=sid)
        if obs is None:
            return ExecutionStatus.STEP_FAILED.value
        return ExecutionStatus.SUCCESS.value
    from orchestration.identity import verified_computer_session_id
    live, live_err = verified_computer_session_id(plan.owner_id, plan.computer_session_id)
    if live is None:
        return live_err
    req = ComputerActionRequest(
        action_id=step.step_id,
        action_type=ActionType(step.action),
        target=TargetIdentity(
            automation_id=str(params.get("automation_id") or ""),
            runtime_id=str(params.get("runtime_id") or ""),
            control_type=str(params.get("control_type") or ""),
            name=str(params.get("name") or ""),
        ),
        text=str(params.get("text") or ""),
        owner_id=plan.owner_id,
        session_id=str(params.get("session_id") or plan.computer_session_id),
        timeout_ms=step.timeout_ms,
        approval_state=ApprovalState.APPROVED,
        precondition_observation_hash=str(params.get("precondition_observation_hash") or ""),
    )
    out = execute_computer_action(req)
    err = str(getattr(out, "error_code", "") or "")
    if err == "STALE_OBSERVATION_HASH":
        return ExecutionStatus.STALE_OBSERVATION_HASH.value
    stat = getattr(out.status, "value", str(out.status))
    if stat == "EMERGENCY_STOP_ACTIVE":
        return ExecutionStatus.EMERGENCY_STOPPED.value
    return stat


def _default_browser(step: PlanStep, plan: GoalPlan) -> str:
    from proactive.computer.browser.kernel import execute_browser_action
    from proactive.computer.actions.types import ApprovalState
    from proactive.computer.browser.types import BrowserActionRequest, BrowserActionType, BrowserTarget
    params = dict(step.parameters)
    req = BrowserActionRequest(
        action_id=step.step_id,
        action_type=BrowserActionType(step.action),
        session_id=str(params.get("session_id") or plan.computer_session_id),
        owner_id=plan.owner_id,
        approval_state=ApprovalState.NONE,
        precondition_observation_hash=str(params.get("precondition_observation_hash") or ""),
        target=BrowserTarget(
            role=str(params.get("role") or ""),
            name=str(params.get("name") or ""),
            element_id=str(params.get("element_id") or ""),
            test_id=str(params.get("test_id") or ""),
        ),
        url=str(params.get("url") or ""),
        text=str(params.get("text") or ""),
    )
    out = execute_browser_action(req)
    return getattr(out.status, "value", str(out.status))


def _default_fs(step: PlanStep, plan: GoalPlan) -> str:
    from proactive.computer.fs.kernel import execute_fs_action
    from proactive.computer.actions.types import ApprovalState
    from proactive.computer.fs.types import FsActionRequest, FsActionType
    params = dict(step.parameters)
    req = FsActionRequest(
        action_id=step.step_id,
        action_type=FsActionType(step.action),
        path=str(params.get("path") or ""),
        owner_id=plan.owner_id,
        dest_path=str(params.get("dest_path") or ""),
        content=b"",
        precondition_observation_hash=str(params.get("precondition_observation_hash") or ""),
        approval_state=ApprovalState.NONE,
    )
    out = execute_fs_action(req)
    return getattr(out.status, "value", str(out.status))


def _default_sequence(step: PlanStep, plan: GoalPlan) -> str:
    from proactive.computer.actions.types import ApprovalState
    from proactive.computer.sequence.kernel import execute_sequence
    from proactive.computer.sequence.types import SequenceSpec

    if step.action != "DECLARE":
        return ExecutionStatus.ACTION_UNAVAILABLE.value
    spec = SequenceSpec(
        sequence_id=step.step_id,
        owner_id=plan.owner_id,
        steps=(),
        max_steps=1,
        timeout_ms=max(int(step.timeout_ms), 100),
        retry_count=0,
        computer_session_id=plan.computer_session_id,
    )
    out = execute_sequence(spec, ApprovalState.NONE, "")
    code = str(getattr(out, "failure_code", "") or "")
    status = getattr(out.status, "value", str(out.status))
    if code == "EMPTY_SEQUENCE":
        return ExecutionStatus.BLOCKED.value
    return str(status)


def _verification_spec_timeout_ms(step_timeout_ms: int) -> int:
    """Clamp plan-step timeout to the verification subsystem cap. Does not raise the cap."""
    from proactive.computer.policy import verify_timeout_ms
    cap = int(verify_timeout_ms())
    raw = int(step_timeout_ms or 0)
    return max(50, min(raw, cap))


def _default_verify(step: PlanStep, plan: GoalPlan) -> str:
    from proactive.computer.observe import capture_structured_observation
    from proactive.computer.verify.kernel import execute_verification
    from proactive.computer.verify.types import VerificationRequest, VerificationSpec
    params = dict(step.parameters)
    verify_timeout = _verification_spec_timeout_ms(int(step.timeout_ms))
    if step.capability_id == "computer":
        cap = "computer"
        vtype = str(step.verification_type or "").strip() or "TARGET_STATE_MATCH"
        expected = []
        for key in ("automation_id", "runtime_id", "control_type", "name"):
            val = params.get(key)
            if val:
                expected.append((str(key), str(val)))
        expected.append(("expected_outcome", "OK"))
        spec = VerificationSpec(
            verification_id=step.step_id,
            capability=cap,
            verification_type=vtype,
            expected=tuple(expected),
            timeout_ms=verify_timeout,
            max_attempts=1,
            owner_id=plan.owner_id,
        )
        out = execute_verification(
            VerificationRequest(spec=spec, action_id=step.step_id, computer_session_id=plan.computer_session_id),
            computer_fn=capture_structured_observation,
        )
        return getattr(out.status, "value", str(out.status))
    spec = VerificationSpec(
        verification_id=step.step_id,
        capability=str(params.get("domain") or "filesystem"),
        verification_type=step.action,
        expected=tuple((str(k), str(v)) for k, v in params.items()),
        timeout_ms=verify_timeout,
        max_attempts=1,
        owner_id=plan.owner_id,
    )
    out = execute_verification(VerificationRequest(spec=spec, action_id=step.step_id, computer_session_id=plan.computer_session_id))
    return getattr(out.status, "value", str(out.status))


def _default_world(step: PlanStep, plan: GoalPlan) -> str:
    from proactive.act_engine import request_run
    params = dict(step.parameters)
    action_id = str(params.get("title") or params.get("note") or "")
    if not action_id:
        return ExecutionStatus.ACTION_UNAVAILABLE.value
    out = request_run(action_id, plan.owner_id, plan.plan_hash)
    if not out.get("ok"):
        return ExecutionStatus.BLOCKED.value
    return ExecutionStatus.SUCCESS.value


def _default_memory(step: PlanStep, plan: GoalPlan) -> str:
    from memory.manager import memory_manager
    params = dict(step.parameters)
    memory_manager.retrieve(str(params.get("query") or ""))
    return ExecutionStatus.SUCCESS.value


def _default_conversation(step: PlanStep, plan: GoalPlan) -> str:
    return ExecutionStatus.SUCCESS.value


_DEFAULTS: Dict[str, Adapter] = {
    "computer": _default_computer,
    "browser": _default_browser,
    "filesystem": _default_fs,
    "sequence": _default_sequence,
    "verification": _default_verify,
    "world_act": _default_world,
    "memory_read": _default_memory,
    "conversation": _default_conversation,
}


def _topo(plan: GoalPlan) -> Optional[List[PlanStep]]:
    remaining = list(plan.steps)
    done: List[str] = []
    ordered: List[PlanStep] = []
    while remaining:
        ready = [s for s in remaining if all(d in done for d in s.dependencies)]
        if not ready:
            return None
        nxt = ready[0]
        ordered.append(nxt)
        done.append(nxt.step_id)
        remaining.remove(nxt)
    return ordered


def execute_plan(
    plan: Any,
    *,
    identity: Any = None,
    authorized_plan_hash: str = "",
) -> ExecutionResult:
    """Orchestrate a validated GoalPlan. Does not invent capabilities or rewrite the plan.

    Public callers cannot supply adapters or execution callbacks. Identity is required
    whenever V8 is enabled. Authorization remains authorized_plan_hash, not identity.
    """
    start = int(time.time() * 1000)
    if not is_v8_enabled():
        return _result(plan if type(plan) is GoalPlan else None, ExecutionStatus.V8_DISABLED, start=start)
    ident_status = validate_execution_identity(identity)
    if ident_status is not None:
        return _result(plan if type(plan) is GoalPlan else None, ident_status, start=start)
    if type(plan) is not GoalPlan:
        return _result(None, ExecutionStatus.INVALID_PLAN, start=start)
    bind_status = bind_execution_identity(identity, plan)
    if bind_status is not None:
        return _result(plan, bind_status, start=start)
    if plan.schema_version != PLAN_SCHEMA_VERSION:
        return _result(plan, ExecutionStatus.INVALID_PLAN, start=start)
    try:
        digest = hash_goal_plan(plan)
    except Exception:
        return _result(plan, ExecutionStatus.INVALID_PLAN, start=start)
    if digest != plan.plan_hash:
        return _result(plan, ExecutionStatus.PLAN_HASH_MISMATCH, start=start)
    invalid = _revalidate(plan)
    if invalid is not None:
        return _result(plan, invalid, start=start)
    if any(_needs_computer_session(s) for s in plan.steps) and not plan.computer_session_id:
        return _result(plan, ExecutionStatus.SESSION_UNAVAILABLE, start=start)
    approval_ok = str(authorized_plan_hash) == plan.plan_hash
    if _policy_needs_approval(plan) and not approval_ok:
        return _result(plan, ExecutionStatus.APPROVAL_REQUIRED, start=start, approval_granted=False)
    ordered = _topo(plan)
    if ordered is None:
        return _result(plan, ExecutionStatus.INVALID_PLAN, start=start)
    with _HOOK_LOCK:
        stop_fn = _TEST_HOOKS.emergency_stop_fn or _default_estop
        cancel = _TEST_HOOKS.cancelled_fn or (lambda: False)
        table = dict(_DEFAULTS) if _TEST_HOOKS.adapters is None else dict(_TEST_HOOKS.adapters)
    eid = str(uuid.uuid4())
    completed: List[str] = []
    records: List[StepExecutionRecord] = []
    succeeded: set = set()
    verify_state = ""

    if stop_fn(plan.owner_id, plan.computer_session_id or plan.session_id):
        return _result(plan, ExecutionStatus.EMERGENCY_STOPPED, eid=eid, start=start, approval_granted=approval_ok)

    for step in ordered:
        if cancel():
            return _result(
                plan, ExecutionStatus.ABORTED, eid=eid, completed=tuple(completed),
                steps=tuple(records), start=start, approval_granted=approval_ok,
            )
        if stop_fn(plan.owner_id, plan.computer_session_id or plan.session_id):
            return _result(
                plan, ExecutionStatus.EMERGENCY_STOPPED, eid=eid, completed=tuple(completed),
                steps=tuple(records), start=start, approval_granted=approval_ok,
            )
        if any(d not in succeeded for d in step.dependencies):
            rec = StepExecutionRecord(step.step_id, step.capability_id, step.action, ExecutionStatus.STEP_FAILED.value, 0, "")
            records.append(rec)
            return _result(
                plan, ExecutionStatus.STEP_FAILED, eid=eid, completed=tuple(completed),
                failed=step.step_id, steps=tuple(records), start=start, approval_granted=approval_ok,
            )
        key = _ledger_key(plan, step.step_id)
        with _LOCK:
            prior = _LEDGER.get(key)
        if prior == ExecutionStatus.SUCCESS.value:
            rec = StepExecutionRecord(step.step_id, step.capability_id, step.action, "SUCCESS", 0, "")
            records.append(rec)
            completed.append(step.step_id)
            succeeded.add(step.step_id)
            continue
        pair = (step.capability_id, step.action)
        max_try = 1 if pair in MUTATION_ACTIONS else (int(step.retry_count) + 1)
        adapter = table.get(step.capability_id)
        if adapter is None:
            return _result(plan, ExecutionStatus.CAPABILITY_UNAVAILABLE, eid=eid, start=start, approval_granted=approval_ok)
        status = ExecutionStatus.STEP_FAILED.value
        attempts = 0
        for _ in range(max_try):
            attempts += 1
            t0 = time.monotonic()
            status = str(adapter(step, plan) or ExecutionStatus.STEP_FAILED.value)
            if step.capability_id == "verification" and status == "VERIFIED":
                status = ExecutionStatus.SUCCESS.value
            if (time.monotonic() - t0) * 1000.0 > float(step.timeout_ms):
                status = ExecutionStatus.TIMEOUT.value
                break
            if status == ExecutionStatus.SUCCESS.value:
                break
            if pair in MUTATION_ACTIONS:
                break
        vstat = ""
        if status == ExecutionStatus.SUCCESS.value and step.verification_required:
            vfn = table.get("verification")
            if vfn is None:
                status = ExecutionStatus.NOT_VERIFIED.value
                vstat = ExecutionStatus.NOT_VERIFIED.value
            else:
                vstat = str(vfn(step, plan) or "")
                if vstat == "VERIFIED":
                    pass
                elif vstat == ExecutionStatus.VERIFICATION_FAILED.value:
                    status = ExecutionStatus.VERIFICATION_FAILED.value
                    verify_state = ExecutionStatus.VERIFICATION_FAILED.value
                else:
                    status = ExecutionStatus.NOT_VERIFIED.value
                    verify_state = ExecutionStatus.NOT_VERIFIED.value
        rec = StepExecutionRecord(step.step_id, step.capability_id, step.action, status, attempts, vstat)
        records.append(rec)
        if status == ExecutionStatus.SUCCESS.value:
            with _LOCK:
                if len(_LEDGER) >= _MAX_LEDGER:
                    _LEDGER.clear()
                _LEDGER[key] = ExecutionStatus.SUCCESS.value
            completed.append(step.step_id)
            succeeded.add(step.step_id)
            continue
        mapped = {
            ExecutionStatus.TIMEOUT.value: ExecutionStatus.TIMEOUT,
            ExecutionStatus.EMERGENCY_STOPPED.value: ExecutionStatus.EMERGENCY_STOPPED,
            ExecutionStatus.NOT_VERIFIED.value: ExecutionStatus.NOT_VERIFIED,
            ExecutionStatus.VERIFICATION_FAILED.value: ExecutionStatus.VERIFICATION_FAILED,
            ExecutionStatus.PRECONDITION_FAILED.value: ExecutionStatus.PRECONDITION_FAILED,
            ExecutionStatus.STALE_OBSERVATION_HASH.value: ExecutionStatus.STALE_OBSERVATION_HASH,
            ExecutionStatus.BLOCKED.value: ExecutionStatus.BLOCKED,
        }.get(status, ExecutionStatus.STEP_FAILED)
        return _result(
            plan, mapped, eid=eid, completed=tuple(completed), failed=step.step_id,
            steps=tuple(records), verification=verify_state or vstat, start=start,
            approval_granted=approval_ok,
        )
    overall = ExecutionStatus.SUCCESS
    if verify_state == ExecutionStatus.NOT_VERIFIED.value:
        overall = ExecutionStatus.NOT_VERIFIED
    elif verify_state == ExecutionStatus.VERIFICATION_FAILED.value:
        overall = ExecutionStatus.VERIFICATION_FAILED
    return _result(
        plan, overall, eid=eid, completed=tuple(completed), steps=tuple(records),
        verification=verify_state, start=start, approval_granted=approval_ok,
    )
