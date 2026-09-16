"""V8.27 User Model feature flag — env mirror, no proactive package import."""

from __future__ import annotations

import os


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_v827_user_model_enabled() -> bool:
    """True when V8 master + V8.27 user model flags are enabled. Default false.

    Mirrors proactive.config style without importing the proactive package
    (proactive/__init__.py eagerly loads worker → DB).
    """
    if not _bool_env("PROACTIVE_V8_ENABLED", False):
        return False
    return _bool_env("PROACTIVE_V827_USER_MODEL_ENABLED", False)
