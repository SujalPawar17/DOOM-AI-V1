"""Deterministic intent normalizer. Class only; no executable parameters."""

from __future__ import annotations

import re

from orchestration.goal.types import IntentClass

_CONVERSATION = re.compile(
    r"^(hello|hi|hey|hey doom|thanks|thank you|good (morning|afternoon|evening)|"
    r"how are you|who are you|what is your name|what can you do)[\s!.?]*$",
    re.IGNORECASE,
)

_CONVERSATION_TOPIC = re.compile(
    r"\b("
    r"what('?s| is| are| am i)|whats|who('?s| is| am i)|"
    r"why (is|are|do|does|can)|"
    r"how (does|do|can|to|is|are)|explain|define|tell me|"
    r"help me understand|can you explain|what does .+ mean|"
    r"what (project|language|colour|color|name|preference)s?|"
    r"what .+ do i prefer|do i prefer|give me advice|considering\b|"
    r"should i|what should i|recommend|brief advice|"
    r"heavy (ai|workload|task)|right now\b"
    r")\b",
    re.IGNORECASE,
)

_GREETING_PREFIX = re.compile(r"^(hello|hi|hey)\b", re.IGNORECASE)

_CONVERSATION_SAY = re.compile(
    r"^\s*(say|repeat|reply with|respond with)\b",
    re.IGNORECASE,
)
_CONVERSATION_CODE = re.compile(
    r"^\s*(write|show|give me|provide)\s+"
    r"(a |an |me )*(simple |short |small )?"
    r"((python|javascript|java)\s+)?"
    r"(function|code|snippet|program|example)\b",
    re.IGNORECASE,
)

# V8.21: short conversational continuations (context resolved in RESPOND).
# Do NOT match bare STT junk like "what" / "okay" / "doom".
_CONVERSATION_CONTINUATION = re.compile(
    r"^(why(\s+is\s+that)?|"
    r"why is (it|that|this)\b|"
    r"tell me more|"
    r"explain (that|more|the last part)|"
    r"what did you mean|what do you mean|"
    r"continue|go on|keep going|carry on|"
    r"what about (the )?(first|second|third|1st|2nd|3rd|other)( (one|option))?|"
    r"what about .{1,60}|"
    r"compare (that|it|this|them)( with .*)?|"
    r"is that better|is this better|is it better|"
    r"which one|which is better|which would you (choose|pick)|"
    r"the other one|"
    r"that|this|it|"
    r"elaborate|go deeper|more details?"
    r")[\s?.!]*$",
    re.IGNORECASE,
)

_INSTRUCTIONAL = re.compile(
    r"^\s*(how (do i|does one|can i|to)|what is the (best )?way to|"
    r"help me (to )?(learn|understand how))\b",
    re.IGNORECASE,
)

_OBSERVE = (
    re.compile(r"what('?s| is) on (my )?screen", re.I),
    re.compile(r"\bobserve (the )?(screen|desktop)\b", re.I),
)

_COMPUTER_ACT = (
    re.compile(r"\b(notepad|calculator|calc\.exe)\b", re.I),
    re.compile(r"\bclick\b", re.I),
    re.compile(r"\btype\b.+\b(into|in)\b", re.I),
    re.compile(r"\btype hello\b", re.I),
    re.compile(r"\bpress\b.+\bbutton\b", re.I),
    re.compile(r"\bopen notepad\b", re.I),
    re.compile(r"\bopen\s+task\s*manager\b", re.I),
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
    re.compile(r"\bwhat did i ask you to remember\b", re.I),
    re.compile(r"\bwhat have you remembered\b", re.I),
    re.compile(r"\brecall\b", re.I),
    re.compile(r"\bread my (saved )?memory\b", re.I),
)

_MEMORY_SAVE = re.compile(
    r"^\s*(please\s+)?(remember|save this|store this|save that|store that)\b",
    re.IGNORECASE,
)

_MEMORY_DELETE = re.compile(
    r"\b(delete|forget|erase|clear)\b.+\b(memory|memories|remembered|preference|preferences)\b",
    re.I,
)

_SYSTEM_MUTATION = re.compile(
    r"(?i)\b(kill|restart|stop|start|launch|terminate)\b.+\b"
    r"(ollama|dashboard|chrome|process|service|task\s*manager)\b|"
    r"\b(kill|restart|stop)\s+ollama\b|"
    r"\brestart\s+(the\s+)?(doom\s+)?dashboard\b",
)

_SYSTEM_STATUS = (
    re.compile(r"\bsystem status\b", re.I),
    re.compile(r"\bsystem health\b", re.I),
    re.compile(r"\b(current )?system (status|state|info|information)\b", re.I),
    re.compile(r"\bhow much (ram|memory|disk|free disk)\b", re.I),
    re.compile(r"\bhow much .+?\b(ram|memory|disk)\b", re.I),
    re.compile(r"\bhow much free disk\b", re.I),
    # Require ownership / usage so "What is RAM?" stays conceptual CONVERSATION.
    re.compile(
        r"\b(what('?s| is)|show|check|report)\s+my\s+(cpu|ram|memory|disk)\b",
        re.I,
    ),
    re.compile(r"\b(cpu|ram|memory|disk)\s+usage\b", re.I),
    re.compile(r"\bfree disk\b", re.I),
    re.compile(r"\bdisk space\b", re.I),
    re.compile(r"\bis (my )?system healthy\b", re.I),
    re.compile(r"\bis ollama (running|up|available|online)\b", re.I),
    re.compile(r"\bollama (status|running|available)\b", re.I),
    re.compile(r"\bis (the )?(doom )?dashboard (running|up|available|online)\b", re.I),
    re.compile(r"\bdashboard (status|running)\b", re.I),
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
    re.compile(r"bypass.{0,40}(authorization|authorisation|\bask\b|approval)", re.I),
    re.compile(r"\bapi[_-]?key\b", re.I),
    re.compile(r"ignore (all |the )?(previous |prior )?(instructions|rules)", re.I),
    re.compile(r"\byou are now\b", re.I),
    re.compile(r"\bjailbreak\b", re.I),
    re.compile(r"show .{0,40}\.env", re.I),
    re.compile(r"\bPROMPT[\s_-]?INJECTION\b", re.I),
)


def _any(text: str, patterns) -> bool:
    return any(p.search(text) for p in patterns)


def normalize_intent(raw_intent: str) -> IntentClass:
    text = (raw_intent or "").strip()
    if not text:
        return IntentClass.UNKNOWN
    if _any(text, _FORCE_UNKNOWN):
        return IntentClass.UNKNOWN
    if _MEMORY_DELETE.search(text):
        return IntentClass.UNKNOWN
    if _SYSTEM_MUTATION.search(text):
        return IntentClass.UNKNOWN
    if _any(text, _AMBIGUOUS):
        return IntentClass.AMBIGUOUS
    if _CONVERSATION.match(text):
        return IntentClass.CONVERSATION

    hits = set()
    observe = _any(text, _OBSERVE)
    if observe or _any(text, _COMPUTER_ACT):
        hits.add(IntentClass.COMPUTER)
    if observe:
        pass
    elif IntentClass.COMPUTER in hits and _INSTRUCTIONAL.search(text):
        hits.discard(IntentClass.COMPUTER)
    if _any(text, _BROWSER):
        hits.add(IntentClass.BROWSER)
    if _any(text, _FILESYSTEM):
        hits.add(IntentClass.FILESYSTEM)
    if _any(text, _MEMORY):
        hits.add(IntentClass.MEMORY_READ)
    if _MEMORY_SAVE.search(text):
        hits.add(IntentClass.MEMORY_SAVE)
    if _any(text, _WORLD):
        hits.add(IntentClass.WORLD_ACTION)
    if _any(text, _SYSTEM_STATUS):
        hits.add(IntentClass.SYSTEM_STATUS)

    executable = hits & {
        IntentClass.COMPUTER,
        IntentClass.BROWSER,
        IntentClass.FILESYSTEM,
        IntentClass.WORLD_ACTION,
    }
    if len(executable) > 1:
        return IntentClass.AMBIGUOUS
    # Executable intents dominate memory/system/conversation.
    if len(executable) == 1:
        return next(iter(executable))
    if IntentClass.MEMORY_SAVE in hits and IntentClass.MEMORY_READ in hits:
        return IntentClass.AMBIGUOUS
    if IntentClass.MEMORY_SAVE in hits:
        return IntentClass.MEMORY_SAVE
    if IntentClass.MEMORY_READ in hits:
        return IntentClass.MEMORY_READ
    if IntentClass.SYSTEM_STATUS in hits:
        return IntentClass.SYSTEM_STATUS
    if len(hits) == 1:
        return next(iter(hits))
    if len(hits) > 1:
        return IntentClass.AMBIGUOUS
    # V8.22: narrow decision / trade-off questions (before broad conversation).
    try:
        from orchestration.decision.relevance import decision_relevant
        if decision_relevant(text):
            return IntentClass.DECISION
    except Exception:
        pass
    if _CONVERSATION_TOPIC.search(text):
        return IntentClass.CONVERSATION
    if _GREETING_PREFIX.search(text):
        return IntentClass.CONVERSATION
    if _CONVERSATION_SAY.search(text) or _CONVERSATION_CODE.search(text):
        return IntentClass.CONVERSATION
    if _CONVERSATION_CONTINUATION.match(text):
        return IntentClass.CONVERSATION
    return IntentClass.UNKNOWN
