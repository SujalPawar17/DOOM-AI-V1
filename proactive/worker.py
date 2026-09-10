"""V6.1 worker: claim → recover undelivered INFORM → else IGNORE/INFORM → ack.

I19: an existing valid INFORM insight with zero delivery rows is a durable
obligation. Attention (budget/cooldown/busy/quiet) must not ACK that signal
as IGNORE. Attention still applies to NEW insights.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Optional

from proactive.attention import may_inform
from proactive.config import (
    INSIGHT_TTL_SECONDS,
    MAX_ATTEMPTS,
    OWNER_ID,
    POLL_INTERVAL_SEC,
    is_proactive_enabled,
)
from proactive.delivery import deliver_inform
from proactive.otp import emit_proactive
from proactive.poller import poll_connectors, poll_internal_sources
from proactive.predict import evaluate_world_predictions
from proactive.prepare import evaluate_world_preparations
from proactive.ask import expire_pending_asks
from proactive.draft import evaluate_world_drafts
from proactive.act import evaluate_world_actions
from proactive.suggest import evaluate_world_suggestions
from proactive.schemas import Insight
from proactive.significance import evaluate_significance
from proactive.snapshot import build_world_snapshot
from proactive.store import proactive_store

_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def _ack_or_retry(signal_id: str, worker_id: str, ok: bool) -> None:
    if ok:
        if not proactive_store.ack(signal_id, worker_id, "PROCESSED"):
            emit_proactive("proactive.outcome", status="skipped", attributes={"reason": "ack_fenced"})
        return
    proactive_store.retry_or_dead(signal_id, worker_id, "UNKNOWN_ERROR")


def _insight_from_row(row: dict, cycle: str) -> Insight:
    return Insight(
        insight_id=row["insight_id"],
        insight_type=row.get("insight_type") or "",
        entity_type=row.get("entity_type") or "",
        entity_id=row.get("entity_id") or "",
        project_id=row.get("project_id"),
        privacy_class=row.get("privacy_class") or "NORMAL",
        recommended_intervention="INFORM",
        urgency=row.get("urgency") or "low",
        dedupe_key=row.get("dedupe_key") or "",
        template_id=row.get("template_id") or "",
        safe_params=row.get("safe_params") if isinstance(row.get("safe_params"), dict) else {},
        owner_id=row.get("owner_id") or OWNER_ID,
        proactive_cycle_id=cycle,
        valid_until=time.time() + INSIGHT_TTL_SECONDS,
    )


def _complete_inform_delivery(sid: str, worker_id: str, ins: Insight) -> None:
    """Deliver an INFORM obligation. ACK iff a delivery row exists afterward."""
    iid = ins.insight_id
    delivered = deliver_inform(ins)
    if delivered or proactive_store.delivery_exists(iid, "hud"):
        emit_proactive("proactive.outcome", attributes={"insight_id": iid[:36], "status": "ok"})
        _ack_or_retry(sid, worker_id, True)
        return
    emit_proactive(
        "proactive.outcome",
        status="error",
        attributes={"insight_id": iid[:36], "reason": "delivery_pending"},
    )
    _ack_or_retry(sid, worker_id, False)


def _recover_undelivered_inform(item: dict, worker_id: str, cycle: str) -> bool:
    """True if this signal was fully handled as an existing INFORM obligation."""
    row = proactive_store.find_valid_inform_insight(
        item.get("owner_id") or OWNER_ID,
        str(item.get("entity_id") or ""),
        str(item.get("signal_type") or ""),
    )
    if not row or row.get("recommended_intervention") != "INFORM":
        return False
    iid = row["insight_id"]
    ins = _insight_from_row(row, cycle)
    if proactive_store.delivery_exists(iid, "hud"):
        emit_proactive(
            "proactive.decision",
            attributes={"intervention": "INFORM", "reason": "delivery_exists"},
        )
        _ack_or_retry(item["signal_id"], worker_id, True)
        return True
    emit_proactive(
        "proactive.decision",
        attributes={"intervention": "INFORM", "reason": "undelivered_recovery"},
    )
    _complete_inform_delivery(item["signal_id"], worker_id, ins)
    return True


def _process_item(item: dict, worker_id: str) -> None:
    cycle = str(uuid.uuid4())
    sid = item["signal_id"]
    if _recover_undelivered_inform(item, worker_id, cycle):
        return

    snap = build_world_snapshot()
    if not snap.is_fresh():
        emit_proactive("proactive.decision", attributes={"intervention": "IGNORE", "reason": "stale_snapshot"})
        _ack_or_retry(sid, worker_id, True)
        return
    sig_res = evaluate_significance(item, snap)
    intervention = "INFORM" if sig_res.candidate_inform else "IGNORE"
    if intervention == "INFORM" and item.get("privacy_class") != "NORMAL":
        intervention = "IGNORE"
    dedupe = f"{item.get('signal_type')}|{item.get('entity_id')}|{sig_res.template_id}"
    if intervention == "INFORM":
        ok, why = may_inform(dedupe, item.get("owner_id") or OWNER_ID)
        if not ok:
            intervention = "IGNORE"
            emit_proactive("proactive.decision", attributes={"intervention": "IGNORE", "reason": why})
    emit_proactive(
        "proactive.decision",
        attributes={
            "intervention": intervention,
            "score_bucket": int(sig_res.score * 10),
            "signal_type": str(item.get("signal_type") or "")[:40],
            "privacy_class": str(item.get("privacy_class") or "NORMAL"),
        },
    )
    if intervention != "INFORM":
        _ack_or_retry(sid, worker_id, True)
        return

    ins = Insight(
        insight_type=sig_res.insight_type,
        source_signal_ids=[item["signal_id"]],
        entity_type=item.get("entity_type") or "",
        entity_id=item.get("entity_id") or "",
        project_id=(item.get("payload") or {}).get("project_id"),
        significance_score=sig_res.score,
        confidence=sig_res.confidence,
        urgency=sig_res.urgency,
        privacy_class="NORMAL",
        recommended_intervention="INFORM",
        valid_until=time.time() + INSIGHT_TTL_SECONDS,
        dedupe_key=dedupe,
        template_id=sig_res.template_id,
        safe_params={
            "disk_percent": int((item.get("payload") or {}).get("disk_percent") or snap.host.get("disk_percent") or 0),
            "open_count": int(snap.circuit.get("open_count") or 0),
            "entity_id": str(item.get("entity_id") or "")[:40],
            "insight_type": sig_res.insight_type,
        },
        owner_id=item.get("owner_id") or OWNER_ID,
        proactive_cycle_id=cycle,
    )
    iid = proactive_store.insert_insight(ins)
    if not iid:
        emit_proactive("proactive.outcome", status="error", attributes={"reason": "insight_persist"})
        _ack_or_retry(sid, worker_id, False)
        return
    ins.insight_id = iid
    emit_proactive(
        "proactive.insight.created",
        attributes={"insight_id": iid[:36], "intervention": "INFORM", "privacy_class": "NORMAL"},
    )
    _complete_inform_delivery(sid, worker_id, ins)


def process_once(worker_id: str = "v61-worker") -> int:
    """One claim/process cycle. Safe if flag off (no-op). Returns processed count."""
    if not is_proactive_enabled():
        return 0
    n = 0
    try:
        proactive_store.recover_expired_leases()
        proactive_store.expire_stale()
        poll_internal_sources()
        poll_connectors()
        try:
            evaluate_world_predictions()
        except Exception:
            emit_proactive("proactive.outcome", status="error", attributes={"reason": "prediction_eval"})
        try:
            evaluate_world_suggestions()
        except Exception:
            emit_proactive("proactive.outcome", status="error", attributes={"reason": "suggestion_eval"})
        try:
            evaluate_world_preparations()
        except Exception:
            emit_proactive("proactive.outcome", status="error", attributes={"reason": "prepare_eval"})
        try:
            evaluate_world_drafts()
        except Exception:
            emit_proactive("proactive.outcome", status="error", attributes={"reason": "draft_eval"})
        try:
            expire_pending_asks()
        except Exception:
            emit_proactive("proactive.outcome", status="error", attributes={"reason": "ask_eval"})
        try:
            evaluate_world_actions()
        except Exception:
            emit_proactive("proactive.outcome", status="error", attributes={"reason": "act_eval"})
        batch = proactive_store.claim_batch(worker_id)
        for item in batch:
            try:
                if int(item.get("attempt_count") or 0) > MAX_ATTEMPTS:
                    proactive_store.retry_or_dead(item["signal_id"], worker_id, "UNKNOWN_ERROR")
                    continue
                _process_item(item, worker_id)
                n += 1
            except Exception:
                emit_proactive("proactive.outcome", status="error", attributes={"reason": "process_error"})
                proactive_store.retry_or_dead(item.get("signal_id") or "", worker_id, "UNKNOWN_ERROR")
    except Exception:
        emit_proactive("proactive.outcome", status="error", attributes={"reason": "cycle_error"})
    return n


def _loop() -> None:
    wid = f"v61-{uuid.uuid4().hex[:8]}"
    while not _stop.is_set():
        try:
            if not is_proactive_enabled():
                break
            process_once(wid)
        except Exception:
            pass
        if not is_proactive_enabled():
            break
        _stop.wait(max(0.2, POLL_INTERVAL_SEC))


def start_proactive_worker() -> bool:
    """Start daemon worker only if flag is on. Never raises."""
    global _thread
    try:
        if not is_proactive_enabled():
            stop_proactive_worker()
            return False
        with _lock:
            if _thread is not None and _thread.is_alive():
                return True
            _stop.clear()
            _thread = threading.Thread(target=_loop, name="doom-v61-proactive", daemon=True)
            _thread.start()
        return True
    except Exception:
        return False


def stop_proactive_worker() -> None:
    global _thread
    _stop.set()
    t = _thread
    if t and t.is_alive():
        t.join(timeout=2.5)
    with _lock:
        if _thread is t:
            _thread = None


def worker_alive() -> bool:
    return (
        is_proactive_enabled()
        and _thread is not None
        and _thread.is_alive()
        and not _stop.is_set()
    )
