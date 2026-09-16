"""V8.24.1 / V8.25 bounded durable plan serialization for conversation recovery.

Informational only. No identity/auth fields. Titles only (no details) so
flattening cannot merge detail into titles. Supports structural state markers.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Sequence, Tuple

from orchestration.plan.continuity.types import StepState
from orchestration.plan.types import (
    MAX_STEP_TITLE_CHARS,
    MAX_STEPS,
    MAX_TITLE_CHARS,
    PlanResult,
    PlanStatus,
    PlanStepItem,
)

# Match V8.21 conversation assistant turn cap (do not raise globally).
DURABLE_PLAN_LIMIT = 300

_PLAN_SHAPE = re.compile(r"(?i)\bplan:\s*\S.+\bsteps:\s*\d+[.)]")

STATE_TO_MARKER = {
    StepState.COMPLETED: "[x]",
    StepState.IN_PROGRESS: "[>]",
    StepState.BLOCKED: "[!]",
    StepState.SKIPPED: "[-]",
    StepState.PENDING: "[ ]",
}

MARKER_TO_STATE = {
    "[x]": StepState.COMPLETED,
    "[>]": StepState.IN_PROGRESS,
    "[!]": StepState.BLOCKED,
    "[-]": StepState.SKIPPED,
    "[ ]": StepState.PENDING,
}

# Exact structural markers only — not arbitrary bracketed text.
_MARKER_REGEX = re.compile(r"^\[([xX>!\- ])\]\s*")


def extract_state_and_clean_title(raw_title: str) -> Tuple[StepState, str]:
    """Extract StepState from structural marker and return (state, clean_title).

    Tolerates leading structural markers ([x], [>], [!], [-], [ ]).
    Strips repeated/nested markers to prevent '[x] [x] title' accumulation.
    Non-matching bracketed text like [delete] is retained in the clean title.
    Unmarked titles default to StepState.PENDING.
    """
    text = str(raw_title or "").strip()
    state = StepState.PENDING
    first_match = _MARKER_REGEX.match(text)
    if first_match:
        m_char = first_match.group(1)
        if m_char in ("x", "X"):
            state = StepState.COMPLETED
        elif m_char == ">":
            state = StepState.IN_PROGRESS
        elif m_char == "!":
            state = StepState.BLOCKED
        elif m_char == "-":
            state = StepState.SKIPPED
        elif m_char == " ":
            state = StepState.PENDING
        text = text[first_match.end() :].strip()

        # Strip nested markers so recovery cycles cannot accumulate them.
        while True:
            nxt = _MARKER_REGEX.match(text)
            if not nxt:
                break
            text = text[nxt.end() :].strip()

    return state, text


def looks_like_plan_text(text: str) -> bool:
    collapsed = " ".join(str(text or "").split())
    return bool(_PLAN_SHAPE.search(collapsed))


def truncate_at_word(text: str, limit: int) -> str:
    """Truncate at a word boundary. Never returns a mid-token fragment."""
    s = " ".join(str(text or "").split())
    lim = max(0, int(limit))
    if not s or lim <= 0:
        return ""
    if len(s) <= lim:
        return s
    cut = s[:lim].rstrip()
    if " " not in cut:
        # First word alone exceeds the limit — fail closed (no mid-token).
        return ""
    cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:|-")


def serialize_plan_durable(
    result: PlanResult,
    *,
    limit: int = DURABLE_PLAN_LIMIT,
    step_states: Optional[Sequence[Any]] = None,
    continuity_state: Optional[Any] = None,
    include_markers: bool = True,
) -> Optional[str]:
    """Return a bounded, parseable plan seed, or None if it cannot fit safely.

    Format (whitespace-normalized for storage):
      Plan: <title> Steps: 1. [<marker>] <title> 2. [<marker>] <title> ...
    Details are omitted intentionally.
    State markers are added AFTER title truncation to avoid mid-token cutting.
    Fails closed (returns None) if any step cannot fit.
    """
    if result is None or result.status is not PlanStatus.OK:
        return None
    steps = list(result.steps or ())
    if not steps or len(steps) > MAX_STEPS:
        return None

    lim = max(32, int(limit))
    title = truncate_at_word(result.title or "Plan", min(MAX_TITLE_CHARS, 80))
    if not title:
        title = "Plan"

    # Resolve step states and clean titles (markers never enter truncation budget).
    resolved_states: List[StepState] = []
    clean_titles: List[str] = []
    for i, step in enumerate(steps):
        st_from_title, c_title = extract_state_and_clean_title(step.title or "")
        clean_titles.append(c_title)
        if step_states is not None and i < len(step_states):
            s = step_states[i]
            if isinstance(s, StepState):
                resolved_states.append(s)
            elif isinstance(s, str) and s in StepState._value2member_map_:
                resolved_states.append(StepState(s))
            else:
                resolved_states.append(st_from_title)
        elif continuity_state is not None and hasattr(continuity_state, "step_records"):
            recs = getattr(continuity_state, "step_records", ())
            if i < len(recs):
                resolved_states.append(recs[i].state)
            else:
                resolved_states.append(st_from_title)
        else:
            resolved_states.append(st_from_title)

    # Shrink step titles until the whole payload fits, or fail closed.
    # Never drop a trailing state-bearing step (no false completion).
    max_title = MAX_STEP_TITLE_CHARS
    while max_title >= 12:
        parts: List[str] = [f"Plan: {title}", "Steps:"]
        ok_steps = True
        for i, c_title in enumerate(clean_titles, start=1):
            st = truncate_at_word(c_title, max_title)
            if not st:
                ok_steps = False
                break
            if include_markers:
                marker = STATE_TO_MARKER.get(resolved_states[i - 1], "[ ]")
                parts.append(f"{i}. {marker} {st}")
            else:
                parts.append(f"{i}. {st}")
        if not ok_steps:
            max_title -= 4
            continue
        payload = " ".join(parts)
        if len(payload) <= lim:
            return payload
        max_title -= 4

    return None


def preserve_plan_durable_text(text: str, *, limit: int = DURABLE_PLAN_LIMIT) -> str:
    """For already plan-shaped assistant text: fit within limit without mid-token cuts.

    Prefers dropping whole trailing steps over chopping a title mid-word for legacy text.
    For state-bearing text, preserves all steps or fails closed to prevent false completion.
    Returns empty string if nothing safe remains.
    """
    raw = " ".join(str(text or "").replace("\x00", "").split())
    lim = max(0, int(limit))
    if not raw or lim <= 0:
        return ""
    if not looks_like_plan_text(raw):
        return ""
    if len(raw) <= lim:
        return raw

    # Parse numbered steps from a flattened body.
    m = re.search(r"(?is)\bplan:\s*(.+?)\s+steps:\s*(.+)$", raw)
    if not m:
        return truncate_at_word(raw, lim)
    plan_title = truncate_at_word(m.group(1), 80)
    body = m.group(2)
    step_chunks = re.findall(r"\b(\d+)[.)]\s+(.+?)(?=\s+\d+[.)]\s+|$)", body)
    if not step_chunks:
        return ""

    has_markers = any(_MARKER_REGEX.match(chunk[1].strip()) for chunk in step_chunks)

    if has_markers:
        # State-bearing plan: NO FALSE COMPLETION.
        # Must NOT drop trailing steps. All steps must fit, or fail closed.
        for mt in range(MAX_STEP_TITLE_CHARS, 11, -4):
            parts = [f"Plan: {plan_title}", "Steps:"]
            ok = True
            for idx, raw_chunk_title in step_chunks:
                st_state, clean_t = extract_state_and_clean_title(raw_chunk_title)
                st = truncate_at_word(clean_t, mt)
                if not st:
                    ok = False
                    break
                marker = STATE_TO_MARKER.get(st_state, "[ ]")
                parts.append(f"{idx}. {marker} {st}")
            if ok:
                payload = " ".join(parts)
                if len(payload) <= lim:
                    return payload
        return ""

    # Keep as many complete steps as fit (legacy behavior for unmarked text).
    for n in range(len(step_chunks), 0, -1):
        parts = [f"Plan: {plan_title}", "Steps:"]
        for idx, title in step_chunks[:n]:
            st = truncate_at_word(title, MAX_STEP_TITLE_CHARS)
            if not st:
                continue
            parts.append(f"{idx}. {st}")
        payload = " ".join(parts)
        if len(payload) <= lim and len(parts) > 2:
            return payload
        # Shrink titles further
        for mt in range(MAX_STEP_TITLE_CHARS, 11, -4):
            parts = [f"Plan: {plan_title}", "Steps:"]
            for idx, title in step_chunks[:n]:
                st = truncate_at_word(title, mt)
                if st:
                    parts.append(f"{idx}. {st}")
            payload = " ".join(parts)
            if len(payload) <= lim and len(parts) > 2:
                return payload
    return ""
