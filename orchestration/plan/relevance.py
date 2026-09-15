"""V8.23 plan relevance. Narrow — requires planning frame and topic when needed."""

from __future__ import annotations

import re

from orchestration.decision.relevance import decision_relevant

_PLAN_CONTINUATION = re.compile(
    r"(?i)^(next steps|what next|how do i proceed|"
    r"what should i do next|what should i do)[\s?.!]*$"
)

_BROAD_NOT_PLAN = re.compile(
    r"(?i)^(help me|plan my life)[\s?.!]*$"
)

_MAKE_PLAN_ONLY = re.compile(
    r"(?i)^(make me a plan|make a plan|create a plan)[\s?.!]*$"
)

_PLAN_FRAME = re.compile(
    r"(?i)\b("
    r"what (are|is) (my )?next steps|"
    r"how (should|do) i (proceed|start|approach)|"
    r"break (this|it) down|"
    r"make (me )?a (plan|checklist)\s+for|"
    r"plan (how|for|to)\b|"
    r"plan how to"
    r")\b"
)

_TOPIC_FOR = re.compile(
    r"(?i)\b(?:next steps|plan|checklist)\s+for\s+(.+?)(?:[?.!]|$)"
)
_TOPIC_PLAN_TO = re.compile(
    r"(?i)\bplan (?:how )?to\s+(.+?)(?:[?.!]|$)"
)
_TOPIC_PLAN_HOW = re.compile(
    r"(?i)\bplan how to\s+(.+?)(?:[?.!]|$)"
)
_TOPIC_IMPROVING = re.compile(
    r"(?i)\bimproving\s+(.+?)(?:[?.!]|$)"
)


def plan_continuation(query: str) -> bool:
    q = " ".join(str(query or "").strip().split())
    return bool(_PLAN_CONTINUATION.match(q))


def _topic_from_query(q: str) -> str:
    for pat in (_TOPIC_FOR, _TOPIC_PLAN_HOW, _TOPIC_PLAN_TO, _TOPIC_IMPROVING):
        m = pat.search(q)
        if m:
            t = " ".join(m.group(1).strip().split())
            if len(t) >= 8:
                return t
    if re.search(r"(?i)\b(break (this|it) down|proceed with this|start this)\b", q):
        return ""
    if len(q) >= 45 and _PLAN_FRAME.search(q):
        return q[:300]
    return ""


def plan_relevant(query: str) -> bool:
    q = " ".join(str(query or "").strip().split())
    if not q:
        return False
    if decision_relevant(q):
        return False
    if _BROAD_NOT_PLAN.match(q):
        return False
    if _MAKE_PLAN_ONLY.match(q):
        return True
    if plan_continuation(q):
        return True
    if not _PLAN_FRAME.search(q):
        return False
    topic = _topic_from_query(q)
    if topic:
        return True
    if re.search(r"(?i)\b(break (this|it) down|how should i proceed|how do i start)\b", q):
        return True
    return False
