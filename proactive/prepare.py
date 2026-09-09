"""V6.2.6 deterministic PREPARE. Preview only. No LLM or tools."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Dict, Tuple

from proactive.attention import may_prepare
from proactive.config import (
    OWNER_ID,
    PREPARE_CANDIDATE_CAP,
    PREPARE_CONFIDENCE_FLOOR,
    PREPARE_RULE_VERSION,
    is_ask_enabled,
    is_prediction_enabled,
    is_prepare_enabled,
    is_proactive_enabled,
    is_suggest_enabled,
)
from proactive.otp import emit_proactive
from proactive.prepare_templates import ALLOWED_PARAM_KEYS
from proactive.store import proactive_store

TYPE_MAP = {
    "CONSIDER_REVIEW_WORK": ("PREPARE_REVIEW_OUTLINE", "prepare_review_outline", "NONE", "NONE"),
    "CONSIDER_CONFIRM_OPEN": ("PREPARE_CONFIRM_PROMPT", "prepare_confirm_prompt", "NONE", "NONE"),
    "CONSIDER_RECONCILE_TIME": ("PREPARE_SCHEDULE_DIFF", "prepare_schedule_diff", "FUTURE_CAL_RECONCILE", "MUTATION"),
    "CONSIDER_UNBLOCK": ("PREPARE_UNBLOCK_NOTE", "prepare_unblock_note", "FUTURE_TASK_NOTE", "MUTATION"),
    "CONSIDER_COMPLETE_REVIEW": ("PREPARE_REVIEW_OPTIONS", "prepare_review_options", "FUTURE_GH_REVIEW", "MUTATION"),
}


def canonical_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def param_hash_of(safe_params: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(safe_params).encode("utf-8")).hexdigest()[:64]


def preparation_fingerprint(
    owner_id: str,
    suggestion_fingerprint: str,
    preparation_type: str,
    template_id: str,
    param_hash: str,
    rule_version: str = PREPARE_RULE_VERSION,
) -> str:
    raw = (
        f"{owner_id}|{suggestion_fingerprint}|{preparation_type}|"
        f"{template_id}|{param_hash}|{rule_version}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def _evidence_ids(pred: Dict[str, Any]) -> list:
    ids = pred.get("evidence_ids")
    if isinstance(ids, list):
        return ids
    prov = pred.get("provenance") or {}
    if isinstance(prov, dict):
        raw = prov.get("evidence_ids") or []
        return raw if isinstance(raw, list) else []
    return []


def evaluate_prepare_worthiness(
    suggestion: Dict[str, Any],
    prediction: Dict[str, Any],
    now: float,
) -> Tuple[str, str]:
    """Pure. No attention. No existing-row handling."""
    sst = str(suggestion.get("status") or "")
    if sst in ("DISMISSED",):
        return "IGNORE", "DISMISSED"
    if sst not in ("OPEN", "DELIVERED"):
        return "IGNORE", "NOT_OPEN"
    try:
        svu = suggestion.get("valid_until")
        if svu is None or float(svu) <= now:
            return "IGNORE", "EXPIRED_SUGGESTION"
    except (TypeError, ValueError):
        return "IGNORE", "EXPIRED_SUGGESTION"
    if str(prediction.get("status") or "") != "ACTIVE":
        return "IGNORE", "PRED_NOT_ACTIVE"
    try:
        pvu = prediction.get("valid_until")
        if pvu is None or float(pvu) <= now:
            return "IGNORE", "EXPIRED_PREDICTION"
    except (TypeError, ValueError):
        return "IGNORE", "EXPIRED_PREDICTION"
    pc = str(suggestion.get("privacy_class") or prediction.get("privacy_class") or "NORMAL")
    if pc == "SENSITIVE":
        return "IGNORE", "PRIVACY_BLOCK"
    try:
        conf = float(suggestion.get("confidence") or prediction.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < PREPARE_CONFIDENCE_FLOOR:
        return "IGNORE", "LOW_CONFIDENCE"
    ptype = str(prediction.get("prediction_type") or "")
    if ptype == "STALE_OPEN_COMMITMENT" and pc == "PRIVATE" and len(_evidence_ids(prediction)) < 2:
        return "IGNORE", "STALE_WEAK_PRIVATE"
    risk = str(suggestion.get("risk_class") or prediction.get("risk_class") or "NONE")
    if risk not in ("NONE", "LOW", "MEDIUM", "HIGH"):
        return "IGNORE", "RISK_BLOCK"
    stype = str(suggestion.get("suggestion_type") or "")
    if stype not in TYPE_MAP:
        return "IGNORE", "UNKNOWN_TYPE"
    return "PREPARE", "ok"


def _horizon_hours(pred: Dict[str, Any], now: float) -> int:
    he = pred.get("horizon_end")
    try:
        delta = (float(he) - now) / 3600.0 if he is not None else 0.0
    except (TypeError, ValueError):
        delta = 0.0
    n = int(delta)
    if n < 0:
        return 0
    if n > 168:
        return 168
    return n


def build_safe_params(
    suggestion: Dict[str, Any],
    prediction: Dict[str, Any],
    action_type: str,
    now: float,
) -> Dict[str, Any]:
    out = {
        "risk_class": str(suggestion.get("risk_class") or prediction.get("risk_class") or "NONE")[:16],
        "horizon_hours": _horizon_hours(prediction, now),
        "prediction_type": str(prediction.get("prediction_type") or "")[:40],
        "suggestion_type": str(suggestion.get("suggestion_type") or "")[:40],
        "action_type": str(action_type or "NONE")[:40],
        "claim_code": str(suggestion.get("claim_code") or prediction.get("claim_code") or "")[:40],
    }
    extra = set(out) - ALLOWED_PARAM_KEYS
    if extra:
        raise ValueError("extra_keys")
    return {k: out[k] for k in sorted(ALLOWED_PARAM_KEYS) if k in out}


def flags_prepare_on() -> bool:
    return bool(
        is_proactive_enabled()
        and is_prediction_enabled()
        and is_suggest_enabled()
        and is_prepare_enabled()
    )


def evaluate_world_preparations(owner_id: str = OWNER_ID) -> int:
    if not flags_prepare_on():
        return 0
    created = 0
    try:
        now = time.time()
        n_sync = proactive_store.sync_preparation_lifecycle(owner_id)
        if n_sync:
            emit_proactive(
                "proactive.prepare.expired",
                attributes={"reason": "lifecycle"},
            )
        cands = proactive_store.list_prepare_candidates(owner_id, PREPARE_CANDIDATE_CAP)
        for row in cands:
            sug = row.get("suggestion") or row
            pred = row.get("prediction") or {}
            stype = str(sug.get("suggestion_type") or "")
            mapped = TYPE_MAP.get(stype)
            if not mapped:
                emit_proactive(
                    "proactive.prepare.evaluated",
                    status="skipped",
                    attributes={"abstain_reason": "UNKNOWN_TYPE"},
                )
                continue
            ptype, tid, action_type, fac = mapped
            decision, reason = evaluate_prepare_worthiness(sug, pred, now)
            if decision != "PREPARE":
                emit_proactive(
                    "proactive.prepare.evaluated",
                    status="skipped",
                    attributes={
                        "rule_id": ptype,
                        "action_type": action_type,
                        "abstain_reason": reason,
                    },
                )
                continue
            try:
                params = build_safe_params(sug, pred, action_type, now)
            except ValueError:
                emit_proactive(
                    "proactive.prepare.evaluated",
                    status="skipped",
                    attributes={"abstain_reason": "PARAM_REJECT"},
                )
                continue
            ph = param_hash_of(params)
            sfp = str(sug.get("fingerprint") or "")
            fp = preparation_fingerprint(owner_id, sfp, ptype, tid, ph, PREPARE_RULE_VERSION)
            existing = proactive_store.get_preparation_by_fingerprint(owner_id, fp)
            est = str((existing or {}).get("status") or "")
            if est in ("CANCELLED", "EXPIRED", "SUPERSEDED"):
                emit_proactive(
                    "proactive.prepare.evaluated",
                    status="skipped",
                    attributes={
                        "preparation_id": str((existing or {}).get("preparation_id") or "")[:36],
                        "abstain_reason": est,
                    },
                )
                continue
            if est in ("READY", "ASKED"):
                pid_ex = str(existing["preparation_id"])
                if est == "READY" and fac == "MUTATION" and is_ask_enabled():
                    from proactive.ask import ensure_pending_ask
                    ensure_pending_ask(existing, owner_id)
                continue
            if proactive_store.has_conflicting_pending_ask(owner_id, str(sug.get("suggestion_id") or "")):
                emit_proactive(
                    "proactive.prepare.evaluated",
                    status="skipped",
                    attributes={"abstain_reason": "PENDING_ASK"},
                )
                continue
            vu = sug.get("valid_until")
            try:
                vu_f = float(vu) if vu is not None else now + 86400
            except (TypeError, ValueError):
                vu_f = now + 86400
            cap = now + 86400
            prep_id = str(uuid.uuid4())
            rec = {
                "preparation_id": prep_id,
                "owner_id": owner_id,
                "project_id": sug.get("project_id"),
                "suggestion_id": sug.get("suggestion_id"),
                "prediction_id": sug.get("prediction_id") or pred.get("prediction_id"),
                "preparation_type": ptype,
                "action_type": action_type,
                "future_act_class": fac,
                "template_id": tid,
                "safe_params": params,
                "param_hash": ph,
                "preview_key": tid,
                "risk_class": str(sug.get("risk_class") or "NONE")[:16],
                "privacy_class": str(sug.get("privacy_class") or "NORMAL")[:16],
                "fingerprint": fp,
                "rule_id": ptype,
                "rule_version": PREPARE_RULE_VERSION,
                "valid_until": min(vu_f, cap),
                "evaluated_at": now,
                "provenance": {
                    "suggestion_id": str(sug.get("suggestion_id") or "")[:64],
                    "prediction_id": str(pred.get("prediction_id") or sug.get("prediction_id") or "")[:64],
                    "suggestion_fingerprint": sfp[:64],
                },
            }
            out_id = proactive_store.upsert_world_preparation(rec)
            if not out_id:
                continue
            created += 1
            emit_proactive(
                "proactive.prepare.created",
                attributes={
                    "preparation_id": out_id[:36],
                    "action_type": action_type,
                    "risk_class": str(rec["risk_class"])[:16],
                },
            )
            live = proactive_store.get_preparation_by_fingerprint(owner_id, fp) or {}
            if str(live.get("status") or "") != "READY":
                continue
            if fac == "MUTATION" and is_ask_enabled():
                from proactive.ask import ensure_pending_ask
                ensure_pending_ask(live, owner_id)
                continue
            priv = str(live.get("privacy_class") or "NORMAL")
            if priv == "PRIVATE":
                continue
            if priv != "NORMAL":
                continue
            ok, why = may_prepare(fp, owner_id)
            if not ok:
                emit_proactive(
                    "proactive.prepare.evaluated",
                    status="skipped",
                    attributes={
                        "preparation_id": out_id[:36],
                        "abstain_reason": why,
                    },
                )
                continue
            from proactive.delivery import deliver_prepare
            deliver_prepare(live)
    except Exception:
        emit_proactive(
            "proactive.prepare.evaluated",
            status="error",
            attributes={"reason": "prepare_eval"},
        )
    return created
