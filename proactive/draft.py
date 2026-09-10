"""V6.2.8 worker draft eval. Non-authoritative. Failures leave preparations intact."""

from __future__ import annotations

from typing import Any, Dict

from proactive.config import (
    DRAFT_PROMPT_VERSION,
    LLM_DRAFT_CANDIDATE_CAP,
    OWNER_ID,
    is_llm_draft_enabled,
    is_prediction_enabled,
    is_prepare_enabled,
    is_proactive_enabled,
    is_suggest_enabled,
)
from proactive.draft_contract import build_input, draft_fingerprint
from proactive.draft_policy import generate_bounded_draft
from proactive.draft_prompts import build_system_prompt, build_user_prompt
from proactive.draft_validate import validate_draft
from proactive.otp import emit_proactive
from proactive.store import proactive_store


def flags_draft_on() -> bool:
    return bool(
        is_proactive_enabled()
        and is_prediction_enabled()
        and is_suggest_enabled()
        and is_prepare_enabled()
        and is_llm_draft_enabled()
    )


def _evidence_ids(row: Dict[str, Any]) -> list:
    ids = row.get("evidence_ids")
    if isinstance(ids, list):
        return [str(x)[:64] for x in ids if str(x or "").strip()]
    prov = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
    raw = prov.get("evidence_ids") or []
    if isinstance(raw, list):
        return [str(x)[:64] for x in raw if str(x or "").strip()]
    return []


def evaluate_world_drafts(owner_id: str = OWNER_ID) -> int:
    if not flags_draft_on():
        return 0
    n = 0
    try:
        proactive_store.supersede_stale_drafts(owner_id)
        cands = proactive_store.list_draft_candidates(owner_id, LLM_DRAFT_CANDIDATE_CAP)
        for row in cands:
            pc = str(row.get("privacy_class") or "NORMAL")
            if pc == "SENSITIVE":
                emit_proactive(
                    "proactive.draft.skipped",
                    status="skipped",
                    attributes={"reason": "SENSITIVE", "privacy_class": "SENSITIVE"},
                )
                continue
            facts = build_input(row, _evidence_ids(row))
            tid = str(facts.get("template_id") or "")
            system = build_system_prompt()
            user = build_user_prompt(facts)
            resp, provider, model, pre_err = generate_bounded_draft(system, user, pc)
            if pre_err and pre_err in ("NO_COMPLIANT_PROVIDER", "TIMEOUT", "PROVIDER"):
                emit_proactive(
                    "proactive.draft.skipped",
                    status="skipped",
                    attributes={
                        "preparation_id": str(row.get("preparation_id") or "")[:36],
                        "reject_reason": pre_err,
                        "privacy_class": pc[:16],
                    },
                )
                continue
            text = str(getattr(resp, "text", "") or "") if resp else ""
            calls = list(getattr(resp, "tool_calls", None) or []) if resp else []
            if pre_err == "TOOL_CALLS":
                ok, payload, reason = False, None, "TOOL_CALLS"
            elif pre_err == "EMPTY":
                ok, payload, reason = False, None, "EMPTY"
            else:
                ok, payload, reason = validate_draft(text, tid, facts.get("evidence_ids") or [], calls)
            ph = str(row.get("param_hash") or "")[:64]
            fp = draft_fingerprint(
                owner_id,
                str(row.get("preparation_id") or ""),
                ph,
                DRAFT_PROMPT_VERSION,
                tid,
            )
            status = "ACCEPTED" if ok else "REJECTED"
            rec = {
                "owner_id": owner_id,
                "preparation_id": row.get("preparation_id"),
                "draft_type": tid,
                "structured_output": payload if ok and payload else {},
                "provider": provider[:32],
                "model": model[:80],
                "prompt_version": DRAFT_PROMPT_VERSION,
                "rule_version": str(row.get("rule_version") or "v626.1")[:16],
                "param_hash_at_generation": ph,
                "validation_status": status,
                "reject_reason": "" if ok else (reason or pre_err or "SCHEMA")[:40],
                "privacy_class": pc[:16],
                "fingerprint": fp,
            }
            did = proactive_store.insert_world_draft(rec)
            if not did:
                continue
            n += 1
            emit_proactive(
                "proactive.draft.accepted" if ok else "proactive.draft.rejected",
                status="ok" if ok else "skipped",
                attributes={
                    "draft_id": did[:36],
                    "preparation_id": str(row.get("preparation_id") or "")[:36],
                    "provider": provider[:32],
                    "model": model[:80],
                    "validation_status": status,
                    "reject_reason": rec["reject_reason"],
                    "privacy_class": pc[:16],
                },
            )
            if ok and pc == "NORMAL":
                live = proactive_store.get_current_draft(str(row.get("preparation_id") or ""), owner_id) or {}
                try:
                    from proactive.delivery import deliver_draft
                    deliver_draft(live)
                except Exception:
                    pass
    except Exception:
        emit_proactive("proactive.draft.skipped", status="error", attributes={"reason": "draft_eval"})
    return n
