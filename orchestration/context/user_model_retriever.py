"""V8.8 read-only user model retrieval. One bounded pass. Never executes or authorizes.

External execution and historical context are separate systems.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

from orchestration.context.errors import InvalidContextRequest
from orchestration.context.user_model_adapter import UserModelAdapter, UserModelHit
from orchestration.context.policy import (
    DEFAULT_POLICY,
    MAX_ID_CHARS,
    MAX_QUERY_CHARS,
    ContextPolicy,
)
from proactive.config import is_v8_context_enabled

_FORBIDDEN_REQUEST_ATTRS = frozenset({
    "eval", "exec", "command", "tool_name", "callable", "function", "callback",
})


def retrieve_user_model_context(
    request: Any,
    user_model_reader: Optional[UserModelAdapter] = None,
    policy: Optional[ContextPolicy] = None,
) -> Tuple[Tuple[UserModelHit, ...], int, bool, str, Tuple[str, ...]]:
    """Retrieve bounded user model data. Does not execute, write, or authorize."""
    if not is_v8_context_enabled():
        return ((), 0, False, ContextStatus.DISABLED.value, ())
    try:
        req = _validate_request(request)
    except InvalidContextRequest:
        return ((), 0, False, ContextStatus.INVALID_REQUEST.value, ())
    
    limits = policy or ContextPolicy(
        max_items=req.max_items,
        max_memory_items=req.max_memory_items,
        max_experience_items=req.max_experience_items,
    )
    errors: List[str] = []
    um_hits: Tuple[Any, ...] = ()
    um_failed = False

    if user_model_reader is not None:
        try:
            um_hits = user_model_reader.read(
                query=req.query,
                owner_id=req.owner_id,
                session_id=req.session_id,
                goal_id=req.goal_id,
                limit=limits.max_memory_items,  # Reuse max_memory_items for user model limit
            )
        except Exception:
            um_failed = True
            errors.append("USER_MODEL_UNAVAILABLE")

    if um_failed:
        return ((), 0, False, ContextStatus.CONTEXT_UNAVAILABLE.value, tuple(errors))
    
    items, truncated = _assemble_user_model(um_hits, limits)
    um_n = len(items)
    if not items and not errors:
        status = ContextStatus.EMPTY.value
    else:
        status = ContextStatus.OK.value
    return (items, um_n, truncated, status, tuple(errors))


def _validate_request(request: Any) -> Any:
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


def _assemble_user_model(
    um_hits: Tuple[Any, ...],
    limits: ContextPolicy,
) -> Tuple[Tuple[Any, ...], bool]:
    # Sort by relevance descending, then by other deterministic fields
    candidates: List[UserModelHit] = list(um_hits)
    candidates.sort(
        key=lambda i: (-i[2], i[5], i[0], i[3]),  # -relevance, owner_id, reference_id, timestamp
    )
    deduped: List[UserModelHit] = []
    seen = set()
    for hit in candidates:
        key = (hit[5], hit[0])  # (owner_id, reference_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hit)
    
    selected: List[UserModelHit] = []
    total_size = 0
    truncated = False
    for hit in deduped:
        # Estimate size as length of content (we could be more accurate)
        content_size = len(hit[1]) if hit[1] else 0
        if total_size + content_size > limits.max_total_chars:
            truncated = True
            break
        selected.append(hit)
        total_size += content_size
        if len(selected) >= limits.max_items:
            truncated = True
            break
    return tuple(selected), truncated


# We need to import ContextRequest and ContextStatus from orchestration.context.types
# But to avoid circular imports, we will define them locally or import from the types module.
# Since we are in a new file, we can import from orchestration.context.types.
from orchestration.context.types import ContextRequest, ContextStatus