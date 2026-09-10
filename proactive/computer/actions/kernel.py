"""V7.2 action kernel. Existing policy, Cost Guard, session stop remain authoritative."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.cost_guard import ResourceRequest, ResourceType, cost_guard
from proactive.computer.actions.adapter import UiaActionAdapter
from proactive.computer.actions.types import (
    REDACTED,
    ActionType,
    ApprovalState,
    ComputerActionRequest,
    ComputerActionResult,
    PATTERN_INVOKE,
    PATTERN_SELECTION_ITEM,
    PATTERN_TOGGLE,
    PATTERN_VALUE,
    Status,
    TargetIdentity,
)
from proactive.computer.observe import ComputerObservation, capture_observation
from proactive.computer.policy import (
    ACTION_SCHEMA_VERSION,
    RISK_CLICK,
    RISK_TYPE,
    click_allowed,
    max_depth,
    max_nodes,
    type_allowed,
)
from proactive.config import OWNER_ID
from proactive.store import proactive_store


def _now_ms() -> int:
    return int(time.time() * 1000)


def _result(
    request: ComputerActionRequest,
    status: Status,
    *,
    precondition: str,
    execution: str,
    error: str = "",
    before: str = "",
    after: str = "",
    telemetry: Optional[Dict[str, Any]] = None,
    target: Optional[TargetIdentity] = None,
) -> ComputerActionResult:
    ident = (target or request.target).as_public()
    return ComputerActionResult(
        action_id=str(request.action_id or ""),
        action_type=request.action_type,
        status=status,
        target_identity=ident,
        precondition_status=precondition,
        execution_status=execution,
        error_code=error or (status.value if status != Status.SUCCESS else ""),
        before_observation_hash=before,
        after_observation_hash=after,
        timestamp_unix_ms=_now_ms(),
        telemetry=telemetry or {},
    )


def _payload_preview(request: ComputerActionRequest, is_password: bool) -> str:
    if request.action_type != ActionType.TYPE:
        return ""
    if request.sensitive or is_password:
        return REDACTED
    return ""


def _cost_ok() -> bool:
    d = cost_guard.authorize(ResourceRequest(
        resource_type=ResourceType.VISION,
        provider="local_uia",
        capability="computer_action",
    ))
    return bool(d.is_allow)


def _emergency_stop(session_id: str, owner_id: str) -> bool:
    owner = str(owner_id or OWNER_ID)[:64]
    sid = str(session_id or "")[:64]
    if sid:
        row = proactive_store.get_computer_session(sid, owner)
        if row and row.get("emergency_stop"):
            return True
        if row and str(row.get("status") or "") == "STOPPED":
            return True
    observing = proactive_store.get_observing_computer_session(owner)
    if observing and observing.get("emergency_stop"):
        return True
    return False


def _flags(request: ComputerActionRequest) -> Optional[Status]:
    if request.action_type == ActionType.CLICK:
        if not click_allowed():
            return Status.POLICY_BLOCKED
        return None
    if request.action_type == ActionType.TYPE:
        if not type_allowed():
            return Status.POLICY_BLOCKED
        return None
    return Status.UNSUPPORTED_ACTION


def _approval(request: ComputerActionRequest) -> Optional[Status]:
    risk = RISK_CLICK if request.action_type == ActionType.CLICK else RISK_TYPE
    if risk in ("MEDIUM", "HIGH", "CRITICAL"):
        if request.approval_state == ApprovalState.DENIED:
            return Status.APPROVAL_DENIED
        if request.approval_state != ApprovalState.APPROVED:
            return Status.APPROVAL_REQUIRED
    return None


def _window_precondition(obs: ComputerObservation, ident: TargetIdentity) -> Optional[Status]:
    if ident.exe_path_norm and str(ident.exe_path_norm) != str(obs.exe_path_norm or ""):
        return Status.PRECONDITION_FAILED
    if ident.window_class and str(ident.window_class) != str(obs.window_class or ""):
        return Status.PRECONDITION_FAILED
    return None


def _click(adapter: UiaActionAdapter, resolved) -> Status:
    patterns = set(resolved.patterns or ())
    if PATTERN_INVOKE in patterns:
        return adapter.invoke(resolved)
    if PATTERN_SELECTION_ITEM in patterns:
        return adapter.select_item(resolved)
    if PATTERN_TOGGLE in patterns:
        return adapter.toggle(resolved)
    return Status.UNSUPPORTED_ACTION


def _type(adapter: UiaActionAdapter, resolved, text: str) -> Status:
    if resolved.is_password:
        return Status.CONTROL_NOT_CAPABLE
    if PATTERN_VALUE not in set(resolved.patterns or ()):
        return Status.UNSUPPORTED_ACTION
    return adapter.set_value(resolved, text)


def execute_computer_action(
    request: ComputerActionRequest,
    *,
    current_observation: Optional[ComputerObservation] = None,
    post_observation: Optional[ComputerObservation] = None,
    adapter: Optional[UiaActionAdapter] = None,
    capture_after: bool = True,
) -> ComputerActionResult:
    tel: Dict[str, Any] = {
        "schema_version": ACTION_SCHEMA_VERSION,
        "capability_id": request.capability_id(),
        "risk_class": RISK_CLICK if request.action_type == ActionType.CLICK else RISK_TYPE,
        "payload_preview": _payload_preview(request, bool(request.target.is_password)),
        "text_length": len(request.text or "") if request.action_type == ActionType.TYPE else 0,
    }
    blocked = _flags(request)
    if blocked is not None:
        return _result(request, blocked, precondition="not_checked", execution="not_started", telemetry=tel)

    if not _cost_ok():
        return _result(request, Status.POLICY_BLOCKED, precondition="not_checked", execution="not_started", error="COST_GUARD_BLOCKED", telemetry=tel)

    owner = str(request.owner_id or OWNER_ID)[:64]
    if _emergency_stop(request.session_id, owner):
        return _result(request, Status.EMERGENCY_STOP_ACTIVE, precondition="not_checked", execution="not_started", telemetry=tel)

    appr = _approval(request)
    if appr is not None:
        return _result(request, appr, precondition="not_checked", execution="not_started", telemetry=tel)

    if request.target.is_password and request.action_type == ActionType.TYPE:
        tel["payload_preview"] = REDACTED
        return _result(request, Status.RISK_BLOCKED, precondition="not_checked", execution="not_started", error="PASSWORD_FIELD", telemetry=tel)

    if int(request.valid_until_unix_ms or 0) > 0 and _now_ms() > int(request.valid_until_unix_ms):
        return _result(request, Status.PRECONDITION_FAILED, precondition="failed", execution="not_started", error="DEADLINE_EXCEEDED", telemetry=tel)

    expected = str(request.precondition_observation_hash or "")
    obs = current_observation
    if obs is None:
        obs, _code = capture_observation(owner)
    if obs is None or not str(obs.observation_hash or ""):
        return _result(
            request, Status.PRECONDITION_FAILED,
            precondition="failed", execution="not_started",
            error="OBSERVATION_UNAVAILABLE",
            before=expected,
            telemetry=tel,
        )
    before = str(obs.observation_hash)
    if expected and before != expected:
        return _result(
            request, Status.PRECONDITION_FAILED,
            precondition="failed", execution="not_started",
            error="STALE_OBSERVATION_HASH",
            before=before,
            telemetry=tel,
        )
    win = _window_precondition(obs, request.target)
    if win is not None:
        return _result(request, win, precondition="failed", execution="not_started", error="WINDOW_IDENTITY", before=before, telemetry=tel)

    act = adapter
    if act is None:
        from proactive.computer.actions.adapter import ComUiaAdapter
        act = ComUiaAdapter(
            int(obs.hwnd or 0),
            timeout_ms=int(request.timeout_ms or 800),
            max_depth=max_depth(),
            max_nodes=max_nodes(),
        )

    if _emergency_stop(request.session_id, owner):
        return _result(request, Status.EMERGENCY_STOP_ACTIVE, precondition="ok", execution="not_started", before=before, telemetry=tel)

    resolved, rstatus = act.find(request.target)
    if rstatus != Status.SUCCESS or resolved is None:
        return _result(request, rstatus, precondition="failed", execution="not_started", before=before, telemetry=tel)

    live = resolved.identity
    if str(live.control_type or "") != str(request.target.control_type or ""):
        return _result(
            request, Status.PRECONDITION_FAILED,
            precondition="failed", execution="not_started",
            error="TARGET_IDENTITY_CHANGED",
            before=before,
            target=live,
            telemetry=tel,
        )
    if request.target.automation_id and str(live.automation_id or "") != str(request.target.automation_id or ""):
        return _result(
            request, Status.PRECONDITION_FAILED,
            precondition="failed", execution="not_started",
            error="TARGET_IDENTITY_CHANGED",
            before=before,
            target=live,
            telemetry=tel,
        )

    tel["payload_preview"] = _payload_preview(request, bool(resolved.is_password))
    tel["pattern"] = ",".join(resolved.patterns or ())

    if request.action_type == ActionType.CLICK:
        exec_status = _click(act, resolved)
    else:
        if resolved.is_password:
            tel["payload_preview"] = REDACTED
            return _result(
                request, Status.CONTROL_NOT_CAPABLE,
                precondition="ok", execution="not_started",
                error="PASSWORD_FIELD",
                before=before, target=live, telemetry=tel,
            )
        exec_status = _type(act, resolved, request.text)

    if exec_status != Status.SUCCESS:
        return _result(
            request, exec_status,
            precondition="ok", execution="failed",
            before=before, target=live, telemetry=tel,
        )

    after = ""
    if capture_after:
        if post_observation is not None:
            after = str(post_observation.observation_hash or "")
        else:
            after_obs, _code = capture_observation(owner)
            after = str(after_obs.observation_hash or "") if after_obs is not None else ""

    return _result(
        request, Status.SUCCESS,
        precondition="ok", execution="ok",
        before=before, after=after, target=live, telemetry=tel,
    )
