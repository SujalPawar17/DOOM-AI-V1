"""V6.3 capability registry. Deterministic. LLM cannot mutate."""

from __future__ import annotations

from typing import Any, Dict, Optional

from proactive.config import (
    is_act_calendar_hold_enabled,
    is_act_enabled,
    is_act_internal_enabled,
)

CAP_INTERNAL = "INTERNAL_LEDGER_NOTE"
CAP_CAL_HOLD = "CALENDAR_CREATE_HOLD"

NOTE_TEMPLATE_ID = "act0_ledger_v1"
CAL_SUMMARY_TEMPLATE_ID = "cal_hold_v1"
CAL_SUMMARY_TEXT = "DOOM hold (approved)"

REGISTRY: Dict[str, Dict[str, Any]] = {
    CAP_INTERNAL: {
        "capability_id": CAP_INTERNAL,
        "write": True,
        "risk_floor": "LOW",
        "privacy_floor": "NORMAL",
        "reversibility": "REVERSIBLE",
        "requires_approval": True,
        "requires_explicit_run": True,
        "idempotency": "INTERNAL_ONLY",
        "writer": "internal_ledger",
        "verifier": "pg_receipt",
        "allowed_account": "",
        "deferred": False,
        "action_type": "FUTURE_TASK_NOTE",
    },
    CAP_CAL_HOLD: {
        "capability_id": CAP_CAL_HOLD,
        "write": True,
        "risk_floor": "MEDIUM",
        "privacy_floor": "NORMAL",
        "reversibility": "PARTIALLY_REVERSIBLE",
        "requires_approval": True,
        "requires_explicit_run": True,
        "idempotency": "EXTERNAL_KEY",
        "writer": "calendar_hold",
        "verifier": "calendar_get",
        "allowed_account": "oauth_google",
        "deferred": False,
        "action_type": "FUTURE_CAL_RECONCILE",
    },
}

_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def capability(cap_id: str) -> Optional[Dict[str, Any]]:
    row = REGISTRY.get(str(cap_id or ""))
    return dict(row) if row else None


def capability_enabled(cap_id: str) -> bool:
    if not is_act_enabled():
        return False
    if cap_id == CAP_INTERNAL:
        return is_act_internal_enabled()
    if cap_id == CAP_CAL_HOLD:
        return is_act_calendar_hold_enabled()
    return False


def raise_risk(floor: str, current: str) -> str:
    a = _RANK.get(str(floor or "NONE"), 0)
    b = _RANK.get(str(current or "NONE"), 0)
    if b < a:
        return str(floor)
    return str(current or "NONE")


def risk_lowered(stored: str, live: str) -> bool:
    return _RANK.get(str(live or "NONE"), 0) < _RANK.get(str(stored or "NONE"), 0)
