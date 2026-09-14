"""RESPOND-only Safe Context. Read-only data. Never authorizes or executes."""

from __future__ import annotations

import re
import threading
from typing import Any, List, Optional, Tuple

from orchestration.context.memory_adapter import MemoryAdapter
from orchestration.context.policy import ContextPolicy
from orchestration.context.retriever import retrieve_context
from orchestration.context.types import ContextRequest, ContextResult, ContextStatus
from orchestration.conversation.prompt import CONTEXT_DATA_NOTICE
from orchestration.goal.plan_types import GoalPlan

RESPOND_MAX_ITEMS = 4
RESPOND_MAX_ITEM_CHARS = 280
RESPOND_MAX_TOTAL_CHARS = 800
RESPOND_MAX_CONTEXT_BLOCK = 800
MAX_CONVERSATION_TURNS = 0
RESPOND_POLICY = ContextPolicy(
    max_items=RESPOND_MAX_ITEMS,
    max_memory_items=RESPOND_MAX_ITEMS,
    max_experience_items=0,
    max_item_chars=RESPOND_MAX_ITEM_CHARS,
    max_total_chars=RESPOND_MAX_TOTAL_CHARS,
)

_LOCK = threading.Lock()
_TEST_MEMORY: Any = None

_EXCLUDE = re.compile(
    r"(?i)("
    r"api[_-]?key|bearer\b|password|passwd|\bpwd\b|"
    r"cookie|csrf|private[_ -]?key|authorization|"
    r"session_id|session_id_hash|ask_session|"
    r"executionidentity|owner_id\s*[:=]|plan_hash|"
    r"authorized_plan_hash|computer_session|browser_session|\bbws_|"
    r"csrf_token|postgres|database.{0,12}(user|pass|cred)|"
    r"-----BEGIN|hidden system prompt|internal (security )?policy|"
    r"\bsecrets?\b"
    r")"
)

_SQLISH = re.compile(r"(?i)\b(select|insert|update|delete)\b.+\bfrom\b|\bcreate table\b")


def use_respond_memory_for_tests(adapter: Any) -> None:
    global _TEST_MEMORY
    with _LOCK:
        _TEST_MEMORY = adapter


def reset_respond_memory_for_tests() -> None:
    global _TEST_MEMORY
    with _LOCK:
        _TEST_MEMORY = None


def _memory_reader() -> Optional[MemoryAdapter]:
    with _LOCK:
        if _TEST_MEMORY is not None:
            return _TEST_MEMORY
    try:
        from memory.manager import memory_manager
        from orchestration.context.memory_adapter import bind_v5_retrieve
        return bind_v5_retrieve(memory_manager.retrieve)
    except Exception:
        return None


def _exclude_item(text: str) -> bool:
    blob = str(text or "")
    if not blob.strip():
        return True
    if "[REDACTED]" in blob or blob.strip() == "SENSITIVE_OMITTED":
        return True
    if _EXCLUDE.search(blob):
        return True
    if _SQLISH.search(blob):
        return True
    return False


def format_safe_context_block(contents: Tuple[str, ...]) -> str:
    lines = []
    for i, item in enumerate(contents, start=1):
        cleaned = " ".join(str(item).split())
        if not cleaned:
            continue
        lines.append(f"{i}. {cleaned[:RESPOND_MAX_ITEM_CHARS]}")
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > RESPOND_MAX_CONTEXT_BLOCK:
        body = body[:RESPOND_MAX_CONTEXT_BLOCK]
    return (
        f"{CONTEXT_DATA_NOTICE}\n"
        f"<safe_context>\n{body}\n</safe_context>"
    )


def load_respond_context(plan: Any, query: str) -> Tuple[str, str]:
    """Return (context_block, status). Status is diagnostic only; never authorizes.

    V8.19 delegates to the contextual assembler (memory + system + conversation + optional V5).
    """
    try:
        from orchestration.conversation.context import assemble_respond_context
        return assemble_respond_context(plan, query)
    except Exception:
        return "", "CONTEXT_UNAVAILABLE"
