"""Deterministic email commitment extraction. No LLM. DATA_ONLY. Abstain if ambiguous."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional

from proactive.fence import fence_payload

COMMITMENT_TYPES = frozenset({
    "DEADLINE", "PROMISE", "FOLLOW_UP", "MEETING", "REPLY_REQUIRED",
    "DELIVERY", "ACTION_REQUIRED", "PAYMENT", "REVIEW_REQUIRED",
})

_SPECULATIVE = (
    "maybe", "perhaps", "might ", "if you want", "considering", "not sure",
    "we should talk", "just thinking",
)
_HISTORICAL = ("yesterday", "last week", "already sent", "already done", "as i mentioned")
_QUOTE_START = re.compile(r"^(>+\s*|on .+ wrote:)", re.IGNORECASE)

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _strip_quoted(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        if _QUOTE_START.match(line.strip()):
            break
        lines.append(line)
    return " ".join(lines)


def _source_dt(occurred_at: float, date_hdr: str) -> datetime:
    if date_hdr:
        try:
            dt = parsedate_to_datetime(date_hdr)
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
        except (TypeError, ValueError, OverflowError):
            pass
    if occurred_at and occurred_at > 0:
        return datetime.fromtimestamp(occurred_at, tz=timezone.utc)
    return datetime.now(timezone.utc)


def _parse_due(text: str, base: datetime) -> tuple:
    """Return (due_ts_or_None, tz_name, had_temporal). Never invent a year on bare month-day."""
    t = text.lower()
    tz_name = str(base.tzinfo) if base.tzinfo else "UTC"
    had = False
    due = None
    hour = 17
    hm = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t)
    if hm:
        had = True
        hour = int(hm.group(1))
        minute = int(hm.group(2) or 0)
        ap = hm.group(3)
        if ap == "pm" and hour < 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
        due = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if "eod" in t or "end of day" in t:
        had = True
        due = base.replace(hour=17, minute=0, second=0, microsecond=0)
        if due <= base:
            due = due + timedelta(days=1)
    if re.search(r"\btomorrow\b", t):
        had = True
        due = (base + timedelta(days=1)).replace(hour=hour if hm else 17, minute=0, second=0, microsecond=0)
    elif re.search(r"\btoday\b", t) and due is None:
        had = True
        due = base.replace(hour=hour if hm else 17, minute=0, second=0, microsecond=0)
    elif re.search(r"\bnext week\b", t):
        had = True
        due = (base + timedelta(days=7)).replace(hour=17, minute=0, second=0, microsecond=0)
    else:
        for name, idx in _WEEKDAYS.items():
            if re.search(rf"\b{name}\b", t):
                had = True
                delta = (idx - base.weekday()) % 7
                if delta == 0:
                    delta = 7
                due = (base + timedelta(days=delta)).replace(hour=hour if hm else 17, minute=0, second=0, microsecond=0)
                break
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", t)
    if iso:
        had = True
        try:
            due = datetime(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)), 17, 0, tzinfo=base.tzinfo or timezone.utc)
        except ValueError:
            due = None
    return (due.timestamp() if due else None, tz_name, had)


def _classify(text: str) -> Optional[str]:
    t = text.lower()
    if re.search(r"\b(please review|review (this|before)|requested reviewers)\b", t):
        return "REVIEW_REQUIRED"
    if re.search(r"\b(meeting|call at|sync at|stand-?up)\b", t):
        return "MEETING"
    if re.search(r"\b(please reply|waiting for your (reply|response)|rsvp)\b", t):
        return "REPLY_REQUIRED"
    if re.search(r"\b(payment|invoice|pay by)\b", t):
        return "PAYMENT"
    if re.search(r"\b(i('ll| will) (send|deliver|ship)|deliver(y| it) (on|by))\b", t):
        return "DELIVERY"
    if re.search(r"\b(i('ll| will) follow up|follow up (next|on|by))\b", t):
        return "FOLLOW_UP"
    if re.search(r"\b(i('ll| will) (send|get this|do this)|please send .{0,40} by|get this to me by)\b", t):
        return "DEADLINE"
    if re.search(r"\b(i('ll| will)|i promise)\b", t) and re.search(r"\b(by|before|on|tomorrow|friday|monday)\b", t):
        return "PROMISE"
    if re.search(r"\b(action required|please (do|complete|finish)|need you to)\b", t):
        return "ACTION_REQUIRED"
    return None


def extract_commitment(subject: str, snippet: str, occurred_at: float, date_hdr: str = "") -> Optional[Dict[str, Any]]:
    raw = _strip_quoted(subject or "") + " " + _strip_quoted(snippet or "")
    fenced, dropped = fence_payload({"t": raw[:240]})
    if dropped or not fenced.get("t"):
        return None
    text = str(fenced["t"])
    low = text.lower()
    if any(s in low for s in _SPECULATIVE):
        return None
    if any(s in low for s in _HISTORICAL):
        return None
    ctype = _classify(text)
    if not ctype:
        return None
    base = _source_dt(occurred_at, date_hdr)
    due_ts, tz_name, had_time = _parse_due(text, base)
    score = 0.45
    if ctype:
        score += 0.15
    if had_time:
        score += 0.20
    if re.search(r"\b(i will|i'll|please send|please review|get this to me)\b", low):
        score += 0.15
    if due_ts is None and ctype in ("DEADLINE", "DELIVERY", "MEETING") and not had_time:
        return None
    if score < 0.70:
        return None
    return {
        "commitment_type": ctype,
        "due_at": due_ts,
        "timezone": tz_name[:40],
        "confidence": min(0.95, round(score, 2)),
        "normalized_code": ctype,
    }


def fingerprint(account_id: str, message_id: str, commitment_type: str, due_at: Optional[float]) -> str:
    bucket = str(int(due_at) // 3600) if due_at else "none"
    raw = f"{account_id}|{message_id}|{commitment_type}|{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]
