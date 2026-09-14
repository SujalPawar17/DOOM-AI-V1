"""V8.20 situational relevance. Prefer less context when uncertain."""

from __future__ import annotations

import re

from orchestration.conversation.context import memory_relevant, system_relevant
from orchestration.conversation.personal_memory import is_simple_personal_fact_question


def situation_relevant(query: str) -> bool:
    """True only for advisory / multi-source situational questions.

    Not for: pure conceptual explainers, simple personal-fact recall,
    pure V8.18 system-status reports, greetings, or code generation.
    """
    q = " ".join(str(query or "").lower().split())
    if not q:
        return False
    # Keep V8.19 deterministic fact path preferred — do not force situation.
    if is_simple_personal_fact_question(q):
        return False
    # Cold conceptual / code prompts: no situation.
    if re.search(
        r"^\s*(explain|define)\s+(what\s+)?(a |an |the )?\w+",
        q,
    ) and not re.search(r"\b(my|me|i|prefer|computer|system|ram|cpu)\b", q):
        return False
    if re.search(
        r"^\s*(write|show|give me|provide)\s+(a |an |me )*(simple |short )?"
        r"((python|javascript|java)\s+)?(function|code|snippet|program|example)\b",
        q,
    ):
        return False
    if re.match(r"^(hello|hi|hey|thanks|thank you)[\s!.?]*$", q):
        return False

    needles = (
        "should i",
        "should we",
        "what should i",
        "what should we",
        "give me advice",
        "brief advice",
        "recommend",
        "right now",
        "heavy ai",
        "heavy workload",
        "heavy task",
        "local ai",
        "considering my",
        "considering what you know",
        "considering my current computer",
        "current computer",
        "focus on",
        "do next",
        "what to do",
    )
    if any(n in q for n in needles):
        return True
    # Advice that mixes personal knowledge + computer/system.
    if memory_relevant(q) and system_relevant(q):
        return True
    if re.search(r"\b(advice|recommend|focus|workload)\b", q) and (
        system_relevant(q) or memory_relevant(q)
    ):
        return True
    return False


def situation_wants_memory(query: str) -> bool:
    q = " ".join(str(query or "").lower().split())
    if not situation_relevant(q):
        return False
    if memory_relevant(q):
        return True
    if any(
        n in q
        for n in (
            "what you know about me",
            "considering what you know",
            "my preferences",
            "about me",
        )
    ):
        return True
    return False


def situation_wants_system(query: str) -> bool:
    q = " ".join(str(query or "").lower().split())
    if not situation_relevant(q):
        return False
    if system_relevant(q):
        return True
    if any(
        n in q
        for n in (
            "right now",
            "heavy ai",
            "heavy workload",
            "heavy task",
            "current computer",
            "my computer",
            "should i run",
            "should i start",
        )
    ):
        return True
    return False


def situation_wants_conversation(query: str, *, has_turns: bool) -> bool:
    if not has_turns:
        return False
    if not situation_relevant(query):
        return False
    q = " ".join(str(query or "").lower().split())
    # Prefer less conversation noise for cold advisory status questions.
    if re.search(r"\b(should i run|heavy ai|right now)\b", q) and not re.search(
        r"\b(earlier|before|we discussed|you said)\b", q
    ):
        return False
    return True
