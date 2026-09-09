"""V6.2.4 deterministic world predictions. No INFORM, no LLM, no memory writes."""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from proactive.config import (
    C_MAX_DEFAULT,
    C_MAX_EMAIL_SINGLE,
    N_CAP,
    OPEN_REVIEW_AGING_FLOOR,
    OWNER_ID,
    PREDICTION_EMIT_FLOOR,
    PREDICTION_EVAL_CAP,
    RELIABILITY_R,
    RULE_VERSION,
    is_prediction_enabled,
    is_proactive_enabled,
)
from proactive.evidence import cite_from_commitment, cite_from_fact, cite_from_task
from proactive.otp import emit_proactive
from proactive.snapshot import invalidate_snapshot
from proactive.store import proactive_store
from proactive.temporal import horizon_bucket_date, horizon_bucket_week, require_source_time, risk_from_horizon

BLOCKED = frozenset({"FAILED", "PAUSED", "WAITING_FOR_APPROVAL", "ERROR"})


def prediction_fingerprint(
    owner_id: str,
    prediction_type: str,
    subject_key: str,
    horizon_bucket: str,
    rule_id: str,
    rule_version: str = RULE_VERSION,
) -> str:
    raw = f"{owner_id}|{prediction_type}|{subject_key}|{horizon_bucket}|{rule_id}|{rule_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def compute_c(evidences: List[Dict[str, Any]]) -> Tuple[float, int, bool]:
    keys = []
    pairs = []
    only_gmail = True
    for e in evidences:
        k = e.get("independence_key") or ""
        if k and k not in keys:
            keys.append(k)
        rel = str(e.get("reliability_class") or "")
        r = float(RELIABILITY_R.get(rel, 0.45))
        s = float(e.get("strength") or 0)
        pairs.append(r * s)
        if rel != "gmail_extract":
            only_gmail = False
    n = min(N_CAP, max(1, len(keys)))
    c_raw = min(C_MAX_DEFAULT, (max(pairs) if pairs else 0.0) + 0.10 * (n - 1))
    if only_gmail and n == 1:
        return min(C_MAX_EMAIL_SINGLE, c_raw), n, True
    return min(C_MAX_DEFAULT, c_raw), n, False


def _privacy(evidences: List[Dict[str, Any]]) -> str:
    order = {"NORMAL": 0, "PRIVATE": 1, "SENSITIVE": 2}
    best = "NORMAL"
    for e in evidences:
        p = str(e.get("privacy_class") or "NORMAL")
        if order.get(p, 0) > order.get(best, 0):
            best = p
    return best


def _same_project(a: Optional[str], b: Optional[str]) -> bool:
    x, y = (a or "") or "", (b or "") or ""
    if not x and not y:
        return True
    return bool(x) and x == y


def evaluate_world_predictions(owner_id: str = OWNER_ID) -> int:
    """Direct worker eval. Never enqueue INFORM. Never raises."""
    if not is_proactive_enabled() or not is_prediction_enabled():
        return 0
    emitted = 0
    try:
        now = time.time()
        commits = proactive_store.list_eval_commitments(owner_id, PREDICTION_EVAL_CAP)
        cals = proactive_store.list_eval_cal_facts(owner_id, PREDICTION_EVAL_CAP)
        reviews = proactive_store.list_eval_review_facts(owner_id, PREDICTION_EVAL_CAP)
        tasks = proactive_store.list_eval_blocked_tasks(PREDICTION_EVAL_CAP)
        cited: Dict[str, Dict[str, Any]] = {}

        def persist_cite(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not row:
                return None
            eid = proactive_store.upsert_world_evidence(row)
            if not eid:
                return None
            out = dict(row)
            out["evidence_id"] = eid
            cited[eid] = out
            return out

        commit_ev = []
        for c in commits:
            ev = persist_cite(cite_from_commitment(c, owner_id))
            if ev:
                ev["_src"] = c
                commit_ev.append(ev)
        cal_ev = []
        for f in cals:
            ev = persist_cite(cite_from_fact(f, owner_id))
            if ev:
                ev["_src"] = f
                cal_ev.append(ev)
        rev_ev = []
        for f in reviews:
            ev = persist_cite(cite_from_fact(f, owner_id))
            if ev:
                ev["_src"] = f
                rev_ev.append(ev)
        task_ev = []
        for t in tasks:
            ev = persist_cite(cite_from_task(t, owner_id))
            if ev:
                ev["_src"] = t
                task_ev.append(ev)

        for ev in commit_ev:
            emitted += _rule_deadline(ev, cal_ev, owner_id, now)
            emitted += _rule_stale(ev, cal_ev, owner_id, now)
            emitted += _rule_conflict(ev, cal_ev, owner_id, now)
        for tev in task_ev:
            emitted += _rule_task_blocked(tev, commit_ev, cal_ev, owner_id, now)
        for rev in rev_ev:
            emitted += _rule_review(rev, owner_id, now)
        for ev in commit_ev:
            src = ev.get("_src") or {}
            if str(src.get("commitment_type") or "") == "REVIEW_REQUIRED":
                emitted += _rule_review(ev, owner_id, now, from_commitment=True)

        proactive_store.expire_world_predictions(owner_id)
        invalidate_snapshot()
    except Exception:
        emit_proactive("proactive.prediction.evaluated", status="error", attributes={"reason": "prediction_eval"})
    return emitted


def _emit(
    owner_id: str,
    ptype: str,
    subject_key: str,
    claim_code: str,
    evidences: List[Dict[str, Any]],
    roles: List[str],
    horizon_end: Optional[float],
    now: float,
    stale: bool = False,
    conflict: bool = False,
    floor: Optional[float] = None,
    bucket: Optional[str] = None,
) -> int:
    if any(str(e.get("privacy_class") or "") == "SENSITIVE" for e in evidences):
        emit_proactive(
            "proactive.prediction.evaluated",
            status="skipped",
            attributes={"rule_id": ptype, "abstain_reason": "PRIVACY_BLOCK"},
        )
        return 0
    c, n, email_single = compute_c(evidences)
    use_floor = float(floor if floor is not None else PREDICTION_EMIT_FLOOR)
    if email_single and ptype != "OPEN_REVIEW_AGING":
        emit_proactive(
            "proactive.prediction.evaluated",
            status="skipped",
            attributes={"rule_id": ptype, "abstain_reason": "LOW_CONFIDENCE", "evidence_n": n},
        )
        return 0
    if c < use_floor:
        emit_proactive(
            "proactive.prediction.evaluated",
            status="skipped",
            attributes={"rule_id": ptype, "abstain_reason": "LOW_CONFIDENCE", "evidence_n": n},
        )
        return 0
    hz = float(horizon_end) if horizon_end else now
    bkt = bucket or horizon_bucket_date(hz)
    fp = prediction_fingerprint(owner_id, ptype, subject_key, bkt, ptype, RULE_VERSION)
    k = risk_from_horizon(horizon_end, now, stale=stale, conflict=conflict)
    priv = _privacy(evidences)
    projects = {str(e.get("project_id") or "") for e in evidences}
    if len(projects) > 1:
        emit_proactive(
            "proactive.prediction.evaluated",
            status="skipped",
            attributes={"rule_id": ptype, "abstain_reason": "CONTEXT_AMBIGUOUS"},
        )
        return 0
    pid = next(iter(projects)) or None
    eids = [str(e["evidence_id"]) for e in evidences if e.get("evidence_id")]
    pred = {
        "prediction_id": str(uuid.uuid4()),
        "owner_id": owner_id,
        "project_id": pid,
        "prediction_type": ptype,
        "subject_key": subject_key[:160],
        "claim_code": claim_code,
        "horizon_start": now,
        "horizon_end": hz,
        "confidence": round(c, 4),
        "risk_class": k,
        "privacy_class": priv,
        "fingerprint": fp,
        "rule_id": ptype,
        "rule_version": RULE_VERSION,
        "evaluated_at": now,
        "valid_until": hz + 86400,
        "provenance": {"evidence_ids": eids[:8]},
    }
    links = []
    for e, role in zip(evidences, roles):
        if e.get("evidence_id"):
            links.append({"evidence_id": e["evidence_id"], "role": role})
    got = proactive_store.upsert_world_prediction(pred, links)
    emit_proactive(
        "proactive.prediction.evaluated",
        attributes={
            "prediction_id": str(got or pred["prediction_id"])[:36],
            "rule_id": ptype,
            "evidence_n": n,
            "confidence_bucket": int(c * 10),
            "risk_class": k,
        },
    )
    return 1 if got else 0


def _rule_deadline(ev: Dict[str, Any], cal_ev: List[Dict[str, Any]], owner_id: str, now: float) -> int:
    src = ev.get("_src") or {}
    due = src.get("due_at")
    if not require_source_time(due):
        emit_proactive(
            "proactive.prediction.evaluated",
            status="skipped",
            attributes={"rule_id": "DEADLINE_HORIZON", "abstain_reason": "MISSING_SOURCE_TIME"},
        )
        return 0
    due_f = float(due)
    if not (now < due_f <= now + 72 * 3600):
        return 0
    extras = []
    for ce in cal_ev:
        f = ce.get("_src") or {}
        start = f.get("occurred_at") or f.get("start_ts")
        if start is None:
            continue
        if abs(float(start) - due_f) <= 2 * 3600 and _same_project(ev.get("project_id"), ce.get("project_id")):
            extras.append(ce)
    evs = [ev] + extras[:2]
    return _emit(
        owner_id, "DEADLINE_HORIZON", f"commitment:{src.get('commitment_id')}", "DUE_72H",
        evs, ["SUPPORTING"] * len(evs), due_f, now,
    )


def _rule_stale(ev: Dict[str, Any], cal_ev: List[Dict[str, Any]], owner_id: str, now: float) -> int:
    src = ev.get("_src") or {}
    due = src.get("due_at")
    if not require_source_time(due):
        return 0
    due_f = float(due)
    if due_f >= now:
        return 0
    extras = [ce for ce in cal_ev if _same_project(ev.get("project_id"), ce.get("project_id"))][:1]
    return _emit(
        owner_id, "STALE_OPEN_COMMITMENT", f"commitment:{src.get('commitment_id')}", "STALE_OPEN",
        [ev] + extras, ["SUPPORTING"] * (1 + len(extras)), due_f, now, stale=True,
    )


def _rule_conflict(ev: Dict[str, Any], cal_ev: List[Dict[str, Any]], owner_id: str, now: float) -> int:
    src = ev.get("_src") or {}
    due = src.get("due_at")
    if not require_source_time(due):
        return 0
    due_f = float(due)
    for ce in cal_ev:
        f = ce.get("_src") or {}
        start = f.get("occurred_at") or f.get("start_ts")
        if start is None or not require_source_time(start):
            continue
        if not _same_project(ev.get("project_id"), ce.get("project_id")):
            continue
        if abs(float(start) - due_f) <= 2 * 3600:
            continue
        if abs(float(start) - due_f) > 14 * 86400:
            continue
        return _emit(
            owner_id, "CAL_VS_COMMITMENT_CONFLICT",
            f"commitment:{src.get('commitment_id')}|cal:{f.get('fact_id')}",
            "CAL_MISMATCH",
            [ev, ce], ["SUPPORTING", "CONFLICTING"], max(due_f, float(start)), now, conflict=True,
        )
    return 0


def _rule_task_blocked(
    tev: Dict[str, Any], commit_ev: List[Dict[str, Any]], cal_ev: List[Dict[str, Any]], owner_id: str, now: float
) -> int:
    tsrc = tev.get("_src") or {}
    st = str(tsrc.get("status") or "").upper()
    if st not in BLOCKED:
        return 0
    partners = []
    due = None
    task_pid = str(tev.get("project_id") or "").strip()
    if not task_pid:
        return 0
    for ev in commit_ev:
        src = ev.get("_src") or {}
        d = src.get("due_at")
        if d is None or not (now < float(d) <= now + 72 * 3600):
            continue
        if _same_project(task_pid, ev.get("project_id")):
            partners.append(ev)
            due = float(d)
            break
    if not partners:
        for ce in cal_ev:
            f = ce.get("_src") or {}
            start = f.get("occurred_at") or f.get("start_ts")
            if start is None or not (now < float(start) <= now + 72 * 3600):
                continue
            if _same_project(task_pid, ce.get("project_id")):
                partners.append(ce)
                due = float(start)
                break
    if not partners:
        return 0
    return _emit(
        owner_id, "TASK_BLOCKED_NEAR_DEADLINE", f"task:{tsrc.get('task_id')}", "TASK_BLOCKED_DUE",
        [tev] + partners, ["SUPPORTING"] * (1 + len(partners)), due, now,
    )


def _rule_review(ev: Dict[str, Any], owner_id: str, now: float, from_commitment: bool = False) -> int:
    src = ev.get("_src") or {}
    if from_commitment and str(src.get("commitment_type") or "") != "REVIEW_REQUIRED":
        return 0
    if not from_commitment and str(src.get("fact_kind") or "") not in ("GH_REVIEW", "GH_PR"):
        return 0
    occurred = src.get("occurred_at") or src.get("source_ts") or src.get("start_ts")
    if not require_source_time(occurred):
        return 0
    age = now - float(occurred)
    if age < 7 * 86400:
        return 0
    subj = f"review:{src.get('fact_id') or src.get('commitment_id')}"
    return _emit(
        owner_id, "OPEN_REVIEW_AGING", subj, "REVIEW_AGING",
        [ev], ["SUPPORTING"], now, now,
        floor=OPEN_REVIEW_AGING_FLOOR,
        bucket=horizon_bucket_week(float(occurred)),
    )
