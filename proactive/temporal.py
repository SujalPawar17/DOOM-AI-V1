"""V6.2.4 temporal gates. Evaluation clock is separate from source event time."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def utc_from_epoch(ts: Optional[float]) -> Optional[datetime]:
    if ts is None:
        return None
    try:
        n = float(ts)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc)


def require_source_time(*timestamps: Optional[float]) -> bool:
    """False if a required source clock is missing (do not substitute now)."""
    return all(utc_from_epoch(t) is not None for t in timestamps)


def horizon_bucket_date(ts: float) -> str:
    dt = utc_from_epoch(ts)
    if dt is None:
        return "none"
    return dt.strftime("%Y-%m-%d")


def horizon_bucket_week(ts: float) -> str:
    dt = utc_from_epoch(ts)
    if dt is None:
        return "none"
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def risk_from_horizon(due_ts: Optional[float], now: float, stale: bool = False, conflict: bool = False) -> str:
    if conflict:
        return "HIGH"
    if stale:
        return "MEDIUM"
    if due_ts is None:
        return "NONE"
    delta = float(due_ts) - now
    if delta <= 2 * 3600:
        return "HIGH"
    if delta <= 24 * 3600:
        return "MEDIUM"
    if delta <= 72 * 3600:
        return "LOW"
    return "NONE"
