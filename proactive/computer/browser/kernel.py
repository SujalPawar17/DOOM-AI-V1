"""V7.3 browser action kernel. Reuses Cost Guard, computer emergency stop, approval state."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urljoin

from core.cost_guard import ResourceRequest, ResourceType, cost_guard
from proactive.computer.actions.types import ApprovalState
from proactive.computer.browser.driver import BrowserDriver, MemoryBrowserDriver
from proactive.computer.browser.observe import bound_observation
from proactive.computer.browser.target import resolve_target
from proactive.computer.browser.types import (
    REDACTED,
    BrowserActionRequest,
    BrowserActionResult,
    BrowserActionType,
    BrowserObservation,
    BrowserSessionState,
    Status,
)
from proactive.computer.browser.url import validate_navigate_url
from proactive.computer.policy import (
    BROWSER_SCHEMA_VERSION,
    CAPABILITY_BROWSER,
    RISK_BROWSER_CLICK,
    RISK_BROWSER_HISTORY,
    RISK_BROWSER_NAVIGATE,
    RISK_BROWSER_TYPE,
    browser_allowed,
    browser_ttl_sec,
)
from proactive.config import OWNER_ID
from proactive.store import proactive_store


_LOCK = threading.Lock()
_SESSIONS: Dict[str, "BrowserSession"] = {}


@dataclass
class BrowserSession:
    session_id: str
    owner_id: str
    driver: BrowserDriver
    state: BrowserSessionState = BrowserSessionState.OPEN
    created_unix_ms: int = 0
    expires_unix_ms: int = 0
    last_observation: Optional[BrowserObservation] = None
    origin: str = ""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _cost_ok() -> bool:
    d = cost_guard.authorize(ResourceRequest(
        resource_type=ResourceType.HTTP,
        provider="local_browser",
        capability="browser_control",
        host="127.0.0.1",
        endpoint="http://127.0.0.1/",
    ))
    return bool(d.is_allow)


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


def _risk(action: BrowserActionType) -> str:
    if action == BrowserActionType.NAVIGATE:
        return RISK_BROWSER_NAVIGATE
    if action == BrowserActionType.CLICK:
        return RISK_BROWSER_CLICK
    if action == BrowserActionType.TYPE:
        return RISK_BROWSER_TYPE
    return RISK_BROWSER_HISTORY


def _approval(request: BrowserActionRequest) -> Optional[Status]:
    if request.action_type == BrowserActionType.CLOSE:
        return None
    if _risk(request.action_type) in ("MEDIUM", "HIGH", "CRITICAL"):
        if request.approval_state == ApprovalState.DENIED:
            return Status.APPROVAL_DENIED
        if request.approval_state != ApprovalState.APPROVED:
            return Status.APPROVAL_REQUIRED
    return None


def _result(request: BrowserActionRequest, status: Status, **kwargs) -> BrowserActionResult:
    return BrowserActionResult(
        action_id=str(request.action_id or ""),
        session_id=str(request.session_id or ""),
        action_type=request.action_type,
        status=status,
        precondition_status=kwargs.get("precondition", ""),
        execution_status=kwargs.get("execution", ""),
        error_code=kwargs.get("error") or (status.value if status != Status.SUCCESS else ""),
        before_observation_hash=kwargs.get("before", ""),
        after_observation_hash=kwargs.get("after", ""),
        target_identity=kwargs.get("target") or {},
        telemetry=kwargs.get("telemetry") or {},
    )


def _get(session_id: str, owner_id: str) -> Optional[BrowserSession]:
    sid = str(session_id or "")
    with _LOCK:
        sess = _SESSIONS.get(sid)
        if sess is None:
            return None
        if sess.owner_id != str(owner_id or OWNER_ID)[:64]:
            return None
        if sess.state == BrowserSessionState.OPEN and sess.expires_unix_ms and _now_ms() > sess.expires_unix_ms:
            sess.state = BrowserSessionState.EXPIRED
            try:
                sess.driver.close()
            except Exception:
                pass
        return sess


def open_browser_session(
    owner_id: str = OWNER_ID,
    *,
    driver: Optional[BrowserDriver] = None,
    computer_session_id: str = "",
) -> Dict[str, Any]:
    tel = {"schema_version": BROWSER_SCHEMA_VERSION, "capability_id": CAPABILITY_BROWSER}
    if not browser_allowed():
        return {"ok": False, "status": Status.POLICY_BLOCKED.value, "telemetry": tel}
    if not _cost_ok():
        return {"ok": False, "status": Status.POLICY_BLOCKED.value, "error": "COST_GUARD_BLOCKED", "telemetry": tel}
    owner = str(owner_id or OWNER_ID)[:64]
    if _emergency_stop(owner, computer_session_id):
        return {"ok": False, "status": Status.EMERGENCY_STOP_ACTIVE.value, "telemetry": tel}
    sid = str(uuid.uuid4())
    drv = driver if driver is not None else MemoryBrowserDriver()
    now = _now_ms()
    sess = BrowserSession(
        session_id=sid,
        owner_id=owner,
        driver=drv,
        state=BrowserSessionState.OPEN,
        created_unix_ms=now,
        expires_unix_ms=now + int(browser_ttl_sec()) * 1000,
    )
    from proactive.computer.browser.observe import observation_hash
    obs = bound_observation(drv.snapshot(sid))
    obs.session_id = sid
    obs.observation_hash = observation_hash(obs.as_authoritative())
    sess.last_observation = obs
    sess.origin = obs.origin
    with _LOCK:
        _SESSIONS[sid] = sess
    return {
        "ok": True,
        "status": Status.SUCCESS.value,
        "session_id": sid,
        "state": sess.state.value,
        "observation": obs,
        "telemetry": tel,
    }


def close_browser_session(session_id: str, owner_id: str = OWNER_ID) -> Dict[str, Any]:
    sess = _get(session_id, owner_id)
    if sess is None:
        return {"ok": False, "status": Status.SESSION_NOT_FOUND.value}
    try:
        sess.driver.close()
    except Exception:
        pass
    sess.state = BrowserSessionState.CLOSED
    return {"ok": True, "status": Status.SUCCESS.value, "session_id": session_id}


def observe_browser_session(
    session_id: str,
    owner_id: str = OWNER_ID,
    *,
    computer_session_id: str = "",
) -> Dict[str, Any]:
    """Public V7.3 observe contract. Snapshot only."""
    tel = {"schema_version": BROWSER_SCHEMA_VERSION, "capability_id": CAPABILITY_BROWSER}
    if not browser_allowed():
        return {"ok": False, "status": Status.POLICY_BLOCKED.value, "observation": None, "telemetry": tel}
    if not _cost_ok():
        return {
            "ok": False, "status": Status.POLICY_BLOCKED.value, "error": "COST_GUARD_BLOCKED",
            "observation": None, "telemetry": tel,
        }
    owner = str(owner_id or OWNER_ID)[:64]
    if _emergency_stop(owner, computer_session_id):
        return {"ok": False, "status": Status.EMERGENCY_STOP_ACTIVE.value, "observation": None, "telemetry": tel}
    sess = _get(session_id, owner)
    if sess is None:
        return {"ok": False, "status": Status.SESSION_NOT_FOUND.value, "observation": None, "telemetry": tel}
    if sess.state != BrowserSessionState.OPEN:
        return {"ok": False, "status": Status.SESSION_CLOSED.value, "observation": None, "telemetry": tel}
    from proactive.computer.browser.observe import observation_hash
    obs = bound_observation(sess.driver.snapshot(sess.session_id))
    obs.session_id = sess.session_id
    obs.observation_hash = observation_hash(obs.as_authoritative())
    sess.last_observation = obs
    sess.origin = obs.origin
    return {"ok": True, "status": Status.SUCCESS.value, "observation": obs, "telemetry": tel}


def reset_browser_sessions_for_tests() -> None:
    with _LOCK:
        _SESSIONS.clear()


def execute_browser_action(
    request: BrowserActionRequest,
    *,
    computer_session_id: str = "",
) -> BrowserActionResult:
    tel: Dict[str, Any] = {
        "schema_version": BROWSER_SCHEMA_VERSION,
        "capability_id": CAPABILITY_BROWSER,
        "risk_class": _risk(request.action_type),
        "payload_preview": "",
        "text_length": len(request.text or "") if request.action_type == BrowserActionType.TYPE else 0,
    }
    if not browser_allowed():
        return _result(request, Status.POLICY_BLOCKED, precondition="not_checked", execution="not_started", telemetry=tel)
    if not _cost_ok():
        return _result(request, Status.POLICY_BLOCKED, precondition="not_checked", execution="not_started", error="COST_GUARD_BLOCKED", telemetry=tel)
    owner = str(request.owner_id or OWNER_ID)[:64]
    if _emergency_stop(owner, computer_session_id):
        return _result(request, Status.EMERGENCY_STOP_ACTIVE, precondition="not_checked", execution="not_started", telemetry=tel)

    appr = _approval(request)
    if appr is not None:
        return _result(request, appr, precondition="not_checked", execution="not_started", telemetry=tel)

    sess = _get(request.session_id, owner)
    if sess is None:
        return _result(request, Status.SESSION_NOT_FOUND, precondition="failed", execution="not_started", telemetry=tel)
    if sess.state == BrowserSessionState.EXPIRED:
        return _result(request, Status.SESSION_CLOSED, precondition="failed", execution="not_started", error="SESSION_EXPIRED", telemetry=tel)
    if sess.state != BrowserSessionState.OPEN:
        return _result(request, Status.SESSION_CLOSED, precondition="failed", execution="not_started", telemetry=tel)

    if request.action_type == BrowserActionType.TYPE and (request.target.is_password or request.sensitive):
        tel["payload_preview"] = REDACTED
    if request.action_type == BrowserActionType.TYPE and request.target.is_password:
        return _result(request, Status.RISK_BLOCKED, precondition="not_checked", execution="not_started", error="PASSWORD_FIELD", telemetry=tel, target=request.target.as_public())

    live = bound_observation(sess.driver.snapshot(sess.session_id))
    live.session_id = sess.session_id
    from proactive.computer.browser.observe import observation_hash
    live.observation_hash = observation_hash(live.as_authoritative())
    before = live.observation_hash
    expected = str(request.precondition_observation_hash or "")
    if expected and expected != before:
        return _result(request, Status.PRECONDITION_FAILED, precondition="failed", execution="not_started", error="STALE_OBSERVATION_HASH", before=before, telemetry=tel)

    if _emergency_stop(owner, computer_session_id):
        return _result(request, Status.EMERGENCY_STOP_ACTIVE, precondition="ok", execution="not_started", before=before, telemetry=tel)

    try:
        if request.action_type == BrowserActionType.NAVIGATE:
            v = validate_navigate_url(request.url)
            if v != Status.SUCCESS:
                return _result(request, v, precondition="ok", execution="not_started", before=before, telemetry=tel)
            after_obs = sess.driver.navigate(request.url)
        elif request.action_type == BrowserActionType.CLICK:
            el, st = resolve_target(live.elements, request.target, origin=live.origin)
            if st != Status.SUCCESS or el is None:
                return _result(request, st, precondition="failed", execution="not_started", before=before, telemetry=tel, target=request.target.as_public())
            if el.download:
                return _result(request, Status.UNSUPPORTED_ACTION, precondition="ok", execution="not_started", error="DOWNLOAD_BLOCKED", before=before, telemetry=tel, target=request.target.as_public())
            if el.href:
                candidate = urljoin(live.url or "", el.href)
                href_status = validate_navigate_url(candidate)
                if href_status != Status.SUCCESS:
                    return _result(
                        request, Status.NAVIGATION_BLOCKED, precondition="ok", execution="not_started",
                        error="HREF_BLOCKED", before=before, telemetry=tel, target=request.target.as_public(),
                    )
            after_obs = sess.driver.activate(el.handle)
            if after_obs.download_attempted:
                return _result(request, Status.UNSUPPORTED_ACTION, precondition="ok", execution="failed", error="DOWNLOAD_BLOCKED", before=before, after=after_obs.observation_hash, telemetry=tel, target=request.target.as_public())
        elif request.action_type == BrowserActionType.TYPE:
            el, st = resolve_target(live.elements, request.target, origin=live.origin)
            if st != Status.SUCCESS or el is None:
                return _result(request, st, precondition="failed", execution="not_started", before=before, telemetry=tel, target=request.target.as_public())
            if el.is_password:
                tel["payload_preview"] = REDACTED
                return _result(request, Status.RISK_BLOCKED, precondition="ok", execution="not_started", error="PASSWORD_FIELD", before=before, telemetry=tel, target=request.target.as_public())
            if not el.editable:
                return _result(request, Status.UNSUPPORTED_ACTION, precondition="ok", execution="not_started", error="NOT_EDITABLE", before=before, telemetry=tel, target=request.target.as_public())
            if request.sensitive:
                tel["payload_preview"] = REDACTED
            after_obs = sess.driver.type_text(el.handle, request.text)
        elif request.action_type == BrowserActionType.BACK:
            after_obs = sess.driver.back()
        elif request.action_type == BrowserActionType.FORWARD:
            after_obs = sess.driver.forward()
        elif request.action_type == BrowserActionType.REFRESH:
            after_obs = sess.driver.refresh()
        elif request.action_type == BrowserActionType.CLOSE:
            close_browser_session(sess.session_id, owner)
            return _result(request, Status.SUCCESS, precondition="ok", execution="ok", before=before, telemetry=tel)
        else:
            return _result(request, Status.UNSUPPORTED_ACTION, precondition="ok", execution="not_started", before=before, telemetry=tel)
    except Exception:
        return _result(request, Status.EXECUTION_FAILED, precondition="ok", execution="failed", before=before, telemetry=tel)

    if getattr(after_obs, "url", None):
        landed = validate_navigate_url(after_obs.url)
        if landed != Status.SUCCESS:
            return _result(
                request, Status.NAVIGATION_BLOCKED, precondition="ok", execution="failed",
                error="LANDING_URL_BLOCKED", before=before, telemetry=tel,
                target=request.target.as_public() if request.target.role or request.target.element_id else {},
            )

    after_obs.session_id = sess.session_id
    after_obs = bound_observation(after_obs)
    after_obs.observation_hash = observation_hash(after_obs.as_authoritative())
    sess.last_observation = after_obs
    sess.origin = after_obs.origin
    return _result(
        request, Status.SUCCESS,
        precondition="ok", execution="ok",
        before=before, after=after_obs.observation_hash,
        telemetry=tel, target=request.target.as_public() if request.target.role or request.target.element_id else {},
    )
