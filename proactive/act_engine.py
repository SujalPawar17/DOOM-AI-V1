"""V6.3 ActionEngine. Sole execution owner. Never called from ASK approve."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

from proactive.act_policy import (
    CAP_CAL_HOLD,
    CAP_INTERNAL,
    capability,
    capability_enabled,
)
from proactive.act_spec import compute_action_hash, reject_client_hash
from proactive.act_verify import verify_action
from proactive.config import (
    ACT_CANDIDATE_CAP,
    ACT_MAX_ATTEMPTS,
    ACT_POLICY_VERSION,
    ACT_WRITER_TIMEOUT_SEC,
    DAILY_ACT_CALENDAR_BUDGET,
    DAILY_ACT_INTERNAL_BUDGET,
    OWNER_ID,
    is_act_enabled,
    is_calendar_enabled,
)
from proactive.otp import emit_proactive
from proactive.store import proactive_store


def _secret_ref(owner_id: str) -> str:
    try:
        rows = proactive_store.list_active_accounts("calendar_google", owner_id) or []
    except Exception:
        rows = []
    for a in rows:
        if str(a.get("secret_ref") or ""):
            return str(a.get("secret_ref"))[:64]
    return ""


def _preconditions(action: Dict[str, Any], owner_id: str) -> Optional[str]:
    if not is_act_enabled():
        return "flag_off"
    cap_id = str(action.get("capability_id") or "")
    if not capability_enabled(cap_id):
        return "capability_disabled"
    cap = capability(cap_id)
    if not cap:
        return "writer_not_allowlisted"
    if str(action.get("owner_id") or "") != owner_id:
        return "owner"
    if str(action.get("privacy_class") or "") == "SENSITIVE":
        return "privacy"
    if cap_id == CAP_CAL_HOLD and str(action.get("privacy_class") or "") != "NORMAL":
        return "privacy"
    live_hash = compute_action_hash(
        str(action.get("owner_id") or ""),
        str(action.get("preparation_id") or ""),
        cap_id,
        str(action.get("action_type") or ""),
        action.get("target_ref") if isinstance(action.get("target_ref"), dict) else {},
        action.get("exec_params") if isinstance(action.get("exec_params"), dict) else {},
        str(action.get("param_hash") or ""),
        str(action.get("risk_class") or ""),
        str(action.get("privacy_class") or ""),
        str(action.get("policy_version") or ACT_POLICY_VERSION),
    )
    if live_hash != str(action.get("action_hash") or ""):
        return "action_hash"
    prep = proactive_store.get_preparation(str(action.get("preparation_id") or ""), owner_id)
    if not prep:
        return "preparation"
    if str(prep.get("status") or "") in ("EXPIRED", "CANCELLED", "SUPERSEDED"):
        return "preparation"
    if str(prep.get("param_hash") or "") != str(action.get("param_hash") or ""):
        return "param_hash"
    if str(prep.get("privacy_class") or "") == "SENSITIVE":
        return "privacy"
    aid = str(action.get("approval_id") or "")
    appr = proactive_store.get_approval(aid, owner_id) if aid else None
    if not appr or str(appr.get("status") or "") != "APPROVED":
        return "approval"
    vu = appr.get("valid_until")
    if vu is not None and float(vu) < time.time():
        return "approval_expired"
    avu = action.get("valid_until")
    if avu is not None and float(avu) < time.time():
        return "action_expired"
    if str(appr.get("rule_version") or "") != ACT_POLICY_VERSION:
        return "legacy_approval"
    if not str(appr.get("action_hash") or "") or str(appr.get("action_hash") or "") != str(action.get("action_hash") or ""):
        return "legacy_approval"
    ide = proactive_store.get_idempotency(owner_id, str(action.get("idempotency_key") or ""))
    st = str((ide or {}).get("state") or "")
    if st in ("COMPLETED", "UNKNOWN"):
        return "idempotency"
    if cap_id == CAP_CAL_HOLD:
        if not is_calendar_enabled():
            return "connector_health"
        if not _secret_ref(owner_id):
            return "vault"
        n = proactive_store.count_actions_today(owner_id, CAP_CAL_HOLD)
        if n >= DAILY_ACT_CALENDAR_BUDGET:
            return "budget"
    if cap_id == CAP_INTERNAL:
        n = proactive_store.count_actions_today(owner_id, CAP_INTERNAL)
        if n >= DAILY_ACT_INTERNAL_BUDGET:
            return "budget"
    if int(action.get("attempt_n") or 0) >= ACT_MAX_ATTEMPTS:
        return "attempts"
    return None


def _dispatch(action: Dict[str, Any]) -> tuple:
    cap = str(action.get("capability_id") or "")
    if cap == CAP_INTERNAL:
        from proactive.writers.internal_ledger import write_internal_ledger
        return write_internal_ledger(action)
    if cap == CAP_CAL_HOLD:
        from proactive.writers.calendar_hold import write_calendar_hold
        return write_calendar_hold(action, timeout=float(ACT_WRITER_TIMEOUT_SEC))
    raise ValueError("writer_not_allowlisted")


def process_claimed(action: Dict[str, Any], worker_id: str) -> str:
    if not isinstance(action, dict) or not action.get("action_id"):
        return ""
    owner = str(action.get("owner_id") or OWNER_ID)
    aid = str(action.get("action_id") or "")
    why = _preconditions(action, owner)
    proactive_store.append_action_event(aid, "PRECONDITION_CHECK", "PRECONDITION_CHECK", "precondition_checked")
    if why:
        emit_proactive(
            "proactive.act.precondition_failed",
            status="skipped",
            attributes={"action_id": aid[:36], "capability_id": str(action.get("capability_id") or "")[:40], "outcome_code": why[:40]},
        )
        proactive_store.finalize_action(aid, owner, "FAILED", why, worker_id, int(action.get("attempt_n") or 0))
        return "FAILED"
    attempt_n = proactive_store.begin_attempt(aid, owner, worker_id)
    if attempt_n is None:
        return "lease"
    if str(action.get("capability_id") or "") == CAP_CAL_HOLD:
        action = dict(action)
        action["_runtime_secret_ref"] = _secret_ref(owner)
    emit_proactive(
        "proactive.act.execution_started",
        attributes={"action_id": aid[:36], "capability_id": str(action.get("capability_id") or "")[:40], "attempt_n": int(attempt_n)},
    )
    receipt = ""
    try:
        receipt, _ch, _st = _dispatch(action)
    except TimeoutError:
        proactive_store.finalize_action(aid, owner, "UNKNOWN_OUTCOME", "timeout", worker_id, attempt_n)
        emit_proactive("proactive.act.unknown_outcome", status="timeout", attributes={"action_id": aid[:36], "outcome_code": "timeout"})
        return "UNKNOWN_OUTCOME"
    except Exception as exc:
        name = type(exc).__name__.lower()
        if "timeout" in name or "network" in str(exc).lower():
            proactive_store.finalize_action(aid, owner, "UNKNOWN_OUTCOME", "timeout", worker_id, attempt_n)
            return "UNKNOWN_OUTCOME"
        proactive_store.finalize_action(aid, owner, "FAILED", "writer", worker_id, attempt_n)
        emit_proactive("proactive.act.failed", status="error", attributes={"action_id": aid[:36], "outcome_code": "writer"})
        return "FAILED"
    ok, verdict, observed = verify_action(action, receipt)
    proactive_store.insert_verification(aid, attempt_n, str(action.get("capability_id") or ""), "ok" if ok else verdict, observed or receipt)
    if not ok:
        proactive_store.finalize_action(aid, owner, "UNKNOWN_OUTCOME" if receipt else "FAILED", verdict, worker_id, attempt_n, receipt)
        emit_proactive("proactive.act.unknown_outcome" if receipt else "proactive.act.failed", attributes={"action_id": aid[:36], "outcome_code": verdict[:40]})
        return "UNKNOWN_OUTCOME" if receipt else "FAILED"
    proactive_store.finalize_action(aid, owner, "COMPLETED", "ok", worker_id, attempt_n, receipt)
    emit_proactive(
        "proactive.act.completed",
        attributes={"action_id": aid[:36], "capability_id": str(action.get("capability_id") or "")[:40], "outcome_code": "ok"},
    )
    return "COMPLETED"


def request_run(action_id: str, owner_id: str, client_action_hash: str) -> Dict[str, Any]:
    row = proactive_store.get_action(action_id, owner_id)
    if not row:
        return {"ok": False, "error": "not_found", "http": 404}
    ok, why = reject_client_hash(row, client_action_hash or str(row.get("action_hash") or ""))
    if not ok:
        return {"ok": False, "error": why, "http": 409}
    if str(row.get("status") or "") == "COMPLETED":
        return {"ok": True, "replay": True, "status": "COMPLETED", "idempotency_hit": True}
    if str(row.get("status") or "") == "UNKNOWN_OUTCOME":
        return {"ok": False, "error": "unknown_outcome", "http": 409}
    if str(row.get("status") or "") == "RUN_REQUESTED":
        return {"ok": True, "replay": True, "status": "RUN_REQUESTED", "idempotency_hit": True}
    if str(row.get("status") or "") != "APPROVED_NOT_RUN":
        return {"ok": False, "error": "not_runnable", "status": row.get("status"), "http": 409}
    if not is_act_enabled() or not capability_enabled(str(row.get("capability_id") or "")):
        return {"ok": False, "error": "disabled", "http": 409}
    st = proactive_store.enqueue_run(action_id, owner_id)
    if not st:
        return {"ok": False, "error": "conflict", "http": 409}
    emit_proactive("proactive.act.run_requested", attributes={"action_id": action_id[:36], "capability_id": str(row.get("capability_id") or "")[:40]})
    return {"ok": True, "status": "RUN_REQUESTED"}


def cancel_action(action_id: str, owner_id: str) -> Dict[str, Any]:
    row = proactive_store.get_action(action_id, owner_id)
    if not row:
        return {"ok": False, "error": "not_found", "http": 404}
    if str(row.get("status") or "") in ("EXECUTING", "VERIFYING", "COMPLETED"):
        return {"ok": False, "error": "conflict", "http": 409}
    ok = proactive_store.finalize_action(action_id, owner_id, "CANCELLED", "cancel", "", int(row.get("attempt_n") or 0))
    return {"ok": bool(ok), "status": "CANCELLED" if ok else str(row.get("status"))}


def process_run_queue(worker_id: str = "v63-act", owner_id: str = OWNER_ID) -> int:
    if not is_act_enabled():
        return 0
    n = 0
    try:
        proactive_store.recover_act_leases(owner_id)
        for _ in range(max(1, int(ACT_CANDIDATE_CAP))):
            row = proactive_store.claim_run(owner_id, worker_id)
            if not row:
                break
            process_claimed(row, worker_id)
            n += 1
    except Exception:
        emit_proactive("proactive.act.skipped", status="error", attributes={"outcome_code": "act_eval"})
    return n
