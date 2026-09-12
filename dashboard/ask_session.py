"""V6.2.6 ASK session + CSRF + origin checks. No secrets in logs."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional, Tuple

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from proactive.config import (
    ASK_SESSION_TTL_SECONDS,
    OWNER_ID,
    ask_allowed_origins,
    ask_cookie_secure,
    ask_unlock_secret,
)
from proactive.store import proactive_store

COOKIE_NAME = "doom_ask_sid"
COOKIE_PATH = "/api/proactive/"
CSRF_HEADER = "X-DOOM-CSRF"
ASK_PREFIXES = (
    "/api/proactive/session",
    "/api/proactive/preparations",
    "/api/proactive/approvals",
    "/api/proactive/actions",
    "/api/proactive/computer",
    "/api/proactive/v8",
)

_unlock_hits: Dict[str, deque] = defaultdict(deque)


def hash_session_token(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        raw_token,
        httponly=True,
        samesite="strict",
        secure=ask_cookie_secure(),
        path=COOKIE_PATH,
        max_age=ASK_SESSION_TTL_SECONDS,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH)


def path_is_ask_plane(path: str) -> bool:
    p = path or ""
    return any(p == pref or p.startswith(pref + "/") for pref in ASK_PREFIXES)


def origin_allowed(request: Request) -> bool:
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    allowed = ask_allowed_origins()
    if origin:
        return origin in allowed
    method = (request.method or "GET").upper()
    if method in ("GET", "HEAD", "OPTIONS"):
        return True
    site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    return site in ("same-origin", "none")


def unlock_rate_ok(ip: str) -> bool:
    now = time.time()
    q = _unlock_hits[ip or "unknown"]
    while q and now - q[0] > 60.0:
        q.popleft()
    if len(q) >= 5:
        return False
    q.append(now)
    return True


def create_session(owner_id: str) -> Tuple[str, str, float]:
    raw = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    sid_hash = hash_session_token(raw)
    exp = time.time() + ASK_SESSION_TTL_SECONDS
    proactive_store.revoke_ask_sessions_for_owner(owner_id)
    ok = proactive_store.insert_ask_session(sid_hash, owner_id, csrf, exp)
    if not ok:
        return "", "", 0.0
    return raw, csrf, exp


def load_session(request: Request) -> Optional[Dict[str, Any]]:
    raw = request.cookies.get(COOKIE_NAME) or ""
    if not raw:
        return None
    row = proactive_store.get_ask_session(hash_session_token(raw))
    if not row:
        return None
    try:
        exp = float(row.get("expires_at") or 0)
    except (TypeError, ValueError):
        return None
    if exp <= time.time():
        return None
    if row.get("revoked_at"):
        return None
    return row


def csrf_ok(request: Request, session: Dict[str, Any]) -> bool:
    header = (request.headers.get(CSRF_HEADER) or "").encode("utf-8")
    stored = str(session.get("csrf_token") or "").encode("utf-8")
    if not header or not stored:
        return False
    if len(header) != len(stored):
        return False
    return hmac.compare_digest(header, stored)


def unauthenticated() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "unauthenticated"}, status_code=401)


def csrf_forbidden() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "csrf"}, status_code=403)


def origin_forbidden() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "origin"}, status_code=403)


def require_ask_session(request: Request, *, need_csrf: bool) -> Tuple[Optional[Dict[str, Any]], Optional[JSONResponse]]:
    if not origin_allowed(request):
        return None, origin_forbidden()
    sess = load_session(request)
    if not sess:
        return None, unauthenticated()
    if need_csrf and not csrf_ok(request, sess):
        return None, csrf_forbidden()
    return sess, None


def compare_unlock(provided: str) -> Tuple[str, Optional[JSONResponse]]:
    expected = ask_unlock_secret()
    if not expected:
        return "", JSONResponse({"ok": False, "error": "unlock_unconfigured"}, status_code=503)
    a = (provided or "").encode("utf-8")
    b = expected.encode("utf-8")
    if len(a) != len(b):
        # still compare to keep timing closer
        hmac.compare_digest(a[:1] or b"x", b[:1])
        return "", JSONResponse({"ok": False, "error": "unauthenticated"}, status_code=401)
    if not hmac.compare_digest(a, b):
        return "", JSONResponse({"ok": False, "error": "unauthenticated"}, status_code=401)
    return OWNER_ID, None
