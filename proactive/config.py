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
EMAIL_POLL_SEC = _int_env("PROACTIVE_EMAIL_POLL_SEC", 900)
CONNECTOR_BACKOFF_SEC = _int_env("PROACTIVE_CONNECTOR_BACKOFF_SEC", 900)


def is_calendar_enabled() -> bool:
    return _bool_env("PROACTIVE_CALENDAR_ENABLED", False)


def is_github_enabled() -> bool:
    return _bool_env("PROACTIVE_GITHUB_ENABLED", False)


def is_email_enabled() -> bool:
    return _bool_env("PROACTIVE_EMAIL_ENABLED", False)


def is_prediction_enabled() -> bool:
    """V6.2.4. Default false. Requires PROACTIVE_ENABLED as well at call sites."""
    return _bool_env("PROACTIVE_PREDICTION_ENABLED", False)


def is_suggest_enabled() -> bool:
    """V6.2.5. Default false. Call sites also require proactive + prediction flags."""
    return _bool_env("PROACTIVE_SUGGEST_ENABLED", False)


DAILY_SUGGEST_BUDGET = _int_env("PROACTIVE_DAILY_SUGGEST_BUDGET", 4)
SUGGEST_COOLDOWN_SECONDS = _int_env("PROACTIVE_SUGGEST_COOLDOWN_SECONDS", 14400)
SUGGEST_CANDIDATE_CAP = _int_env("PROACTIVE_SUGGEST_CANDIDATE_CAP", 50)
SUGGEST_CONFIDENCE_FLOOR = 0.70
SUGGEST_RULE_VERSION = "v625.1"


def is_prepare_enabled() -> bool:
    """V6.2.6 PREPARE. Default false. Call sites also require suggest+prediction+proactive."""
    return _bool_env("PROACTIVE_PREPARE_ENABLED", False)


def is_ask_enabled() -> bool:
    """V6.2.6 ASK. Default false. Requires PREPARE flag as well at call sites."""
    return _bool_env("PROACTIVE_ASK_ENABLED", False)


def is_llm_draft_enabled() -> bool:
    """V6.2.8 bounded LLM draft. Default false. Call sites also require prepare flags."""
    return _bool_env("PROACTIVE_LLM_DRAFT_ENABLED", False)


def is_llm_draft_normal_enabled() -> bool:
    return _bool_env("PROACTIVE_LLM_DRAFT_NORMAL_ENABLED", False)


def is_llm_draft_private_enabled() -> bool:
    return _bool_env("PROACTIVE_LLM_DRAFT_PRIVATE_ENABLED", False)


def is_act_enabled() -> bool:
    """V6.3 ACT master flag. Default false. Does not auto-run APPROVED rows."""
    return _bool_env("PROACTIVE_ACT_ENABLED", False)


def is_act_internal_enabled() -> bool:
    return _bool_env("PROACTIVE_ACT_INTERNAL_ENABLED", False)


def is_act_calendar_hold_enabled() -> bool:
    return _bool_env("PROACTIVE_ACT_CALENDAR_HOLD_ENABLED", False)


def is_computer_enabled() -> bool:
    """V7.1 computer observation master flag. Default false."""
    return _bool_env("PROACTIVE_COMPUTER_ENABLED", False)


def is_computer_observe_enabled() -> bool:
    """V7.1 observe capability. Both computer flags must be true to capture."""
    return _bool_env("PROACTIVE_COMPUTER_OBSERVE_ENABLED", False)


def is_computer_click_enabled() -> bool:
    """V7.2 UIA click. Default false. Requires computer master flag at the kernel."""
    return _bool_env("PROACTIVE_COMPUTER_CLICK_ENABLED", False)


def is_computer_type_enabled() -> bool:
    """V7.2 UIA type. Default false. Requires computer master flag at the kernel."""
    return _bool_env("PROACTIVE_COMPUTER_TYPE_ENABLED", False)


def is_computer_browser_enabled() -> bool:
    """V7.3 bounded browser. Default false. Requires computer master flag at the kernel."""
    return _bool_env("PROACTIVE_COMPUTER_BROWSER_ENABLED", False)


def is_computer_filesystem_enabled() -> bool:
    """V7.4 bounded filesystem. Default false. Requires computer master flag."""
    return _bool_env("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED", False)


def is_computer_sequences_enabled() -> bool:
    """V7.5 bounded sequences. Default false. Requires computer master flag."""
    return _bool_env("PROACTIVE_COMPUTER_SEQUENCES_ENABLED", False)


def is_computer_verification_enabled() -> bool:
    """V7.6 structured verification. Default false. Requires computer master flag."""
    return _bool_env("PROACTIVE_COMPUTER_VERIFICATION_ENABLED", False)


def is_computer_experience_enabled() -> bool:
    """V7.7 hashed experience recording. Default false. Requires computer master flag."""
    return _bool_env("PROACTIVE_COMPUTER_EXPERIENCE_ENABLED", False)


COMPUTER_TIMEOUT_MS = _int_env("PROACTIVE_COMPUTER_TIMEOUT_MS", 800)
COMPUTER_MAX_DEPTH = _int_env("PROACTIVE_COMPUTER_MAX_DEPTH", 6)
COMPUTER_MAX_NODES = _int_env("PROACTIVE_COMPUTER_MAX_NODES", 80)
COMPUTER_MAX_TITLE = _int_env("PROACTIVE_COMPUTER_MAX_TITLE", 80)
COMPUTER_SESSION_TTL_SEC = _int_env("PROACTIVE_COMPUTER_SESSION_TTL_SEC", 3600)
COMPUTER_RETENTION = _int_env("PROACTIVE_COMPUTER_RETENTION", 20)
COMPUTER_BROWSER_TTL_SEC = _int_env("PROACTIVE_COMPUTER_BROWSER_TTL_SEC", 300)
COMPUTER_BROWSER_MAX_NODES = _int_env("PROACTIVE_COMPUTER_BROWSER_MAX_NODES", 40)
COMPUTER_FS_ALLOWED_ROOTS = os.environ.get("PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS", "") or ""
COMPUTER_FS_DENIED_ROOTS = os.environ.get("PROACTIVE_COMPUTER_FS_DENIED_ROOTS", "") or ""
COMPUTER_FS_MAX_READ_BYTES = _int_env("PROACTIVE_COMPUTER_FS_MAX_READ_BYTES", 65536)
COMPUTER_FS_MAX_WRITE_BYTES = _int_env("PROACTIVE_COMPUTER_FS_MAX_WRITE_BYTES", 65536)
COMPUTER_FS_MAX_LIST = _int_env("PROACTIVE_COMPUTER_FS_MAX_LIST", 50)
COMPUTER_SEQUENCE_MAX_STEPS = _int_env("PROACTIVE_COMPUTER_SEQUENCE_MAX_STEPS", 8)
COMPUTER_SEQUENCE_TIMEOUT_MS = _int_env("PROACTIVE_COMPUTER_SEQUENCE_TIMEOUT_MS", 30000)
COMPUTER_SEQUENCE_MAX_RETRIES = _int_env("PROACTIVE_COMPUTER_SEQUENCE_MAX_RETRIES", 0)
COMPUTER_VERIFY_TIMEOUT_MS = _int_env("PROACTIVE_COMPUTER_VERIFY_TIMEOUT_MS", 2000)
COMPUTER_VERIFY_MAX_ATTEMPTS = _int_env("PROACTIVE_COMPUTER_VERIFY_MAX_ATTEMPTS", 3)


PREPARE_RULE_VERSION = "v626.1"
PREPARE_CANDIDATE_CAP = _int_env("PROACTIVE_PREPARE_CANDIDATE_CAP", 50)
PREPARE_CONFIDENCE_FLOOR = 0.70
DAILY_PREPARE_BUDGET = _int_env("PROACTIVE_DAILY_PREPARE_BUDGET", 4)
PREPARE_COOLDOWN_SECONDS = _int_env("PROACTIVE_PREPARE_COOLDOWN_SECONDS", 14400)
DAILY_ASK_BUDGET = _int_env("PROACTIVE_DAILY_ASK_BUDGET", 4)
ASK_COOLDOWN_SECONDS = _int_env("PROACTIVE_ASK_COOLDOWN_SECONDS", 14400)
ASK_SESSION_TTL_SECONDS = 12 * 3600
WORKER_CSRF_BINDING_ID = "worker"


DRAFT_PROMPT_VERSION = "v628.1"
LLM_DRAFT_CANDIDATE_CAP = _int_env("PROACTIVE_LLM_DRAFT_CANDIDATE_CAP", 5)
LLM_DRAFT_TIMEOUT_SEC = _float_env("PROACTIVE_LLM_DRAFT_TIMEOUT_SEC", 8.0)
LLM_DRAFT_MAX_FAILOVER_HOPS = 2

ACT_POLICY_VERSION = "v63.1"
ACT_CANDIDATE_CAP = _int_env("PROACTIVE_ACT_CANDIDATE_CAP", 1)
ACT_LEASE_SECONDS = _int_env("PROACTIVE_ACT_LEASE_SECONDS", 45)
ACT_WRITER_TIMEOUT_SEC = _float_env("PROACTIVE_ACT_WRITER_TIMEOUT_SEC", 8.0)
ACT_MAX_ATTEMPTS = _int_env("PROACTIVE_ACT_MAX_ATTEMPTS", 3)
DAILY_ACT_INTERNAL_BUDGET = _int_env("PROACTIVE_DAILY_ACT_INTERNAL_BUDGET", 8)
DAILY_ACT_CALENDAR_BUDGET = _int_env("PROACTIVE_DAILY_ACT_CALENDAR_BUDGET", 2)


def ask_ttl_seconds() -> int:
    n = _int_env("PROACTIVE_ASK_TTL_SECONDS", 3600)
    if n < 1:
        return 3600
    if n > 86400:
        return 86400
    return n


def ask_private_in_app() -> bool:
    return _bool_env("ASK_PRIVATE_IN_APP", False)


def ask_cookie_secure() -> bool:
    return _bool_env("DOOM_ASK_COOKIE_SECURE", False)


def ask_unlock_secret() -> str:
    return (os.getenv("DOOM_ASK_UNLOCK") or "").strip()


def ask_allowed_origins() -> frozenset:
    raw = (os.getenv("DOOM_ASK_ALLOWED_ORIGINS") or "").strip()
    if not raw:
        return frozenset({"http://127.0.0.1:8000", "http://localhost:8000"})
    return frozenset(p.strip().rstrip("/") for p in raw.split(",") if p.strip())


PREDICTION_EMIT_FLOOR = _float_env("PROACTIVE_PREDICTION_EMIT_FLOOR", 0.70)
C_MAX_EMAIL_SINGLE = 0.55
C_MAX_DEFAULT = 0.95
N_CAP = 3
PREDICTION_EVAL_CAP = _int_env("PROACTIVE_PREDICTION_EVAL_CAP", 80)
OPEN_REVIEW_AGING_FLOOR = 0.68
RULE_VERSION = "v624.1"
RELIABILITY_R = {
    "task_engine": 0.90,
    "calendar": 0.85,
    "host": 0.80,
    "github": 0.70,
    "gmail_extract": 0.45,
}


def connector_vault_path() -> str:
    override = (os.getenv("DOOM_CONNECTOR_VAULT_PATH") or "").strip()
    if override:
        return override
    local = os.getenv("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(local, "DOOM", "connector_vault.dpapi")
