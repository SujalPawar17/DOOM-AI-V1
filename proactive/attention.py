"""Attention budget: daily cap, cooldown, quiet hours, busy gate. Conservative."""

from __future__ import annotations

import datetime as dt
import time
from typing import Tuple

from core.state_machine import DoomState, state_machine
from proactive.config import (
    COOLDOWN_SECONDS,
    DAILY_ASK_BUDGET,
    DAILY_INFORM_BUDGET,
    DAILY_PREPARE_BUDGET,
    DAILY_SUGGEST_BUDGET,
    OWNER_ID,
    ASK_COOLDOWN_SECONDS,
    PREPARE_COOLDOWN_SECONDS,
    QUIET_HOURS,
    SUGGEST_COOLDOWN_SECONDS,
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


def may_suggest(dedupe_key: str, owner_id: str = OWNER_ID) -> Tuple[bool, str]:
    if cognition_busy():
        return False, "busy"
    if in_quiet_hours():
        return False, "quiet_hours"
    att = proactive_store.get_attention(owner_id, _day_key())
    if int(att.get("suggest_count") or 0) >= DAILY_SUGGEST_BUDGET:
        return False, "budget"
    cd = att.get("cooldowns") or {}
    last = cd.get("suggest|" + dedupe_key)
    try:
        last_ts = float(last) if last is not None else 0.0
    except (TypeError, ValueError):
        last_ts = 0.0
    if last_ts and (time.time() - last_ts) < SUGGEST_COOLDOWN_SECONDS:
        return False, "cooldown"
    return True, "ok"


def record_suggest(dedupe_key: str, owner_id: str = OWNER_ID) -> None:
    proactive_store.bump_suggest_attention(owner_id, "suggest|" + dedupe_key, _day_key())


def may_prepare(dedupe_key: str, owner_id: str = OWNER_ID) -> Tuple[bool, str]:
    if cognition_busy():
        return False, "busy"
    if in_quiet_hours():
        return False, "quiet_hours"
    att = proactive_store.get_attention(owner_id, _day_key())
    if int(att.get("prepare_count") or 0) >= DAILY_PREPARE_BUDGET:
        return False, "budget"
    cd = att.get("cooldowns") or {}
    last = cd.get("prepare|" + dedupe_key)
    try:
        last_ts = float(last) if last is not None else 0.0
    except (TypeError, ValueError):
        last_ts = 0.0
    if last_ts and (time.time() - last_ts) < PREPARE_COOLDOWN_SECONDS:
        return False, "cooldown"
    return True, "ok"


def record_prepare(dedupe_key: str, owner_id: str = OWNER_ID) -> None:
    proactive_store.bump_prepare_attention(owner_id, "prepare|" + dedupe_key, _day_key())


def may_ask(dedupe_key: str, owner_id: str = OWNER_ID) -> Tuple[bool, str]:
    if cognition_busy():
        return False, "busy"
    if in_quiet_hours():
        return False, "quiet_hours"
    att = proactive_store.get_attention(owner_id, _day_key())
    if int(att.get("ask_count") or 0) >= DAILY_ASK_BUDGET:
        return False, "budget"
    cd = att.get("cooldowns") or {}
    last = cd.get("ask|" + dedupe_key)
    try:
        last_ts = float(last) if last is not None else 0.0
    except (TypeError, ValueError):
        last_ts = 0.0
    if last_ts and (time.time() - last_ts) < ASK_COOLDOWN_SECONDS:
        return False, "cooldown"
    return True, "ok"


def record_ask(dedupe_key: str, owner_id: str = OWNER_ID) -> None:
    proactive_store.bump_ask_attention(owner_id, "ask|" + dedupe_key, _day_key())
