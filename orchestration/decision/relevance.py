"""V8.22 decision relevance. Narrow — not every 'should I'."""

from __future__ import annotations

import re

from orchestration.conversation.personal_memory import is_simple_personal_fact_question
from orchestration.situation.relevance import situation_relevant

# Explicit comparative / choice frames (require alternatives or binary choose).
_OR_PAIR = re.compile(
    r"(?i)\b("
    r"which (is|are|would be) better|"
    r"which should i (use|choose|pick)|"
    r"which (one )?(to use|to choose)|"
    r"should i (use|choose|pick|go with)|"
    r"should i choose|"
    r"choose between|"
    r"choose (from|among)|"
    r"pick between|"
    r"recommend|"
    r"what are the trade[- ]?offs|"
    r"trade[- ]?offs? between|"
    r"vs\.?|versus"
    r")\b"
)

_DECISION_CONTINUATION = re.compile(
    r"(?i)^(what should i use|which (one )?should i (use|choose|pick)|"
    r"which (is|would be) better|which one)[\s?.!]*$"
)

# Require two alternatives joined by or / versus / between A and B.
_HAS_ALTERNATIVES = re.compile(
    r"(?i)("
    r"\bor\b|"
    r"\bvs\.?\b|"
    r"\bversus\b|"
    r"\bbetween\b.+\band\b|"
    r"\b[12][.)]\s*\S|"
    r"\b[a-c][.)]\s*\S|"
    r"^\s*[-*•]\s+\S"
    r")"
)

# Pure situation advisory without named alternatives (keep V8.20 RESPOND).
_SITUATION_ONLY = re.compile(
    r"(?i)\b("
    r"heavy (ai|workload|task)|"
    r"right now|"
    r"current computer|"
    r"considering (my|what you know)"
    r")\b"
)


def decision_relevant(query: str) -> bool:
    """True only for narrow choice / trade-off questions with alternatives."""
    q = " ".join(str(query or "").strip().split())
    if not q:
        return False
    if is_simple_personal_fact_question(q):
        return False
    if re.match(r"(?i)^(what is|what's|explain|define|tell me about)\b", q):
        # "What are the trade-offs between X and Y" is allowed via _OR_PAIR.
        if not re.search(r"(?i)trade[- ]?offs?", q):
            return False
    if _DECISION_CONTINUATION.match(q):
        return True
    if not _OR_PAIR.search(q):
        return False
    # Situation-only advisories without alternatives stay V8.20.
    if situation_relevant(q) and not _HAS_ALTERNATIVES.search(q):
        return False
    if _SITUATION_ONLY.search(q) and not _HAS_ALTERNATIVES.search(q):
        return False
    if not _HAS_ALTERNATIVES.search(q) and not _DECISION_CONTINUATION.match(q):
        # "Recommend Python or C++" has or; "Recommend something" alone → no.
        if re.search(r"(?i)\brecommend\b", q) and not re.search(r"(?i)\bor\b|\bbetween\b", q):
            return False
        if not re.search(r"(?i)\bor\b|\bbetween\b|\bvs\b|\bversus\b", q):
            return False
    return bool(_HAS_ALTERNATIVES.search(q) or _DECISION_CONTINUATION.match(q))
