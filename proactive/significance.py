"""Deterministic significance. Hard gates. new ≠ important. No LLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from proactive.config import CONFIDENCE_FLOOR, SIGNIFICANCE_INFORM_FLOOR
from proactive.snapshot import WorldSnapshot


@dataclass
class SignificanceResult:
    score: float
    confidence: float
    urgency: str
    template_id: str
    insight_type: str
    candidate_inform: bool
    reason: str


def evaluate_significance(signal: Dict[str, Any], snapshot: WorldSnapshot) -> SignificanceResult:
    pc = str(signal.get("privacy_class") or "NORMAL").upper()
    if pc == "SENSITIVE":
        return SignificanceResult(0.0, 0.0, "none", "", "", False, "privacy_sensitive")
    if pc == "PRIVATE":
        return SignificanceResult(0.0, 1.0, "none", "", "", False, "privacy_private")

    st = signal.get("signal_type")
    payload = signal.get("payload") or {}
    score = 0.20
    confidence = 0.70
    urgency = "low"
    template_id = "generic_event"
    insight_type = st or "UNKNOWN"
    reason = "baseline"

    if st == "HOST_TELEMETRY":
        disk = float(payload.get("disk_percent") or snapshot.host.get("disk_percent") or 0)
        if disk >= 90:
            score, urgency, template_id, reason = 0.85, "high", "host_disk_critical", "disk"
        elif disk >= 80:
            score, urgency, template_id, reason = 0.70, "medium", "host_disk_high", "disk"
        else:
            score, reason = 0.15, "host_ok"
    elif st == "PROVIDER_CIRCUIT":
        if payload.get("state") == "OPEN" or (snapshot.circuit.get("open_count") or 0) > 0:
            score, urgency, template_id, reason = 0.78, "medium", "provider_circuit_open", "circuit"
        else:
            score, reason = 0.10, "circuit_ok"
    elif st == "TASK_STATUS":
        status = str(payload.get("status") or "").upper()
        if status in ("FAILED", "ERROR"):
            score, urgency, template_id, reason = 0.80, "high", "task_failed", "task"
        elif status in ("PAUSED", "WAITING_FOR_APPROVAL"):
            score, urgency, template_id, reason = 0.68, "medium", "task_blocked", "task"
        else:
            score, reason = 0.25, "task_other"
    elif st == "STRATEGY_FAILURE":
        score, urgency, template_id, reason = 0.72, "medium", "strategy_failure", "strategy"
    elif st == "INACTIVITY":
        days = int(payload.get("idle_days") or 0)
        if days >= 7:
            score, urgency, template_id, reason = 0.74, "medium", "inactivity_7d", "idle"
        else:
            score, reason = 0.20, "idle_short"
    elif st == "EXPERIENCE_CREATED":
        outcome = str(payload.get("outcome_status") or "").upper()
        if outcome == "FAILURE":
            score, urgency, template_id, reason = 0.76, "medium", "experience_failure", "exp"
        else:
            score, reason = 0.22, "exp_ok"
    elif st == "MEMORY_LIFECYCLE":
        score, reason = 0.18, "lifecycle_novelty"
    elif st == "PROJECT_CHANGE":
        score, reason = 0.30, "project_change"
    elif st == "REQUEST_COMPLETED":
        score, reason = 0.12, "request_noise"
    elif st == "CALENDAR_EVENT":
        score, reason = 0.22, "calendar_novelty"
    elif st == "GITHUB_ISSUE":
        score, reason = 0.25, "github_issue"
    elif st == "GITHUB_NOTIFICATION":
        score, reason = 0.20, "github_notification"
    elif st == "GITHUB_REVIEW_REQUEST":
        score, reason = 0.55, "github_review"

    if not snapshot.is_fresh():
        return SignificanceResult(0.0, confidence, "none", "", insight_type, False, "stale_snapshot")

    candidate = (
        score >= SIGNIFICANCE_INFORM_FLOOR
        and confidence >= CONFIDENCE_FLOOR
        and pc == "NORMAL"
    )
    return SignificanceResult(score, confidence, urgency, template_id, insight_type, candidate, reason)
