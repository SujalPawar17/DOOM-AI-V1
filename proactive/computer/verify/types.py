"""V7.6 verification contracts. Data only. No callbacks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Tuple


class VerificationType(str, Enum):
    OBSERVATION_HASH_MATCH = "OBSERVATION_HASH_MATCH"
    TARGET_EXISTS = "TARGET_EXISTS"
    TARGET_ABSENT = "TARGET_ABSENT"
    TARGET_IDENTITY_MATCH = "TARGET_IDENTITY_MATCH"
    TARGET_STATE_MATCH = "TARGET_STATE_MATCH"
    URL_MATCH = "URL_MATCH"
    ORIGIN_MATCH = "ORIGIN_MATCH"
    FILE_METADATA_MATCH = "FILE_METADATA_MATCH"
    DIRECTORY_ENTRY_MATCH = "DIRECTORY_ENTRY_MATCH"
    TEXT_PRESENT = "TEXT_PRESENT"
    TEXT_ABSENT = "TEXT_ABSENT"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFICATION_TIMEOUT = "VERIFICATION_TIMEOUT"
    VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"
    INVALID_VERIFICATION_SPEC = "INVALID_VERIFICATION_SPEC"
    VERIFICATION_BLOCKED = "VERIFICATION_BLOCKED"
    EMERGENCY_STOP_ACTIVE = "EMERGENCY_STOP_ACTIVE"
    APPROVAL_INVALIDATED = "APPROVAL_INVALIDATED"


MAX_TEXT_NEEDLE = 80

BROWSER_ONLY = frozenset({VerificationType.URL_MATCH, VerificationType.ORIGIN_MATCH})
FS_ONLY = frozenset({VerificationType.FILE_METADATA_MATCH, VerificationType.DIRECTORY_ENTRY_MATCH})


def freeze_expected(raw: Optional[Mapping[str, Any]]) -> Tuple[Tuple[str, str], ...]:
    if not raw:
        return ()
    items = []
    for key in sorted(str(k) for k in raw.keys()):
        val = raw[key]
        if val is None:
            text = ""
        elif isinstance(val, bool):
            text = "true" if val else "false"
        else:
            text = str(val)
        items.append((str(key), text[:65536]))
    return tuple(items)


def expected_map(pairs: Tuple[Tuple[str, str], ...]) -> Dict[str, str]:
    return {k: v for k, v in pairs}


@dataclass(frozen=True)
class VerificationSpec:
    verification_id: str
    capability: str
    verification_type: str
    expected: Tuple[Tuple[str, str], ...] = ()
    timeout_ms: int = 2000
    max_attempts: int = 1
    evidence_mode: str = "structured"
    owner_id: str = ""
    schema_version: str = "v76.1"

    def spec_hash(self) -> str:
        payload = {
            "capability": str(self.capability or ""),
            "evidence_mode": str(self.evidence_mode or ""),
            "expected": [[k, v] for k, v in self.expected],
            "max_attempts": int(self.max_attempts),
            "owner_id": str(self.owner_id or ""),
            "schema_version": str(self.schema_version or ""),
            "timeout_ms": int(self.timeout_ms),
            "verification_id": str(self.verification_id or ""),
            "verification_type": str(self.verification_type or ""),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerificationEvidence:
    source: str = "structured"
    fields: Tuple[Tuple[str, str], ...] = ()
    visual_available: bool = False


@dataclass
class VerificationRequest:
    spec: VerificationSpec
    action_id: str = ""
    approval_state: str = "NONE"
    approved_spec_hash: str = ""
    before_hash: str = ""
    computer_session_id: str = ""


@dataclass
class VerificationResult:
    verification_id: str
    action_id: str
    verification_type: str
    status: VerificationStatus
    before_hash: str = ""
    after_hash: str = ""
    failure_code: str = ""
    duration_ms: int = 0
    evidence: VerificationEvidence = None  # type: ignore[assignment]
    telemetry: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.evidence is None:
            self.evidence = VerificationEvidence()
        if self.telemetry is None:
            self.telemetry = {}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "verification_id": self.verification_id,
            "action_id": self.action_id,
            "verification_type": self.verification_type,
            "status": self.status.value,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "failure_code": self.failure_code,
            "duration_ms": self.duration_ms,
            "evidence": {
                "source": self.evidence.source,
                "fields": [[k, v] for k, v in self.evidence.fields],
                "visual_available": self.evidence.visual_available,
            },
            "telemetry": dict(self.telemetry or {}),
        }
