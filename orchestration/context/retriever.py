"""V8.8 read-only context retrieval. One bounded pass. Never executes or authorizes.

context_hash is diagnostic identity only. It is not plan_hash and not authorization.
External execution and historical context are separate systems.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from orchestration.context.errors import InvalidContextRequest
from orchestration.context.experience_adapter import ExperienceAdapter
from orchestration.context.memory_adapter import MemoryAdapter
from orchestration.context.policy import (
    DEFAULT_POLICY,
    MAX_ID_CHARS,
    MAX_QUERY_CHARS,
    ContextPolicy,
)
from orchestration.context.sanitizer import sanitize_text
from orchestration.context.types import (
    ContextItem,
    ContextRequest,
    ContextResult,
    ContextSource,
    ContextStatus,
    hash_context_items,
)
from proactive.config import is_v8_context_enabled

_FORBIDDEN_REQUEST_ATTRS = frozenset({
    "eval", "exec", "command", "tool_name", "callable", "function", "callback",
})


def retrieve_context(
    request: Any,
    memory_reader: Optional[MemoryAdapter] = None,
    experience_reader: Optional[ExperienceAdapter] = None,
    policy: Optional[ContextPolicy] = None,
) -> ContextResult:
    """Retrieve bounded historical DATA. Does not execute, write, or authorize."""
    if not is_v8_context_enabled():
        return _result((), 0, 0, False, ContextStatus.DISABLED, ())
    try:
        req = _validate_request(request)
    except InvalidContextRequest:
        return _result((), 0, 0, False, ContextStatus.INVALID_REQUEST, ())

    limits = policy or ContextPolicy(
        max_items=req.max_items,
        max_memory_items=req.max_memory_items,
        max_experience_items=req.max_experience_items,
    )
    errors: List[str] = []
    memory_hits: Tuple[Any, ...] = ()
    exp_hits: Tuple[Any, ...] = ()
    memory_failed = False
    experience_failed = False

    if memory_reader is not None:
        try:
            memory_hits = memory_reader.read(
                query=req.query,
                owner_id=req.owner_id,
                session_id=req.session_id,
                goal_id=req.goal_id,
                limit=limits.max_memory_items,
            )
        except Exception:
            memory_failed = True
            errors.append("MEMORY_UNAVAILABLE")
    if experience_reader is not None:
        try:
            exp_hits = experience_reader.read(
                owner_id=req.owner_id,
                session_id=req.session_id,
                goal_id=req.goal_id,
                query=req.query,
                limit=limits.max_experience_items,
            )
        except Exception:
            experience_failed = True
            errors.append("EXPERIENCE_UNAVAILABLE")

    if memory_failed and experience_failed:
        return _result((), 0, 0, False, ContextStatus.CONTEXT_UNAVAILABLE, tuple(errors))
    if memory_reader is not None and experience_reader is not None and memory_failed and not experience_failed:
        status_hint = ContextStatus.PARTIAL
    elif memory_reader is not None and experience_reader is not None and experience_failed and not memory_failed:
        status_hint = ContextStatus.PARTIAL
    else:
        status_hint = None

    items, truncated = _assemble(memory_hits, exp_hits, limits)
    mem_n = sum(1 for i in items if i.source is ContextSource.MEMORY)
    exp_n = sum(1 for i in items if i.source is ContextSource.EXPERIENCE)
    if not items and not errors:
        status = ContextStatus.EMPTY
    elif status_hint is not None:
        status = status_hint
    else:
        status = ContextStatus.OK
    return _result(items, mem_n, exp_n, truncated, status, tuple(errors))


def _validate_request(request: Any) -> ContextRequest:
    if type(request) is not ContextRequest:
        raise InvalidContextRequest()
    for name in _FORBIDDEN_REQUEST_ATTRS:
        if hasattr(request, name) and name not in ContextRequest.__dataclass_fields__:
            raise InvalidContextRequest()
    if any(callable(getattr(request, f)) for f in ContextRequest.__dataclass_fields__):
        raise InvalidContextRequest()
    for field in ("goal_id", "owner_id", "session_id"):
        value = str(getattr(request, field) or "")
        if len(value) > MAX_ID_CHARS:
            raise InvalidContextRequest("IDENTIFIER_TOO_LONG")
        if "\x00" in value:
            raise InvalidContextRequest("INVALID_IDENTIFIER")
    query = str(request.query or "")
    if len(query) > MAX_QUERY_CHARS or "\x00" in query:
        raise InvalidContextRequest("INVALID_QUERY")
    if int(request.max_items) < 0 or int(request.max_memory_items) < 0 or int(request.max_experience_items) < 0:
        raise InvalidContextRequest("INVALID_LIMIT")
    return request


def _assemble(
    memory_hits: Tuple[Any, ...],
    exp_hits: Tuple[Any, ...],
    limits: ContextPolicy,
) -> Tuple[Tuple[ContextItem, ...], bool]:
    candidates: List[ContextItem] = []
    for hit in memory_hits:
        ref, content, relevance, ts, provenance, _owner = hit
        text, item_trunc = sanitize_text(content, limits.max_item_chars)
        candidates.append(ContextItem(
            source=ContextSource.MEMORY,
            reference_id=str(ref)[:MAX_ID_CHARS],
            content=text,
            relevance=float(relevance),
            outcome="",
            provenance=str(provenance)[:64],
            timestamp_unix_ms=int(ts),
            truncated=item_trunc,
        ))
    for hit in exp_hits:
        ref, content, outcome, provenance, ts, _owner, relevance = hit
        text, item_trunc = sanitize_text(content, limits.max_item_chars)
        candidates.append(ContextItem(
            source=ContextSource.EXPERIENCE,
            reference_id=str(ref)[:MAX_ID_CHARS],
            content=text,
            relevance=float(relevance),
            outcome=str(outcome)[:32],
            provenance=str(provenance)[:64],
            timestamp_unix_ms=int(ts),
            truncated=item_trunc,
        ))
    candidates.sort(
        key=lambda i: (-i.relevance, i.source.value, i.reference_id, i.timestamp_unix_ms),
    )
    deduped: List[ContextItem] = []
    seen = set()
    for item in candidates:
        key = (item.source.value, item.reference_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    selected: List[ContextItem] = []
    total = 0
    truncated = any(i.truncated for i in deduped)
    mem_used = 0
    exp_used = 0
    for item in deduped:
        if len(selected) >= limits.max_items:
            truncated = True
            break
        if item.source is ContextSource.MEMORY and mem_used >= limits.max_memory_items:
            truncated = True
            continue
        if item.source is ContextSource.EXPERIENCE and exp_used >= limits.max_experience_items:
            truncated = True
            continue
        size = len(item.content)
        if total + size > limits.max_total_chars:
            truncated = True
            continue
        selected.append(item)
        total += size
        if item.source is ContextSource.MEMORY:
            mem_used += 1
        else:
            exp_used += 1
    return tuple(selected), truncated


def _result(
    items: Tuple[ContextItem, ...],
    memory_count: int,
    experience_count: int,
    truncated: bool,
    status: ContextStatus,
    errors: Tuple[str, ...],
) -> ContextResult:
    return ContextResult(
        items=items,
        memory_count=memory_count,
        experience_count=experience_count,
        truncated=truncated,
        status=status,
        source_errors=errors,
        context_hash=hash_context_items(items),
    )
