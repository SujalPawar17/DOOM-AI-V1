"""V8 observation → planner-context bridge. Read-only. Does not execute or authorize."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from orchestration.executor import ExecutionIdentity, validate_execution_identity
from orchestration.executor_errors import ExecutionStatus
from proactive.computer.policy import observation_allowed

MAX_TARGETS = 32
MAX_AGE_MS = 5000
_DEAD = frozenset({"STOPPED", "EXPIRED", "REVOKED", "CANCELLED"})


def computer_planner_context(
    identity: Any,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return bounded structured_targets for the planner, or a fail-closed status.

    Uses ExecutionIdentity only. Does not read request bodies, headers, or context.
    Does not call V7 action kernels.
    """
    if validate_execution_identity(identity) is not None or type(identity) is not ExecutionIdentity:
        return None, ExecutionStatus.IDENTITY_REQUIRED.value
    sid = str(identity.computer_session_id or "").strip()
    if not sid:
        return None, ExecutionStatus.SESSION_UNAVAILABLE.value
    if not observation_allowed():
        return None, ExecutionStatus.CAPABILITY_UNAVAILABLE.value
    from proactive.computer.session import get_session
    row = get_session(sid, identity.owner_id)
    if not row:
        return None, ExecutionStatus.SESSION_UNAVAILABLE.value
    if str(row.get("owner_id") or "") != identity.owner_id:
        return None, ExecutionStatus.SESSION_UNAVAILABLE.value
    if str(row.get("session_id") or "") != sid:
        return None, ExecutionStatus.SESSION_UNAVAILABLE.value
    status = str(row.get("status") or "").upper()
    if status in _DEAD:
        return None, ExecutionStatus.SESSION_UNAVAILABLE.value
    if not int(row.get("bound_hwnd") or 0):
        return None, ExecutionStatus.SESSION_UNAVAILABLE.value
    from proactive.computer.observe import capture_structured_observation
    from proactive.computer.drivers.uia_win import TREE_LIMIT, UIA_FAIL_CLOSED
    obs, code, targets = capture_structured_observation(
        identity.owner_id,
        computer_session_id=sid,
    )
    usable = str(code or "") in ("OK", TREE_LIMIT)
    if obs is None or not str(obs.observation_hash or "") or not usable:
        return None, ExecutionStatus.SESSION_UNAVAILABLE.value
    if str(getattr(obs, "outcome_code", "") or "") in UIA_FAIL_CLOSED:
        return None, ExecutionStatus.SESSION_UNAVAILABLE.value
    age = int(time.time() * 1000) - int(obs.capture_unix_ms or 0)
    if age < 0 or age > MAX_AGE_MS:
        return None, ExecutionStatus.SESSION_UNAVAILABLE.value
    bounded = []
    for item in targets[:MAX_TARGETS]:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("automation_id") or "")[:128]
        rid = str(item.get("runtime_id") or "")[:128]
        ctype = str(item.get("control_type") or "")[:64]
        name = str(item.get("name") or "")[:80]
        if callable(item.get("name")):
            continue
        if not ((aid or rid) and ctype and name):
            continue
        row_t = {
            "automation_id": aid,
            "runtime_id": rid,
            "control_type": ctype,
            "name": name,
        }
        if str(item.get("selected") or "").lower() in ("1", "true", "yes"):
            row_t["selected"] = "true"
        bounded.append(row_t)
    return {
        "structured_targets": bounded,
        "observation_hash": str(obs.observation_hash)[:64],
        "capture_unix_ms": int(obs.capture_unix_ms or 0),
        "authorizes_execution": False,
        "approved": False,
    }, "OK"
