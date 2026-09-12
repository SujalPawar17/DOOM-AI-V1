"""Narrow V7.7 experience READ adapter. Never writes. No computer-kernel imports."""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from orchestration.context.errors import ContextUnavailable
from orchestration.context.policy import MAX_ID_CHARS, MAX_OUTCOME_CHARS, MAX_PROVENANCE_CHARS

ExperienceHit = Tuple[str, str, str, str, int, str, float]


class ExperienceAdapter:
    """Read-only. Bound function must only list/get historical records."""

    __slots__ = ("_list_fn",)

    def __init__(self, list_fn: Callable[..., Any]) -> None:
        if not callable(list_fn):
            raise ContextUnavailable("EXPERIENCE_UNAVAILABLE")
        self._list_fn = list_fn

    def read(
        self,
        *,
        owner_id: str,
        session_id: str,
        goal_id: str,
        query: str,
        limit: int,
    ) -> Tuple[ExperienceHit, ...]:
        raw = self._list_fn(
            owner_id=owner_id,
            session_id=session_id,
            goal_id=goal_id,
            query=query,
            limit=int(limit),
        )
        return _normalize_experience_hits(raw, owner_id=owner_id, limit=int(limit))


def _normalize_experience_hits(raw: Any, *, owner_id: str, limit: int) -> Tuple[ExperienceHit, ...]:
    if raw is None:
        rows: List[Any] = []
    elif isinstance(raw, (list, tuple)):
        rows = list(raw)
    else:
        raise ContextUnavailable("EXPERIENCE_UNAVAILABLE")
    out: List[ExperienceHit] = []
    seen = set()
    for rec in rows:
        hit = _one_hit(rec, owner_id=owner_id)
        if hit is None:
            continue
        if hit[0] in seen:
            continue
        seen.add(hit[0])
        out.append(hit)
        if len(out) >= limit:
            break
    return tuple(out)


def _one_hit(rec: Any, *, owner_id: str) -> Optional[ExperienceHit]:
    if isinstance(rec, dict):
        ref = str(rec.get("experience_id") or rec.get("reference_id") or "")[:MAX_ID_CHARS]
        owner = str(rec.get("owner_id") or "")
        outcome = str(rec.get("outcome") or "")[:MAX_OUTCOME_CHARS]
        provenance = str(rec.get("provenance") or "")[:MAX_PROVENANCE_CHARS]
        ts = int(rec.get("timestamp_unix_ms") or 0)
        capability = str(rec.get("capability") or "")
        action = str(rec.get("action") or "")
        verification = str(rec.get("verification_status") or "")
        sensitive = bool(rec.get("sensitive"))
        relevance = float(rec.get("relevance") or 0.0)
    else:
        ref = str(getattr(rec, "experience_id", "") or "")[:MAX_ID_CHARS]
        owner = str(getattr(rec, "owner_id", "") or "")
        outcome_obj = getattr(rec, "outcome", "")
        outcome = str(getattr(outcome_obj, "value", outcome_obj) or "")[:MAX_OUTCOME_CHARS]
        prov_obj = getattr(rec, "provenance", "")
        provenance = str(getattr(prov_obj, "value", prov_obj) or "")[:MAX_PROVENANCE_CHARS]
        ts = int(getattr(rec, "timestamp_unix_ms", 0) or 0)
        capability = str(getattr(rec, "capability", "") or "")
        action = str(getattr(rec, "action", "") or "")
        verification = str(getattr(rec, "verification_status", "") or "")
        sensitive = bool(getattr(rec, "sensitive", False))
        relevance = float(getattr(rec, "relevance", 0.0) or 0.0)
    if not ref:
        return None
    if not owner or owner != owner_id:
        return None
    if sensitive:
        content = "SENSITIVE_OMITTED"
    else:
        content = (
            "capability={0} action={1} outcome={2} verification={3}".format(
                capability, action, outcome, verification,
            )
        )
    if relevance < 0:
        relevance = 0.0
    if relevance > 1:
        relevance = 1.0
    return (ref, content, outcome, provenance, ts, owner, relevance)
