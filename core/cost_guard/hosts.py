"""Loopback / local-host helpers. No network. Used by Cost Guard and Postgres."""

from __future__ import annotations

from urllib.parse import urlparse


_LOOPBACK = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "0:0:0:0:0:0:0:1",
    "localhost.localdomain",
})


def hostname_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    try:
        return (urlparse(raw).hostname or "").lower().strip()
    except Exception:
        return ""


def is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if "%" in h:
        h = h.split("%", 1)[0]
    return h in _LOOPBACK


def is_loopback_url(url: str) -> bool:
    return is_loopback_host(hostname_from_url(url) or (url or "").strip().lower())
