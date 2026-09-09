"""V6.2.6 preparation templates. Fixed strings. Not executed."""

from __future__ import annotations

from typing import Any, Dict

PREPARE_TEMPLATES = {
    "prepare_review_outline": (
        "Prepared for review. A remaining-work outline is recorded. Nothing was carried out."
    ),
    "prepare_confirm_prompt": (
        "Prepared for review. A confirmation prompt is recorded. Nothing was carried out."
    ),
    "prepare_schedule_diff": (
        "Prepared for review. A schedule difference is recorded. Approval does not run any action."
    ),
    "prepare_unblock_note": (
        "Prepared for review. An unblock note is recorded. Approval does not run any action."
    ),
    "prepare_review_options": (
        "Prepared for review. Review options are recorded. Approval does not run any action."
    ),
}

ALLOWED_PARAM_KEYS = frozenset({
    "risk_class",
    "horizon_hours",
    "prediction_type",
    "suggestion_type",
    "action_type",
    "claim_code",
})

FORBIDDEN_TEMPLATE_SUBSTRINGS = (
    "send ",
    "create ",
    "update ",
    "delete ",
    "patch ",
    "merge",
    "push ",
    "execute",
)


def render_prepare(template_id: str, params: Dict[str, Any] | None = None) -> str:
    tmpl = PREPARE_TEMPLATES.get(str(template_id or ""))
    if not tmpl:
        return PREPARE_TEMPLATES["prepare_review_outline"]
    return tmpl
