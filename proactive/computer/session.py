"""Computer session start/list/get/bind. Observe-only. No EXECUTING."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from proactive.computer.drivers.win32_id import (
    INVALID_IDENTITY,
    OK,
    WINDOW_GONE,
    Win32Identity,
    enumerate_visible_windows,
    exe_basename,
    read_hwnd_win32,
)
from proactive.computer.policy import PRIVACY_DEFAULT, max_title, observation_allowed, session_ttl_sec
from proactive.config import OWNER_ID
from proactive.store import proactive_store

WINDOW_UNBOUND = "WINDOW_UNBOUND"
CANDIDATE_TTL_SEC = 30
_CAND_LOCK = threading.Lock()
_CANDIDATES: Dict[str, Dict[str, Any]] = {}


def start_session(owner_id: str = OWNER_ID, privacy_class: str = PRIVACY_DEFAULT) -> Dict[str, Any]:
    if not observation_allowed():
        return {"ok": False, "enabled": False, "http": 403}
    owner = str(owner_id or "")[:64]
    if not owner:
        return {"ok": False, "error": "unauthenticated", "http": 401}
    privacy = str(privacy_class or PRIVACY_DEFAULT).upper()
    if privacy not in ("NORMAL", "PRIVATE", "SENSITIVE"):
        privacy = PRIVACY_DEFAULT
    sid = str(uuid.uuid4())
    row = proactive_store.insert_computer_session(
        session_id=sid,
        owner_id=owner,
        privacy_class=privacy,
        ttl_sec=session_ttl_sec(),
    )
    if not row:
        existing = proactive_store.get_observing_computer_session(owner)
        if existing:
            return {"ok": False, "error": "already_observing", "http": 409, "session_id": existing.get("session_id")}
        return {"ok": False, "error": "persist_failed", "http": 503}
    return {"ok": True, "session": row, "http": 200}


def list_sessions(owner_id: str = OWNER_ID, limit: int = 20) -> List[Dict[str, Any]]:
    return proactive_store.list_computer_sessions(str(owner_id)[:64], limit)


def get_session(session_id: str, owner_id: str = OWNER_ID) -> Optional[Dict[str, Any]]:
    return proactive_store.get_computer_session(str(session_id)[:64], str(owner_id)[:64])


def public_computer_session(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    bound = int(row.get("bound_hwnd") or 0) > 0
    return {
        "session_id": str(row.get("session_id") or "")[:64],
        "owner_id": str(row.get("owner_id") or "")[:64],
        "status": str(row.get("status") or ""),
        "privacy_class": str(row.get("privacy_class") or ""),
        "emergency_stop": bool(row.get("emergency_stop")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "expires_at": row.get("expires_at"),
        "bound": bound,
        "bound_title": str(row.get("bound_title_advisory") or "")[:80],
        "bound_exe": exe_basename(str(row.get("bound_exe_path_norm") or "")),
    }


def reset_bind_candidates_for_tests() -> None:
    with _CAND_LOCK:
        _CANDIDATES.clear()


def seed_bind_candidate_for_tests(
    owner_id: str,
    session_id: str,
    ident: Win32Identity,
    *,
    candidate_id: Optional[str] = None,
    expires_at: Optional[float] = None,
) -> str:
    cid = str(candidate_id or uuid.uuid4())[:64]
    ts = float(expires_at if expires_at is not None else (time.time() + CANDIDATE_TTL_SEC))
    rec = {
        "candidate_id": cid,
        "owner_id": str(owner_id)[:64],
        "session_id": str(session_id)[:64],
        "ident": ident,
        "expires_at": ts,
    }
    with _CAND_LOCK:
        _CANDIDATES[cid] = rec
    return cid


def _purge_candidates(now: float) -> None:
    dead = [cid for cid, rec in _CANDIDATES.items() if float(rec.get("expires_at") or 0) <= now]
    for cid in dead:
        _CANDIDATES.pop(cid, None)


def _public_candidate(rec: Dict[str, Any]) -> Dict[str, str]:
    ident: Win32Identity = rec["ident"]
    return {
        "candidate_id": str(rec["candidate_id"]),
        "title": str(ident.title_advisory or "")[:80],
        "exe": exe_basename(ident.exe_path_norm)[:64],
        "pid": str(int(ident.pid or 0)),
    }


def identities_match(stored: Dict[str, Any], live: Win32Identity) -> bool:
    if live.outcome != OK:
        return False
    if int(live.hwnd or 0) != int(stored.get("bound_hwnd") or stored.get("hwnd") or 0):
        return False
    if int(live.pid or 0) != int(stored.get("bound_pid") or stored.get("pid") or 0):
        return False
    if str(live.exe_path_norm or "") != str(stored.get("bound_exe_path_norm") or stored.get("exe_path_norm") or ""):
        return False
    if str(live.window_class or "") != str(stored.get("bound_window_class") or stored.get("window_class") or ""):
        return False
    return True


def list_bind_candidates(
    owner_id: str,
    session_id: str,
    *,
    enumerator=None,
    now: Optional[float] = None,
) -> Tuple[Optional[List[Dict[str, str]]], str]:
    owner = str(owner_id or "")[:64]
    sid = str(session_id or "")[:64]
    if not owner or not sid:
        return None, "SESSION_UNAVAILABLE"
    row = get_session(sid, owner)
    if not row or str(row.get("status") or "").upper() != "OBSERVING":
        return None, "SESSION_UNAVAILABLE"
    if int(row.get("bound_hwnd") or 0):
        return None, "ALREADY_BOUND"
    scan = enumerator or enumerate_visible_windows
    ident_rows = list(scan(max_title=max_title(), limit=40) or [])
    ts = float(now if now is not None else time.time())
    public: List[Dict[str, str]] = []
    with _CAND_LOCK:
        _purge_candidates(ts)
        for ident in ident_rows:
            if not isinstance(ident, Win32Identity) or ident.outcome != OK:
                continue
            cid = str(uuid.uuid4())
            rec = {
                "candidate_id": cid,
                "owner_id": owner,
                "session_id": sid,
                "ident": ident,
                "expires_at": ts + CANDIDATE_TTL_SEC,
            }
            _CANDIDATES[cid] = rec
            public.append(_public_candidate(rec))
    return public, "OK"


def bind_session_window(
    owner_id: str,
    session_id: str,
    candidate_id: str,
    *,
    identity_fn=None,
    now: Optional[float] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    owner = str(owner_id or "")[:64]
    sid = str(session_id or "")[:64]
    cid = str(candidate_id or "").strip()[:64]
    if not owner or not sid or not cid:
        return None, "SESSION_UNAVAILABLE"
    if cid.startswith("hwnd:") or cid.isdigit():
        return None, "INVALID_CANDIDATE"
    row = get_session(sid, owner)
    if not row or str(row.get("status") or "").upper() != "OBSERVING":
        return None, "SESSION_UNAVAILABLE"
    if int(row.get("bound_hwnd") or 0):
        return None, "ALREADY_BOUND"
    ts = float(now if now is not None else time.time())
    with _CAND_LOCK:
        _purge_candidates(ts)
        rec = _CANDIDATES.get(cid)
        if rec is None:
            return None, "CANDIDATE_EXPIRED"
        if rec.get("owner_id") != owner or rec.get("session_id") != sid:
            return None, "INVALID_CANDIDATE"
        snap: Win32Identity = rec["ident"]
        _CANDIDATES.pop(cid, None)
    reader = identity_fn or read_hwnd_win32
    live = reader(int(snap.hwnd), max_title=max_title())
    if live.outcome == WINDOW_GONE:
        return None, "WINDOW_GONE"
    if live.outcome != OK:
        return None, INVALID_IDENTITY
    snap_row = {
        "hwnd": snap.hwnd,
        "pid": snap.pid,
        "exe_path_norm": snap.exe_path_norm,
        "window_class": snap.window_class,
    }
    live_row = {
        "bound_hwnd": live.hwnd,
        "bound_pid": live.pid,
        "bound_exe_path_norm": live.exe_path_norm,
        "bound_window_class": live.window_class,
    }
    if not identities_match(snap_row, live) or not identities_match(live_row, snap):
        if int(live.pid or 0) != int(snap.pid or 0):
            return None, "PID_MISMATCH"
        if str(live.exe_path_norm or "") != str(snap.exe_path_norm or ""):
            return None, "EXE_MISMATCH"
        if str(live.window_class or "") != str(snap.window_class or ""):
            return None, "CLASS_MISMATCH"
        return None, "HWND_REUSE"
    bound = proactive_store.bind_computer_session(
        sid,
        owner,
        hwnd=int(live.hwnd),
        pid=int(live.pid),
        exe_path_norm=str(live.exe_path_norm),
        window_class=str(live.window_class),
        title_advisory=str(live.title_advisory or snap.title_advisory or ""),
    )
    if bound is None:
        again = get_session(sid, owner)
        if again and int(again.get("bound_hwnd") or 0):
            return None, "ALREADY_BOUND"
        return None, "SESSION_UNAVAILABLE"
    return bound, "OK"


def validate_bound_identity(session: Dict[str, Any], *, identity_fn=None) -> Tuple[Optional[Win32Identity], str]:
    hwnd = int(session.get("bound_hwnd") or 0)
    if hwnd <= 0:
        return None, "WINDOW_UNBOUND"
    reader = identity_fn or read_hwnd_win32
    live = reader(hwnd, max_title=max_title())
    if live.outcome == WINDOW_GONE:
        return None, "WINDOW_GONE"
    if live.outcome != OK:
        return None, INVALID_IDENTITY
    if not identities_match(session, live):
        if int(live.pid or 0) != int(session.get("bound_pid") or 0):
            return None, "PID_MISMATCH"
        if str(live.exe_path_norm or "") != str(session.get("bound_exe_path_norm") or ""):
            return None, "EXE_MISMATCH"
        if str(live.window_class or "") != str(session.get("bound_window_class") or ""):
            return None, "CLASS_MISMATCH"
        return None, "HWND_REUSE"
    return live, "OK"
