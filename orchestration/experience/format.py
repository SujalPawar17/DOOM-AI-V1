"""V8.28 Goal Experience user-facing formatters. Deterministic. No internals."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from orchestration.experience.policy import is_sensitive_experience_content
from orchestration.experience.types import GoalExperience, Outcome

MAX_UX_RESULTS = 4
MAX_UX_CHARS = 2048
MAX_STEP_SHOW = 3
MAX_BLOCKER_SHOW = 2


def _outcome_label(outcome: Any) -> str:
    try:
        if isinstance(outcome, Outcome):
            return outcome.value
        return str(outcome or "").strip().upper() or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _safe_title(exp: GoalExperience) -> str:
    title = " ".join(str(getattr(exp, "title", "") or "").split())
    if not title or is_sensitive_experience_content(title):
        return "(goal experience)"
    return title[:160]


def format_experience_item(exp: GoalExperience, *, index: int = 0) -> str:
    """One bounded display block. Never includes owner/ids/versions."""
    if exp is None:
        return ""
    try:
        title = _safe_title(exp)
        outcome = _outcome_label(exp.outcome)
        lines = [f"{index}. {title} — {outcome}" if index else f"{title} — {outcome}"]

        steps: list = []
        for step in exp.step_summary or ():
            s = " ".join(str(step or "").split())
            if not s or is_sensitive_experience_content(s):
                continue
            steps.append(s[:80])
            if len(steps) >= MAX_STEP_SHOW:
                break
        if steps:
            lines.append("   Steps: " + "; ".join(steps))

        blockers: list = []
        for blocker in exp.blockers or ():
            b = " ".join(str(blocker or "").split())
            if not b or is_sensitive_experience_content(b):
                continue
            blockers.append(b[:120])
            if len(blockers) >= MAX_BLOCKER_SHOW:
                break
        if blockers:
            lines.append("   Blockers: " + "; ".join(blockers))

        note = " ".join(str(exp.user_note or "").split())
        if note and not is_sensitive_experience_content(note):
            lines.append(f"   Note: {note[:160]}")

        return "\n".join(lines)[:512]
    except Exception:
        return ""


def format_experience_list(
    experiences: Sequence[GoalExperience],
    *,
    title: str = "Stored goal experiences",
) -> str:
    items = []
    for exp in list(experiences or []):
        if exp is None:
            continue
        block = format_experience_item(exp, index=len(items) + 1)
        if not block:
            continue
        items.append(block)
        if len(items) >= MAX_UX_RESULTS:
            break
    if not items:
        return format_none_found()
    head = (title or "Stored goal experiences").strip()
    body = "\n\n".join(items)
    return f"{head} (up to {MAX_UX_RESULTS}):\n\n{body}".strip()[:MAX_UX_CHARS]


def format_none_found(topic: str = "") -> str:
    tip = f' matching "{topic[:60]}"' if topic else ""
    return f"I don't have a stored goal experience{tip}."[:MAX_UX_CHARS]


def format_why(exp: Optional[GoalExperience], *, topic: str = "") -> str:
    if exp is None:
        return format_none_found(topic)
    try:
        title = _safe_title(exp)
        outcome = _outcome_label(exp.outcome)
        return (
            f'I remember "{title}" because it was captured after a finished goal '
            f"reached a terminal outcome ({outcome}). "
            f"It is kept only as bounded historical goal context — "
            f"not as an active goal, preference, or profile fact."
        )[:MAX_UX_CHARS]
    except Exception:
        return format_none_found(topic)


def format_forget_confirm(*, title: str, outcome: str = "") -> str:
    shown = " ".join(str(title or "").split())[:160] or "(goal experience)"
    if is_sensitive_experience_content(shown):
        shown = "(goal experience)"
    oc = str(outcome or "").strip().upper()
    detail = f"{shown} — {oc}" if oc else shown
    return (
        f"I can forget this stored goal experience:\n"
        f"{detail}.\n"
        f"Confirm? Yes/No"
    )[:MAX_UX_CHARS]


def format_forget_success(*, title: str = "") -> str:
    shown = " ".join(str(title or "").split())[:160]
    if shown and not is_sensitive_experience_content(shown):
        return f'Forgot the stored goal experience: "{shown}".'[:MAX_UX_CHARS]
    return "Forgot that stored goal experience."[:MAX_UX_CHARS]


def format_ambiguous_forget(experiences: Sequence[GoalExperience]) -> str:
    items = []
    for exp in list(experiences or [])[:MAX_UX_RESULTS]:
        title = _safe_title(exp)
        outcome = _outcome_label(getattr(exp, "outcome", ""))
        items.append(f"- {title} — {outcome}")
    listing = "\n".join(items) if items else "- (several matching experiences)"
    return (
        "Several goal experiences could match. "
        "Which one should I forget?\n"
        f"{listing}"
    )[:MAX_UX_CHARS]


def format_cancelled() -> str:
    return "Okay — cancelled. No goal experience was forgotten."


def format_need_yes_no() -> str:
    return "Please reply YES to confirm or NO to cancel."


def format_expired() -> str:
    return "That confirmation expired. Please ask again."


def format_conflict() -> str:
    return "That goal experience changed since confirmation. Please try again."


def format_unavailable() -> str:
    return "Goal experiences are unavailable right now."


def format_rejected() -> str:
    return "I couldn't update that goal experience."


def format_already_forgotten() -> str:
    return "That goal experience is already forgotten or unavailable."
