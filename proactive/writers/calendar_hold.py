"""ACT-1 CALENDAR_CREATE_HOLD typed writer. Fixed URL template. Not SafeHttp POST."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from proactive.act_policy import CAL_SUMMARY_TEMPLATE_ID, CAL_SUMMARY_TEXT
from proactive.connectors.oauth_token import refresh_google_access_token
from proactive.vault import get_secret

_EVENTS = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
_CAL = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")
_RFC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})$")
ALLOWED_EXEC = frozenset({"calendar_id", "start_rfc3339", "end_rfc3339", "summary_template_id"})


def _post(url: str, headers: Dict[str, str], data: bytes, timeout: float) -> Tuple[int, bytes]:
    req = Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", None) or resp.getcode() or 200), resp.read(65536)
    except HTTPError as exc:
        body = exc.read(65536) if exc.fp else b""
        return int(exc.code or 0), body
    except URLError as exc:
        raise TimeoutError("network") from exc


def write_calendar_hold(action: Dict[str, Any], timeout: float = 8.0) -> Tuple[str, str, int]:
    params = action.get("exec_params") if isinstance(action.get("exec_params"), dict) else {}
    if set(params.keys()) - ALLOWED_EXEC:
        raise ValueError("exec_params")
    cal = str(params.get("calendar_id") or "")
    start = str(params.get("start_rfc3339") or "")
    end = str(params.get("end_rfc3339") or "")
    tid = str(params.get("summary_template_id") or "")
    if tid != CAL_SUMMARY_TEMPLATE_ID or not _CAL.match(cal) or not _RFC.match(start) or not _RFC.match(end):
        raise ValueError("payload")
    secret_ref = str((action.get("target_ref") or {}).get("secret_ref") or "")
    token = refresh_google_access_token(secret_ref) if secret_ref else ""
    if not token:
        raise RuntimeError("credential")
    from core.cost_guard import ResourceRequest, ResourceType, cost_guard
    hold_decision = cost_guard.authorize(ResourceRequest(
        resource_type=ResourceType.CONNECTOR_WRITE,
        provider="google_calendar",
        capability="calendar_hold",
        host="www.googleapis.com",
        endpoint="https://www.googleapis.com/calendar/v3/calendars",
    ))
    if not hold_decision.is_allow:
        raise RuntimeError("cost_policy_blocked")
    blob = get_secret(secret_ref) or {}
    vault_cal = str(blob.get("calendar_id") or "primary")
    if vault_cal != cal and not (cal == "primary"):
        raise ValueError("calendar_mismatch")
    url = _EVENTS.format(cal=quote(cal, safe="@.-_"))
    payload = {
        "summary": CAL_SUMMARY_TEXT,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    status, raw = _post(url, headers, json.dumps(payload).encode("utf-8"), timeout)
    if status in (0,) or status >= 500:
        raise TimeoutError("provider")
    if status < 200 or status >= 300:
        raise RuntimeError(f"http_{status}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TimeoutError("parse") from exc
    eid = str(data.get("id") or "")[:80]
    if not eid:
        raise TimeoutError("no_id")
    return eid, eid, status
