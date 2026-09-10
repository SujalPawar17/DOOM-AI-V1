"""ACT-0 INTERNAL_LEDGER_NOTE writer. PostgreSQL only."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, Tuple

from proactive.act_policy import NOTE_TEMPLATE_ID
from proactive.store import proactive_store

ALLOWED_EXEC = frozenset({"claim_code", "note_template_id"})


def write_internal_ledger(action: Dict[str, Any]) -> Tuple[str, str, str]:
    params = action.get("exec_params") if isinstance(action.get("exec_params"), dict) else {}
    if set(params.keys()) - ALLOWED_EXEC:
        raise ValueError("exec_params")
    claim = str(params.get("claim_code") or "")[:40]
    tid = str(params.get("note_template_id") or "")
    if tid != NOTE_TEMPLATE_ID or not claim:
        raise ValueError("payload")
    body = json.dumps({"claim_code": claim, "note_template_id": tid}, sort_keys=True, separators=(",", ":"))
    ch = hashlib.sha256(body.encode("utf-8")).hexdigest()
    rid = str(uuid.uuid4())[:64]
    ok = proactive_store.insert_action_receipt(
        {
            "receipt_id": rid,
            "action_id": action.get("action_id"),
            "owner_id": action.get("owner_id"),
            "content_hash": ch,
            "note_template_id": tid,
            "claim_code": claim,
        }
    )
    if not ok:
        raise RuntimeError("receipt_persist")
    return rid, ch, ""
