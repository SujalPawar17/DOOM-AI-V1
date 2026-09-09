"""DATA_ONLY fencing for V6.1 signals. Never treat payload as instruction."""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

from proactive.config import PAYLOAD_MAX_BYTES
from proactive.schemas import (
    CREDENTIAL_VALUE_MARKERS,
    FORBIDDEN_PAYLOAD_KEYS,
    INJECTION_MARKERS,
    dump_bounded_payload,
)

_ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\ufeff\u2060\u2061\u2062\u2063"), None)
_HTML_TAG = re.compile(r"<[^>]*>")
_MAX_STR = 120


def _sanitize_text(raw: str) -> str:
    s = raw.translate(_ZW)
    s = _HTML_TAG.sub(" ", s)
    s = " ".join(s.split())
    return s[:_MAX_STR]


def fence_payload(raw: Dict[str, Any] | None) -> Tuple[Dict[str, Any], bool]:
    """Return (sanitized, dropped). dropped=True means fail-closed discard."""
    if not isinstance(raw, dict):
        return {}, False
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        lk = str(k).lower()
        if lk in FORBIDDEN_PAYLOAD_KEYS:
            continue
        if isinstance(v, str):
            v = _sanitize_text(v)
            if not v:
                continue
            low = v.lower()
            if any(m in low for m in INJECTION_MARKERS):
                return {}, True
            if any(p in low for p in CREDENTIAL_VALUE_MARKERS):
                return {}, True
        elif isinstance(v, (int, float, bool)):
            pass
        elif v is None:
            continue
        else:
            continue
        out[str(k)[:40]] = v
    blob = dump_bounded_payload(out)
    if len(blob.encode("utf-8")) > PAYLOAD_MAX_BYTES:
        return {}, True
    return out, False
