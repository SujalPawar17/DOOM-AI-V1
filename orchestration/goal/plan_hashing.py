"""Deterministic GoalPlan hashing. Integrity only; not authorization."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from orchestration.goal.hashing import canonical_dumps


def _canon_value(value: Any) -> Any:
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    return str(value)


def canonical_plan_payload(fields: Mapping[str, Any]) -> dict:
    steps = []
    for step in fields.get("steps") or ():
        params = {str(k): _canon_value(v) for k, v in (step.get("parameters") or ())}
        steps.append({
            "action": str(step.get("action") or ""),
            "approval_required": bool(step.get("approval_required")),
            "capability_id": str(step.get("capability_id") or ""),
            "dependencies": list(step.get("dependencies") or ()),
            "parameters": params,
            "retry_count": int(step.get("retry_count") or 0),
            "risk": str(step.get("risk") or ""),
            "step_id": str(step.get("step_id") or ""),
            "timeout_ms": int(step.get("timeout_ms") or 0),
            "verification_required": bool(step.get("verification_required")),
            "verification_type": str(step.get("verification_type") or ""),
        })
    return {
        "approval_required": bool(fields.get("approval_required")),
        "computer_session_id": str(fields.get("computer_session_id") or ""),
        "goal_id": str(fields.get("goal_id") or ""),
        "owner_id": str(fields.get("owner_id") or ""),
        "plan_risk": str(fields.get("plan_risk") or ""),
        "provenance": str(fields.get("provenance") or ""),
        "schema_version": str(fields.get("schema_version") or ""),
        "session_id": str(fields.get("session_id") or ""),
        "steps": steps,
    }


def plan_hash(fields: Mapping[str, Any]) -> str:
    body = canonical_plan_payload(fields)
    return hashlib.sha256(canonical_dumps(body).encode("utf-8")).hexdigest()
