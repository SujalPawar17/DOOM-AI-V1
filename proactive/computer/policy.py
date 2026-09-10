"""V7.1 computer observation policy. Flags default off. No screenshot flag."""

from __future__ import annotations

from proactive.config import (
    COMPUTER_MAX_DEPTH,
    COMPUTER_MAX_NODES,
    COMPUTER_MAX_TITLE,
    COMPUTER_RETENTION,
    COMPUTER_SESSION_TTL_SEC,
    COMPUTER_TIMEOUT_MS,
    is_computer_enabled,
    is_computer_observe_enabled,
)

CAPABILITY_ID = "COMPUTER_OBSERVE_DESKTOP"
SCHEMA_VERSION = "v71.1"
PRIVACY_DEFAULT = "PRIVATE"

ALLOWED_SESSION_STATUS = frozenset({
    "CREATED",
    "OBSERVING",
    "PAUSED",
    "STOPPED",
    "CANCELLED",
    "EXPIRED",
})


def observation_allowed() -> bool:
    return is_computer_enabled() and is_computer_observe_enabled()


def timeout_ms() -> int:
    return int(COMPUTER_TIMEOUT_MS)


def max_depth() -> int:
    return int(COMPUTER_MAX_DEPTH)


def max_nodes() -> int:
    return int(COMPUTER_MAX_NODES)


def max_title() -> int:
    return int(COMPUTER_MAX_TITLE)


def session_ttl_sec() -> int:
    return int(COMPUTER_SESSION_TTL_SEC)


def retention_n() -> int:
    return int(COMPUTER_RETENTION)
