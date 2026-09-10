"""Canonical SHA256 for ComputerObservation authoritative fields."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping

AUTHORITATIVE_KEYS = (
    "owner_id",
    "capability_id",
    "hwnd",
    "pid",
    "exe_path_norm",
    "publisher_norm",
    "window_class",
    "uia_runtime_id",
    "uia_automation_id",
    "uia_control_type",
    "uia_tree_digest",
    "monitor_id",
    "privacy_class",
    "schema_version",
)


def authoritative_payload(fields: Mapping[str, Any]) -> Dict[str, Any]:
    hwnd = int(fields.get("hwnd") or 0)
    pid = int(fields.get("pid") or 0)
    monitor_id = int(fields.get("monitor_id") or 0)
    return {
        "owner_id": str(fields.get("owner_id") or ""),
        "capability_id": str(fields.get("capability_id") or ""),
        "hwnd": hwnd,
        "pid": pid,
        "exe_path_norm": str(fields.get("exe_path_norm") or ""),
        "publisher_norm": str(fields.get("publisher_norm") or ""),
        "window_class": str(fields.get("window_class") or "")[:64],
        "uia_runtime_id": str(fields.get("uia_runtime_id") or ""),
        "uia_automation_id": str(fields.get("uia_automation_id") or "")[:128],
        "uia_control_type": str(fields.get("uia_control_type") or ""),
        "uia_tree_digest": str(fields.get("uia_tree_digest") or ""),
        "monitor_id": monitor_id,
        "privacy_class": str(fields.get("privacy_class") or ""),
        "schema_version": str(fields.get("schema_version") or ""),
    }


def canonical_json(fields: Mapping[str, Any]) -> str:
    payload = authoritative_payload(fields)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def observation_hash(fields: Mapping[str, Any]) -> str:
    raw = canonical_json(fields)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()
