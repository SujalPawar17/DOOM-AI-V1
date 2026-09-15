"""V8.24 bounded prior-plan parser. Untrusted text only; never executes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from orchestration.conversation.context import sanitize_context_text
from orchestration.plan.types import (
    MAX_STEP_DETAIL_CHARS,
    MAX_STEP_TITLE_CHARS,
    MAX_STEPS,
    MAX_TITLE_CHARS,
    PlanConfidence,
    PlanResult,
    PlanStatus,
    PlanStepItem,
    PlanStepKind,
)

CLARIFY_NEED_PRIOR = (
    "To refine or prioritize a plan, I need a recent plan in this conversation. "
    "Ask me for a plan first, then we can improve it."
)


@dataclass(frozen=True)
class ParseOutcome:
    ok: bool
    result: Optional[PlanResult] = None
    clarify: str = ""
    ambiguous: bool = False


_PLAN_HEADER = re.compile(r"(?i)\bplan:\s*")
_STEPS_MARK = re.compile(r"(?i)\bsteps:\s*")


def _guess_kind(title: str) -> PlanStepKind:
    t = title.lower()
    if re.search(r"\b(optional|if needed|defer)\b", t):
        return PlanStepKind.OPTIONAL
    if re.search(r"\b(verify|confirm|test|re-?check|review)\b", t):
        return PlanStepKind.VERIFY
    if re.search(r"\b(decide|choose|identify|select)\b", t):
        return PlanStepKind.DECIDE
    if re.search(r"\b(check|list|confirm available)\b", t):
        return PlanStepKind.CHECK
    return PlanStepKind.PREPARE


def _extract_title(raw: str) -> str:
    m = re.search(r"(?is)\bplan:\s*(.+?)(?=\bsteps:|\Z)", raw)
    if not m:
        return ""
    title = sanitize_context_text(m.group(1), limit=MAX_TITLE_CHARS)
    return title or "Plan"


def _extract_steps_region(raw: str) -> str:
    m = re.search(
        r"(?is)\bsteps:\s*(.+?)(?=\b(?:why|watch-?outs?|priority|dependencies|"
        r"blockers|next step|confidence|assumptions)\s*:|\Z)",
        raw,
    )
    return m.group(1) if m else ""


def _parse_numbered(body: str) -> List[Tuple[str, str]]:
    """Parse numbered steps. Titles only — never treat flattened detail as required."""
    from orchestration.plan.durable import truncate_at_word

    body = " ".join(str(body or "").split())
    if not body:
        return []
    parts = re.split(r"(?=\b\d+[.)]\s+)", body)
    out: List[Tuple[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        sm = re.match(r"^(\d+)[.)]\s+(.+)$", part)
        if not sm:
            continue
        rest = sm.group(2).strip()
        # Durable seeds store titles only. Sensitive blobs sanitize to empty → drop.
        cleaned = sanitize_context_text(rest, limit=MAX_STEP_TITLE_CHARS * 2)
        if not cleaned:
            continue
        title = truncate_at_word(cleaned, MAX_STEP_TITLE_CHARS)
        if title:
            out.append((title, ""))
    return out


def parse_plan_from_assistant_text(text: str) -> ParseOutcome:
    """Parse a V8.23/V8.24 formatted plan (multiline or V8.21-flattened)."""
    raw = str(text or "").strip()
    if not raw:
        return ParseOutcome(ok=False, clarify=CLARIFY_NEED_PRIOR)

    plan_hits = len(_PLAN_HEADER.findall(raw))
    steps_hits = len(_STEPS_MARK.findall(raw))
    if plan_hits > 1 or steps_hits > 1:
        return ParseOutcome(ok=False, clarify=CLARIFY_NEED_PRIOR, ambiguous=True)
    if plan_hits < 1 or steps_hits < 1:
        return ParseOutcome(ok=False, clarify=CLARIFY_NEED_PRIOR)

    title = _extract_title(raw)
    if not title:
        return ParseOutcome(ok=False, clarify=CLARIFY_NEED_PRIOR)

    body = _extract_steps_region(raw)
    steps_raw = _parse_numbered(body)
    if not steps_raw:
        return ParseOutcome(ok=False, clarify=CLARIFY_NEED_PRIOR)
    if len(steps_raw) > MAX_STEPS:
        return ParseOutcome(
            ok=False,
            clarify="That plan has too many steps for me to refine safely. "
            "Please ask for a shorter plan (up to 6 steps).",
        )

    items: List[PlanStepItem] = []
    for i, (t, d) in enumerate(steps_raw, start=1):
        if not t:
            continue
        items.append(
            PlanStepItem(
                index=i,
                title=t[:MAX_STEP_TITLE_CHARS],
                detail=(d or "")[:MAX_STEP_DETAIL_CHARS],
                kind=_guess_kind(t),
            )
        )
    if not items:
        return ParseOutcome(ok=False, clarify=CLARIFY_NEED_PRIOR)

    items = [
        PlanStepItem(index=i, title=s.title, detail=s.detail, kind=s.kind)
        for i, s in enumerate(items[:MAX_STEPS], start=1)
    ]

    conf_m = re.search(r"(?i)\bconfidence:\s*(high|medium|low)\b", raw)
    conf = PlanConfidence.MEDIUM
    if conf_m:
        c = conf_m.group(1).lower()
        if c == "high":
            conf = PlanConfidence.HIGH
        elif c == "low":
            conf = PlanConfidence.LOW

    return ParseOutcome(
        ok=True,
        result=PlanResult(
            status=PlanStatus.OK,
            title=title[:MAX_TITLE_CHARS],
            steps=tuple(items),
            reasons=(),
            blockers=(),
            confidence=conf,
            assumptions=(),
        ),
    )
