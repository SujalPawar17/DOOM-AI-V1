"""V6.1 Proactive Foundation configuration. Conservative defaults. No LLM keys."""

from __future__ import annotations

import os


OWNER_ID = os.getenv("DOOM_OWNER_ID", "sujal")


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def is_proactive_enabled() -> bool:
    """Authoritative V6.1 flag. Default FALSE.

    Lifecycle: the worker loop reads this every poll tick. Other constants
    (lease, budget, TTL) are process-import values and are not hot-reloaded.
    When the flag is false, start_proactive_worker() refuses to start and an
    already-running loop exits so worker_alive() becomes false. Operators
    should call stop_proactive_worker() on process shutdown.
    """
    return _bool_env("PROACTIVE_ENABLED", False)


POLL_INTERVAL_SEC = _float_env("PROACTIVE_POLL_INTERVAL_SEC", 2.0)
LEASE_SECONDS = _int_env("PROACTIVE_LEASE_SECONDS", 45)
MAX_ATTEMPTS = _int_env("PROACTIVE_MAX_ATTEMPTS", 5)
# Backpressure: reject *new* signals when PENDING+CLAIMED reach this count.
# Duplicates of an existing idempotency_key still resolve to the stored row.
# Does not delete valid PENDING rows.
PENDING_QUEUE_MAX = _int_env("PROACTIVE_PENDING_QUEUE_MAX", 256)
SIGNAL_RETENTION_HOURS = _int_env("PROACTIVE_SIGNAL_RETENTION_HOURS", 48)
INSIGHT_TTL_SECONDS = _int_env("PROACTIVE_INSIGHT_TTL_SECONDS", 86400)
DELIVERY_RETENTION_HOURS = _int_env("PROACTIVE_DELIVERY_RETENTION_HOURS", 72)
DAILY_INFORM_BUDGET = _int_env("PROACTIVE_DAILY_INFORM_BUDGET", 8)
COOLDOWN_SECONDS = _int_env("PROACTIVE_COOLDOWN_SECONDS", 3600)
PAYLOAD_MAX_BYTES = _int_env("PROACTIVE_PAYLOAD_MAX_BYTES", 2048)
TIME_BUCKET_SECONDS = _int_env("PROACTIVE_TIME_BUCKET_SECONDS", 300)
SNAPSHOT_TTL_SECONDS = _int_env("PROACTIVE_SNAPSHOT_TTL_SECONDS", 60)
SIGNIFICANCE_INFORM_FLOOR = _float_env("PROACTIVE_SIGNIFICANCE_FLOOR", 0.65)
CONFIDENCE_FLOOR = _float_env("PROACTIVE_CONFIDENCE_FLOOR", 0.50)
# Quiet hours as "22-07" (local) or empty to disable
QUIET_HOURS = os.getenv("PROACTIVE_QUIET_HOURS", "").strip()
TTS_PROACTIVE_ALLOWED = False  # V6.1: never

# V6.2.2 connector flags — default OFF. Hot-read like PROACTIVE_ENABLED.
CALENDAR_POLL_SEC = _int_env("PROACTIVE_CALENDAR_POLL_SEC", 900)
GITHUB_POLL_SEC = _int_env("PROACTIVE_GITHUB_POLL_SEC", 600)
CONNECTOR_BACKOFF_SEC = _int_env("PROACTIVE_CONNECTOR_BACKOFF_SEC", 900)


def is_calendar_enabled() -> bool:
    return _bool_env("PROACTIVE_CALENDAR_ENABLED", False)


def is_github_enabled() -> bool:
    return _bool_env("PROACTIVE_GITHUB_ENABLED", False)


def connector_vault_path() -> str:
    override = (os.getenv("DOOM_CONNECTOR_VAULT_PATH") or "").strip()
    if override:
        return override
    local = os.getenv("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(local, "DOOM", "connector_vault.dpapi")
