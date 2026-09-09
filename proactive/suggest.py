"""V6.2.5 deterministic suggestions. No INFORM, LLM, tools, or memory writes."""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from proactive.attention import may_suggest
from proactive.config import (
    OWNER_ID,
    SUGGEST_CANDIDATE_CAP,
    SUGGEST_CONFIDENCE_FLOOR,
    SUGGEST_RULE_VERSION,
    is_prediction_enabled,
    is_proactive_enabled,
    is_suggest_enabled,
)
from proactive.otp import emit_proactive
from proactive.store import proactive_store
from proactive.suggest_templates import ALLOWED_PARAM_KEYS, render_suggest

TYPE_MAP = {
    "DEADLINE_HORIZON": ("CONSIDER_REVIEW_WORK", "suggest_review_work"),
    "STALE_OPEN_COMMITMENT": ("CONSIDER_CONFIRM_OPEN", "suggest_confirm_open"),
    "CAL_VS_COMMITMENT_CONFLICT": ("CONSIDER_RECONCILE_TIME", "suggest_reconcile_time"),
    "TASK_BLOCKED_NEAR_DEADLINE": ("CONSIDER_UNBLOCK", "suggest_unblock"),
    "OPEN_REVIEW_AGING": ("CONSIDER_COMPLETE_REVIEW", "suggest_complete_review"),
}


def suggestion_fingerprint(
    owner_id: str,
    prediction_fingerprint: str,
    suggestion_type: str,
    template_id: str,
    rule_version: str = SUGGEST_RULE_VERSION,
) -> str:
    raw = f"{owner_id}|{prediction_fingerprint}|{suggestion_type}|{template_id}|{rule_version}"
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


def evaluate_suggest_worthiness(pred: Dict[str, Any], now: float) -> Tuple[str, str]:
    """Pure. No attention. No existing-row handling."""
    st = str(pred.get("status") or "")
    if st != "ACTIVE":
        return "IGNORE", "NOT_ACTIVE"
    vu = pred.get("valid_until")
    try:
        if vu is None or float(vu) <= now:
            return "IGNORE", "EXPIRED_PREDICTION"
    except (TypeError, ValueError):
        return "IGNORE", "EXPIRED_PREDICTION"
    pc = str(pred.get("privacy_class") or "NORMAL")
    if pc == "SENSITIVE":
        return "IGNORE", "PRIVACY_BLOCK"
    try:
        conf = float(pred.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < SUGGEST_CONFIDENCE_FLOOR:
        return "IGNORE", "LOW_CONFIDENCE"
    ptype = str(pred.get("prediction_type") or "")
    if ptype == "STALE_OPEN_COMMITMENT" and pc == "PRIVATE" and len(_evidence_ids(pred)) < 2:
        return "IGNORE", "STALE_WEAK_PRIVATE"
    risk = str(pred.get("risk_class") or "NONE")
    if ptype == "DEADLINE_HORIZON":
        if risk in ("MEDIUM", "HIGH"):
            return "SUGGEST", "ok"
        return "IGNORE", "LOW_URGENCY"
    if ptype == "STALE_OPEN_COMMITMENT":
        return "SUGGEST", "ok"
    if ptype in (
        "CAL_VS_COMMITMENT_CONFLICT",
        "TASK_BLOCKED_NEAR_DEADLINE",
        "OPEN_REVIEW_AGING",
    ):
        return "SUGGEST", "ok"
    return "IGNORE", "UNKNOWN_TYPE"


def _priority(pred: Dict[str, Any]) -> str:
    if str(pred.get("prediction_type") or "") == "OPEN_REVIEW_AGING":
        return "LOW"
    if str(pred.get("risk_class") or "") == "HIGH":
        return "HIGH"
    return "MEDIUM"


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


def _safe_params(pred: Dict[str, Any], now: float) -> Dict[str, Any]:
    out = {
        "risk_class": str(pred.get("risk_class") or "NONE")[:16],
        "horizon_hours": _horizon_hours(pred, now),
        "prediction_type": str(pred.get("prediction_type") or "")[:40],
        "claim_code": str(pred.get("claim_code") or "")[:40],
    }
    return {k: v for k, v in out.items() if k in ALLOWED_PARAM_KEYS}


def evaluate_world_suggestions(owner_id: str = OWNER_ID) -> int:
    if not (is_proactive_enabled() and is_prediction_enabled() and is_suggest_enabled()):
        return 0
    created = 0
    try:
        now = time.time()
        try:
            proactive_store.expire_world_predictions(owner_id)
        except Exception:
            pass
        n_exp = proactive_store.sync_suggestion_lifecycle(owner_id)
        if n_exp:
            emit_proactive(
                "proactive.suggestion.expired",
                attributes={"count": int(n_exp)},
            )
        cands = proactive_store.list_suggest_candidates(owner_id, SUGGEST_CANDIDATE_CAP)
        for pred in cands:
            mapped = TYPE_MAP.get(str(pred.get("prediction_type") or ""))
            if not mapped:
                emit_proactive(
                    "proactive.suggestion.evaluated",
                    status="skipped",
                    attributes={"abstain_reason": "UNKNOWN_TYPE"},
                )
                continue
            stype, tid = mapped
            pfp = str(pred.get("fingerprint") or "")
            fp = suggestion_fingerprint(owner_id, pfp, stype, tid, SUGGEST_RULE_VERSION)
            decision, reason = evaluate_suggest_worthiness(pred, now)
            if decision != "SUGGEST":
                emit_proactive(
                    "proactive.suggestion.evaluated",
                    status="skipped",
                    attributes={
                        "rule_id": stype,
                        "suggestion_type": stype,
                        "abstain_reason": reason,
                    },
                )
                continue
            existing = proactive_store.get_suggestion_by_fingerprint(owner_id, fp)
            est = str((existing or {}).get("status") or "")
            if est in ("DISMISSED", "EXPIRED", "SUPERSEDED"):
                emit_proactive(
                    "proactive.suggestion.evaluated",
                    status="skipped",
                    attributes={
                        "suggestion_id": str((existing or {}).get("suggestion_id") or "")[:36],
                        "suggestion_type": stype,
                        "abstain_reason": est,
                    },
                )
                continue
            if est == "DELIVERED":
                proactive_store.touch_suggestion_evaluated(
                    str(existing["suggestion_id"]), owner_id, now
                )
                continue
            vu = pred.get("valid_until")
            try:
                vu_f = float(vu) if vu is not None else now + 86400
            except (TypeError, ValueError):
                vu_f = now + 86400
            cap = now + 86400
            row = {
                "suggestion_id": str(uuid.uuid4()),
                "owner_id": owner_id,
                "project_id": pred.get("project_id"),
                "prediction_id": pred.get("prediction_id"),
                "suggestion_type": stype,
                "claim_code": str(pred.get("claim_code") or "")[:40],
                "template_id": tid,
                "safe_params": _safe_params(pred, now),
                "priority": _priority(pred),
                "confidence": float(pred.get("confidence") or 0),
                "risk_class": str(pred.get("risk_class") or "NONE")[:16],
                "privacy_class": str(pred.get("privacy_class") or "NORMAL")[:16],
                "fingerprint": fp,
                "rule_id": stype,
                "rule_version": SUGGEST_RULE_VERSION,
                "valid_until": min(vu_f, cap),
                "evaluated_at": now,
                "provenance": {
                    "prediction_id": str(pred.get("prediction_id") or "")[:64],
                    "prediction_fingerprint": pfp[:64],
                },
            }
            sid = ""
            if est == "OPEN" and existing:
                sid = str(existing["suggestion_id"])
                proactive_store.touch_suggestion_evaluated(sid, owner_id, now)
            else:
                sid = proactive_store.upsert_world_suggestion(row)
                if sid and not existing:
                    created += 1
                    emit_proactive(
                        "proactive.suggestion.evaluated",
                        attributes={
                            "suggestion_id": sid[:36],
                            "prediction_id": str(pred.get("prediction_id") or "")[:36],
                            "rule_id": stype,
                            "suggestion_type": stype,
                            "confidence_bucket": int(float(pred.get("confidence") or 0) * 10),
                            "risk_class": str(pred.get("risk_class") or "")[:16],
                        },
                    )
            if not sid:
                continue
            live = proactive_store.get_suggestion_by_fingerprint(owner_id, fp) or {}
            if str(live.get("status") or "") != "OPEN":
                continue
            priv = str(pred.get("privacy_class") or "NORMAL")
            if priv == "PRIVATE":
                continue
            if priv != "NORMAL":
                continue
            ok, why = may_suggest(fp, owner_id)
            if not ok:
                emit_proactive(
                    "proactive.suggestion.evaluated",
                    status="skipped",
                    attributes={
                        "suggestion_id": sid[:36],
                        "suggestion_type": stype,
                        "abstain_reason": why,
                    },
                )
                continue
            sug = {
                "suggestion_id": sid,
                "owner_id": owner_id,
                "template_id": tid,
                "priority": _priority(pred),
                "privacy_class": "NORMAL",
                "fingerprint": fp,
                "safe_params": _safe_params(pred, now),
            }
            from proactive.delivery import deliver_suggest
            deliver_suggest(sug)
    except Exception:
        emit_proactive(
            "proactive.suggestion.evaluated",
            status="error",
            attributes={"reason": "suggestion_eval"},
        )
    return created
