"""V7.5 sequence execution kernel. Composes V7.2/V7.3/V7.4 public kernels only."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.cost_guard import ResourceRequest, ResourceType, cost_guard
from proactive.computer.actions.kernel import execute_computer_action
from proactive.computer.actions.types import (
    ActionType,
    ApprovalState,
    ComputerActionRequest,
    TargetIdentity,
)
from proactive.computer.browser.kernel import execute_browser_action
from proactive.computer.browser.types import BrowserActionRequest, BrowserActionType, BrowserTarget
from proactive.computer.fs.kernel import execute_fs_action
from proactive.computer.fs.types import FsActionRequest, FsActionType, ObjectType
from proactive.computer.verify.kernel import execute_verification
from proactive.computer.policy import (
    SEQUENCE_SCHEMA_VERSION,
    sequence_max_retries,
    sequence_max_steps,
    sequence_timeout_ms,
    sequences_allowed,
)
from proactive.computer.sequence.registry import (
    ALLOWED,
    CAP_BROWSER,
    CAP_COMPUTER,
    CAP_FILESYSTEM,
    CAP_VERIFY,
    IDEMPOTENT_RETRY,
    MUTATION_ACTIONS,
    aggregate_risk,
    allowed_params,
)
from proactive.computer.sequence.types import (
    SequenceResult,
    SequenceSpec,
    SequenceStatus,
    SequenceStep,
    params_map,
)
from proactive.config import OWNER_ID
from proactive.store import proactive_store

FORBIDDEN_VALUE_MARKERS = tuple("".join(parts) for parts in (
    ("ev", "al("),
    ("ex", "ec("),
    ("com", "pile("),
    ("__", "import__"),
    ("import", "lib"),
    ("java", "script:"),
    ("sub", "process"),
    ("os.", "system"),
    ("power", "shell"),
    ("cmd", ".exe"),
    ("pick", "le"),
    ("mar", "shal"),
    ("lam", "bda "),
))

FORBIDDEN_KEYS = frozenset({
    "code", "python", "javascript", "script", "shell", "command", "cmd",
    "callback", "lambda", "eval", "exec", "import", "module",
})

_KERNEL_TO_SEQ = {
    "SUCCESS": SequenceStatus.SUCCESS,
    "PRECONDITION_FAILED": SequenceStatus.PRECONDITION_FAILED,
    "POLICY_BLOCKED": SequenceStatus.POLICY_BLOCKED,
    "RISK_BLOCKED": SequenceStatus.RISK_BLOCKED,
    "APPROVAL_REQUIRED": SequenceStatus.APPROVAL_REQUIRED,
    "APPROVAL_DENIED": SequenceStatus.APPROVAL_DENIED,
    "EMERGENCY_STOP_ACTIVE": SequenceStatus.EMERGENCY_STOP_ACTIVE,
    "UNSUPPORTED_ACTION": SequenceStatus.UNSUPPORTED_ACTION,
    "UNSUPPORTED_CAPABILITY": SequenceStatus.UNSUPPORTED_CAPABILITY,
    "VERIFIED": SequenceStatus.SUCCESS,
    "NOT_VERIFIED": SequenceStatus.STEP_FAILED,
    "VERIFICATION_FAILED": SequenceStatus.STEP_FAILED,
    "VERIFICATION_TIMEOUT": SequenceStatus.SEQUENCE_TIMEOUT,
    "VERIFICATION_UNAVAILABLE": SequenceStatus.STEP_FAILED,
    "INVALID_VERIFICATION_SPEC": SequenceStatus.VALIDATION_FAILED,
    "VERIFICATION_BLOCKED": SequenceStatus.POLICY_BLOCKED,
}


def sequence_spec_hash(spec: SequenceSpec) -> str:
    return spec.spec_hash()


def _result(
    spec: SequenceSpec,
    status: SequenceStatus,
    *,
    completed: int = 0,
    failed_step_id: str = "",
    failed_action: str = "",
    failure_code: str = "",
    risk: str = "",
    before: str = "",
    after: str = "",
    telemetry: Optional[Tuple[Dict[str, Any], ...]] = None,
    approval_state: str = "NONE",
) -> SequenceResult:
    recs: Tuple[Any, ...] = ()
    from proactive.computer.policy import experiences_allowed
    if experiences_allowed():
        from proactive.computer.experience.kernel import record_sequence_experiences
        recs = record_sequence_experiences(
            sequence_id=str(spec.sequence_id or ""),
            sequence_spec_hash=spec.spec_hash(),
            sequence_status=status.value,
            approval_state=approval_state,
            aggregate_risk=risk,
            telemetry=telemetry or (),
            owner_id=str(spec.owner_id or ""),
        )
    return SequenceResult(
        sequence_id=str(spec.sequence_id or ""),
        status=status,
        total_steps=len(spec.steps),
        completed_step_count=completed,
        failed_step_id=failed_step_id,
        failed_action=failed_action,
        failure_code=failure_code or (status.value if status != SequenceStatus.SUCCESS else ""),
        aggregate_risk=risk,
        spec_hash=spec.spec_hash(),
        last_before_hash=before,
        last_after_hash=after,
        telemetry=telemetry or (),
        experiences=recs,
    )


def _emergency_stop(owner_id: str, computer_session_id: str = "") -> bool:
    owner = str(owner_id or OWNER_ID)[:64]
    sid = str(computer_session_id or "")[:64]
    if sid:
        row = proactive_store.get_computer_session(sid, owner)
        if row and (row.get("emergency_stop") or str(row.get("status") or "") == "STOPPED"):
            return True
    observing = proactive_store.get_observing_computer_session(owner)
    if observing and observing.get("emergency_stop"):
        return True
    return False


def _cost_ok(capabilities: Tuple[str, ...]) -> bool:
    seen = set(capabilities)
    if CAP_COMPUTER in seen:
        d = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.VISION,
            provider="local_uia",
            capability="computer_sequence",
        ))
        if not d.is_allow:
            return False
    if CAP_BROWSER in seen:
        d = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.HTTP,
            provider="local_browser",
            capability="computer_sequence",
            host="127.0.0.1",
            endpoint="http://127.0.0.1/",
        ))
        if not d.is_allow:
            return False
    if CAP_FILESYSTEM in seen or CAP_VERIFY in seen:
        d = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.OTHER,
            provider="local_filesystem",
            capability="computer_sequence",
        ))
        if not d.is_allow:
            return False
    return True


def _truthy(raw: str) -> Optional[bool]:
    s = (raw or "").strip().lower()
    if s in ("", "none"):
        return None
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def _object_type(raw: str) -> Optional[ObjectType]:
    s = (raw or "").strip().lower()
    if not s:
        return None
    for item in ObjectType:
        if item.value == s:
            return item
    return None


def _validate(spec: SequenceSpec) -> Optional[SequenceResult]:
    limit = sequence_max_steps()
    tmax = sequence_timeout_ms()
    rmax = sequence_max_retries()
    if not str(spec.sequence_id or "").strip():
        return _result(spec, SequenceStatus.VALIDATION_FAILED, failure_code="MISSING_SEQUENCE_ID")
    if not str(spec.owner_id or "").strip():
        return _result(spec, SequenceStatus.VALIDATION_FAILED, failure_code="MISSING_OWNER")
    if spec.failure_policy.value != "FAIL_CLOSED":
        return _result(spec, SequenceStatus.VALIDATION_FAILED, failure_code="INVALID_FAILURE_POLICY")
    if int(spec.max_steps) < 1 or int(spec.max_steps) > limit:
        return _result(spec, SequenceStatus.VALIDATION_FAILED, failure_code="INVALID_MAX_STEPS")
    if int(spec.timeout_ms) < 100 or int(spec.timeout_ms) > tmax:
        return _result(spec, SequenceStatus.VALIDATION_FAILED, failure_code="INVALID_TIMEOUT")
    if int(spec.retry_count) < 0:
        return _result(spec, SequenceStatus.VALIDATION_FAILED, failure_code="INVALID_RETRY")
    if not spec.steps:
        return _result(spec, SequenceStatus.VALIDATION_FAILED, failure_code="EMPTY_SEQUENCE")
    if len(spec.steps) > limit or len(spec.steps) > int(spec.max_steps):
        return _result(spec, SequenceStatus.SEQUENCE_LIMIT_EXCEEDED, failure_code="TOO_MANY_STEPS")

    seen_ids = set()
    pairs = []
    for step in spec.steps:
        sid = str(step.step_id or "").strip()
        if not sid or sid in seen_ids:
            return _result(spec, SequenceStatus.VALIDATION_FAILED, failure_code="INVALID_STEP_ID")
        seen_ids.add(sid)
        cap = str(step.capability or "").strip()
        act = str(step.action or "").strip()
        if cap not in ALLOWED:
            return _result(
                spec, SequenceStatus.UNSUPPORTED_CAPABILITY,
                failed_step_id=sid, failed_action=act, failure_code="UNSUPPORTED_CAPABILITY",
            )
        if act not in ALLOWED[cap]:
            return _result(
                spec, SequenceStatus.UNSUPPORTED_ACTION,
                failed_step_id=sid, failed_action=act, failure_code="UNSUPPORTED_ACTION",
            )
        allowed = allowed_params(cap, act)
        pmap = params_map(step.parameters)
        for key, val in pmap.items():
            if key not in allowed or key in FORBIDDEN_KEYS:
                return _result(
                    spec, SequenceStatus.VALIDATION_FAILED,
                    failed_step_id=sid, failed_action=act, failure_code="INVALID_PARAMETERS",
                )
            low = val.lower()
            for marker in FORBIDDEN_VALUE_MARKERS:
                if marker in low:
                    return _result(
                        spec, SequenceStatus.VALIDATION_FAILED,
                        failed_step_id=sid, failed_action=act, failure_code="DYNAMIC_CODE_BLOCKED",
                    )
            if len(val) > 65536:
                return _result(
                    spec, SequenceStatus.VALIDATION_FAILED,
                    failed_step_id=sid, failed_action=act, failure_code="INVALID_PARAMETERS",
                )
        err = _required_params(cap, act, pmap)
        if err:
            return _result(
                spec, SequenceStatus.VALIDATION_FAILED,
                failed_step_id=sid, failed_action=act, failure_code=err,
            )
        if str(step.expected_result or "") not in ("", "SUCCESS"):
            return _result(
                spec, SequenceStatus.VALIDATION_FAILED,
                failed_step_id=sid, failed_action=act, failure_code="INVALID_EXPECTED_RESULT",
            )
        pairs.append((cap, act))

    if int(spec.retry_count) > 0:
        for cap, act in pairs:
            if (cap, act) not in IDEMPOTENT_RETRY:
                return _result(spec, SequenceStatus.VALIDATION_FAILED, failure_code="MUTATION_RETRY_BLOCKED")
    if int(spec.retry_count) > rmax:
        return _result(spec, SequenceStatus.VALIDATION_FAILED, failure_code="INVALID_RETRY")
    return None


def _required_params(cap: str, act: str, pmap: Dict[str, str]) -> str:
    if cap == CAP_COMPUTER:
        if not (pmap.get("automation_id") or pmap.get("runtime_id")):
            return "INVALID_PARAMETERS"
        if not pmap.get("control_type"):
            return "INVALID_PARAMETERS"
        if not pmap.get("precondition_observation_hash"):
            return "INVALID_PARAMETERS"
        if act == "TYPE" and not pmap.get("text"):
            return "INVALID_PARAMETERS"
    elif cap == CAP_BROWSER:
        if not pmap.get("session_id"):
            return "INVALID_PARAMETERS"
        if act == "NAVIGATE":
            url = pmap.get("url") or ""
            if not url or url.lower().startswith("java" + "script:"):
                return "INVALID_PARAMETERS"
        if act in ("CLICK", "TYPE"):
            has_id = bool(pmap.get("element_id") or pmap.get("test_id"))
            has_rn = bool(pmap.get("role") and pmap.get("name"))
            if not has_id and not has_rn:
                return "INVALID_PARAMETERS"
        if act == "TYPE" and not pmap.get("text"):
            return "INVALID_PARAMETERS"
        if not pmap.get("precondition_observation_hash") and act not in ("BACK", "FORWARD", "REFRESH"):
            return "INVALID_PARAMETERS"
    elif cap == CAP_FILESYSTEM:
        if not pmap.get("path"):
            return "INVALID_PARAMETERS"
        if act in ("COPY_FILE", "MOVE_FILE") and not pmap.get("dest_path"):
            return "INVALID_PARAMETERS"
        if pmap.get("expected_exists") and _truthy(pmap.get("expected_exists") or "") is None:
            return "INVALID_PARAMETERS"
        if pmap.get("expected_type") and _object_type(pmap.get("expected_type") or "") is None:
            return "INVALID_PARAMETERS"
    elif cap == CAP_VERIFY:
        domain = pmap.get("domain") or ""
        if domain not in ("computer", "browser", "filesystem"):
            return "INVALID_PARAMETERS"
        if domain == "filesystem" and not pmap.get("path"):
            return "INVALID_PARAMETERS"
        if domain == "browser" and not pmap.get("session_id"):
            return "INVALID_PARAMETERS"
        if domain == "filesystem" and pmap.get("session_id"):
            return "CAPABILITY_ESCALATION"
        if domain == "browser" and pmap.get("path"):
            return "CAPABILITY_ESCALATION"
        if domain == "computer" and (pmap.get("path") or pmap.get("session_id")):
            return "CAPABILITY_ESCALATION"
        if act in ("URL_MATCH", "ORIGIN_MATCH") and domain != "browser":
            return "CAPABILITY_ESCALATION"
        if act in ("FILE_METADATA_MATCH", "DIRECTORY_ENTRY_MATCH") and domain != "filesystem":
            return "CAPABILITY_ESCALATION"
    return ""


def _build_computer(spec: SequenceSpec, step: SequenceStep, approval: ApprovalState) -> ComputerActionRequest:
    p = params_map(step.parameters)
    at = ActionType.CLICK if step.action == "CLICK" else ActionType.TYPE
    return ComputerActionRequest(
        action_id=step.step_id,
        action_type=at,
        target=TargetIdentity(
            automation_id=p.get("automation_id", ""),
            runtime_id=p.get("runtime_id", ""),
            control_type=p.get("control_type", ""),
            name=p.get("name", ""),
        ),
        precondition_observation_hash=p.get("precondition_observation_hash", ""),
        owner_id=spec.owner_id,
        session_id=p.get("session_id") or spec.computer_session_id,
        approval_state=approval,
        text=p.get("text", ""),
        sensitive=_truthy(p.get("sensitive", "")) is True,
    )


def _build_browser(spec: SequenceSpec, step: SequenceStep, approval: ApprovalState) -> BrowserActionRequest:
    p = params_map(step.parameters)
    at = BrowserActionType(step.action)
    return BrowserActionRequest(
        action_id=step.step_id,
        action_type=at,
        session_id=p.get("session_id", ""),
        owner_id=spec.owner_id,
        precondition_observation_hash=p.get("precondition_observation_hash", ""),
        target=BrowserTarget(
            role=p.get("role", ""),
            name=p.get("name", ""),
            element_id=p.get("element_id", ""),
            test_id=p.get("test_id", ""),
        ),
        url=p.get("url", ""),
        text=p.get("text", ""),
        sensitive=_truthy(p.get("sensitive", "")) is True,
        approval_state=approval,
    )


def _build_fs(spec: SequenceSpec, step: SequenceStep, approval: ApprovalState) -> FsActionRequest:
    p = params_map(step.parameters)
    content = (p.get("content") or "").encode("utf-8")
    return FsActionRequest(
        action_id=step.step_id,
        action_type=FsActionType(step.action),
        path=p.get("path", ""),
        owner_id=spec.owner_id,
        dest_path=p.get("dest_path", ""),
        content=content,
        precondition_observation_hash=p.get("precondition_observation_hash", ""),
        expected_exists=_truthy(p.get("expected_exists", "")),
        expected_type=_object_type(p.get("expected_type", "")),
        approval_state=approval,
        computer_session_id=spec.computer_session_id,
    )


def _build_verify(spec: SequenceSpec, step: SequenceStep) -> Any:
    from proactive.computer.verify.types import VerificationRequest, VerificationSpec

    p = params_map(step.parameters)
    try:
        tms = int(p.get("timeout_ms") or 2000)
    except ValueError:
        tms = 2000
    try:
        attempts = int(p.get("max_attempts") or 1)
    except ValueError:
        attempts = 1
    vspec = VerificationSpec(
        verification_id=step.step_id,
        capability=p.get("domain", ""),
        verification_type=step.action,
        expected=step.parameters,
        timeout_ms=tms,
        max_attempts=attempts,
        owner_id=spec.owner_id,
    )
    return VerificationRequest(
        spec=vspec,
        action_id=step.step_id,
        computer_session_id=spec.computer_session_id,
    )


def _dispatch(
    spec: SequenceSpec,
    step: SequenceStep,
    approval: ApprovalState,
    computer_fn: Callable,
    browser_fn: Callable,
    fs_fn: Callable,
    verify_fn: Callable,
) -> Any:
    if step.capability == CAP_COMPUTER:
        return computer_fn(_build_computer(spec, step, approval))
    if step.capability == CAP_BROWSER:
        return browser_fn(_build_browser(spec, step, approval))
    if step.capability == CAP_FILESYSTEM:
        return fs_fn(_build_fs(spec, step, approval))
    if step.capability == CAP_VERIFY:
        return verify_fn(_build_verify(spec, step))
    raise RuntimeError("unreachable_capability")


def _kernel_status(out: Any) -> str:
    st = getattr(out, "status", None)
    if st is None:
        return "EXECUTION_FAILED"
    return getattr(st, "value", str(st))


def execute_sequence(
    spec: SequenceSpec,
    approval_state: ApprovalState = ApprovalState.NONE,
    approved_spec_hash: str = "",
    *,
    computer_fn: Callable = execute_computer_action,
    browser_fn: Callable = execute_browser_action,
    fs_fn: Callable = execute_fs_action,
    verify_fn: Callable = execute_verification,
    clock: Callable[[], float] = time.monotonic,
    emergency_stop_fn: Optional[Callable[[str, str], bool]] = None,
    cost_ok_fn: Optional[Callable[[Tuple[str, ...]], bool]] = None,
) -> SequenceResult:
    stop_fn = emergency_stop_fn or _emergency_stop
    cost_fn = cost_ok_fn or _cost_ok
    bad = _validate(spec)
    if bad is not None:
        return bad
    pairs = tuple((s.capability, s.action) for s in spec.steps)
    risk = aggregate_risk(pairs)
    if not sequences_allowed():
        return _result(spec, SequenceStatus.POLICY_BLOCKED, risk=risk, failure_code="SEQUENCES_DISABLED", approval_state=approval_state.value)
    caps = tuple(sorted({s.capability for s in spec.steps}))
    if not cost_fn(caps):
        return _result(spec, SequenceStatus.POLICY_BLOCKED, risk=risk, failure_code="COST_GUARD_BLOCKED", approval_state=approval_state.value)
    if risk in ("MEDIUM", "HIGH", "CRITICAL"):
        if approval_state == ApprovalState.DENIED:
            return _result(spec, SequenceStatus.APPROVAL_DENIED, risk=risk, approval_state=approval_state.value)
        if approval_state != ApprovalState.APPROVED:
            return _result(spec, SequenceStatus.APPROVAL_REQUIRED, risk=risk, approval_state=approval_state.value)
        if str(approved_spec_hash or "") != spec.spec_hash():
            return _result(spec, SequenceStatus.APPROVAL_INVALIDATED, risk=risk, approval_state=approval_state.value)
    elif approval_state == ApprovalState.DENIED:
        return _result(spec, SequenceStatus.APPROVAL_DENIED, risk=risk, approval_state=approval_state.value)

    owner = str(spec.owner_id or OWNER_ID)[:64]
    start = float(clock())
    deadline = start + (int(spec.timeout_ms) / 1000.0)
    completed = 0
    tel: List[Dict[str, Any]] = []
    last_before = ""
    last_after = ""
    retries_left = int(spec.retry_count)

    idx = 0
    while idx < len(spec.steps):
        if float(clock()) >= deadline:
            return _result(
                spec, SequenceStatus.SEQUENCE_TIMEOUT, completed=completed, risk=risk,
                before=last_before, after=last_after, telemetry=tuple(tel),
                approval_state=approval_state.value,
            )
        if stop_fn(owner, spec.computer_session_id):
            return _result(
                spec, SequenceStatus.EMERGENCY_STOP_ACTIVE, completed=completed, risk=risk,
                before=last_before, after=last_after, telemetry=tuple(tel),
                approval_state=approval_state.value,
            )
        step = spec.steps[idx]
        t0 = float(clock())
        out = _dispatch(spec, step, approval_state, computer_fn, browser_fn, fs_fn, verify_fn)
        dur = int((float(clock()) - t0) * 1000)
        kstatus = _kernel_status(out)
        before = str(getattr(out, "before_observation_hash", "") or "")
        after = str(getattr(out, "after_observation_hash", "") or "")
        last_before, last_after = before, after
        err = str(getattr(out, "error_code", "") or kstatus)
        row = {
            "sequence_id": spec.sequence_id,
            "step_id": step.step_id,
            "capability": step.capability,
            "action": step.action,
            "status": kstatus,
            "duration_ms": dur,
            "before_hash": before,
            "after_hash": after,
            "failure_code": "" if kstatus in ("SUCCESS", "VERIFIED") else err[:80],
            "schema_version": SEQUENCE_SCHEMA_VERSION,
        }
        tel.append(row)
        if kstatus in ("SUCCESS", "VERIFIED"):
            completed += 1
            idx += 1
            continue
        if (
            retries_left > 0
            and (step.capability, step.action) in IDEMPOTENT_RETRY
            and (step.capability, step.action) not in MUTATION_ACTIONS
        ):
            retries_left -= 1
            continue
        mapped = _KERNEL_TO_SEQ.get(kstatus, SequenceStatus.STEP_FAILED)
        return _result(
            spec, mapped, completed=completed, risk=risk,
            failed_step_id=step.step_id, failed_action=step.action,
            failure_code=err[:80],
            before=last_before, after=last_after, telemetry=tuple(tel),
            approval_state=approval_state.value,
        )

    return _result(
        spec, SequenceStatus.SUCCESS, completed=completed, risk=risk,
        before=last_before, after=last_after, telemetry=tuple(tel),
        approval_state=approval_state.value,
    )
