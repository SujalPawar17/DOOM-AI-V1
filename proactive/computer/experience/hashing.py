"""Deterministic canonical hashing for V7.7 experience records."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping

CONTENT_KEYS = (
    "action",
    "action_id",
    "after_observation_hash",
    "approval_state",
    "before_observation_hash",
    "capability",
    "failure_code",
    "outcome",
    "provenance",
    "risk_level",
    "sensitive",
    "sequence_id",
    "sequence_spec_hash",
    "step_id",
    "verification_status",
    "verification_type",
)


def _norm(value: Any) -> Any:
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    return str(value or "")


def canonical_content_payload(fields: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: _norm(fields.get(key, "")) for key in CONTENT_KEYS}


def canonical_dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def content_hash(fields: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_dumps(canonical_content_payload(fields)))


def experience_hash(fields: Mapping[str, Any]) -> str:
    payload = {
        "content_hash": str(fields.get("content_hash") or ""),
        "experience_id": str(fields.get("experience_id") or ""),
        "previous_experience_hash": str(fields.get("previous_experience_hash") or ""),
        "schema_version": str(fields.get("schema_version") or ""),
        "timestamp_unix_ms": int(fields.get("timestamp_unix_ms") or 0),
    }
    return sha256_hex(canonical_dumps(payload))


def is_sha256_hex(value: str) -> bool:
    text = str(value or "")
    if not text:
        return True
    if len(text) != 64:
        return False
    try:
        int(text, 16)
    except ValueError:
        return False
    return True
