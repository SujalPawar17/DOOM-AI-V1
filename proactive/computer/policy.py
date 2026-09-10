"""V7.1 computer observation policy. Flags default off. No screenshot flag. V7.2 click/type flags default off."""

from __future__ import annotations

import os

from proactive.config import (
    COMPUTER_BROWSER_MAX_NODES,
    COMPUTER_BROWSER_TTL_SEC,
    COMPUTER_MAX_DEPTH,
    COMPUTER_MAX_NODES,
    COMPUTER_MAX_TITLE,
    COMPUTER_RETENTION,
    COMPUTER_SESSION_TTL_SEC,
    COMPUTER_TIMEOUT_MS,
    is_computer_click_enabled,
    is_computer_enabled,
    is_computer_observe_enabled,
    is_computer_type_enabled,
    is_computer_browser_enabled,
    is_computer_filesystem_enabled,
    is_computer_sequences_enabled,
    is_computer_verification_enabled,
    is_computer_experience_enabled,
)

CAPABILITY_ID = "COMPUTER_OBSERVE_DESKTOP"
CAPABILITY_CLICK = "COMPUTER_CLICK_CONTROL"
CAPABILITY_TYPE = "COMPUTER_TYPE_TEXT"
CAPABILITY_BROWSER = "COMPUTER_BROWSER_CONTROL"
CAPABILITY_FILESYSTEM = "COMPUTER_FILESYSTEM"
SCHEMA_VERSION = "v71.1"
ACTION_SCHEMA_VERSION = "v72.1"
BROWSER_SCHEMA_VERSION = "v73.1"
FS_SCHEMA_VERSION = "v74.1"
SEQUENCE_SCHEMA_VERSION = "v75.1"
VERIFY_SCHEMA_VERSION = "v76.1"
EXPERIENCE_SCHEMA_VERSION = "v77.1"
CAPABILITY_SEQUENCE = "COMPUTER_SEQUENCE"
CAPABILITY_VERIFY = "COMPUTER_VERIFICATION"
CAPABILITY_EXPERIENCE = "COMPUTER_EXPERIENCE"
PRIVACY_DEFAULT = "PRIVATE"
RISK_CLICK = "MEDIUM"
RISK_TYPE = "MEDIUM"
RISK_BROWSER_NAVIGATE = "MEDIUM"
RISK_BROWSER_CLICK = "MEDIUM"
RISK_BROWSER_TYPE = "MEDIUM"
RISK_BROWSER_HISTORY = "MEDIUM"
RISK_FS_READ = "LOW"
RISK_FS_MUTATE = "MEDIUM"
RISK_FS_DELETE = "HIGH"
RISK_VERIFY = "LOW"

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


def click_allowed() -> bool:
    return is_computer_enabled() and is_computer_click_enabled()


def type_allowed() -> bool:
    return is_computer_enabled() and is_computer_type_enabled()


def browser_allowed() -> bool:
    return is_computer_enabled() and is_computer_browser_enabled()


def filesystem_allowed() -> bool:
    return is_computer_enabled() and is_computer_filesystem_enabled()


def sequences_allowed() -> bool:
    return is_computer_enabled() and is_computer_sequences_enabled()


def verification_allowed() -> bool:
    return is_computer_enabled() and is_computer_verification_enabled()


def experiences_allowed() -> bool:
    return is_computer_enabled() and is_computer_experience_enabled()


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


def browser_ttl_sec() -> int:
    return int(COMPUTER_BROWSER_TTL_SEC)


def browser_max_nodes() -> int:
    return int(COMPUTER_BROWSER_MAX_NODES)


def fs_max_read_bytes() -> int:
    from proactive.config import COMPUTER_FS_MAX_READ_BYTES
    return int(os.getenv("PROACTIVE_COMPUTER_FS_MAX_READ_BYTES") or COMPUTER_FS_MAX_READ_BYTES)


def fs_max_write_bytes() -> int:
    from proactive.config import COMPUTER_FS_MAX_WRITE_BYTES
    return int(os.getenv("PROACTIVE_COMPUTER_FS_MAX_WRITE_BYTES") or COMPUTER_FS_MAX_WRITE_BYTES)


def fs_max_list() -> int:
    from proactive.config import COMPUTER_FS_MAX_LIST
    return int(os.getenv("PROACTIVE_COMPUTER_FS_MAX_LIST") or COMPUTER_FS_MAX_LIST)


def sequence_max_steps() -> int:
    from proactive.config import COMPUTER_SEQUENCE_MAX_STEPS
    try:
        v = int(os.getenv("PROACTIVE_COMPUTER_SEQUENCE_MAX_STEPS") or COMPUTER_SEQUENCE_MAX_STEPS)
    except ValueError:
        v = 8
    return max(1, min(16, v))


def sequence_timeout_ms() -> int:
    from proactive.config import COMPUTER_SEQUENCE_TIMEOUT_MS
    try:
        v = int(os.getenv("PROACTIVE_COMPUTER_SEQUENCE_TIMEOUT_MS") or COMPUTER_SEQUENCE_TIMEOUT_MS)
    except ValueError:
        v = 30000
    return max(100, min(120000, v))


def sequence_max_retries() -> int:
    from proactive.config import COMPUTER_SEQUENCE_MAX_RETRIES
    raw = os.getenv("PROACTIVE_COMPUTER_SEQUENCE_MAX_RETRIES")
    if raw is None or raw == "":
        try:
            v = int(COMPUTER_SEQUENCE_MAX_RETRIES)
        except (TypeError, ValueError):
            v = 0
    else:
        try:
            v = int(raw)
        except ValueError:
            v = 0
    return max(0, min(2, v))


def verify_timeout_ms() -> int:
    from proactive.config import COMPUTER_VERIFY_TIMEOUT_MS
    try:
        v = int(os.getenv("PROACTIVE_COMPUTER_VERIFY_TIMEOUT_MS") or COMPUTER_VERIFY_TIMEOUT_MS)
    except ValueError:
        v = 2000
    return max(50, min(10000, v))


def verify_max_attempts() -> int:
    from proactive.config import COMPUTER_VERIFY_MAX_ATTEMPTS
    try:
        v = int(os.getenv("PROACTIVE_COMPUTER_VERIFY_MAX_ATTEMPTS") or COMPUTER_VERIFY_MAX_ATTEMPTS)
    except ValueError:
        v = 3
    return max(1, min(5, v))
