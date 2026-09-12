"""Deterministic GoalSpec content hash. Integrity only; not authorization."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

CONTENT_KEYS = (
    "capability_class",
    "computer_session_id",
    "normalized_intent",
    "owner_id",
    "provenance",
    "raw_intent",
    "schema_version",
    "session_id",
)


def canonical_dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def goal_hash(fields: Mapping[str, Any]) -> str:
    body = {key: str(fields.get(key) or "") for key in CONTENT_KEYS}
    return hashlib.sha256(canonical_dumps(body).encode("utf-8")).hexdigest()
