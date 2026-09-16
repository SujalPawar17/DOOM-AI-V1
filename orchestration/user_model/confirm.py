"""V8.27 User Model ephemeral confirmation (process-local).

Isolated from Goal Registry. Owner + session scoped. Short TTL.
Nonce binds apply to the exact pending object (anti-stale-replace).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from orchestration.user_model.policy import (
    is_sensitive_profile_content,
    is_valid_owner_id,
    normalize_owner_id,
)
from orchestration.user_model.types import Category

CONFIRM_TTL_SEC = 180

ACTION_UPSERT = "UPSERT"
ACTION_FORGET = "FORGET"
ACTION_FORGET_CATEGORY = "FORGET_CATEGORY"
ACTION_CLEAR = "CLEAR"
ACTION_CLEAR_PREFERENCES = "CLEAR_PREFERENCES"


@dataclass(frozen=True)
class ProfileConfirm:
    owner_id: str
    session_id: str
    action: str
    category: str
    key: str
    value: str
    expected_version: int
    expires_at: float
    nonce: str
    old_value: str = ""


_LOCK = threading.Lock()
_PENDING: Dict[Tuple[str, str], ProfileConfirm] = {}


def reset_profile_confirms_for_tests() -> None:
    with _LOCK:
        _PENDING.clear()


def make_profile_confirm(
    *,
    owner_id: str,
    session_id: str,
    action: str,
    category: str = "",
    key: str = "",
    value: str = "",
    expected_version: int = 0,
    old_value: str = "",
    ttl_sec: int = CONFIRM_TTL_SEC,
) -> Optional[ProfileConfirm]:
    if not is_valid_owner_id(owner_id):
        return None
    owner = normalize_owner_id(owner_id)
    session = str(session_id or "").strip()[:64]
    if not owner or not session:
        return None
    act = str(action or "").strip()
    if act not in (
        ACTION_UPSERT,
        ACTION_FORGET,
        ACTION_FORGET_CATEGORY,
        ACTION_CLEAR,
        ACTION_CLEAR_PREFERENCES,
    ):
        return None
    # Defense-in-depth: never stage sensitive content for confirmation.
    if is_sensitive_profile_content(value) or is_sensitive_profile_content(key):
        return None
    if is_sensitive_profile_content(old_value):
        old_value = ""
    nonce = uuid.uuid4().hex[:16]
    if not nonce:
        return None
    return ProfileConfirm(
        owner_id=owner,
        session_id=session,
        action=act,
        category=str(category or "")[:32],
        key=str(key or "")[:96],
        value=str(value or "")[:160],
        expected_version=int(expected_version or 0),
        expires_at=time.time() + max(30, int(ttl_sec)),
        nonce=nonce,
        old_value=str(old_value or "")[:160],
    )


def set_profile_confirm(confirm: ProfileConfirm) -> None:
    if confirm is None:
        return
    if not confirm.nonce or not is_valid_owner_id(confirm.owner_id):
        return
    key = (confirm.owner_id, confirm.session_id)
    with _LOCK:
        _PENDING[key] = confirm


def get_profile_confirm(owner_id: str, session_id: str) -> Optional[ProfileConfirm]:
    owner = str(owner_id or "").strip()[:64]
    session = str(session_id or "").strip()[:64]
    if not owner or not session:
        return None
    with _LOCK:
        conf = _PENDING.get((owner, session))
        if conf is None:
            return None
        # Align with profile expiry: expires_at <= now is expired.
        if float(conf.expires_at) <= time.time():
            _PENDING.pop((owner, session), None)
            return None
        if conf.owner_id != owner or conf.session_id != session:
            return None
        if not conf.nonce:
            _PENDING.pop((owner, session), None)
            return None
        return conf


def clear_profile_confirm(owner_id: str, session_id: str) -> None:
    owner = str(owner_id or "").strip()[:64]
    session = str(session_id or "").strip()[:64]
    with _LOCK:
        _PENDING.pop((owner, session), None)


def category_enum(raw: str) -> Optional[Category]:
    try:
        return Category(str(raw or "").strip())
    except ValueError:
        return None
