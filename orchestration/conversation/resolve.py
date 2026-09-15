"""V8.21 deterministic conversation reference resolution. No LLM calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from orchestration.conversation.thread import (
    ConversationTurn,
    last_complete_turn,
)

# Clarifications — never invent missing referents.
CLARIFY_NO_CONTEXT = (
    "I'm not sure what you're referring to. What would you like me to continue from?"
)
CLARIFY_WHICH = (
    "Which one do you mean? Please name the option or give a bit more detail."
)
CLARIFY_COMPARE = (
    "What would you like me to compare that with?"
)
CLARIFY_AMBIGUOUS = (
    "That reference is ambiguous. Could you clarify what you mean?"
)

_WHY = re.compile(
    r"^(why|why\?|why is that|why is that\?)[\s?.!]*$",
    re.IGNORECASE,
)
_WHY_IT = re.compile(
    r"^why is (it|that|this)\b[\s?.!,a-z 0-9-]{0,60}$",
    re.IGNORECASE,
)
_TELL_MORE = re.compile(
    r"^(tell me more|explain that|explain more|more (please|detail|details)?|"
    r"elaborate|go deeper)[\s?.!]*$",
    re.IGNORECASE,
)
_CONTINUE = re.compile(
    r"^(continue|go on|keep going|carry on)[\s?.!]*$",
    re.IGNORECASE,
)
_WHAT_MEAN = re.compile(
    r"^(what did you mean|what do you mean|"
    r"explain the last part|explain the (last|previous) (part|point|bit))"
    r"[\s?.!]*$",
    re.IGNORECASE,
)
_WHICH = re.compile(
    r"^(which one|which|which is better|which would you (choose|pick))"
    r"[\s?.!]*$",
    re.IGNORECASE,
)
_IS_BETTER = re.compile(
    r"^(is that better|is this better|is it better)[\s?.!]*$",
    re.IGNORECASE,
)
_OTHER = re.compile(
    r"^(the other one|what about the other( one)?|and the other)[\s?.!]*$",
    re.IGNORECASE,
)
_OPTION_N = re.compile(
    r"^what about (the )?(first|second|third|1st|2nd|3rd)"
    r"( (one|option))?[\s?.!]*$",
    re.IGNORECASE,
)
_COMPARE_WITH = re.compile(
    r"^compare (that|it|this|them)(?:\s+with\s*(.*))?[\s?.!]*$",
    re.IGNORECASE,
)
_WHAT_ABOUT = re.compile(
    r"^what about\s+(.+?)[\s?.!]*$",
    re.IGNORECASE,
)
_PRONOUN = re.compile(
    r"^(that|this|it)[\s?.!]*$",
    re.IGNORECASE,
)
_ORDINAL = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
}

_NUMBERED_OPTION = re.compile(
    r"(?m)^\s*(?:[-*•]\s*)?(?:(\d+)[.)]|([A-C])[.)])\s+(.+?)(?=\n|$)"
)
_INLINE_NUMBERED = re.compile(
    r"(?:(?<=^)|(?<=\s))(?:(\d+)[.)]|([A-C])[.)])\s+(.+?)(?=(?:\s+(?:\d+|[A-C])[.)])|$)"
)
_INLINE_OPTIONS = re.compile(
    r"(?i)\b(?:options?|choices?)\s*:\s*(.+)$"
)
_BULLET_LINE = re.compile(
    r"(?m)^\s*([-*•])\s+(\S.+?)(?=\n|$)"
)
_BULLET_INLINE = re.compile(
    r"(?:(?<=^)|(?<=\s))([-*•])\s+([^*\-•]+?)(?=(?:\s+[-*•]\s+)|$)"
)


@dataclass(frozen=True)
class ResolutionResult:
    """Internal only — never authorization material."""

    status: str  # PASSTHROUGH | RESOLVED | CLARIFY
    kind: str
    original_text: str
    effective_text: str
    clarification: str = ""
    used_context: bool = False


def is_new_standalone_topic(text: str) -> bool:
    """Conservative: self-contained question starts a new anchor (not a short follow-up)."""
    q = " ".join(str(text or "").strip().split())
    if not q or is_continuation_utterance(q):
        return False
    if len(q) < 10:
        return False
    if re.match(
        r"(?i)^(what|who|how|explain|define|tell me about|describe|compare)\b",
        q,
    ):
        return True
    if "?" in q and len(q) >= 12:
        return True
    return False


def is_continuation_utterance(text: str) -> bool:
    """True for short follow-ups that need thread context."""
    q = " ".join(str(text or "").strip().split())
    if not q:
        return False
    return bool(
        _WHY.match(q)
        or _WHY_IT.match(q)
        or _TELL_MORE.match(q)
        or _CONTINUE.match(q)
        or _WHAT_MEAN.match(q)
        or _WHICH.match(q)
        or _IS_BETTER.match(q)
        or _OTHER.match(q)
        or _OPTION_N.match(q)
        or _COMPARE_WITH.match(q)
        or _PRONOUN.match(q)
        or (
            _WHAT_ABOUT.match(q)
            and len(q) <= 80
        )
    )


def extract_options(assistant_text: str) -> Tuple[str, ...]:
    """Deterministic numbered/lettered/bullet options from the last assistant reply."""
    text = str(assistant_text or "")
    found: List[str] = []
    for m in _NUMBERED_OPTION.finditer(text):
        body = _clean_option_item(m.group(3) or "")
        if body:
            found.append(body)
    # Prefer inline parse when collapsed history yields <2 line-based hits.
    if len(found) < 2:
        inline: List[str] = []
        for m in _INLINE_NUMBERED.finditer(text):
            body = _clean_option_item(m.group(3) or "")
            if body:
                inline.append(body)
        if len(inline) >= 2:
            found = inline
    if len(found) >= 2:
        return tuple(found[:6])

    bullets = _extract_bullet_options(text)
    if len(bullets) >= 2:
        return bullets

    # Fallback: "First X. Second Y." style.
    parts = re.split(
        r"(?i)\b(?:first(?:ly)?|second(?:ly)?|third(?:ly)?)\s*[,:]\s*",
        text,
    )
    if len(parts) >= 3:
        # split leaves preamble + segments
        segs = [_clean_option_item(p) for p in parts[1:]]
        segs = [s for s in segs if s]
        if len(segs) >= 2:
            return tuple(segs[:6])
    m = _INLINE_OPTIONS.search(text)
    if m:
        chunk = m.group(1)
        bits = re.split(r"\s*(?:,|;|\/|\bor\b)\s*", chunk)
        bits = [_clean_option_item(b) for b in bits]
        bits = [b for b in bits if b]
        if len(bits) >= 2:
            return tuple(bits[:6])
    return ()


def durable_assistant_thread_text(text: str, *, limit: int) -> str:
    """Bound assistant text for thread storage without losing a structured option list.

    Extraction runs on the full scrubbed reply first. If truncating to ``limit``
    would drop a clear option list, store a compact numbered list instead.
    Does not raise overall conversation turn/total caps.

    V8.24.1: plan-shaped replies use plan-preserving bounds (no option-compact
    rewrite; no mid-token truncation of step titles).
    """
    raw = str(text or "").replace("\x00", "")
    if not raw.strip():
        return ""
    collapsed = " ".join(raw.split())
    from orchestration.conversation.context import exclude_sensitive_text

    if exclude_sensitive_text(collapsed):
        return ""

    # V8.24.1 plan-aware path.
    try:
        from orchestration.plan.durable import looks_like_plan_text, preserve_plan_durable_text

        if looks_like_plan_text(collapsed):
            preserved = preserve_plan_durable_text(collapsed, limit=limit)
            if preserved and not exclude_sensitive_text(preserved):
                return preserved
            # Fail closed: do not store a corrupted mid-cut plan.
            return ""
    except Exception:
        pass

    opts = extract_options(raw)
    truncated = collapsed[: max(0, int(limit))]
    if len(opts) < 2:
        return truncated
    if len(extract_options(truncated)) >= 2:
        return truncated
    compact = " ".join(f"{i}. {o}" for i, o in enumerate(opts[:6], start=1))
    if exclude_sensitive_text(compact):
        return truncated
    return compact[: max(0, int(limit))]


def _clean_option_item(body: str) -> str:
    item = " ".join(str(body or "").split()).strip(" ,;:|")
    if not item or len(item) > 120:
        return ""
    if ". " in item or item.count(".") >= 2:
        return ""
    low = item.lower()
    if any(
        tok in low
        for tok in (
            "plan_hash",
            "authorized_plan",
            "csrf",
            "owner_id",
            "session_id",
            "api_key",
            "password",
            "executionidentity",
        )
    ):
        return ""
    return item[:200]


def _extract_bullet_options(text: str) -> Tuple[str, ...]:
    """Conservative unlabeled bullet runs. Not free prose."""
    raw = str(text or "")
    line_items: List[str] = []
    line_mark = ""
    for m in _BULLET_LINE.finditer(raw):
        mark = m.group(1)
        item = _clean_option_item(m.group(2))
        if not item:
            if len(line_items) >= 2:
                break
            line_items = []
            line_mark = ""
            continue
        if line_mark and mark != line_mark:
            if len(line_items) >= 2:
                break
            line_items = [item]
            line_mark = mark
            continue
        line_mark = mark
        line_items.append(item)
    if len(line_items) >= 2:
        return tuple(line_items[:6])

    inline_items: List[str] = []
    inline_mark = ""
    for m in _BULLET_INLINE.finditer(raw):
        mark = m.group(1)
        item = _clean_option_item(m.group(2))
        if not item:
            if len(inline_items) >= 2:
                break
            inline_items = []
            inline_mark = ""
            continue
        if inline_mark and mark != inline_mark:
            if len(inline_items) >= 2:
                break
            inline_items = [item]
            inline_mark = mark
            continue
        inline_mark = mark
        inline_items.append(item)
    if len(inline_items) >= 2:
        return tuple(inline_items[:6])
    return ()


def _topic_phrase(turn: ConversationTurn) -> str:
    if turn.user_text:
        return turn.user_text[:220]
    if turn.assistant_text:
        return turn.assistant_text[:220]
    return ""


def _assistant_snip(turn: ConversationTurn, n: int = 280) -> str:
    return (turn.assistant_text or "")[:n]


def resolve_conversation_reference(
    user_text: str,
    thread: Tuple[ConversationTurn, ...],
) -> ResolutionResult:
    """Resolve short references using the current thread. No LLM."""
    original = " ".join(str(user_text or "").strip().split())
    if not original:
        return ResolutionResult(
            status="PASSTHROUGH",
            kind="empty",
            original_text=original,
            effective_text=original,
        )

    if not is_continuation_utterance(original):
        return ResolutionResult(
            status="PASSTHROUGH",
            kind="standalone",
            original_text=original,
            effective_text=original,
        )

    last = last_complete_turn(thread)
    if last is None or not last.assistant_text:
        return ResolutionResult(
            status="CLARIFY",
            kind="no_context",
            original_text=original,
            effective_text=original,
            clarification=CLARIFY_NO_CONTEXT,
            used_context=False,
        )

    topic = _topic_phrase(last)
    prior = _assistant_snip(last)
    options = extract_options(last.assistant_text)

    if _WHY.match(original) or _WHY_IT.match(original):
        if not prior:
            return ResolutionResult(
                status="CLARIFY",
                kind="why",
                original_text=original,
                effective_text=original,
                clarification=CLARIFY_NO_CONTEXT,
                used_context=True,
            )
        effective = (
            "The user is asking why you made the recommendation in your "
            "immediately preceding answer. Answer only that question; do not "
            "start a new topic or discuss unrelated languages or history. "
            f"User question was: {topic}. Your prior answer was: {prior}"
        )
        return ResolutionResult(
            status="RESOLVED",
            kind="why",
            original_text=original,
            effective_text=effective[:800],
            used_context=True,
        )

    if _TELL_MORE.match(original):
        effective = (
            f"Tell me more about the current topic: {topic}. "
            f"Continue from: {prior}"
        )
        return ResolutionResult(
            status="RESOLVED",
            kind="tell_more",
            original_text=original,
            effective_text=effective[:800],
            used_context=True,
        )

    if _CONTINUE.match(original):
        effective = (
            f"Continue the current conversation about: {topic}. "
            f"Pick up from: {prior}"
        )
        return ResolutionResult(
            status="RESOLVED",
            kind="continue",
            original_text=original,
            effective_text=effective[:800],
            used_context=True,
        )

    if _WHAT_MEAN.match(original):
        effective = (
            f"What did you mean in the last part of your previous answer "
            f"about: {topic}? Last answer: {prior}"
        )
        return ResolutionResult(
            status="RESOLVED",
            kind="what_mean",
            original_text=original,
            effective_text=effective[:800],
            used_context=True,
        )

    if _COMPARE_WITH.match(original):
        m = _COMPARE_WITH.match(original)
        target = (m.group(2) if m else "") or ""
        target = target.strip().strip("?.!")
        if not target:
            return ResolutionResult(
                status="CLARIFY",
                kind="compare",
                original_text=original,
                effective_text=original,
                clarification=CLARIFY_COMPARE,
                used_context=True,
            )
        subject = topic or prior or "the previous topic"
        effective = f"Compare {subject} with {target}."
        return ResolutionResult(
            status="RESOLVED",
            kind="compare",
            original_text=original,
            effective_text=effective[:800],
            used_context=True,
        )

    if _OPTION_N.match(original):
        m = _OPTION_N.match(original)
        raw = (m.group(2) if m else "").lower()
        idx = _ORDINAL.get(raw, 0)
        if not options:
            return ResolutionResult(
                status="CLARIFY",
                kind="option",
                original_text=original,
                effective_text=original,
                clarification=CLARIFY_WHICH,
                used_context=True,
            )
        if idx < 1 or idx > len(options):
            return ResolutionResult(
                status="CLARIFY",
                kind="option",
                original_text=original,
                effective_text=original,
                clarification=CLARIFY_WHICH,
                used_context=True,
            )
        choice = options[idx - 1]
        effective = (
            f"Regarding option {idx} ({choice}) from your previous answer "
            f"about {topic}: tell me more."
        )
        return ResolutionResult(
            status="RESOLVED",
            kind="option",
            original_text=original,
            effective_text=effective[:800],
            used_context=True,
        )

    if _OTHER.match(original):
        if len(options) == 2:
            choice = options[1]
            effective = (
                f"What about the other option ({choice}) compared with the "
                f"current topic: {topic}?"
            )
            return ResolutionResult(
                status="RESOLVED",
                kind="other",
                original_text=original,
                effective_text=effective[:800],
                used_context=True,
            )
        return ResolutionResult(
            status="CLARIFY",
            kind="other",
            original_text=original,
            effective_text=original,
            clarification=CLARIFY_WHICH,
            used_context=True,
        )

    if _WHICH.match(original) or _IS_BETTER.match(original):
        if len(options) >= 2:
            return ResolutionResult(
                status="CLARIFY",
                kind="which",
                original_text=original,
                effective_text=original,
                clarification=CLARIFY_WHICH,
                used_context=True,
            )
        if len(options) == 1:
            effective = (
                f"Regarding the option ({options[0]}) in the context of "
                f"{topic}: which would you choose and why?"
            )
            return ResolutionResult(
                status="RESOLVED",
                kind="which",
                original_text=original,
                effective_text=effective[:800],
                used_context=True,
            )
        # No explicit options — compare against prior topic if present.
        if topic and prior:
            if _IS_BETTER.match(original):
                effective = (
                    f"Is that better? Evaluate the previous recommendation "
                    f"about: {topic}. Previous answer: {prior}"
                )
                return ResolutionResult(
                    status="RESOLVED",
                    kind="is_better",
                    original_text=original,
                    effective_text=effective[:800],
                    used_context=True,
                )
            return ResolutionResult(
                status="CLARIFY",
                kind="which",
                original_text=original,
                effective_text=original,
                clarification=CLARIFY_WHICH,
                used_context=True,
            )
        return ResolutionResult(
            status="CLARIFY",
            kind="which",
            original_text=original,
            effective_text=original,
            clarification=CLARIFY_AMBIGUOUS,
            used_context=True,
        )

    if _PRONOUN.match(original):
        if not topic and not prior:
            return ResolutionResult(
                status="CLARIFY",
                kind="pronoun",
                original_text=original,
                effective_text=original,
                clarification=CLARIFY_AMBIGUOUS,
                used_context=True,
            )
        effective = (
            f"Regarding that (from the current thread about: {topic}): "
            f"please explain further. Previous answer: {prior}"
        )
        return ResolutionResult(
            status="RESOLVED",
            kind="pronoun",
            original_text=original,
            effective_text=effective[:800],
            used_context=True,
        )

    m_about = _WHAT_ABOUT.match(original)
    if m_about and len(original) <= 80:
        target = (m_about.group(1) or "").strip()
        # Already handled ordinals above; treat remainder as comparison target.
        if target and not re.match(
            r"(?i)^(the )?(first|second|third|1st|2nd|3rd|other)",
            target,
        ):
            if not topic and not prior:
                return ResolutionResult(
                    status="CLARIFY",
                    kind="what_about",
                    original_text=original,
                    effective_text=original,
                    clarification=CLARIFY_NO_CONTEXT,
                    used_context=False,
                )
            effective = (
                f"What about {target}, in relation to the current topic: "
                f"{topic}?"
            )
            return ResolutionResult(
                status="RESOLVED",
                kind="what_about",
                original_text=original,
                effective_text=effective[:800],
                used_context=True,
            )

    return ResolutionResult(
        status="PASSTHROUGH",
        kind="unmatched",
        original_text=original,
        effective_text=original,
    )
