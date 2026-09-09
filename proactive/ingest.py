"""Typed ingest + normalize. Flag-off is a no-op. Malformed → drop."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from proactive.config import (
    OWNER_ID,
    TIME_BUCKET_SECONDS,
    is_proactive_enabled,
)
from proactive.fence import fence_payload
from proactive.otp import emit_proactive
from proactive.schemas import (
    PRIVACY_CLASSES,
    SIGNAL_TYPES,
    SOURCES,
    ProactiveSignal,
    compute_idempotency_key,
    time_bucket,
)
from proactive.store import proactive_store


class IngestError(ValueError):
    pass


def normalize_signal(
    signal_type: str,
    source: str,
    entity_type: str,
    entity_id: str,
    payload: Optional[Dict[str, Any]] = None,
    privacy_class: str = "NORMAL",
    occurred_at: Optional[float] = None,
    owner_id: str = OWNER_ID,
    extra_idem: str = "",
) -> Optional[ProactiveSignal]:
    st = str(signal_type or "").upper()
    src = str(source or "").lower()
    if st not in SIGNAL_TYPES or src not in SOURCES:
        return None
    if not entity_id or not str(entity_id).strip():
        return None
    pc = str(privacy_class or "NORMAL").upper()
    if pc not in PRIVACY_CLASSES:
        return None
    ts = occurred_at if occurred_at is not None else time.time()
    if ts <= 0 or ts > time.time() + 86400:
        return None
    fenced, dropped = fence_payload(payload or {})
    if dropped:
        return None
    if pc == "SENSITIVE":
        return None
    bucket = time_bucket(ts, TIME_BUCKET_SECONDS)
    key = compute_idempotency_key(src, str(entity_id), st, bucket, extra_idem)
    return ProactiveSignal(
        signal_type=st,
        source=src,
        entity_type=str(entity_type)[:40],
        entity_id=str(entity_id)[:120],
        occurred_at=ts,
        ingested_at=time.time(),
        privacy_class=pc,
        idempotency_key=key,
        payload=fenced,
        owner_id=owner_id,
        metadata={"time_bucket": bucket},
    )


def ingest_signal(**kwargs) -> str:
    """Enqueue a normalized signal. Returns signal_id or empty. Never raises."""
    try:
        if not is_proactive_enabled():
            return ""
        sig = normalize_signal(**kwargs)
        if sig is None:
            emit_proactive("proactive.signal.ingested", status="skipped", attributes={"reason": "dropped"})
            return ""
        sid = proactive_store.enqueue_signal(sig)
        emit_proactive(
            "proactive.signal.ingested",
            attributes={
                "signal_id": (sid or "")[:36],
                "signal_type": sig.signal_type,
                "privacy_class": sig.privacy_class,
                "owner_id": sig.owner_id,
            },
        )
        return sid
    except Exception:
        try:
            emit_proactive("proactive.signal.ingested", status="error")
        except Exception:
            pass
        return ""
