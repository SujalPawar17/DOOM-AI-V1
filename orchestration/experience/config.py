"""V8.28 Goal Experience feature flag — env mirror, no proactive package import."""

from __future__ import annotations

import os


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_v828_goal_experience_enabled() -> bool:
    """True when V8 master + V8.28 goal experience flags are enabled. Default false.

    Mirrors proactive.config style without importing the proactive package
    (proactive/__init__.py eagerly loads worker → DB).
    """
    if not _bool_env("PROACTIVE_V8_ENABLED", False):
        return False
    return _bool_env("PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED", False)
