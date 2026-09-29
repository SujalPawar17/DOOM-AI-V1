"""Narrow V8 user model READ adapter. Never writes. No computer-kernel imports."""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from orchestration.context.errors import ContextUnavailable
from orchestration.context.policy import MAX_ID_CHARS

UserModelHit = Tuple[str, str, float, int, str, str, str, str]  # (reference_id, content, relevance, timestamp_unix_ms, provenance, owner_id, category, key)


class UserModelAdapter:
    """Read-only. The bound function must only retrieve/search."""

    __slots__ = ("_read_fn",)

    def __init__(self, read_fn: Callable[..., Any]) -> None:
        if not callable(read_fn):
            raise ContextUnavailable("USER_MODEL_UNAVAILABLE")
        self._read_fn = read_fn

    def read(
        self,
        *,
        query: str,
        owner_id: str,
        session_id: str,
        goal_id: str,
        limit: int,
    ) -> Tuple[UserModelHit, ...]:
        raw = self._read_fn(
            query=query,
            owner_id=owner_id,
            session_id=session_id,
            goal_id=goal_id,
            limit=int(limit),
        )
        return _normalize_user_model_hits(raw, owner_id=owner_id, limit=int(limit))


def _normalize_user_model_hits(raw: Any, *, owner_id: str, limit: int) -> Tuple[UserModelHit, ...]:
    rows: List[Any]
    if raw is None:
        rows = []
    elif isinstance(raw, (list, tuple)):
        rows = list(raw)
    else:
        # Assume it's an object with a 'entries' attribute (like ProfileResult)
        entries = getattr(raw, "entries", None)
        if entries is None:
            raise ContextUnavailable("USER_MODEL_UNAVAILABLE")
        rows = list(entries)
    out: List[UserModelHit] = []
    seen = set()
    for entry in rows:
        hit = _one_hit(entry, owner_id=owner_id)
        if hit is None:
            continue
        ref = hit[0]
        if ref in seen:
            continue
        seen.add(ref)
        out.append(hit)
        if len(out) >= limit:
            break
    return tuple(out)


def _one_hit(entry: Any, *, owner_id: str) -> Optional[UserModelHit]:
    if isinstance(entry, dict):
        ref = str(entry.get("entry_id") or entry.get("reference_id") or "")[:MAX_ID_CHARS]
        owner_from_entry = str(entry.get("owner_id") or "")
        category = str(entry.get("category") or "")
        content = str(entry.get("value") or "")
        provenance = str(entry.get("provenance") or entry.get("source") or "USER_MODEL")[:64]
        # Relevance will be computed by the bound function using the query; if not provided, default to 0.0
        relevance = float(entry.get("relevance") or 0.0)
        ts = int(entry.get("updated_at") or entry.get("created_at") or 0)
        key = str(entry.get("key") or "")[:MAX_ID_CHARS]
    else:
        ref = str(getattr(entry, "entry_id", "") or "")[:MAX_ID_CHARS]
        owner_from_entry = str(getattr(entry, "owner_id", "") or "")
        category = str(getattr(entry, "category", "") or "")
        content = str(getattr(entry, "value", "") or "")
        prov_obj = getattr(entry, "provenance", None)
        provenance = str(getattr(prov_obj, "value", prov_obj) or "")[:64] if prov_obj else "USER_MODEL"
        relevance = float(getattr(entry, "relevance", 0.0) or 0.0)
        ts = int(getattr(entry, "updated_at", 0) or getattr(entry, "created_at", 0) or 0)
        key = str(getattr(entry, "key", "") or "")[:MAX_ID_CHARS]
    
    # Determine the owner to use for this entry
    if owner_from_entry:
        # Entry has its own owner_id - check if it matches the target
        if owner_from_entry != owner_id:
            return None  # Wrong owner, filter out
        final_owner = owner_from_entry
    else:
        # Entry doesn't have owner_id (like ProfileEntry from list_profile_entries)
        # Assume it belongs to the target owner_id
        final_owner = owner_id
    
    if not ref:
        return None
    if relevance < 0:
        relevance = 0.0
    if relevance > 1:
        relevance = 1.0
    return (ref, content, relevance, ts, provenance, final_owner, category, key)


def bind_v8_user_model_retrieve(retrieve_fn: Callable[..., Any]) -> UserModelAdapter:
    """Wrap UserModelStore.list_profile_entries (or a test double) as a read-only adapter."""

    def _read(*, query: str, owner_id: str, session_id: str, goal_id: str, limit: int) -> Any:
        try:
            # The retrieve_fn is expected to be list_profile_entries or similar
            # We pass the query to it so it can score relevance
            return retrieve_fn(query=query, owner_id=owner_id, limit=limit)
        except TypeError:
            # Fallback for functions that don't take query
            return retrieve_fn(owner_id=owner_id, limit=limit)

    return UserModelAdapter(_read)