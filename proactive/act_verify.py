"""V6.3 outcome verification. External GET / PG only. Not LLM or snapshot."""

from __future__ import annotations

import json
from typing import Any, Dict, Tuple
from urllib.parse import quote

from proactive.act_policy import CAL_SUMMARY_TEXT, CAP_CAL_HOLD, CAP_INTERNAL
from proactive.connectors.http_safe import SafeHttp
from proactive.connectors.oauth_token import refresh_google_access_token
from proactive.store import proactive_store

_http = SafeHttp()
_EVENT = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events/{eid}"


def verify_action(action: Dict[str, Any], receipt_ref: str) -> Tuple[bool, str, str]:
    cap = str(action.get("capability_id") or "")
    if cap == CAP_INTERNAL:
        rec = proactive_store.get_action_receipt(str(action.get("action_id") or ""), str(action.get("owner_id") or ""))
        if not rec:
            return False, "missing_receipt", ""
        if str(rec.get("receipt_id") or "") != str(receipt_ref or rec.get("receipt_id") or ""):
            if receipt_ref and str(rec.get("receipt_id")) != receipt_ref:
                return False, "receipt_mismatch", ""
        return True, "ok", str(rec.get("content_hash") or "")
    if cap == CAP_CAL_HOLD:
        return _verify_calendar(action, receipt_ref)
    return False, "unknown_capability", ""


def _verify_calendar(action: Dict[str, Any], event_id: str) -> Tuple[bool, str, str]:
    params = action.get("exec_params") if isinstance(action.get("exec_params"), dict) else {}
    cal = str(params.get("calendar_id") or "primary")
    secret_ref = str(action.get("_runtime_secret_ref") or "")
    token = refresh_google_access_token(secret_ref) if secret_ref else ""
    if not token or not event_id:
        return False, "verify_auth", ""
    url = _EVENT.format(cal=quote(cal, safe="@.-_"), eid=quote(event_id, safe=""))
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    try:
        status, _h, raw = _http.get(url, headers=headers)
    except Exception:
        return False, "verify_get", ""
    if status != 200:
        return False, f"http_{status}", ""
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return False, "verify_parse", ""
    if str(data.get("id") or "") != event_id:
        return False, "id_mismatch", ""
    start = ((data.get("start") or {}) if isinstance(data.get("start"), dict) else {}).get("dateTime") or ""
    end = ((data.get("end") or {}) if isinstance(data.get("end"), dict) else {}).get("dateTime") or ""
    if str(start)[:19] != str(params.get("start_rfc3339") or "")[:19]:
        return False, "start_mismatch", event_id
    if str(end)[:19] != str(params.get("end_rfc3339") or "")[:19]:
        return False, "end_mismatch", event_id
    if str(data.get("summary") or "") != CAL_SUMMARY_TEXT:
        return False, "summary_mismatch", event_id
    return True, "ok", event_id
