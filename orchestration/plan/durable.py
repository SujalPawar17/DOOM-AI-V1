"""V8.24.1 bounded durable plan serialization for conversation recovery.

Informational only. No identity/auth fields. Titles only (no details) so
flattening cannot merge detail into titles.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

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

def _step_line(index: int, title: str) -> str:
    t = truncate_at_word(title, MAX_STEP_TITLE_CHARS)
    return f"{index}. {t}"


def serialize_plan_durable(
    result: PlanResult,
    *,
    limit: int = DURABLE_PLAN_LIMIT,
) -> Optional[str]:
    """Return a bounded, parseable plan seed, or None if it cannot fit safely.

    Format (whitespace-normalized for storage):
      Plan: <title> Steps: 1. <title> 2. <title> ...
    Details are omitted intentionally.
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

    # Shrink step titles until the whole payload fits, or fail closed.
    max_title = MAX_STEP_TITLE_CHARS
    while max_title >= 12:
        parts: List[str] = [f"Plan: {title}", "Steps:"]
        ok_steps = True
        for i, step in enumerate(steps, start=1):
            st = truncate_at_word(step.title or "", max_title)
            if not st:
                ok_steps = False
                break
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

    Prefers dropping whole trailing steps over chopping a title mid-word.
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

    # Keep as many complete steps as fit.
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
