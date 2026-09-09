"""V6.2.5 advisory suggestion templates. Constants only. No source text."""

from __future__ import annotations

from typing import Any, Dict

SUGGEST_TEMPLATES = {
    "suggest_review_work": (
        "A recorded deadline is approaching. Consider reviewing remaining work for that item."
    ),
    "suggest_confirm_open": (
        "An open commitment is past its recorded due time. Consider confirming whether it is still relevant."
    ),
    "suggest_reconcile_time": (
        "A calendar time and a recorded commitment disagree. Consider reconciling the schedule."
    ),
    "suggest_unblock": (
        "A blocked task sits near a recorded deadline. Consider unblocking or replanning that work."
    ),
    "suggest_complete_review": (
        "An open review is older than seven days. Consider completing or declining the review."
    ),
}

ALLOWED_PARAM_KEYS = frozenset({"risk_class", "horizon_hours", "prediction_type", "claim_code"})

FORBIDDEN_TEMPLATE_SUBSTRINGS = (
    "create ",
    "send ",
    "execute",
    "run ",
    "approve",
    "merge",
    "update calendar",
    "open github",
)


def render_suggest(template_id: str, params: Dict[str, Any] | None = None) -> str:
    tmpl = SUGGEST_TEMPLATES.get(str(template_id or ""))
    if not tmpl:
        return SUGGEST_TEMPLATES["suggest_review_work"]
    return tmpl
