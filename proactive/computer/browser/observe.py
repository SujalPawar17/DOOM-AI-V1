"""Bounded browser observation hash. Title is advisory and not hashed."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping

from proactive.computer.browser.types import BrowserObservation
from proactive.computer.policy import BROWSER_SCHEMA_VERSION, browser_max_nodes


def canonical_json(fields: Mapping[str, Any]) -> str:
    return json.dumps(dict(fields), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def observation_hash(fields: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(fields).encode("ascii")).hexdigest()


def bound_observation(obs: BrowserObservation) -> BrowserObservation:
    cap = int(browser_max_nodes())
    obs.elements = list(obs.elements or [])[:cap]
    obs.node_count = len(obs.elements)
    rows = []
    for el in obs.elements:
        rows.append({
            "e": str(el.element_id or "")[:128],
            "r": str(el.role or "")[:64],
            "t": str(el.test_id or "")[:128],
        })
    obs.page_identity = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()
    obs.schema_version = BROWSER_SCHEMA_VERSION
    obs.observation_hash = observation_hash(obs.as_authoritative())
    return obs
