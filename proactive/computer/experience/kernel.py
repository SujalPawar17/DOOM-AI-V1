"""V7.7 experience recording kernel. Does not execute actions or learn."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.cost_guard import ResourceRequest, ResourceType, cost_guard
from proactive.computer.experience.hashing import content_hash, experience_hash, is_sha256_hex
from proactive.computer.experience.store import (
    find_by_content_hash,
    last_hash,
    list_experiences,
    put_experience,
    reset_experience_store_for_tests,
)
from proactive.computer.experience.types import (
    ExperienceDraft,
    ExperienceOutcome,
    ExperienceProvenance,
    ExperienceRecord,
    ExperienceStatus,
)
from proactive.computer.policy import EXPERIENCE_SCHEMA_VERSION, experiences_allowed
from proactive.computer.sequence.registry import step_risk
from proactive.config import OWNER_ID

_BLOCKED = frozenset({
    "POLICY_BLOCKED", "RISK_BLOCKED", "APPROVAL_REQUIRED", "APPROVAL_DENIED",
    "APPROVAL_INVALIDATED", "PATH_NOT_ALLOWED", "PATH_DENIED", "PATH_ESCAPE_BLOCKED",
    "NAVIGATION_BLOCKED", "SENSITIVE_FILE_BLOCKED", "VERIFICATION_BLOCKED",
    "SEQUENCES_DISABLED", "COST_GUARD_BLOCKED",
})


def _cost_ok() -> bool:
    d = cost_guard.authorize(ResourceRequest(
        resource_type=ResourceType.OTHER,
        provider="local_experience",
        capability="computer_experience",
    ))
    return bool(d.is_allow)


def map_outcome(status: str, verification_status: str = "") -> ExperienceOutcome:
    st = str(status or "")
    vs = str(verification_status or "")
    if vs in ("NOT_VERIFIED", "VERIFICATION_FAILED") or st in ("NOT_VERIFIED", "VERIFICATION_FAILED"):
        return ExperienceOutcome.NOT_VERIFIED
    if st in ("EMERGENCY_STOP_ACTIVE",) or vs == "EMERGENCY_STOP_ACTIVE":
        return ExperienceOutcome.EMERGENCY_STOPPED
    if st in ("PRECONDITION_FAILED",):
        return ExperienceOutcome.PRECONDITION_FAILED
    if st in ("SEQUENCE_TIMEOUT", "VERIFICATION_TIMEOUT") or "TIMEOUT" in st:
        return ExperienceOutcome.TIMEOUT
    if st in ("SEQUENCE_ABORTED",):
        return ExperienceOutcome.ABORTED
    if st in _BLOCKED:
        return ExperienceOutcome.BLOCKED
    if st in ("SUCCESS", "VERIFIED"):
        return ExperienceOutcome.SUCCESS
    return ExperienceOutcome.FAILED


def _safe_hash(value: str) -> str:
    text = str(value or "")
    if not is_sha256_hex(text):
        return ""
    return text


def record_experience(draft: ExperienceDraft) -> ExperienceRecord:
    if not experiences_allowed():
        return _rejected(draft, ExperienceStatus.POLICY_BLOCKED.value)
    if not _cost_ok():
        return _rejected(draft, ExperienceStatus.COST_GUARD_BLOCKED.value)
    before = _safe_hash(draft.before_observation_hash)
    after = _safe_hash(draft.after_observation_hash)
    spec_hash = _safe_hash(draft.sequence_spec_hash)
    outcome = map_outcome(draft.outcome_status, draft.verification_status)
    eid = str(draft.experience_id or uuid.uuid4())
    ts = int(draft.timestamp_unix_ms or int(time.time() * 1000))
    stream = str(draft.stream_id or draft.sequence_id or f"direct:{draft.owner_id or OWNER_ID}:{draft.capability}")
    prev = last_hash(stream)
    content_fields = {
        "action": str(draft.action or "")[:64],
        "action_id": str(draft.action_id or draft.step_id or "")[:64],
        "after_observation_hash": after,
        "approval_state": str(draft.approval_state or "NONE")[:32],
        "before_observation_hash": before,
        "capability": str(draft.capability or "")[:32],
        "failure_code": str(draft.failure_code or "")[:80],
        "outcome": outcome.value,
        "provenance": draft.provenance.value,
        "risk_level": str(draft.risk_level or "")[:16],
        "sensitive": bool(draft.sensitive),
        "sequence_id": str(draft.sequence_id or "")[:64],
        "sequence_spec_hash": spec_hash,
        "step_id": str(draft.step_id or "")[:64],
        "verification_status": str(draft.verification_status or "")[:64],
        "verification_type": str(draft.verification_type or "")[:64],
    }
    ch = content_hash(content_fields)
    existing = find_by_content_hash(ch)
    inst = {
        "content_hash": ch,
        "experience_id": eid,
        "previous_experience_hash": prev,
        "schema_version": EXPERIENCE_SCHEMA_VERSION,
        "timestamp_unix_ms": ts,
    }
    rec = ExperienceRecord(
        experience_id=eid,
        schema_version=EXPERIENCE_SCHEMA_VERSION,
        timestamp_unix_ms=ts,
        capability=content_fields["capability"],
        action=content_fields["action"],
        sequence_id=content_fields["sequence_id"],
        step_id=content_fields["step_id"],
        action_id=content_fields["action_id"],
        sequence_spec_hash=spec_hash,
        before_observation_hash=before,
        after_observation_hash=after,
        verification_status=content_fields["verification_status"],
        verification_type=content_fields["verification_type"],
        outcome=outcome,
        risk_level=content_fields["risk_level"],
        approval_state=content_fields["approval_state"],
        duration_ms=int(draft.duration_ms or 0),
        failure_code=content_fields["failure_code"],
        provenance=draft.provenance,
        sensitive=bool(draft.sensitive),
        previous_experience_hash=prev,
        content_hash=ch,
        experience_hash=experience_hash(inst),
        duplicate_of=existing.experience_id if existing and existing.experience_id != eid else "",
    )
    put_experience(rec, stream)
    return rec


def _rejected(draft: ExperienceDraft, reason: str) -> ExperienceRecord:
    eid = str(draft.experience_id or "rejected")
    return ExperienceRecord(
        experience_id=eid,
        schema_version=EXPERIENCE_SCHEMA_VERSION,
        timestamp_unix_ms=int(draft.timestamp_unix_ms or 0),
        capability=str(draft.capability or ""),
        action=str(draft.action or ""),
        sequence_id=str(draft.sequence_id or ""),
        step_id=str(draft.step_id or ""),
        action_id=str(draft.action_id or ""),
        sequence_spec_hash="",
        before_observation_hash="",
        after_observation_hash="",
        verification_status="",
        verification_type="",
        outcome=ExperienceOutcome.BLOCKED,
        risk_level=str(draft.risk_level or ""),
        approval_state=str(draft.approval_state or "NONE"),
        duration_ms=0,
        failure_code=reason,
        provenance=draft.provenance,
        sensitive=False,
        previous_experience_hash="",
        content_hash="",
        experience_hash="",
        duplicate_of="",
    )


def record_from_kernel_result(
    *,
    capability: str,
    action: str,
    result: Any,
    provenance: ExperienceProvenance = ExperienceProvenance.DIRECT_ACTION,
    risk_level: str = "",
    approval_state: str = "NONE",
    sequence_id: str = "",
    step_id: str = "",
    sequence_spec_hash: str = "",
    sensitive: bool = False,
    owner_id: str = "",
    duration_ms: int = 0,
) -> ExperienceRecord:
    status = getattr(getattr(result, "status", None), "value", None) or str(getattr(result, "status", "") or "")
    vstatus = ""
    vtype = ""
    if capability == "verify":
        vstatus = status
        vtype = action
        provenance = ExperienceProvenance.VERIFICATION_RESULT
    return record_experience(ExperienceDraft(
        capability=capability,
        action=action,
        outcome_status=status,
        owner_id=owner_id or OWNER_ID,
        sequence_id=sequence_id,
        step_id=step_id,
        action_id=str(getattr(result, "action_id", "") or step_id),
        sequence_spec_hash=sequence_spec_hash,
        before_observation_hash=str(getattr(result, "before_observation_hash", "") or ""),
        after_observation_hash=str(getattr(result, "after_observation_hash", "") or ""),
        verification_status=vstatus,
        verification_type=vtype,
        risk_level=risk_level,
        approval_state=approval_state,
        duration_ms=duration_ms,
        failure_code=str(getattr(result, "error_code", "") or getattr(result, "failure_code", "") or ""),
        provenance=provenance,
        sensitive=sensitive,
        stream_id=sequence_id,
    ))


def record_sequence_experiences(
    *,
    sequence_id: str,
    sequence_spec_hash: str,
    sequence_status: str,
    approval_state: str,
    aggregate_risk: str,
    telemetry: Iterable[Dict[str, Any]],
    owner_id: str = "",
) -> Tuple[ExperienceRecord, ...]:
    rows = list(telemetry or [])
    out: List[ExperienceRecord] = []
    for row in rows:
        cap = str(row.get("capability") or "")
        action = str(row.get("action") or "")
        status = str(row.get("status") or "")
        vstatus = status if cap == "verify" else ""
        vtype = action if cap == "verify" else ""
        prov = ExperienceProvenance.VERIFICATION_RESULT if cap == "verify" else ExperienceProvenance.SEQUENCE_STEP
        rec = record_experience(ExperienceDraft(
            capability=cap,
            action=action,
            outcome_status=status,
            owner_id=owner_id or OWNER_ID,
            sequence_id=sequence_id,
            step_id=str(row.get("step_id") or ""),
            action_id=str(row.get("step_id") or ""),
            sequence_spec_hash=sequence_spec_hash,
            before_observation_hash=str(row.get("before_hash") or ""),
            after_observation_hash=str(row.get("after_hash") or ""),
            verification_status=vstatus,
            verification_type=vtype,
            risk_level=step_risk(cap, action) if cap and action else aggregate_risk,
            approval_state=approval_state,
            duration_ms=int(row.get("duration_ms") or 0),
            failure_code=str(row.get("failure_code") or ""),
            provenance=prov,
            sensitive=False,
            stream_id=sequence_id,
        ))
        out.append(rec)
    summary = record_experience(ExperienceDraft(
        capability="sequence",
        action="SUMMARY",
        outcome_status=sequence_status,
        owner_id=owner_id or OWNER_ID,
        sequence_id=sequence_id,
        step_id="",
        action_id=sequence_id,
        sequence_spec_hash=sequence_spec_hash,
        before_observation_hash="",
        after_observation_hash="",
        verification_status="",
        verification_type="",
        risk_level=aggregate_risk,
        approval_state=approval_state,
        duration_ms=0,
        failure_code="" if sequence_status in ("SUCCESS",) else sequence_status,
        provenance=ExperienceProvenance.SEQUENCE_SUMMARY,
        stream_id=sequence_id,
    ))
    out.append(summary)
    return tuple(out)


__all__ = [
    "ExperienceDraft",
    "ExperienceRecord",
    "list_experiences",
    "map_outcome",
    "record_experience",
    "record_from_kernel_result",
    "record_sequence_experiences",
    "reset_experience_store_for_tests",
]
