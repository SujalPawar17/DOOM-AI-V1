"""Canonical Cost Guard domain objects. Policy authority lives here, not on providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CostClass(str, Enum):
    LOCAL_FREE = "LOCAL_FREE"
    FREE_TIER = "FREE_TIER"
    PAID = "PAID"
    UNKNOWN = "UNKNOWN"
    UNAVAILABLE = "UNAVAILABLE"


class CostDecisionAction(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    WAIT = "WAIT"
    FALLBACK = "FALLBACK"


class CostReason(str, Enum):
    LOCAL_RESOURCE_ALLOWED = "LOCAL_RESOURCE_ALLOWED"
    FREE_TIER_ALLOWED = "FREE_TIER_ALLOWED"
    FREE_TIER_NOT_VERIFIED = "FREE_TIER_NOT_VERIFIED"
    PAID_PROVIDER_BLOCKED = "PAID_PROVIDER_BLOCKED"
    UNKNOWN_PROVIDER_BLOCKED = "UNKNOWN_PROVIDER_BLOCKED"
    UNKNOWN_MODEL_BLOCKED = "UNKNOWN_MODEL_BLOCKED"
    UNKNOWN_ENDPOINT_BLOCKED = "UNKNOWN_ENDPOINT_BLOCKED"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    LOCAL_MODEL_MISSING = "STT_UNAVAILABLE_LOCAL_MODEL_MISSING"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    HARD_ZERO_DEFAULT = "HARD_ZERO_DEFAULT"


class ResourceType(str, Enum):
    LLM = "LLM"
    EMBEDDING = "EMBEDDING"
    EMBEDDING_DOWNLOAD = "EMBEDDING_DOWNLOAD"
    TTS = "TTS"
    STT = "STT"
    STT_DOWNLOAD = "STT_DOWNLOAD"
    TRANSLATION = "TRANSLATION"
    SEARCH = "SEARCH"
    HTTP = "HTTP"
    CONNECTOR = "CONNECTOR"
    CONNECTOR_WRITE = "CONNECTOR_WRITE"
    DATABASE = "DATABASE"
    VISION = "VISION"
    OTHER = "OTHER"


class CostPolicyMode(str, Enum):
    HARD_ZERO = "HARD_ZERO"


@dataclass(frozen=True)
class ResourceRequest:
    resource_type: ResourceType
    provider: str
    capability: str = ""
    model: str = ""
    endpoint: str = ""
    host: str = ""
    privacy_class: str = ""
    risk_class: str = ""
    correlation_id: str = ""


@dataclass(frozen=True)
class CostDecision:
    action: CostDecisionAction
    cost_class: CostClass
    reason: CostReason
    request: ResourceRequest
    detail: str = ""
    recorded: bool = False

    @property
    def is_allow(self) -> bool:
        return self.action == CostDecisionAction.ALLOW

    @property
    def allows_invoke(self) -> bool:
        return self.action == CostDecisionAction.ALLOW


class CostGuardBlockedError(Exception):
    """Raised when an invoke is attempted after a non-ALLOW Cost Guard decision."""

    def __init__(self, decision: CostDecision):
        self.decision = decision
        super().__init__(
            f"Cost Guard {decision.action.value}: {decision.reason.value} "
            f"provider={decision.request.provider!r} resource={decision.request.resource_type.value}"
        )


SECRET_FIELD_NAMES = frozenset({
    "api_key", "authorization", "password", "secret", "token", "headers",
    "key", "xi-api-key", "openai_key", "groq_key",
})
