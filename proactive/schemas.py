"""V6.1 canonical signal / insight models. Metadata only. Not memory."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SIGNAL_TYPES = frozenset({
    "TASK_STATUS",
    "MEMORY_LIFECYCLE",
    "PROJECT_CHANGE",
    "EXPERIENCE_CREATED",
    "STRATEGY_FAILURE",
    "REQUEST_COMPLETED",
    "HOST_TELEMETRY",
    "PROVIDER_CIRCUIT",
    "INACTIVITY",
    "CALENDAR_EVENT",
    "GITHUB_NOTIFICATION",
    "GITHUB_ISSUE",
    "GITHUB_REVIEW_REQUEST",
})

SOURCES = frozenset({
    "task_engine",
    "lifecycle",
    "projects",
    "experiences",
    "strategies",
    "otp",
    "system_telemetry",
    "circuit_breaker",
    "clock",
    "test",
    "calendar_google",
    "github",
})

PRIVACY_CLASSES = frozenset({"NORMAL", "PRIVATE", "SENSITIVE"})
SIGNAL_STATUSES = frozenset({
    "PENDING", "CLAIMED", "PROCESSED", "DEAD", "DROPPED", "DEDUPED",
})
INTERVENTIONS = frozenset({"IGNORE", "INFORM"})
INSIGHT_STATUSES = frozenset({
    "OPEN", "EXPIRED", "DELIVERED", "SUPPRESSED",
})

FORBIDDEN_PAYLOAD_KEYS = frozenset({
    "prompt", "response", "reasoning", "reasoning_summary", "chain_of_thought",
    "cot", "memory_content", "content", "query", "request", "stdout", "stderr",
    "file_content", "body", "api_key", "authorization", "password", "secret",
    "token", "headers", "url", "user_request", "goal_text", "command",
    "user_command", "response_text", "command_logs",
})

INJECTION_MARKERS = (
    "ignore previous",
    "system instruction",
    "bypass governance",
    "execute tool",
    "run shell",
    "sudo ",
    "ignore all rules",
    "approve action",
    "send message",
    "delete memory",
    "change policy",
    "[/data_only]",
    "[data_only]",
    "system:",
)

CREDENTIAL_VALUE_MARKERS = (
    "gsk_",
    "sk-",
    "nvapi-",
    "akia",
    "bearer ",
    "password=",
    "password :",
    "passwd=",
    "pwd=",
    "api_key=",
    "api_key:",
    "api-key=",
    "apikey=",
    "secret=",
    "secret:",
    "token=",
    "token:",
    "authorization=",
    "private_key",
    "private-key",
    "client_secret",
    "begin rsa private",
)


@dataclass
class ProactiveSignal:
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    signal_type: str = ""
    source: str = ""
    entity_type: str = ""
    entity_id: str = ""
    occurred_at: float = field(default_factory=time.time)
    ingested_at: float = field(default_factory=time.time)
    privacy_class: str = "NORMAL"
    idempotency_key: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    status: str = "PENDING"
    owner_id: str = "sujal"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Insight:
    insight_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    insight_type: str = ""
    source_signal_ids: List[str] = field(default_factory=list)
    entity_type: str = ""
    entity_id: str = ""
    project_id: Optional[str] = None
    significance_score: float = 0.0
    confidence: float = 0.0
    urgency: str = "low"
    privacy_class: str = "NORMAL"
    recommended_intervention: str = "IGNORE"
    created_at: float = field(default_factory=time.time)
    valid_until: float = 0.0
    status: str = "OPEN"
    dedupe_key: str = ""
    template_id: str = ""
    safe_params: Dict[str, Any] = field(default_factory=dict)
    owner_id: str = "sujal"
    proactive_cycle_id: str = ""


def compute_idempotency_key(
    source: str,
    entity_id: str,
    signal_type: str,
    time_bucket: int,
    extra: str = "",
) -> str:
    raw = f"{source}|{entity_id}|{signal_type}|{time_bucket}|{extra}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]


def time_bucket(ts: float, bucket_seconds: int) -> int:
    return int(ts) // max(1, bucket_seconds)


def dump_bounded_payload(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
