"""V8.27 User Model user-facing formatters. Deterministic. No internals."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from orchestration.user_model.types import (
    MAX_TRANSPARENCY_RESULTS,
    Category,
    ProfileEntry,
)


def _label_key(key: str) -> str:
    text = str(key or "").replace("_", " ").strip()
    return text or "item"


def _label_category(category: Any) -> str:
    if isinstance(category, Category):
        return category.value.replace("_", " ")
    return str(category or "profile").replace("_", " ")


def format_assert_confirm(
    *,
    category: Any,
    key: str,
    value: str,
    old_value: str = "",
) -> str:
    cat = _label_category(category).capitalize()
    label = _label_key(key)
    if old_value and old_value != value:
        return (
            f"I can update your profile:\n"
            f"{cat} — {label}: {old_value} → {value}.\n"
            f"Confirm? Yes/No"
        )[:2048]
    return (
        f"I can save this to your profile:\n"
        f"{cat} — {label}: {value}.\n"
        f"Confirm? Yes/No"
    )[:2048]


def format_forget_confirm(*, category: Any, key: str, value: str) -> str:
    cat = _label_category(category).capitalize()
    label = _label_key(key)
    shown = value or "(saved entry)"
    return (
        f"I can remove this from your profile:\n"
        f"{cat} — {label}: {shown}.\n"
        f"Confirm? Yes/No"
    )[:2048]


def format_forget_category_confirm(*, category: Any) -> str:
    cat = _label_category(category)
    return (
        f"I can clear all active {cat} entries from your profile. "
        f"Confirm? Yes/No"
    )[:2048]


def format_clear_confirm(*, preferences_only: bool = False) -> str:
    if preferences_only:
        return (
            "I can clear all active preferences from your profile. "
            "Confirm? Yes/No"
        )[:2048]
    return (
        "I can clear what I know about you in your profile. "
        "This does not delete other memory systems. Confirm? Yes/No"
    )[:2048]


def format_upsert_success(*, category: Any, key: str, value: str) -> str:
    cat = _label_category(category).capitalize()
    return f"Saved to your profile: {cat} — {_label_key(key)}: {value}."[:2048]


def format_forget_success(*, category: Any, key: str) -> str:
    return (
        f"Removed from your profile: {_label_category(category)} — {_label_key(key)}."
    )[:2048]


def format_clear_success(*, preferences_only: bool = False) -> str:
    if preferences_only:
        return "Cleared your active preferences from the profile."[:2048]
    return "Cleared your active profile entries."[:2048]


def format_cancelled() -> str:
    return "Okay — cancelled. No profile changes were made."


def format_need_yes_no() -> str:
    return "Please reply YES to confirm or NO to cancel."


def format_expired() -> str:
    return "That confirmation expired. Please ask again."


def format_conflict() -> str:
    return "Your profile changed since confirmation. Please try again."


def format_unavailable() -> str:
    return "Your profile is unavailable right now."


def format_rejected() -> str:
    return "I couldn't update your profile with that."


def format_sensitive_rejected() -> str:
    return (
        "I cannot store passwords, API keys, session material, "
        "or other sensitive credentials in your profile."
    )


def format_clarify_assertion() -> str:
    return (
        "I need a clearer profile statement. "
        "For example: I prefer Python. / My current project is DOOM."
    )


def format_nothing_known() -> str:
    return "I don't have anything stored in your profile yet."


def format_transparency(entries: Sequence[ProfileEntry], *, title: str = "") -> str:
    from orchestration.user_model.policy import is_sensitive_profile_content

    items = []
    for e in list(entries or []):
        if e is None:
            continue
        try:
            if is_sensitive_profile_content(e.value) or is_sensitive_profile_content(e.key):
                continue
            items.append(e)
        except Exception:
            continue
        if len(items) >= MAX_TRANSPARENCY_RESULTS:
            break
    if not items:
        return format_nothing_known()
    head = (title or "What I know about you").strip()
    lines = [f"{head} (up to {MAX_TRANSPARENCY_RESULTS}):", ""]
    for i, e in enumerate(items, 1):
        conf = getattr(e.confidence, "value", str(e.confidence))
        lines.append(
            f"{i}. {_label_category(e.category).capitalize()} — "
            f"{_label_key(e.key)}: {e.value} "
            f"(confidence: {conf})"
        )
    return "\n".join(lines).strip()[:2048]


def format_why(entry: Optional[ProfileEntry], *, topic: str = "") -> str:
    from orchestration.user_model.policy import is_sensitive_profile_content

    if entry is None:
        tip = f' about "{topic}"' if topic else ""
        return (
            f"I don't have enough profile information{tip} "
            "to explain that."
        )[:2048]
    try:
        if is_sensitive_profile_content(entry.value) or is_sensitive_profile_content(entry.key):
            tip = f' about "{topic}"' if topic else ""
            return (
                f"I don't have enough profile information{tip} "
                "to explain that."
            )[:2048]
    except Exception:
        return format_nothing_known()
    prov = getattr(entry.provenance, "value", str(entry.provenance))
    conf = getattr(entry.confidence, "value", str(entry.confidence))
    return (
        f"Profile entry: {_label_category(entry.category)} — "
        f"{_label_key(entry.key)}: {entry.value}.\n"
        f"Provenance: {prov}. Confidence: {conf}."
    )[:2048]
