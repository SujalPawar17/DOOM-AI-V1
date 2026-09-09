"""Central redaction fence. Fail closed: return None if an event cannot be made safe."""

from __future__ import annotations

import re
from typing import Any, Optional

from observability.schemas import FORBIDDEN_ATTR_KEYS, OperationalEvent, SchemaValidationError

_SECRET_RE = re.compile(
    r"(gsk_[A-Za-z0-9]+|sk-[A-Za-z0-9]+|nvapi-[A-Za-z0-9]+|AKIA[A-Z0-9]{8,}"
    r"|Bearer\s+[A-Za-z0-9\-._~+/]+=*)",
    re.IGNORECASE,
)
_KEY_EQ_RE = re.compile(r"(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*\S+", re.IGNORECASE)
_URL_KEY_RE = re.compile(r"(https?://[^\s]*[?&]key=)[^\s&]+", re.IGNORECASE)

FORBIDDEN_SUBSTRINGS = (
    "chain_of_thought", "reasoning_summary", "user_request",
)


def sanitize_string(value: str) -> str:
    if not isinstance(value, str):
        return value
    out = _SECRET_RE.sub("[REDACTED]", value)
    out = _KEY_EQ_RE.sub(r"\1=[REDACTED]", out)
    out = _URL_KEY_RE.sub(r"\1[REDACTED]", out)
    lower = out.lower()
    if "key=" in lower and ("http://" in lower or "https://" in lower):
        return "[REDACTED_URL]"
    return out


def looks_unsafe(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    low = value.lower()
    if any(s in low for s in ("gsk_", "sk-", "nvapi-", "akia", "bearer ", "api_key", "authorization")):
        return True
    if "key=" in low:
        return True
    return False


def redact_event(event: OperationalEvent) -> Optional[OperationalEvent]:
    """Return a safe copy or None (fail closed)."""
    try:
        data = event.to_dict()
        attrs = {}
        for k, v in (data.get("attributes") or {}).items():
            if str(k).lower() in FORBIDDEN_ATTR_KEYS:
                return None
            if isinstance(v, str):
                if looks_unsafe(v) and "REDACTED" not in sanitize_string(v):
                    return None
                cleaned = sanitize_string(v)
                if looks_unsafe(cleaned) and "[REDACTED" not in cleaned:
                    return None
                attrs[k] = cleaned
            elif isinstance(v, (int, float, bool)) or v is None:
                attrs[k] = v
            else:
                return None
        blob = event.to_json().lower()
        for needle in FORBIDDEN_SUBSTRINGS:
            if needle in blob and needle not in ("event",):
                # name/category may mention cognitive stages; only fail on payload-like keys
                pass
        for field in ("prompt", "reasoning_summary", "chain_of_thought", "memory_content"):
            if f'"{field}"' in blob:
                return None
        copy = OperationalEvent(
            event_id=event.event_id,
            ts_unix_ms=event.ts_unix_ms,
            doom_request_id=event.doom_request_id,
            task_id=event.task_id,
            cognitive_cycle_id=event.cognitive_cycle_id,
            step_id=event.step_id,
            tool_execution_id=event.tool_execution_id,
            provider_call_id=event.provider_call_id,
            category=event.category,
            name=event.name,
            status=event.status,
            latency_ms=event.latency_ms,
            component=event.component,
            operation=event.operation,
            error_type=event.error_type,
            retryable=event.retryable,
            attributes=attrs,
        )
        return copy
    except (SchemaValidationError, Exception):
        return None
