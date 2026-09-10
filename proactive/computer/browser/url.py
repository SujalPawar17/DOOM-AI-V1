"""HTTP(S) only. Block javascript/data/file and other executable schemes."""

from __future__ import annotations

from urllib.parse import urlparse

from proactive.computer.browser.types import Status

_BLOCKED = frozenset({
    "javascript", "data", "file", "vbscript", "blob", "about",
    "ms-appx", "ms-its", "shell", "view-source",
})
_ALLOWED = frozenset({"http", "https"})


def validate_navigate_url(url: str) -> Status:
    raw = (url or "").strip()
    if not raw:
        return Status.INVALID_URL
    if "\\" in raw or raw.startswith("//"):
        return Status.NAVIGATION_BLOCKED
    try:
        parsed = urlparse(raw)
    except Exception:
        return Status.INVALID_URL
    scheme = (parsed.scheme or "").lower()
    if not scheme:
        return Status.INVALID_URL
    if scheme in _BLOCKED:
        return Status.NAVIGATION_BLOCKED
    if scheme not in _ALLOWED:
        return Status.NAVIGATION_BLOCKED
    if not (parsed.netloc or "").strip():
        return Status.INVALID_URL
    return Status.SUCCESS


def origin_of(url: str) -> str:
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
