"""V8.9 redaction. Secret values only. Ordinary words such as authorization remain."""

from __future__ import annotations

import re
from typing import Tuple

_SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-+/=]+"),
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?key|access_token|refresh_token|id_token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)authorization\s*[:=]\s*\S+(?:\s+\S+)?"),
    re.compile(r"(?i)(cookie|set-cookie)\s*[:=]\s*\S+"),
    re.compile(r"(?i)private[_-]?key\s*[:=]\s*\S+"),
    re.compile(r"(?i)\btoken\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)


def redact_text(text: str, max_chars: int) -> Tuple[str, bool]:
    raw = str(text or "").replace("\x00", "")
    cleaned = raw
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    truncated = len(cleaned) > int(max_chars)
    if truncated:
        cleaned = cleaned[: int(max_chars)]
    return cleaned, truncated
