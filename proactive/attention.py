"""Attention budget: daily cap, cooldown, quiet hours, busy gate. Conservative."""

from __future__ import annotations

import datetime as dt
import time
from typing import Tuple

from core.state_machine import DoomState, state_machine
from proactive.config import (
    COOLDOWN_SECONDS,
    DAILY_INFORM_BUDGET,
    OWNER_ID,
    QUIET_HOURS,
)
from proactive.store import proactive_store


def _day_key() -> str:
    return dt.date.today().isoformat()


def in_quiet_hours(now: dt.datetime | None = None) -> bool:
    spec = QUIET_HOURS
    if not spec or "-" not in spec:
        return False
    try:
        a, b = spec.split("-", 1)
        start, end = int(a), int(b)
    except ValueError:
        return False
    hour = (now or dt.datetime.now()).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def cognition_busy() -> bool:
    try:
        st = state_machine.current_state
        return st in (
            DoomState.PROCESSING,
            DoomState.PLANNING,
            DoomState.THINKING,
            DoomState.EXECUTING,
            DoomState.VERIFYING,
        )
    except Exception:
        return False


def may_inform(dedupe_key: str, owner_id: str = OWNER_ID) -> Tuple[bool, str]:
    if cognition_busy():
        return False, "busy"
    if in_quiet_hours():
        return False, "quiet_hours"
    att = proactive_store.get_attention(owner_id, _day_key())
    if int(att.get("inform_count") or 0) >= DAILY_INFORM_BUDGET:
        return False, "budget"
    cd = att.get("cooldowns") or {}
    last = cd.get(dedupe_key)
    try:
        last_ts = float(last) if last is not None else 0.0
    except (TypeError, ValueError):
        last_ts = 0.0
    if last_ts and (time.time() - last_ts) < COOLDOWN_SECONDS:
        return False, "cooldown"
    return True, "ok"


def record_inform(dedupe_key: str, owner_id: str = OWNER_ID) -> None:
    proactive_store.bump_attention(owner_id, dedupe_key, _day_key())
