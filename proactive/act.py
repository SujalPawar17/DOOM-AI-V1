"""V6.3 worker entry. Materialize specs. Process RUN_REQUESTED only. Never APPROVED→writer."""

from __future__ import annotations

from typing import Any, Dict

from proactive.act_engine import process_run_queue
from proactive.act_spec import materialize_for_preparation
from proactive.config import OWNER_ID, is_act_enabled, is_prepare_enabled, is_proactive_enabled
from proactive.otp import emit_proactive
from proactive.store import proactive_store


def flags_act_on() -> bool:
    return bool(is_proactive_enabled() and is_prepare_enabled() and is_act_enabled())


def ensure_action_for_preparation(preparation: Dict[str, Any], owner_id: str = OWNER_ID) -> Dict[str, Any]:
    """Called from ASK create path. Does not execute."""
    if not flags_act_on():
        return {}
    if str(preparation.get("privacy_class") or "") == "SENSITIVE":
        return {}
    existing = proactive_store.get_action_by_preparation(str(preparation.get("preparation_id") or ""), owner_id)
    if existing:
        return existing
    spec = materialize_for_preparation(preparation, owner_id)
    if not spec:
        return {}
    did = proactive_store.insert_world_action(spec)
    if not did:
        return proactive_store.get_action_by_preparation(str(preparation.get("preparation_id") or ""), owner_id) or {}
    emit_proactive(
        "proactive.act.created",
        attributes={"action_id": did[:36], "capability_id": str(spec.get("capability_id") or "")[:40]},
    )
    return proactive_store.get_action(did, owner_id) or spec


def evaluate_world_actions(owner_id: str = OWNER_ID) -> int:
    """Recover + claim RUN_REQUESTED. Must not scan APPROVED for execution."""
    if not flags_act_on():
        return 0
    return process_run_queue("v63-act", owner_id)
