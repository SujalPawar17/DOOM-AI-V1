"""Map a validated ASK session to ExecutionIdentity. Does not authenticate by itself."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from orchestration.executor import ExecutionIdentity, validate_execution_identity
from orchestration.executor_errors import ExecutionStatus

_ACTIVE = "ACTIVE"
_EXPIRED = "EXPIRED"
_REVOKED = "REVOKED"
_MISSING = "MISSING"


def session_lifecycle(row: Optional[Dict[str, Any]]) -> str:
    if not row:
        return _MISSING
    if row.get("revoked_at"):
        return _REVOKED
    try:
        exp = float(row.get("expires_at") or 0)
    except (TypeError, ValueError):
        return _EXPIRED
    if exp <= time.time():
        return _EXPIRED
    owner = str(row.get("owner_id") or "")
    sid = str(row.get("session_id_hash") or "")
    if not owner or not sid:
        return _MISSING
    return _ACTIVE


def _verified_computer_session(owner_id: str, requested: str) -> Optional[str]:
    sid = str(requested or "").strip()
    if not sid or "\x00" in sid or len(sid) > 64:
        return None
    from proactive.computer.session import get_session
    row = get_session(sid, owner_id)
    if not row:
        return None
    if str(row.get("owner_id") or "") != owner_id:
        return None
    status = str(row.get("status") or "").upper()
    if status in ("STOPPED", "EXPIRED", "REVOKED", "CANCELLED"):
        return None
    found = str(row.get("session_id") or "")[:64]
    if found != sid:
        return None
    return found


def verified_computer_session_id(owner_id: str, requested: str) -> Tuple[Optional[str], str]:
    """Confirm a V7 computer session is live for this owner. Not an ASK session."""
    if not str(requested or "").strip():
        return None, "COMPUTER_SESSION_REQUIRED"
    found = _verified_computer_session(owner_id, requested)
    if found is None:
        return None, ExecutionStatus.SESSION_UNAVAILABLE.value
    return found, "OK"


def identity_from_ask_session_row(
    row: Optional[Dict[str, Any]],
    *,
    computer_session_id: str = "",
) -> Tuple[Optional[ExecutionIdentity], str]:
    """Build ExecutionIdentity from a server-loaded ASK session only.

    Caller must already have authenticated the cookie (require_ask_session /
    load_session). This function does not read request bodies, headers, or
    OWNER_ID from the environment.
    """
    state = session_lifecycle(row)
    if state != _ACTIVE:
        if state in (_EXPIRED, _REVOKED):
            return None, ExecutionStatus.SESSION_UNAVAILABLE.value
        return None, ExecutionStatus.IDENTITY_REQUIRED.value
    owner = str(row.get("owner_id") or "")[:64]
    session_id = str(row.get("session_id_hash") or "")[:64]
    requested = str(computer_session_id or "").strip()
    bound_cs = ""
    if requested:
        verified = _verified_computer_session(owner, requested)
        if verified is None:
            return None, ExecutionStatus.SESSION_UNAVAILABLE.value
        bound_cs = verified
    ident = ExecutionIdentity(
        owner_id=owner,
        session_id=session_id,
        computer_session_id=bound_cs,
    )
    bad = validate_execution_identity(ident)
    if bad is not None:
        return None, bad.value
    return ident, "OK"
