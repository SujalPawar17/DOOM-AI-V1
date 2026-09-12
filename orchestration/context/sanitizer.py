"""Deterministic V8.8 sanitizer. Redacts secret-like values. Does not grant authority."""

from __future__ import annotations

import re

from typing import Tuple

from orchestration.context.policy import MAX_ITEM_CHARS

_SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-+/=]+"),
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)authorization\s*[:=]\s*\S+(?:\s+\S+)?"),
    re.compile(r"(?i)cookie\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
)


def sanitize_text(text: str, max_chars: int = MAX_ITEM_CHARS) -> Tuple[str, bool]:
    raw = str(text or "").replace("\x00", "")
    cleaned = raw
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    truncated = len(cleaned) > int(max_chars)
    if truncated:
        cleaned = cleaned[: int(max_chars)]
    return cleaned, truncated
