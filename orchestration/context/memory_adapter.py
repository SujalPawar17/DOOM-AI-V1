"""Narrow V5 memory READ adapter. Never returns MemoryManager. Never writes."""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from orchestration.context.errors import ContextUnavailable
from orchestration.context.policy import MAX_ID_CHARS

MemoryHit = Tuple[str, str, float, int, str, str]


class MemoryAdapter:
    """Read-only. The bound function must only retrieve/search."""

    __slots__ = ("_read_fn",)

    def __init__(self, read_fn: Callable[..., Any]) -> None:
        if not callable(read_fn):
            raise ContextUnavailable("MEMORY_UNAVAILABLE")
        self._read_fn = read_fn

    def read(
        self,
        *,
        query: str,
        owner_id: str,
        session_id: str,
        goal_id: str,
        limit: int,
    ) -> Tuple[MemoryHit, ...]:
        raw = self._read_fn(
            query=query,
            owner_id=owner_id,
            session_id=session_id,
            goal_id=goal_id,
            limit=int(limit),
        )
        return _normalize_memory_hits(raw, owner_id=owner_id, limit=int(limit))


def _normalize_memory_hits(raw: Any, *, owner_id: str, limit: int) -> Tuple[MemoryHit, ...]:
    rows: List[Any]
    if raw is None:
        rows = []
    elif isinstance(raw, (list, tuple)):
        rows = list(raw)
    else:
        memories = getattr(raw, "retrieved_memories", None)
        scores = getattr(raw, "relevance_scores", {}) or {}
        if memories is None:
            raise ContextUnavailable("MEMORY_UNAVAILABLE")
        rows = [(rec, scores) for rec in memories]
    out: List[MemoryHit] = []
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


def _one_hit(entry: Any, *, owner_id: str) -> Optional[MemoryHit]:
    scores: Any = {}
    rec: Any = entry
    if isinstance(entry, tuple) and len(entry) == 2 and not isinstance(entry[0], str):
        rec, scores = entry
    if isinstance(rec, dict):
        ref = str(rec.get("memory_id") or rec.get("reference_id") or "")[:MAX_ID_CHARS]
        content = str(rec.get("content") or "")
        owner = str(rec.get("owner_id") or "")
        provenance = str(rec.get("provenance") or rec.get("source") or "MEMORY")[:64]
        relevance = float(rec.get("relevance") or rec.get("score") or 0.0)
        ts = int(rec.get("timestamp_unix_ms") or 0)
    else:
        ref = str(getattr(rec, "memory_id", "") or "")[:MAX_ID_CHARS]
        content = str(getattr(rec, "content", "") or "")
        meta = getattr(rec, "metadata", None) or {}
        owner = str(meta.get("owner_id") or getattr(rec, "owner_id", "") or "")
        provenance = str(getattr(getattr(rec, "source", None), "value", getattr(rec, "source", "")) or "MEMORY")[:64]
        relevance = 0.0
        if isinstance(scores, dict):
            relevance = float(scores.get(ref, 0.0) or 0.0)
        ts = 0
        created = getattr(rec, "created_at", "") or ""
        if isinstance(created, str) and created[:4].isdigit():
            ts = _approx_ts(created)
    if not ref:
        return None
    if not owner or owner != owner_id:
        return None
    if relevance < 0:
        relevance = 0.0
    if relevance > 1:
        relevance = 1.0
    return (ref, content, relevance, ts, provenance, owner)


def _approx_ts(iso: str) -> int:
    digits = "".join(ch for ch in iso if ch.isdigit())
    try:
        return int(digits[:13] or "0")
    except ValueError:
        return 0


def bind_v5_retrieve(retrieve_fn: Callable[..., Any]) -> MemoryAdapter:
    """Wrap MemoryManager.retrieve (or a test double) as a read-only adapter."""

    def _read(*, query: str, owner_id: str, session_id: str, goal_id: str, limit: int) -> Any:
        try:
            return retrieve_fn(query=query, task_id=goal_id or None)
        except TypeError:
            return retrieve_fn(query=query)

    return MemoryAdapter(_read)
