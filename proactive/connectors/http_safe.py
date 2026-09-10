"""GET-only HTTP plus allowlisted OAuth token POST. Blocks SSRF and write verbs."""

from __future__ import annotations

import re
from typing import Callable, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ALLOWED_GET_HOSTS = frozenset({
    "www.googleapis.com",
    "calendar.googleapis.com",
    "gmail.googleapis.com",
    "api.github.com",
})
_GMAIL_LIST = "/gmail/v1/users/me/messages"
_GMAIL_GET = re.compile(r"^/gmail/v1/users/me/messages/[A-Za-z0-9._-]+$")
TOKEN_POST_URL = "https://oauth2.googleapis.com/token"
TOKEN_POST_HOST = "oauth2.googleapis.com"
MAX_BODY = 256 * 1024
DEFAULT_TIMEOUT = 10.0

Transport = Callable[[str, str, Dict[str, str], Optional[bytes], float], Tuple[int, Dict[str, str], bytes]]


class SafeHttpError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class _SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old_host = urlparse(req.full_url).hostname
        new_host = urlparse(newurl).hostname
        if (old_host or "").lower() != (new_host or "").lower():
            raise SafeHttpError("redirect host mismatch")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_url(method: str, url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise SafeHttpError("https required")
    host = (parsed.hostname or "").lower()
    if not host or host in ("localhost", "127.0.0.1", "::1", "metadata.google.internal"):
        raise SafeHttpError("host not allowed")
    if any(ch.isdigit() for ch in host.split(".")[0:1]) and host.replace(".", "").isdigit():
        raise SafeHttpError("literal IP not allowed")
    # dotted IPv4
    parts = host.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        raise SafeHttpError("literal IP not allowed")
    method = method.upper()
    if method == "GET":
        if host not in ALLOWED_GET_HOSTS:
            raise SafeHttpError("GET host not allowlisted")
        if host == "gmail.googleapis.com" or (
            host == "www.googleapis.com" and parsed.path.startswith("/gmail/")
        ):
            path = parsed.path or ""
            if path != _GMAIL_LIST and not _GMAIL_GET.match(path):
                raise SafeHttpError("Gmail GET path not allowlisted")
        return
    if method == "POST":
        canon = f"{parsed.scheme}://{host}{parsed.path}".rstrip("/")
        if canon != TOKEN_POST_URL.rstrip("/") or host != TOKEN_POST_HOST:
            raise SafeHttpError("POST host not allowlisted")
        if parsed.query:
            raise SafeHttpError("token URL query not allowed")
        return
    raise SafeHttpError("method not allowed")


def _urllib_transport(
    method: str, url: str, headers: Dict[str, str], data: Optional[bytes], timeout: float
) -> Tuple[int, Dict[str, str], bytes]:
    req = Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    opener = build_opener(_SameHostRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(MAX_BODY + 1)
            status = getattr(resp, "status", None) or resp.getcode() or 200
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
    except HTTPError as exc:
        body = exc.read(MAX_BODY + 1) if exc.fp else b""
        if len(body) > MAX_BODY:
            raise SafeHttpError("response too large", status=int(exc.code or 0))
        raise SafeHttpError(f"http {exc.code}", status=int(exc.code or 0))
    except URLError as exc:
        raise SafeHttpError("network error") from exc
    if len(body) > MAX_BODY:
        raise SafeHttpError("response too large", status=int(status))
    return int(status), hdrs, body


class SafeHttp:
    def __init__(self, transport: Optional[Transport] = None):
        self._transport = transport or _urllib_transport

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> Tuple[int, Dict[str, str], bytes]:
        m = str(method or "").upper()
        _validate_url(m, url)
        from core.cost_guard import ResourceRequest, ResourceType, cost_guard
        from core.cost_guard.hosts import hostname_from_url
        host = hostname_from_url(url)
        provider = "google_oauth" if host == TOKEN_POST_HOST else (
            "gmail" if "gmail" in host or "/gmail/" in url else (
                "google_calendar" if "calendar" in host else (
                    "github" if host == "api.github.com" else "arbitrary"
                )
            )
        )
        rtype = ResourceType.CONNECTOR
        decision = cost_guard.authorize(ResourceRequest(
            resource_type=rtype,
            provider=provider,
            capability="connector",
            host=host,
            endpoint=f"{urlparse(url).scheme}://{host}{urlparse(url).path}"[:200],
        ))
        if not decision.is_allow:
            raise SafeHttpError(f"cost_policy_blocked:{decision.reason.value}")
        hdrs = dict(headers or {})
        if m == "POST":
            ct = (hdrs.get("Content-Type") or hdrs.get("content-type") or "").lower()
            if "application/x-www-form-urlencoded" not in ct:
                raise SafeHttpError("token POST requires form encoding")
        return self._transport(m, url, hdrs, data, timeout)

    def get(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: float = DEFAULT_TIMEOUT):
        return self.request("GET", url, headers=headers, timeout=timeout)

    def post_token(self, url: str, headers: Optional[Dict[str, str]] = None, data: Optional[bytes] = None,
                   timeout: float = DEFAULT_TIMEOUT):
        return self.request("POST", url, headers=headers, data=data, timeout=timeout)
