"""Deterministic intent normalizer. Class only; no executable parameters."""

from __future__ import annotations

import re

from orchestration.goal.types import IntentClass

_CONVERSATION = re.compile(
    r"^(hello|hi|hey|hey doom|thanks|thank you|good (morning|afternoon|evening)|"
    r"how are you|who are you|what is your name|what can you do)[\s!.?]*$",
    re.IGNORECASE,
)

_COMPUTER = (
    re.compile(r"\b(notepad|calculator|calc\.exe)\b", re.I),
    re.compile(r"\bclick\b", re.I),
    re.compile(r"\btype\b.+\b(into|in)\b", re.I),
    re.compile(r"\btype hello\b", re.I),
    re.compile(r"\bpress\b.+\bbutton\b", re.I),
    re.compile(r"\bopen notepad\b", re.I),
    re.compile(r"what('?s| is) on (my )?screen", re.I),
    re.compile(r"\bobserve (the )?(screen|desktop)\b", re.I),
)

_BROWSER = (
    re.compile(r"\b(website|webpage|browser)\b", re.I),
    re.compile(r"\bhttps?://", re.I),
    re.compile(r"\bopen this website\b", re.I),
    re.compile(r"\bnavigate to\b", re.I),
)

_FILESYSTEM = (
    re.compile(r"\blist (this )?(folder|directory)\b", re.I),
    re.compile(r"\b(folder|directory)\b", re.I),
    re.compile(r"\bread this file\b", re.I),
    re.compile(r"\bwrite this file\b", re.I),
    re.compile(r"\blist files\b", re.I),
    re.compile(r"\bdelete .+", re.I),
    re.compile(r"\b(rm -rf|rmdir)\b", re.I),
)

_MEMORY = (
    re.compile(r"\bsaved memory\b", re.I),
    re.compile(r"\bmemory about\b", re.I),
    re.compile(r"\bwhat do you remember\b", re.I),
    re.compile(r"\brecall\b", re.I),
    re.compile(r"\bread my (saved )?memory\b", re.I),
)

_WORLD = (
    re.compile(r"\bcalendar hold\b", re.I),
    re.compile(r"\bcreate a calendar hold\b", re.I),
)

_AMBIGUOUS = (
    re.compile(r"^open this[\s!.?]*$", re.I),
    re.compile(r"\bdo something with this file\b", re.I),
    re.compile(r"\bdownload this file\b", re.I),
)

_FORCE_UNKNOWN = (
    re.compile(r"javascript:", re.I),
    re.compile(r"file://", re.I),
    re.compile(r"\bdata:", re.I),
    re.compile(r"\beval\s*\(", re.I),
    re.compile(r"\bexec\s*\(", re.I),
    re.compile(r"__import__", re.I),
    re.compile(r"\bsubprocess\b", re.I),
    re.compile(r"python\s+-c\b", re.I),
    re.compile(r"\bexecute_(fs|computer|browser)_action\b", re.I),
    re.compile(r"\bimport os\b", re.I),
    re.compile(r"\bopen terminal\b", re.I),
)


def _any(text: str, patterns) -> bool:
    return any(p.search(text) for p in patterns)


def normalize_intent(raw_intent: str) -> IntentClass:
    text = (raw_intent or "").strip()
    if not text:
        return IntentClass.UNKNOWN
    if _any(text, _FORCE_UNKNOWN):
        return IntentClass.UNKNOWN
    if _any(text, _AMBIGUOUS):
        return IntentClass.AMBIGUOUS
    if _CONVERSATION.match(text):
        return IntentClass.CONVERSATION

    hits = set()
    if _any(text, _COMPUTER):
        hits.add(IntentClass.COMPUTER)
    if _any(text, _BROWSER):
        hits.add(IntentClass.BROWSER)
    if _any(text, _FILESYSTEM):
        hits.add(IntentClass.FILESYSTEM)
    if _any(text, _MEMORY):
        hits.add(IntentClass.MEMORY_READ)
    if _any(text, _WORLD):
        hits.add(IntentClass.WORLD_ACTION)

    executable = hits & {
        IntentClass.COMPUTER,
        IntentClass.BROWSER,
        IntentClass.FILESYSTEM,
        IntentClass.WORLD_ACTION,
    }
    if len(executable) > 1:
        return IntentClass.AMBIGUOUS
    if len(hits) == 1:
        return next(iter(hits))
    if len(hits) > 1:
        return IntentClass.AMBIGUOUS
    return IntentClass.UNKNOWN
