"""Google OAuth refresh. POST only to the allowlisted token URL. Never logs tokens."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from proactive.connectors.http_safe import TOKEN_POST_URL, SafeHttp, SafeHttpError
from proactive.vault import get_secret, put_secret

_http = SafeHttp()


def refresh_google_access_token(secret_ref: str, http: Optional[SafeHttp] = None) -> Optional[str]:
    """Return a usable access token, refreshing when expiry is near. Writes vault on success."""
    client = http or _http
    blob = get_secret(secret_ref)
    if not blob or blob.get("type") != "oauth_google":
        return None
    token = blob.get("token") or ""
    expiry = float(blob.get("expiry") or 0)
    if token and expiry > time.time() + 60:
        return token
    refresh = blob.get("refresh") or ""
    client_id = blob.get("client_id") or ""
    client_secret = blob.get("client_secret") or ""
    if not refresh or not client_id:
        return token or None
    body = urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        status, _hdrs, raw = client.post_token(TOKEN_POST_URL, headers=headers, data=body)
    except SafeHttpError:
        return None
    if status != 200:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    access = str(payload.get("access_token") or "")
    if not access:
        return None
    expires_in = float(payload.get("expires_in") or 3600)
    updated: Dict[str, Any] = dict(blob)
    updated["token"] = access
    updated["expiry"] = time.time() + expires_in
    new_refresh = str(payload.get("refresh_token") or "")
    if new_refresh:
        updated["refresh"] = new_refresh
    try:
        put_secret(secret_ref, updated)
    except Exception:
        pass
    return access
