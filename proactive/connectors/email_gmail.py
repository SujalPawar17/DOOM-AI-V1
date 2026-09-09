"""Gmail READ. users.messages.list + get format=metadata only. No send/modify."""

from __future__ import annotations

import json
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from proactive.connectors.base import FencedRecord, ReadConnector
from proactive.connectors.http_safe import SafeHttp, SafeHttpError
from proactive.connectors.oauth_token import refresh_google_access_token
from proactive.fence import fence_payload

_LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
_GET_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}"
_SNIPPET_MAX = 160
_http = SafeHttp()


def _header_map(payload: Dict[str, Any]) -> Dict[str, str]:
    headers = payload.get("headers") if isinstance(payload.get("headers"), list) else []
    out: Dict[str, str] = {}
    for h in headers:
        if not isinstance(h, dict):
            continue
        name = str(h.get("name") or "").lower()
        if name in ("from", "to", "subject", "date", "message-id"):
            out[name] = str(h.get("value") or "")[:500]
    return out


def _from_domain(raw: str) -> str:
    s = str(raw or "")
    if "<" in s and ">" in s:
        s = s[s.rfind("<") + 1:s.rfind(">")]
    if "@" not in s:
        return ""
    return s.split("@", 1)[-1].strip().lower()[:80]


def _parse_internal(ms: Any, date_hdr: str) -> float:
    try:
        n = int(ms or 0)
        if n > 10_000_000_000:
            return n / 1000.0
        if n > 0:
            return float(n)
    except (TypeError, ValueError):
        pass
    if date_hdr:
        try:
            dt = parsedate_to_datetime(date_hdr)
            if dt is not None:
                return dt.timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    return 0.0


class GmailReadConnector(ReadConnector):
    connector_type = "gmail"

    def __init__(self, http: Optional[SafeHttp] = None):
        self._http = http or _http

    def fetch_updates(self, account: Dict[str, Any], cursor: str) -> Tuple[List[FencedRecord], str]:
        secret_ref = str(account.get("secret_ref") or "")
        token = refresh_google_access_token(secret_ref, http=self._http)
        if not token:
            raise SafeHttpError("gmail auth missing", status=401)
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        params = {"maxResults": "15", "q": "newer_than:14d"}
        if cursor and not cursor.startswith("{"):
            params["pageToken"] = cursor
        list_url = _LIST_URL + "?" + urlencode(params)
        status, _hdrs, raw = self._http.get(list_url, headers=headers)
        if status in (403, 429):
            raise SafeHttpError(f"http {status}", status=status)
        if status == 401:
            raise SafeHttpError("http 401", status=401)
        if status != 200:
            raise SafeHttpError(f"http {status}", status=status)
        try:
            listing = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return [], cursor
        if not isinstance(listing, dict):
            return [], cursor
        msgs = listing.get("messages") if isinstance(listing.get("messages"), list) else []
        next_cursor = str(listing.get("nextPageToken") or cursor or "")
        records: List[FencedRecord] = []
        for item in msgs[:15]:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or "")[:128]
            if not mid:
                continue
            rec = self._get_metadata(mid, token, account)
            if rec:
                records.append(rec)
        return records, next_cursor

    def _get_metadata(self, mid: str, token: str, account: Dict[str, Any]) -> Optional[FencedRecord]:
        q = urlencode([
            ("format", "metadata"),
            ("metadataHeaders", "From"),
            ("metadataHeaders", "To"),
            ("metadataHeaders", "Subject"),
            ("metadataHeaders", "Date"),
            ("metadataHeaders", "Message-ID"),
        ])
        url = _GET_URL.format(mid=quote(mid, safe="")) + "?" + q
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        status, _hdrs, raw = self._http.get(url, headers=headers)
        if status in (401, 403, 429):
            raise SafeHttpError(f"http {status}", status=status)
        if status != 200:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        payload_obj = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        hdrs = _header_map(payload_obj)
        snippet = str(data.get("snippet") or "")[:_SNIPPET_MAX]
        labels = data.get("labelIds") if isinstance(data.get("labelIds"), list) else []
        unread = 1 if "UNREAD" in [str(x) for x in labels] else 0
        occurred = _parse_internal(data.get("internalDate"), hdrs.get("date") or "")
        persist = {
            "message_id": str(data.get("id") or mid)[:128],
            "thread_id": str(data.get("threadId") or "")[:128],
            "from_domain": _from_domain(hdrs.get("from") or ""),
            "label_count": int(len(labels)),
            "unread": unread,
        }
        fenced, dropped = fence_payload(persist)
        if dropped:
            return None
        eph_src = {
            "subject": str(hdrs.get("subject") or "")[:80],
            "snippet": snippet,
            "date_hdr": str(hdrs.get("date") or "")[:80],
        }
        eph, eph_drop = fence_payload(eph_src)
        if eph_drop:
            return None
        return FencedRecord(
            connector_type="gmail",
            source_record_id=str(data.get("id") or mid)[:128],
            occurred_at=occurred,
            privacy_class="PRIVATE",
            payload=fenced,
            fact_kind="EMAIL_META",
            signal_type="",
            project_id=str(account.get("project_id") or ""),
            ephemeral=eph,
        )
