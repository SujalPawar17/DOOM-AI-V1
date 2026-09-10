"""V6.2.8 draft I/O contract. Presentation only. Not authority."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

OUTPUT_KEYS = (
    "draft_type",
    "title",
    "summary",
    "body",
    "warnings",
    "uncertainties",
    "source_refs",
)

DRAFT_TYPES = frozenset({
    "prepare_review_outline",
    "prepare_confirm_prompt",
    "prepare_schedule_diff",
    "prepare_unblock_note",
    "prepare_review_options",
})

INPUT_KEYS = (
    "preparation_id",
    "preparation_type",
    "action_type",
    "template_id",
    "claim_code",
    "prediction_type",
    "suggestion_type",
    "risk_class",
    "privacy_class",
    "horizon_hours",
    "rule_version",
    "evidence_ids",
    "deterministic_preview",
)

TITLE_MAX = 120
SUMMARY_MAX = 400
BODY_MAX = 2000
LIST_MAX = 5
ITEM_MAX = 200
REFS_MAX = 8
BODY_NEWLINES_MAX = 20


def draft_fingerprint(
    owner_id: str,
    preparation_id: str,
    param_hash: str,
    prompt_version: str,
    draft_type: str,
) -> str:
    raw = (
        f"{owner_id}|{preparation_id}|{param_hash}|{prompt_version}|{draft_type}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def _ids(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for x in raw:
        s = str(x or "").strip()[:64]
        if s and s not in out:
            out.append(s)
        if len(out) >= REFS_MAX:
            break
    return out


def build_input(preparation: Dict[str, Any], evidence_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    params = preparation.get("safe_params") if isinstance(preparation.get("safe_params"), dict) else {}
    eids = _ids(evidence_ids)
    if not eids:
        prov = preparation.get("provenance") if isinstance(preparation.get("provenance"), dict) else {}
        eids = _ids(prov.get("evidence_ids"))
    try:
        hh = int(params.get("horizon_hours") or 0)
    except (TypeError, ValueError):
        hh = 0
    if hh < 0:
        hh = 0
    if hh > 168:
        hh = 168
    from proactive.prepare_templates import render_prepare
    tid = str(preparation.get("template_id") or "")[:64]
    return {
        "preparation_id": str(preparation.get("preparation_id") or "")[:64],
        "preparation_type": str(preparation.get("preparation_type") or "")[:40],
        "action_type": str(preparation.get("action_type") or "NONE")[:40],
        "template_id": tid,
        "claim_code": str(params.get("claim_code") or "")[:40],
        "prediction_type": str(params.get("prediction_type") or "")[:40],
        "suggestion_type": str(params.get("suggestion_type") or "")[:40],
        "risk_class": str(preparation.get("risk_class") or "NONE")[:16],
        "privacy_class": str(preparation.get("privacy_class") or "NORMAL")[:16],
        "horizon_hours": hh,
        "rule_version": str(preparation.get("rule_version") or "v626.1")[:16],
        "evidence_ids": eids,
        "deterministic_preview": render_prepare(tid)[:400],
    }


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str):
        return None
    s = text.strip()
    if not s or s.startswith("```"):
        return None
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj
