"""Capture one ComputerObservation per tick. No mutation. No screenshots."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from proactive.computer.drivers.uia_win import (
    OBSERVATION_TIMEOUT,
    REDACTION_FAILED,
    TREE_LIMIT,
    UIA_UNAVAILABLE,
    UiaWalkNode,
    read_foreground_uia_meta,
)
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


def capture_observation(
    owner_id: str,
    *,
    win32: Optional[Win32Identity] = None,
    uia_root: Optional[UiaWalkNode] = None,
) -> Tuple[Optional[ComputerObservation], str]:
    t0 = time.monotonic()
    budget = float(timeout_ms())
    ident = win32 if win32 is not None else read_foreground_win32(max_title=max_title())
    obs = ComputerObservation(owner_id=str(owner_id or "")[:64], privacy_class=PRIVACY_DEFAULT)
    obs.capture_unix_ms = int(time.time() * 1000)
    obs.title_advisory = str(ident.title_advisory or "")[: max_title()]
    obs.rect = ident.rect
    if ident.outcome == INVALID_IDENTITY:
        obs.outcome_code = INVALID_IDENTITY
        obs.latency_ms = (time.monotonic() - t0) * 1000.0
        return None, INVALID_IDENTITY
    obs.hwnd = int(ident.hwnd or 0)
    obs.pid = int(ident.pid or 0)
    obs.exe_path_norm = str(ident.exe_path_norm or "")
    obs.publisher_norm = str(ident.publisher_norm or "")
    obs.window_class = str(ident.window_class or "")[:64]
    obs.monitor_id = int(ident.monitor_id or 0)
    if ident.outcome == WINDOW_GONE or obs.hwnd == 0:
        obs.outcome_code = WINDOW_GONE
    else:
        remain = budget - (time.monotonic() - t0) * 1000.0
        uia_timeout = max(1, int(remain))
        uia = read_foreground_uia_meta(
            obs.hwnd,
            timeout_ms=uia_timeout,
            max_depth=max_depth(),
            max_nodes=max_nodes(),
            tree_root=uia_root,
        )
        if uia.outcome == REDACTION_FAILED:
            obs.outcome_code = REDACTION_FAILED
            obs.latency_ms = (time.monotonic() - t0) * 1000.0
            return None, REDACTION_FAILED
        if uia.outcome == OBSERVATION_TIMEOUT:
            obs.outcome_code = OBSERVATION_TIMEOUT
        elif uia.outcome == UIA_UNAVAILABLE:
            obs.outcome_code = UIA_UNAVAILABLE if ident.outcome != WINDOW_GONE else WINDOW_GONE
            if ident.outcome != WINDOW_GONE and ident.outcome != INVALID_IDENTITY:
                obs.outcome_code = UIA_UNAVAILABLE
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
        return None, HASH_FAILURE
    if not obs.observation_hash:
        obs.outcome_code = HASH_FAILURE
        return None, HASH_FAILURE
    obs.latency_ms = (time.monotonic() - t0) * 1000.0
    return obs, obs.outcome_code


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
