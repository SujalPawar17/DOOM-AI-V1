"""V8.28 Phase 2 — nonfatal capture of terminal Goal Registry state.

Historical side effect only. Never mutates Goal Registry / Continuity /
User Model. Never authorizes. Never calls models or network.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from orchestration.experience.config import is_v828_goal_experience_enabled
from orchestration.experience.types import (
    ExperienceResult,
    ExperienceResultStatus,
    Outcome,
)

_TERMINAL_OUTCOMES = frozenset(
    {
        Outcome.COMPLETED,
        Outcome.ABANDONED,
        Outcome.STALE,
    }
)


def _map_lifecycle_to_outcome(lifecycle: Any) -> Optional[Outcome]:
    raw = getattr(lifecycle, "value", lifecycle)
    text = str(raw or "").strip().upper()
    if text == "COMPLETED":
        return Outcome.COMPLETED
    if text == "ABANDONED":
        return Outcome.ABANDONED
    if text == "STALE":
        return Outcome.STALE
    return None


def _step_summaries(snapshot: Any) -> Tuple[str, ...]:
    titles = tuple(getattr(snapshot, "step_titles", ()) or ())
    states = tuple(getattr(snapshot, "step_states", ()) or ())
    out: List[str] = []
    n = min(len(titles), len(states), 8)
    for i in range(n):
        title = " ".join(str(titles[i] or "").split())
        state = getattr(states[i], "value", states[i])
        state_s = str(state or "").strip().upper()
        if not title:
            continue
        line = f"{title} [{state_s}]" if state_s else title
        out.append(line[:80])
        if len(out) >= 8:
            break
    return tuple(out)


def _blockers_from_snapshot(snapshot: Any) -> Tuple[str, ...]:
    out: List[str] = []
    summary = str(getattr(snapshot, "blocker_summary", "") or "").strip()
    if summary:
        out.append(summary[:120])
    # Explicit BLOCKED steps as secondary trusted blockers.
    titles = tuple(getattr(snapshot, "step_titles", ()) or ())
    states = tuple(getattr(snapshot, "step_states", ()) or ())
    for i in range(min(len(titles), len(states))):
        if len(out) >= 4:
            break
        st = getattr(states[i], "value", states[i])
        if str(st or "").upper() != "BLOCKED":
            continue
        title = " ".join(str(titles[i] or "").split())
        if not title:
            continue
        line = f"Blocked: {title}"[:120]
        if line not in out:
            out.append(line)
    stale = str(getattr(snapshot, "staleness_reason", "") or "").strip()
    if stale and len(out) < 4:
        line = f"Stale: {stale}"[:120]
        if line not in out:
            out.append(line)
    return tuple(out[:4])


def _safe_text_tuple(items: Tuple[str, ...]) -> Tuple[str, ...]:
    from orchestration.experience.policy import is_sensitive_experience_content

    out: List[str] = []
    for raw in items:
        text = " ".join(str(raw or "").split())
        if not text:
            continue
        if is_sensitive_experience_content(text):
            continue
        out.append(text)
    return tuple(out)


def capture_goal_experience_from_terminal_state(
    snapshot: Any,
    *,
    outcome: Any = None,
) -> ExperienceResult:
    """Create a historical experience from a trusted terminal GoalSnapshot.

    Never raises. Never mutates the snapshot / registry. Failures are soft.
    """
    try:
        if not is_v828_goal_experience_enabled():
            return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)
        if snapshot is None:
            return ExperienceResult(ExperienceResultStatus.REJECTED)

        owner = str(getattr(snapshot, "owner_id", "") or "").strip()[:64]
        if not owner:
            return ExperienceResult(ExperienceResultStatus.REJECTED)

        mapped = _map_lifecycle_to_outcome(
            outcome if outcome is not None else getattr(snapshot, "lifecycle", None)
        )
        if mapped is None or mapped not in _TERMINAL_OUTCOMES:
            return ExperienceResult(ExperienceResultStatus.REJECTED)

        gid = str(getattr(snapshot, "goal_id", "") or "").strip()[:64]
        title = str(getattr(snapshot, "title", "") or "").strip()
        if not title:
            title = str(getattr(snapshot, "plan_title", "") or "").strip()
        if not title:
            return ExperienceResult(ExperienceResultStatus.REJECTED)

        from orchestration.experience.policy import (
            is_sensitive_experience_content,
            normalize_source_goal_id,
        )

        if is_sensitive_experience_content(title):
            return ExperienceResult(ExperienceResultStatus.SENSITIVE_REJECTED)

        fields = {
            "title": title,
            "outcome": mapped,
            "step_summary": _safe_text_tuple(_step_summaries(snapshot)),
            "blockers": _safe_text_tuple(_blockers_from_snapshot(snapshot)),
            "user_note": "",  # no trusted explicit note field on registry snapshot
            "tags": (),
            "source_goal_id": normalize_source_goal_id(gid),
        }

        from orchestration.experience.store import create_experience

        return create_experience(owner, fields)
    except Exception:
        return ExperienceResult(ExperienceResultStatus.UNAVAILABLE)


def maybe_capture_after_registry_result(result: Any) -> None:
    """Nonfatal side effect after a successful terminal registry transition.

    Never raises. Never changes ``result``.
    """
    try:
        if not is_v828_goal_experience_enabled():
            return
        status = getattr(result, "status", None)
        # RegistryStatus.OK
        if str(getattr(status, "value", status) or "").upper() != "OK":
            return
        snap = getattr(result, "snapshot", None)
        if snap is None:
            return
        outcome = _map_lifecycle_to_outcome(getattr(snap, "lifecycle", None))
        if outcome is None:
            return
        capture_goal_experience_from_terminal_state(snap, outcome=outcome)
    except Exception:
        return
