"""Capture one ComputerObservation per tick. No mutation. No screenshots."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from proactive.computer.drivers.uia_win import (
    OBSERVATION_TIMEOUT,
    REDACTION_FAILED,
    TREE_LIMIT,
    UIA_FAIL_CLOSED,
    UIA_UNAVAILABLE,
    UiaWalkNode,
    read_foreground_uia_meta,
    read_foreground_uia_tree,
)

_DRIVER_UIA_META = read_foreground_uia_meta
from proactive.computer.drivers.win32_id import (
    INVALID_IDENTITY,
    WINDOW_GONE,
    Win32Identity,
    exe_basename,
    read_foreground_win32,
)
from proactive.computer.hash import authoritative_payload, observation_hash
from proactive.computer.policy import (
    CAPABILITY_ID,
    PRIVACY_DEFAULT,
    SCHEMA_VERSION,
    max_depth,
    max_nodes,
    max_title,
    observation_allowed,
    retention_n,
    timeout_ms,
)
from proactive.config import OWNER_ID
from proactive.otp import emit_proactive
from proactive.store import proactive_store

HASH_FAILURE = "HASH_FAILURE"
SESSION_STOPPED = "SESSION_STOPPED"
OK = "OK"


@dataclass
class ComputerObservation:
    owner_id: str = ""
    capability_id: str = CAPABILITY_ID
    hwnd: int = 0
    pid: int = 0
    exe_path_norm: str = ""
    publisher_norm: str = ""
    window_class: str = ""
    uia_runtime_id: str = ""
    uia_automation_id: str = ""
    uia_control_type: str = ""
    uia_tree_digest: str = ""
    monitor_id: int = 0
    privacy_class: str = PRIVACY_DEFAULT
    schema_version: str = SCHEMA_VERSION
    title_advisory: str = ""
    rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
    capture_unix_ms: int = 0
    node_count: int = 0
    latency_ms: float = 0.0
    outcome_code: str = WINDOW_GONE
    data_only: bool = True
    observation_hash: str = ""

    def as_authoritative(self) -> Dict[str, Any]:
        return authoritative_payload({
            "owner_id": self.owner_id,
            "capability_id": self.capability_id,
            "hwnd": self.hwnd,
            "pid": self.pid,
            "exe_path_norm": self.exe_path_norm,
            "publisher_norm": self.publisher_norm,
            "window_class": self.window_class,
            "uia_runtime_id": self.uia_runtime_id,
            "uia_automation_id": self.uia_automation_id,
            "uia_control_type": self.uia_control_type,
            "uia_tree_digest": self.uia_tree_digest,
            "monitor_id": self.monitor_id,
            "privacy_class": self.privacy_class,
            "schema_version": self.schema_version,
        })


def _hud_ws(card: Dict[str, Any]) -> None:
    try:
        from proactive.delivery import _ws_send
        _ws_send(card)
    except Exception:
        pass


MAX_STRUCTURED_TARGETS = 32
_UIA_TYPES = {
    "50000": "Button",
    "50002": "CheckBox",
    "50003": "ComboBox",
    "50004": "Edit",
    "50005": "Hyperlink",
    "50011": "MenuItem",
    "50013": "RadioButton",
    "50020": "Text",
    "50030": "Document",
}
_UIA_NAMES = {name.lower(): code for code, name in _UIA_TYPES.items()}


def canonical_uia_control_type(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text in _UIA_TYPES:
        return _UIA_TYPES[text]
    return text


def uia_control_types_equal(left: str, right: str) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    ca = canonical_uia_control_type(a)
    cb = canonical_uia_control_type(b)
    if ca and cb and ca.lower() == cb.lower():
        return True
    if a in _UIA_TYPES and _UIA_TYPES[a].lower() == b.lower():
        return True
    if b in _UIA_TYPES and _UIA_TYPES[b].lower() == a.lower():
        return True
    if a.lower() in _UIA_NAMES and _UIA_NAMES[a.lower()] == b:
        return True
    if b.lower() in _UIA_NAMES and _UIA_NAMES[b.lower()] == a:
        return True
    return False


def _flatten_structured_targets(root: Optional[UiaWalkNode]) -> Tuple[Dict[str, str], ...]:
    if root is None:
        return ()
    out: List[Dict[str, str]] = []
    stack = [root]
    while stack and len(out) < MAX_STRUCTURED_TARGETS:
        node = stack.pop()
        if node is None:
            continue
        if bool(getattr(node, "is_password", False)):
            continue
        aid = str(getattr(node, "automation_id", "") or "")[:128]
        rid = str(getattr(node, "runtime_id", "") or "")[:256]
        raw_type = str(getattr(node, "control_type", "") or "")[:64]
        ctype = _UIA_TYPES.get(raw_type, raw_type)
        name = str(getattr(node, "name", "") or "")[:80]
        if (aid or rid) and ctype and name:
            row = {
                "automation_id": aid,
                "runtime_id": rid,
                "control_type": ctype,
                "name": name,
            }
            if bool(getattr(node, "focused", False)):
                row["selected"] = "true"
            out.append(row)
        kids = list(getattr(node, "children", None) or [])
        for child in reversed(kids):
            stack.append(child)
    return tuple(out)


def capture_structured_observation(
    owner_id: str,
    *,
    win32: Optional[Win32Identity] = None,
    uia_root: Optional[UiaWalkNode] = None,
    computer_session_id: str = "",
    identity_fn=None,
) -> Tuple[Optional[ComputerObservation], str, Tuple[Dict[str, str], ...]]:
    """One read-only capture plus bounded named targets. No click/type."""
    from proactive.computer.session import get_session, validate_bound_identity

    t0 = time.monotonic()
    budget = float(timeout_ms())
    sid = str(computer_session_id or "").strip()[:64]
    ident = win32
    if sid:
        row = get_session(sid, str(owner_id or "")[:64])
        if not row:
            return None, "SESSION_UNAVAILABLE", ()
        live, err = validate_bound_identity(row, identity_fn=identity_fn)
        if live is None:
            return None, err, ()
        ident = live
    elif ident is None:
        ident = read_foreground_win32(max_title=max_title())
    obs = ComputerObservation(owner_id=str(owner_id or "")[:64], privacy_class=PRIVACY_DEFAULT)
    obs.capture_unix_ms = int(time.time() * 1000)
    obs.title_advisory = str(ident.title_advisory or "")[: max_title()]
    obs.rect = ident.rect
    if ident.outcome == INVALID_IDENTITY:
        obs.outcome_code = INVALID_IDENTITY
        obs.latency_ms = (time.monotonic() - t0) * 1000.0
        return None, INVALID_IDENTITY, ()
    obs.hwnd = int(ident.hwnd or 0)
    obs.pid = int(ident.pid or 0)
    obs.exe_path_norm = str(ident.exe_path_norm or "")
    obs.publisher_norm = str(ident.publisher_norm or "")
    obs.window_class = str(ident.window_class or "")[:64]
    obs.monitor_id = int(ident.monitor_id or 0)
    if ident.outcome == WINDOW_GONE or obs.hwnd == 0:
        obs.outcome_code = WINDOW_GONE
        root = uia_root
    else:
        remain = budget - (time.monotonic() - t0) * 1000.0
        uia_timeout = max(1, int(remain))
        kwargs = dict(
            timeout_ms=uia_timeout,
            max_depth=max_depth(),
            max_nodes=max_nodes(),
            tree_root=uia_root,
        )
        if uia_root is not None:
            uia, root = read_foreground_uia_tree(obs.hwnd, **kwargs)
        elif read_foreground_uia_meta is not _DRIVER_UIA_META:
            uia = read_foreground_uia_meta(obs.hwnd, **kwargs)
            root = None
        else:
            uia, root = read_foreground_uia_tree(obs.hwnd, **kwargs)
        if uia.outcome == REDACTION_FAILED:
            obs.outcome_code = REDACTION_FAILED
            obs.latency_ms = (time.monotonic() - t0) * 1000.0
            return None, REDACTION_FAILED, ()
        if uia.outcome == OBSERVATION_TIMEOUT:
            obs.outcome_code = OBSERVATION_TIMEOUT
        elif uia.outcome in UIA_FAIL_CLOSED:
            obs.outcome_code = str(uia.outcome)
            if ident.outcome == WINDOW_GONE:
                obs.outcome_code = WINDOW_GONE
        elif uia.outcome == TREE_LIMIT:
            obs.outcome_code = TREE_LIMIT
            obs.uia_runtime_id = uia.runtime_id
            obs.uia_automation_id = uia.automation_id
            obs.uia_control_type = uia.control_type
            obs.uia_tree_digest = uia.tree_digest
            obs.node_count = int(uia.node_count or 0)
        else:
            obs.outcome_code = OK
            obs.uia_runtime_id = uia.runtime_id
            obs.uia_automation_id = uia.automation_id
            obs.uia_control_type = uia.control_type
            obs.uia_tree_digest = uia.tree_digest
            obs.node_count = int(uia.node_count or 0)
            if ident.outcome == WINDOW_GONE:
                obs.outcome_code = WINDOW_GONE
    if (time.monotonic() - t0) * 1000.0 > budget:
        obs.outcome_code = OBSERVATION_TIMEOUT
    try:
        obs.observation_hash = observation_hash(obs.as_authoritative())
    except Exception:
        obs.outcome_code = HASH_FAILURE
        obs.latency_ms = (time.monotonic() - t0) * 1000.0
        return None, HASH_FAILURE, ()
    if not obs.observation_hash:
        obs.outcome_code = HASH_FAILURE
        return None, HASH_FAILURE, ()
    obs.latency_ms = (time.monotonic() - t0) * 1000.0
    return obs, obs.outcome_code, _flatten_structured_targets(root)


def capture_observation(
    owner_id: str,
    *,
    win32: Optional[Win32Identity] = None,
    uia_root: Optional[UiaWalkNode] = None,
    computer_session_id: str = "",
    identity_fn=None,
) -> Tuple[Optional[ComputerObservation], str]:
    obs, code, _targets = capture_structured_observation(
        owner_id,
        win32=win32,
        uia_root=uia_root,
        computer_session_id=computer_session_id,
        identity_fn=identity_fn,
    )
    return obs, code


def _persist(session: Dict[str, Any], obs: ComputerObservation) -> str:
    oid = str(uuid.uuid4())
    ok = proactive_store.insert_computer_observation({
        "observation_id": oid,
        "session_id": session.get("session_id"),
        "owner_id": obs.owner_id,
        "observation_hash": obs.observation_hash,
        "capability_id": obs.capability_id,
        "authoritative_json": obs.as_authoritative(),
        "title_advisory": obs.title_advisory,
        "outcome_code": obs.outcome_code,
        "node_count": obs.node_count,
        "latency_ms": obs.latency_ms,
    })
    if not ok:
        return ""
    proactive_store.prune_computer_observations(str(session.get("session_id") or ""), int(retention_n()))
    proactive_store.insert_computer_observation_event(
        str(session.get("session_id") or ""),
        obs.owner_id,
        "created",
        reason=str(obs.outcome_code or "")[:40],
    )
    return oid


def evaluate_computer_observation(owner_id: str = OWNER_ID) -> int:
    if not observation_allowed():
        return 0
    owner = str(owner_id or OWNER_ID)[:64]
    try:
        proactive_store.expire_computer_sessions(owner)
    except Exception:
        pass
    session = proactive_store.get_observing_computer_session(owner)
    if not session:
        return 0
    if session.get("emergency_stop"):
        return 0
    if str(session.get("status") or "") != "OBSERVING":
        return 0
    sid = str(session.get("session_id") or "")[:64]
    if int(session.get("bound_hwnd") or 0) > 0:
        obs, drop_reason = capture_observation(owner, computer_session_id=sid)
    else:
        obs, drop_reason = capture_observation(owner)
    if obs is None:
        proactive_store.insert_computer_observation_event(
            str(session.get("session_id") or ""),
            owner,
            "dropped",
            reason=str(drop_reason or INVALID_IDENTITY)[:40],
        )
        emit_proactive(
            "proactive.outcome",
            status="skipped",
            attributes={"reason": "computer_obs_drop", "screenshot_present": False},
        )
        return 0
    oid = _persist(session, obs)
    if not oid:
        return 0
    emit_proactive(
        "proactive.outcome",
        status="ok",
        latency_ms=float(obs.latency_ms),
        attributes={
            "reason": "computer_obs",
            "session_id": str(session.get("session_id") or "")[:36],
            "observation_id": oid[:36],
            "observation_hash": obs.observation_hash[:16],
            "node_count": int(obs.node_count or 0),
            "screenshot_present": False,
            "outcome_code": str(obs.outcome_code or "")[:40],
            "capability_id": CAPABILITY_ID,
        },
    )
    _hud_ws({
        "type": "computer_observation",
        "session_id": session.get("session_id"),
        "observation_id": oid,
        "observation_hash": obs.observation_hash,
        "exe_basename": exe_basename(obs.exe_path_norm),
        "window_class": obs.window_class,
        "outcome_code": obs.outcome_code,
        "tts": False,
    })
    return 1
