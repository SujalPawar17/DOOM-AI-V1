"""V5.3.7.4 OperationalEvent schema — metadata only, strongly validated."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

CATEGORIES = frozenset({
    "request", "cognitive", "task", "step", "tool", "provider",
    "memory", "vector", "verify", "retry", "security", "ws",
    "proactive",
})

STATUSES = frozenset({"ok", "error", "timeout", "skipped", "fallback"})

ERROR_TYPES = frozenset({
    "VALIDATION_ERROR", "AUTHENTICATION_ERROR", "AUTHORIZATION_ERROR",
    "TIMEOUT", "RATE_LIMIT", "PROVIDER_ERROR", "MODEL_ERROR", "TOOL_ERROR",
    "DATABASE_ERROR", "MEMORY_ERROR", "VECTOR_ERROR", "PLANNER_ERROR",
    "COGNITIVE_ERROR", "VERIFICATION_ERROR", "NETWORK_ERROR",
    "DEPENDENCY_ERROR", "UNKNOWN_ERROR",
})

FORBIDDEN_ATTR_KEYS = frozenset({
    "prompt", "response", "reasoning", "reasoning_summary", "chain_of_thought",
    "cot", "memory_content", "content", "query", "request", "stdout", "stderr",
    "file_content", "file_contents", "api_key", "authorization", "password",
    "secret", "token", "headers", "url", "body", "user_request", "goal_text",
})

ALLOWED_ATTR_KEYS = frozenset({
    "provider", "model", "cost_tier", "capability", "attempt", "fallback",
    "final_provider", "tool", "stage", "step_count", "replan_count",
    "decision_type", "termination_reason", "privacy_class", "memory_type",
    "memory_id", "candidate_count", "selected_count", "retrieval_mode",
    "verdict", "attempt_number", "budget_exhausted", "circuit_skipped",
    "next_provider", "failed_provider", "reason", "prompt_len", "response_len",
    "intent", "status_detail", "hop", "hops", "cycle", "enabled",
    "event", "privacy_ok", "has_memories", "status",
    "insight_id", "signal_id", "intervention", "score_bucket",
    "owner_id", "queue_depth", "signal_type", "privacy_ok",
    "connector_id", "fact_id", "capability",
})


class SchemaValidationError(ValueError):
    pass


def classify_error(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = str(exc).lower()
    if "timeout" in name.lower() or "timeout" in msg:
        return "TIMEOUT"
    if "ratelimit" in name.lower() or "rate limit" in msg or "429" in msg:
        return "RATE_LIMIT"
    if "auth" in name.lower() or "401" in msg or "403" in msg:
        if "401" in msg:
            return "AUTHENTICATION_ERROR"
        return "AUTHORIZATION_ERROR"
    if "Provider" in name or "no capable" in msg:
        return "PROVIDER_ERROR"
    if "vector" in msg:
        return "VECTOR_ERROR"
    if "memory" in msg:
        return "MEMORY_ERROR"
    if "verif" in msg:
        return "VERIFICATION_ERROR"
    if "network" in msg or "connection" in msg:
        return "NETWORK_ERROR"
    return "UNKNOWN_ERROR"


@dataclass
class OperationalEvent:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts_unix_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    doom_request_id: str = ""
    task_id: Optional[str] = None
    cognitive_cycle_id: Optional[str] = None
    step_id: Optional[str] = None
    tool_execution_id: Optional[str] = None
    provider_call_id: Optional[str] = None
    category: str = "request"
    name: str = ""
    status: str = "ok"
    latency_ms: Optional[float] = None
    component: str = ""
    operation: str = ""
    error_type: Optional[str] = None
    retryable: Optional[bool] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.category not in CATEGORIES:
            raise SchemaValidationError(f"invalid category: {self.category}")
        if self.status not in STATUSES:
            raise SchemaValidationError(f"invalid status: {self.status}")
        if not self.name or not isinstance(self.name, str):
            raise SchemaValidationError("name required")
        if self.error_type is not None and self.error_type not in ERROR_TYPES:
            raise SchemaValidationError(f"invalid error_type: {self.error_type}")
        if not isinstance(self.attributes, dict):
            raise SchemaValidationError("attributes must be a dict")
        for key in self.attributes:
            lk = str(key).lower()
            if lk in FORBIDDEN_ATTR_KEYS:
                raise SchemaValidationError(f"forbidden attribute: {key}")
            if lk not in ALLOWED_ATTR_KEYS:
                raise SchemaValidationError(f"unknown attribute: {key}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "ts_unix_ms": self.ts_unix_ms,
            "doom_request_id": self.doom_request_id,
            "task_id": self.task_id,
            "cognitive_cycle_id": self.cognitive_cycle_id,
            "step_id": self.step_id,
            "tool_execution_id": self.tool_execution_id,
            "provider_call_id": self.provider_call_id,
            "category": self.category,
            "name": self.name,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "component": self.component,
            "operation": self.operation,
            "error_type": self.error_type,
            "retryable": self.retryable,
            "attributes": dict(sorted(self.attributes.items())),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
