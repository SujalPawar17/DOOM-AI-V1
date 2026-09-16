"""V8.25 deterministic progress and pivot detection.

No LLM. No Ollama. Zero execution authority.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from orchestration.decision.relevance import decision_relevant
from orchestration.plan.continuity.types import (
    MAX_RAW_EVIDENCE_CHARS,
    PlanContinuityState,
    ProgressEvent,
    ProgressEventKind,
)
from orchestration.plan.types import PlanConfidence

_SENSITIVE_PATTERNS = re.compile(
    r"(?i)(api[_-]?key\s*[:=]\s*\S+|bearer\s+\S+|password\s*[:=]\s*\S+|"
    r"passwd\s*[:=]\s*\S+|token\s*[:=]\s*\S+|secret\s*[:=]\s*\S+|"
    r"eval\s*\(|exec\s*\(|rm\s+-rf|__import__|subprocess)"
)

_EXPLICIT_COMPLETION = re.compile(
    r"(?i)\b(?:i\s+)?(?:already\s+)?(?:finished|completed|done(?:\s+with)?|did)\s+(?:step\s+)?(\d+)\b"
)
_STEP_IS_DONE = re.compile(
    r"(?i)\bstep\s+(\d+)\s+(?:is\s+)?(?:already\s+)?(?:done|finished|complete|completed)\b"
)

_EXPLICIT_BLOCKER = re.compile(
    r"(?i)\b(?:step\s+)?(\d+)\s+is\s+(?:blocked|stuck|failing)\b"
)
_I_AM_BLOCKED_STEP = re.compile(
    r"(?i)\b(?:i(?:'m| am)\s+)?(?:stuck|blocked)\s+on\s+(?:step\s+)?(\d+)\b"
)

_EXPLICIT_SKIP = re.compile(
    r"(?i)\b(?:skip|bypass|drop)\s+(?:step\s+)?(\d+)\b"
)

_EXPLICIT_STARTED = re.compile(
    r"(?i)\b(?:started|starting|working on)\s+(?:step\s+)?(\d+)\b"
)

_GENERIC_COMPLETION = re.compile(
    r"(?i)^(?:i\s+)?(?:finished|completed|done)(?:\s+(?:it|that|this))?[\s?.!]*$"
)
_GENERIC_BLOCKER = re.compile(
    r"(?i)^(?:i(?:'m| am)\s+)?(?:stuck|blocked)[\s?.!]*$"
)
_GENERIC_SKIP = re.compile(
    r"(?i)^(?:skip|bypass|drop)(?:\s+(?:it|that|this))?[\s?.!]*$"
)

_STEP_QUESTION = re.compile(
    r"(?i)\b(?:what|how|why|explain)\s+(?:is|about)?\s*(?:step\s+\d+)\b"
)

_PIVOT_FRAME = re.compile(
    r"(?i)\b(?:actually|instead|switch to|change (?:my |the )?goal to|forget that,? let's)\b"
)
_PIVOT_TOPIC_EXTRACT = re.compile(
    r"(?i)(?:switch to|change (?:my |the )?goal to|focus on|let's do|let's work on)\s+(.+?)(?:\s+instead)?(?:[?.!]|$)"
)


def sanitize_progress_evidence(text: str) -> str:
    """Scrub sensitive credentials, tokens, and control commands from evidence."""
    raw = " ".join(str(text or "").replace("\x00", "").split())
    if not raw:
        return ""
    scrubbed = _SENSITIVE_PATTERNS.sub("", raw)
    scrubbed = " ".join(scrubbed.split())
    return scrubbed[:MAX_RAW_EVIDENCE_CHARS]


def _stem(word: str) -> str:
    w = word.lower()
    if w.endswith("ing") and len(w) > 5:
        return w[:-3]
    if w.endswith("ed") and len(w) > 4:
        return w[:-2]
    if w.endswith("s") and len(w) > 3 and not w.endswith("ss"):
        return w[:-1]
    return w


def _match_step_title(
    query: str,
    step_titles: Sequence[Tuple[int, str]],
) -> Optional[int]:
    """Deterministic title substring/word-token matching. No fuzzy/semantic model."""
    q_norm = " ".join(str(query or "").lower().split())
    # Strip common completion verbs to isolate content
    stripped = re.sub(
        r"(?i)\b(i finished|finished|completed|done with|done|did|working on|started|skip|bypass)\b",
        "",
        q_norm,
    ).strip()
    words = [w for w in re.split(r"\W+", stripped) if len(w) >= 3]
    if not words:
        return None
    stems = [_stem(w) for w in words]

    matches: List[int] = []
    for idx, title in step_titles:
        t_norm = title.lower()
        t_words = [w for w in re.split(r"\W+", t_norm) if len(w) >= 3]
        t_stems = [_stem(tw) for tw in t_words]

        # Direct phrase match or stem hit
        if stripped and (stripped in t_norm or all(s in t_norm for s in stems)):
            matches.append(idx)
            continue
        word_hits = sum(1 for s in stems if s in t_norm or s in t_stems)
        if word_hits >= max(1, len(stems) // 2 + 1) or (len(stems) == 1 and word_hits == 1):
            matches.append(idx)

    if len(matches) == 1:
        return matches[0]
    return None


def detect_progress_event(
    query: str,
    continuity_state: Optional[PlanContinuityState] = None,
    *,
    active_step_index: int = 0,
    step_titles: Optional[Sequence[Tuple[int, str]]] = None,
) -> ProgressEvent:
    """Classify user query into a ProgressEvent. Deterministic and pure."""
    q = " ".join(str(query or "").strip().split())
    evidence = sanitize_progress_evidence(q)
    if not q:
        return ProgressEvent(kind=ProgressEventKind.NOOP)

    # Resolve active step cursor
    active_idx = int(active_step_index)
    if active_idx <= 0 and continuity_state is not None:
        active_idx = int(continuity_state.active_step_index)

    # Resolve step titles if available
    titles = list(step_titles or [])
    if not titles and continuity_state is not None:
        titles = [(r.index, getattr(r, "detail", "") or "") for r in continuity_state.step_records]

    # 1. Explicit Step Completion
    m = _EXPLICIT_COMPLETION.search(q) or _STEP_IS_DONE.search(q)
    if m:
        step_num = int(m.group(1))
        return ProgressEvent(
            kind=ProgressEventKind.STEP_COMPLETED,
            target_step_index=step_num,
            raw_evidence=evidence,
            confidence=PlanConfidence.HIGH,
        )

    # 2. Explicit Step Blocker
    m = _EXPLICIT_BLOCKER.search(q) or _I_AM_BLOCKED_STEP.search(q)
    if m:
        step_num = int(m.group(1))
        return ProgressEvent(
            kind=ProgressEventKind.STEP_BLOCKED,
            target_step_index=step_num,
            raw_evidence=evidence,
            confidence=PlanConfidence.HIGH,
        )

    # 3. Explicit Step Skip
    m = _EXPLICIT_SKIP.search(q)
    if m:
        step_num = int(m.group(1))
        return ProgressEvent(
            kind=ProgressEventKind.STEP_SKIPPED,
            target_step_index=step_num,
            raw_evidence=evidence,
            confidence=PlanConfidence.HIGH,
        )

    # 4. Explicit Step Started
    m = _EXPLICIT_STARTED.search(q)
    if m:
        step_num = int(m.group(1))
        return ProgressEvent(
            kind=ProgressEventKind.STEP_STARTED,
            target_step_index=step_num,
            raw_evidence=evidence,
            confidence=PlanConfidence.HIGH,
        )

    # 5. Generic Statements (require valid active step or mark ambiguous)
    if _GENERIC_COMPLETION.match(q):
        if active_idx > 0:
            return ProgressEvent(
                kind=ProgressEventKind.STEP_COMPLETED,
                target_step_index=active_idx,
                raw_evidence=evidence,
                confidence=PlanConfidence.MEDIUM,
                is_ambiguous=False,
            )
        return ProgressEvent(
            kind=ProgressEventKind.STEP_COMPLETED,
            target_step_index=0,
            raw_evidence=evidence,
            confidence=PlanConfidence.LOW,
            is_ambiguous=True,
        )

    if _GENERIC_BLOCKER.match(q):
        if active_idx > 0:
            return ProgressEvent(
                kind=ProgressEventKind.STEP_BLOCKED,
                target_step_index=active_idx,
                raw_evidence=evidence,
                confidence=PlanConfidence.MEDIUM,
                is_ambiguous=False,
            )
        return ProgressEvent(
            kind=ProgressEventKind.STEP_BLOCKED,
            target_step_index=0,
            raw_evidence=evidence,
            confidence=PlanConfidence.LOW,
            is_ambiguous=True,
        )

    if _GENERIC_SKIP.match(q):
        if active_idx > 0:
            return ProgressEvent(
                kind=ProgressEventKind.STEP_SKIPPED,
                target_step_index=active_idx,
                raw_evidence=evidence,
                confidence=PlanConfidence.MEDIUM,
                is_ambiguous=False,
            )
        return ProgressEvent(
            kind=ProgressEventKind.STEP_SKIPPED,
            target_step_index=0,
            raw_evidence=evidence,
            confidence=PlanConfidence.LOW,
            is_ambiguous=True,
        )

    # 6. Title-Oriented Completion Match
    if titles and re.search(r"(?i)\b(finished|completed|done with|did)\b", q):
        matched_idx = _match_step_title(q, titles)
        if matched_idx is not None:
            return ProgressEvent(
                kind=ProgressEventKind.STEP_COMPLETED,
                target_step_index=matched_idx,
                raw_evidence=evidence,
                confidence=PlanConfidence.MEDIUM,
            )

    # 7. Goal Pivot Detection with Strict False-Positive Guards
    if _PIVOT_FRAME.search(q):
        # False pivot guard: decision questions
        if decision_relevant(q):
            return ProgressEvent(kind=ProgressEventKind.NOOP, raw_evidence=evidence)
        # False pivot guard: questions about steps
        if _STEP_QUESTION.search(q) or re.search(r"(?i)\bstep\s+\d+\b", q):
            return ProgressEvent(kind=ProgressEventKind.NOOP, raw_evidence=evidence)
        # False pivot guard: completion/blocker words without new topic
        if re.search(r"(?i)\b(done|finished|completed|blocked|stuck|skip|failing)\b", q):
            return ProgressEvent(kind=ProgressEventKind.NOOP, raw_evidence=evidence)

        # Look for explicit target topic
        topic = ""
        tm = _PIVOT_TOPIC_EXTRACT.search(q)
        if tm:
            topic = tm.group(1).strip()
        else:
            # Fallback: strip the pivot preamble
            stripped_topic = re.sub(
                r"(?i)^(actually|instead|forget that,? let's|switch to|change (?:my |the )?goal to)\s*",
                "",
                q,
            ).strip()
            if len(stripped_topic) >= 4 and not stripped_topic.endswith("?"):
                topic = stripped_topic

        if topic and len(topic) >= 3:
            sanitized_topic = sanitize_progress_evidence(topic)
            return ProgressEvent(
                kind=ProgressEventKind.GOAL_PIVOT,
                target_step_index=0,
                raw_evidence=sanitized_topic,
                confidence=PlanConfidence.HIGH,
            )

    return ProgressEvent(kind=ProgressEventKind.NOOP, raw_evidence=evidence)
