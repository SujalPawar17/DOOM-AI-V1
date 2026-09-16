"""V8.28 Goal Experience ephemeral confirmation (process-local).

Isolated from Goal Registry and User Model. Owner + session scoped.
Short TTL. Nonce binds apply to the exact pending object.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from orchestration.experience.policy import (
    is_sensitive_experience_content,
    is_valid_owner_id,
    normalize_owner_id,
)

CONFIRM_TTL_SEC = 180
ACTION_FORGET = "FORGET"


@dataclass(frozen=True)
class ExperienceConfirm:
    owner_id: str
    session_id: str
    action: str
    experience_id: str
    expected_version: int
    expires_at: float
    nonce: str
    title: str = ""
    outcome: str = ""


_LOCK = threading.Lock()
_PENDING: Dict[Tuple[str, str], ExperienceConfirm] = {}


def reset_experience_confirms_for_tests() -> None:
    with _LOCK:
        _PENDING.clear()


def make_experience_confirm(
    *,
    owner_id: str,
    session_id: str,
    action: str,
    experience_id: str,
    expected_version: int = 0,
    title: str = "",
    outcome: str = "",
    ttl_sec: int = CONFIRM_TTL_SEC,
) -> Optional[ExperienceConfirm]:
    if not is_valid_owner_id(owner_id):
        return None
    owner = normalize_owner_id(owner_id)
    session = str(session_id or "").strip()[:64]
    if not owner or not session:
        return None
    act = str(action or "").strip()
    if act != ACTION_FORGET:
        return None
    eid = str(experience_id or "").strip()[:64]
    if not eid.startswith("ge_"):
        return None
    shown_title = str(title or "").strip()[:160]
    if is_sensitive_experience_content(shown_title):
        shown_title = "(goal experience)"
    shown_outcome = str(outcome or "").strip()[:32]
    nonce = uuid.uuid4().hex[:16]
    if not nonce:
        return None
    return ExperienceConfirm(
        owner_id=owner,
        session_id=session,
        action=act,
        experience_id=eid,
        expected_version=int(expected_version or 0),
        expires_at=time.time() + max(30, int(ttl_sec)),
        nonce=nonce,
        title=shown_title,
        outcome=shown_outcome,
    )


def set_experience_confirm(confirm: ExperienceConfirm) -> None:
    if confirm is None:
        return
    if not confirm.nonce or not is_valid_owner_id(confirm.owner_id):
        return
    if not str(confirm.experience_id or "").startswith("ge_"):
        return
    key = (confirm.owner_id, confirm.session_id)
    with _LOCK:
        _PENDING[key] = confirm


def get_experience_confirm(owner_id: str, session_id: str) -> Optional[ExperienceConfirm]:
    owner = str(owner_id or "").strip()[:64]
    session = str(session_id or "").strip()[:64]
    if not owner or not session:
        return None
    with _LOCK:
        conf = _PENDING.get((owner, session))
        if conf is None:
            return None
        if float(conf.expires_at) <= time.time():
            _PENDING.pop((owner, session), None)
            return None
        if conf.owner_id != owner or conf.session_id != session:
            return None
        if not conf.nonce:
            _PENDING.pop((owner, session), None)
            return None
        return conf


def clear_experience_confirm(owner_id: str, session_id: str) -> None:
    owner = str(owner_id or "").strip()[:64]
    session = str(session_id or "").strip()[:64]
    with _LOCK:
        _PENDING.pop((owner, session), None)
