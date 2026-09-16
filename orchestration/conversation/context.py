"""V8.19 bounded contextual assembly for RESPOND. Read-only. Untrusted data only."""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from orchestration.conversation.prompt import CONTEXT_DATA_NOTICE
from orchestration.goal.plan_types import GoalPlan

# Hard bounds (chars).
MAX_CONV_TURNS = 4
MAX_CONV_MSG_CHARS = 300
MAX_CONV_TOTAL_CHARS = 1000
MAX_MEMORY_SECTION_CHARS = 800
MAX_SYSTEM_SECTION_CHARS = 1200
MAX_TOTAL_CONTEXT_CHARS = 3000
MAX_ITEM_CHARS = 280
MAX_MEMORY_ITEMS = 4

_LOCK = threading.Lock()
# Trusted key: owner_id + session_id from plan only.
_CONV: Dict[str, List[Dict[str, str]]] = {}
# Continuation anchor freshness (seconds). Not persistent memory.
ANCHOR_TTL_SEC = 1200  # 20 minutes
_TEST_ANCHOR_TTL_SEC: Optional[float] = None

_EXCLUDE = re.compile(
    r"(?i)("
    r"api[_-]?key|bearer\b|password|passwd|\bpwd\b|"
    r"cookie|csrf|private[_ -]?key|authorization|"
    r"session_id|session_id_hash|ask_session|"
    r"executionidentity|owner_id\s*[:=]|plan_hash|"
    r"authorized_plan_hash|computer_session|browser_session|\bbws_|"
    r"csrf_token|postgres|database.{0,12}(user|pass|cred)|"
    r"-----BEGIN|hidden system prompt|internal (security )?policy|"
    r"\bsecrets?\b|pm_[a-f0-9]{8,}"
    r")"
)
_SQLISH = re.compile(r"(?i)\b(select|insert|update|delete)\b.+\bfrom\b|\bcreate table\b")
_DB_IDISH = re.compile(r"(?i)\b(memory_id|row_id|db_id|uuid)\s*[:=]\s*\S+")


def _conv_key(owner_id: str, session_id: str) -> str:
    o = str(owner_id or "").strip()[:64]
    s = str(session_id or "").strip()[:64]
    if not o or not s:
        return ""
    return f"{o}|{s}"


def reset_conversation_context_for_tests() -> None:
    global _TEST_ANCHOR_TTL_SEC
    with _LOCK:
        _CONV.clear()
    _TEST_ANCHOR_TTL_SEC = None


def set_anchor_ttl_for_tests(seconds: Optional[float]) -> None:
    global _TEST_ANCHOR_TTL_SEC
    _TEST_ANCHOR_TTL_SEC = seconds


def _anchor_ttl() -> float:
    if _TEST_ANCHOR_TTL_SEC is not None:
        return float(_TEST_ANCHOR_TTL_SEC)
    return float(ANCHOR_TTL_SEC)


def clear_conversation_thread(owner_id: str, session_id: str) -> None:
    """Drop in-memory turns for this owner/session. Does not touch personal memory."""
    key = _conv_key(owner_id, session_id)
    if not key:
        return
    with _LOCK:
        _CONV.pop(key, None)


def _last_assistant_ts(rows: List[Dict[str, str]]) -> float:
    for row in reversed(rows):
        if str(row.get("role") or "") == "assistant":
            try:
                return float(row.get("ts") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _anchor_exchange_rows(rows: List[Dict[str, str]]) -> Tuple[Dict[str, str], ...]:
    """Last user+assistant pair only (current anchor)."""
    if not rows:
        return ()
    last_user_idx = -1
    last_asst_idx = -1
    for i, row in enumerate(rows):
        role = str(row.get("role") or "")
        if role == "user" and str(row.get("text") or "").strip():
            last_user_idx = i
        elif role == "assistant" and str(row.get("text") or "").strip():
            last_asst_idx = i
    if last_asst_idx < 0 or last_user_idx < 0 or last_user_idx > last_asst_idx:
        return ()
    return tuple(rows[last_user_idx : last_asst_idx + 1])


def exclude_sensitive_text(text: str) -> bool:
    blob = str(text or "")
    if not blob.strip():
        return True
    if "[REDACTED]" in blob or blob.strip() == "SENSITIVE_OMITTED":
        return True
    if _EXCLUDE.search(blob):
        return True
    if _SQLISH.search(blob):
        return True
    if _DB_IDISH.search(blob):
        return True
    return False


def sanitize_context_text(text: str, *, limit: int = MAX_ITEM_CHARS) -> str:
    cleaned = " ".join(str(text or "").replace("\x00", "").split())
    if exclude_sensitive_text(cleaned):
        return ""
    return cleaned[: max(0, int(limit))]


def memory_relevant(query: str) -> bool:
    q = " ".join(str(query or "").lower().split())
    if not q:
        return False
    needles = (
        "what do you remember",
        "remember about me",
        "what did i ask you to remember",
        "about me",
        "my favorite",
        "my favourite",
        "i prefer",
        "do i prefer",
        "my preference",
        "my programming",
        "my language",
        "my project",
        "what am i building",
        "what project am i",
        "considering my prefer",
        "considering what you know",
        "my preferences",
        "what you know about me",
    )
    if any(n in q for n in needles):
        return True
    if re.search(r"\b(my|i|me)\b", q) and re.search(
        r"\b(prefer|favorite|favourite|language|project|building|ui|theme|remember)\b",
        q,
    ):
        return True
    return False


def system_relevant(query: str) -> bool:
    q = " ".join(str(query or "").lower().split())
    if not q:
        return False
    if re.search(
        r"\b(cpu|ram|disk|ollama|dashboard|system health|system status|"
        r"system state|computer state|machine state|free disk|disk space|"
        r"memory usage|cpu usage)\b",
        q,
    ):
        return True
    if re.search(r"\b(current )?(system|computer|machine)\b", q) and re.search(
        r"\b(status|health|state|usage|pressure|load)\b",
        q,
    ):
        return True
    if re.search(r"\bconsidering\b.+\b(system|computer|machine|ram|cpu)\b", q):
        return True
    if re.search(r"\bis (my )?system healthy\b", q):
        return True
    return False


def conversation_relevant(query: str, *, has_turns: bool) -> bool:
    """Include recent turns when present; skip for cold standalone code prompts."""
    if not has_turns:
        return False
    q = " ".join(str(query or "").lower().split())
    # Prefer less context for greetings / empty-context probes.
    if re.match(r"^(hello|hi|hey|thanks|thank you)[\s!.?]*$", q):
        return False
    # Prefer less context for cold instructional/code prompts with no personal cues.
    if re.search(
        r"^\s*(write|show|give me|provide)\s+(a |an |me )*(simple |short )?"
        r"((python|javascript|java)\s+)?(function|code|snippet|program|example)\b",
        q,
    ):
        return False
    if re.search(r"^\s*(explain|define)\s+what\s+(a |an )?\w+", q) and not re.search(
        r"\b(my|me|i|prefer|remember|system|cpu|ram)\b", q
    ):
        return False
    # V8.21 continuations always want thread context when turns exist.
    try:
        from orchestration.conversation.resolve import is_continuation_utterance
        if is_continuation_utterance(q):
            return True
    except Exception:
        pass
    return True


def record_conversation_turn(
    owner_id: str,
    session_id: str,
    *,
    user_text: str,
    assistant_text: str,
) -> None:
    key = _conv_key(owner_id, session_id)
    if not key:
        return
    user = sanitize_context_text(user_text, limit=MAX_CONV_MSG_CHARS)
    try:
        from orchestration.conversation.resolve import durable_assistant_thread_text
        assistant = durable_assistant_thread_text(
            assistant_text, limit=MAX_CONV_MSG_CHARS
        )
    except Exception:
        assistant = sanitize_context_text(assistant_text, limit=MAX_CONV_MSG_CHARS)
    if not user and not assistant:
        return
    now = str(time.time())
    with _LOCK:
        rows = _CONV.setdefault(key, [])
        if user:
            rows.append({"role": "user", "text": user, "ts": now})
        if assistant:
            rows.append({"role": "assistant", "text": assistant, "ts": now})
        # Keep at most MAX_CONV_TURNS message pairs ≈ 2*turns entries, trim by count + chars.
        while len(rows) > MAX_CONV_TURNS * 2:
            rows.pop(0)
        total = sum(len(r.get("text") or "") for r in rows)
        while rows and total > MAX_CONV_TOTAL_CHARS:
            removed = rows.pop(0)
            total -= len(removed.get("text") or "")


def get_conversation_turns(
    owner_id: str,
    session_id: str,
    *,
    for_continuation: bool = False,
) -> Tuple[Dict[str, str], ...]:
    key = _conv_key(owner_id, session_id)
    if not key:
        return ()
    with _LOCK:
        rows = list(_CONV.get(key, []))
    if for_continuation:
        if not rows:
            return ()
        last_ts = _last_assistant_ts(rows)
        if last_ts <= 0.0 or (time.time() - last_ts) > _anchor_ttl():
            return ()
        rows = list(_anchor_exchange_rows(rows))
        if not rows:
            return ()
    out: List[Dict[str, str]] = []
    budget = MAX_CONV_TOTAL_CHARS
    for row in reversed(rows):
        text = sanitize_context_text(row.get("text") or "", limit=MAX_CONV_MSG_CHARS)
        if not text:
            continue
        if len(text) > budget:
            continue
        out.append({"role": str(row.get("role") or "user")[:16], "text": text})
        budget -= len(text)
        if len(out) >= MAX_CONV_TURNS * 2:
            break
    out.reverse()
    return tuple(out)


def _load_personal_section(owner_id: str, query: str) -> str:
    if not memory_relevant(query):
        return ""
    profile_texts: List[str] = []
    try:
        from orchestration.user_model.config import is_v827_user_model_enabled
        from orchestration.user_model.resolve import profile_strings_for_consumer

        if is_v827_user_model_enabled():
            # Conversation bias: communication + preference, max 4.
            profile_texts = list(
                profile_strings_for_consumer(
                    owner_id, "CONVERSATION", query=query, limit=MAX_MEMORY_ITEMS
                )
            )
    except Exception:
        profile_texts = []

    mem_texts: List[str] = []
    try:
        from orchestration.conversation.personal_memory import (
            list_personal_memories,
            search_personal_memories,
        )
        hits = search_personal_memories(owner_id, query, limit=MAX_MEMORY_ITEMS)
        if not hits:
            # Preference/advice phrasing may not lexically overlap stored facts.
            hits = list_personal_memories(owner_id, limit=MAX_MEMORY_ITEMS)
        for hit in hits:
            text = sanitize_context_text(
                getattr(hit, "content", "") or "", limit=MAX_ITEM_CHARS
            )
            if text:
                mem_texts.append(text)
    except Exception:
        mem_texts = []

    try:
        if profile_texts:
            from orchestration.user_model.resolve import merge_profile_then_memory

            merged = merge_profile_then_memory(
                profile_texts, mem_texts, limit=MAX_MEMORY_ITEMS
            )
        else:
            merged = mem_texts[:MAX_MEMORY_ITEMS]
    except Exception:
        merged = mem_texts[:MAX_MEMORY_ITEMS]

    lines: List[str] = []
    for i, text in enumerate(merged, start=1):
        if not text:
            continue
        lines.append(f"{i}. {text}")
        if len(lines) >= MAX_MEMORY_ITEMS:
            break
    if not lines:
        return ""
    body = "Personal memory:\n" + "\n".join(lines)
    return body[:MAX_MEMORY_SECTION_CHARS]


def _load_system_section(query: str) -> str:
    if not system_relevant(query):
        return ""
    try:
        from orchestration.system.observe import collect_system_observation
        obs = collect_system_observation(cpu_interval=0.05)
    except Exception:
        return ""
    disks = []
    for d in list(obs.disks)[:3]:
        disks.append(f"{d.label} {d.free_gb:.0f}/{d.total_gb:.0f} GB free")
    lines = [
        f"CPU: {obs.cpu_percent:.0f}%",
        (
            f"RAM: {obs.memory_used_gb:.1f} GB / {obs.memory_total_gb:.1f} GB "
            f"({obs.memory_percent:.0f}%)"
        ),
        "Disks: " + (", ".join(disks) if disks else "unavailable"),
        f"Ollama: {obs.ollama_status}",
        f"Dashboard: {obs.doom_dashboard_status}",
        f"Health: {obs.health}",
    ]
    body = "System observation:\n" + "\n".join(lines)
    if exclude_sensitive_text(body):
        return ""
    return body[:MAX_SYSTEM_SECTION_CHARS]


def _load_conversation_section(owner_id: str, session_id: str, query: str) -> str:
    turns = get_conversation_turns(owner_id, session_id)
    if not conversation_relevant(query, has_turns=bool(turns)):
        return ""
    lines: List[str] = []
    total = 0
    for row in turns:
        role = "User" if row.get("role") == "user" else "DOOM"
        text = row.get("text") or ""
        line = f"{role}: {text}"
        if total + len(line) > MAX_CONV_TOTAL_CHARS:
            break
        lines.append(line)
        total += len(line)
    if not lines:
        return ""
    return ("Recent conversation:\n" + "\n".join(lines))[:MAX_CONV_TOTAL_CHARS]


def _load_v5_section(plan: GoalPlan, query: str, remaining: int) -> str:
    if remaining <= 0:
        return ""
    try:
        from proactive.config import is_v8_context_enabled
        if not is_v8_context_enabled():
            return ""
        from orchestration.context.types import ContextRequest, ContextResult, ContextStatus
        from orchestration.context.retriever import retrieve_context
        from orchestration.conversation.safe_context import RESPOND_POLICY, _memory_reader
        request = ContextRequest(
            goal_id=str(plan.goal_id or "")[:64],
            owner_id=str(plan.owner_id or "")[:64],
            session_id=str(plan.session_id or "")[:64],
            normalized_intent="CONVERSATION",
            capability_class="conversation",
            query=str(query or "")[:512],
            max_items=MAX_MEMORY_ITEMS,
            max_memory_items=MAX_MEMORY_ITEMS,
            max_experience_items=0,
        )
        result = retrieve_context(request, _memory_reader(), None, policy=RESPOND_POLICY)
        if not isinstance(result, ContextResult) or result.status in (
            ContextStatus.DISABLED,
            ContextStatus.CONTEXT_UNAVAILABLE,
            ContextStatus.INVALID_REQUEST,
        ):
            return ""
        lines: List[str] = []
        for i, item in enumerate(result.items, start=1):
            text = sanitize_context_text(getattr(item, "content", "") or "", limit=MAX_ITEM_CHARS)
            if not text:
                continue
            lines.append(f"{i}. {text}")
            if len(lines) >= MAX_MEMORY_ITEMS:
                break
        if not lines:
            return ""
        body = "\n".join(lines)
        cap = min(remaining, 800)
        return body[:cap]
    except Exception:
        return ""


def assemble_respond_context(plan: Any, query: str) -> Tuple[str, str]:
    """Return (context_block, status). Never authorizes. Never mutates the system."""
    if type(plan) is not GoalPlan:
        return "", "CONTEXT_EMPTY"

    owner = str(plan.owner_id or "")[:64]
    session = str(plan.session_id or "")[:64]
    q = str(query or "")[:2048]

    parts: List[str] = []
    truncated = False

    personal = _load_personal_section(owner, q)
    if personal:
        parts.append(personal)

    system = _load_system_section(q)
    if system:
        parts.append(system)

    conv = _load_conversation_section(owner, session, q)
    if conv:
        parts.append(conv)

    used = sum(len(p) for p in parts)
    v5 = _load_v5_section(plan, q, MAX_TOTAL_CONTEXT_CHARS - used)
    if v5:
        parts.append(v5)

    if not parts:
        return "", "CONTEXT_EMPTY"

    body = "\n\n".join(parts)
    if len(body) > MAX_TOTAL_CONTEXT_CHARS:
        body = body[:MAX_TOTAL_CONTEXT_CHARS]
        truncated = True

    block = (
        f"{CONTEXT_DATA_NOTICE}\n"
        f"<safe_context>\n{body}\n</safe_context>"
    )
    if truncated or used >= MAX_TOTAL_CONTEXT_CHARS:
        return block, "CONTEXT_LIMITED"
    return block, "CONTEXT_OK"
