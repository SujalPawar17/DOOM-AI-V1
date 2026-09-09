"""Google Calendar READ. events.list GET only. No insert/patch/delete."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from proactive.connectors.base import FencedRecord, ReadConnector
from proactive.connectors.http_safe import SafeHttp, SafeHttpError
from proactive.connectors.oauth_token import refresh_google_access_token
from proactive.fence import fence_payload

_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
_http = SafeHttp()


def _parse_ts(raw: Any) -> float:
    if isinstance(raw, dict):
        raw = raw.get("dateTime") or raw.get("date") or ""
    s = str(raw or "").strip()
    if not s:
        return 0.0
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        if "T" not in s:
            dt = datetime.fromisoformat(s + "T00:00:00+00:00")
        else:
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


class GoogleCalendarConnector(ReadConnector):
    connector_type = "calendar_google"

    def __init__(self, http: Optional[SafeHttp] = None):
        self._http = http or _http

    def fetch_updates(self, account: Dict[str, Any], cursor: str) -> Tuple[List[FencedRecord], str]:
        secret_ref = str(account.get("secret_ref") or "")
        token = refresh_google_access_token(secret_ref, http=self._http)
        if not token:
            raise SafeHttpError("calendar auth missing", status=401)
        from proactive.vault import get_secret
        blob = get_secret(secret_ref) or {}
        cal = quote(str(blob.get("calendar_id") or "primary"), safe="@.-_")
        params = {
            "singleEvents": "true",
            "maxResults": "50",
            "orderBy": "updated",
        }
        if cursor:
            params["updatedMin"] = cursor
        url = _EVENTS_URL.format(cal=cal) + "?" + urlencode(params)
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        status, _hdrs, raw = self._http.get(url, headers=headers)
        if status in (403, 429):
            raise SafeHttpError(f"http {status}", status=status)
        if status != 200:
            raise SafeHttpError(f"http {status}", status=status)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return [], cursor
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return [], cursor
        records: List[FencedRecord] = []
        newest = cursor
        for item in items[:50]:
            if not isinstance(item, dict):
                continue
            rec, upd = self._to_record(item, account)
            if rec:
                records.append(rec)
            if upd and (not newest or upd > newest):
                newest = upd
        return records, newest or cursor

    def _to_record(self, item: Dict[str, Any], account: Dict[str, Any]) -> Tuple[Optional[FencedRecord], str]:
        eid = str(item.get("id") or "")[:128]
        if not eid:
            return None, ""
        updated = str(item.get("updated") or "")
        attendees = item.get("attendees") if isinstance(item.get("attendees"), list) else []
        summary = str(item.get("summary") or "")[:80]
        payload = {
            "event_id": eid,
            "status": str(item.get("status") or "")[:32],
            "start": str((item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date") or "")[:40],
            "end": str((item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date") or "")[:40],
            "time_zone": str((item.get("start") or {}).get("timeZone") or "")[:40],
            "attendee_count": int(len(attendees)),
            "recurring_event_id": str(item.get("recurringEventId") or "")[:80],
            "updated": updated[:40],
            "summary": summary,
        }
        fenced, dropped = fence_payload(payload)
        if dropped:
            return None, updated
        occurred = _parse_ts(item.get("start")) or _parse_ts(item.get("updated"))
        rec = FencedRecord(
            connector_type="calendar_google",
            source_record_id=eid,
            occurred_at=occurred,
            privacy_class=str(account.get("privacy_class_default") or "NORMAL"),
            payload=fenced,
            fact_kind="CAL_EVENT",
            signal_type="CALENDAR_EVENT",
            project_id=str(account.get("project_id") or ""),
        )
        return rec, updated
