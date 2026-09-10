"""DOOM Global Cost Guard — policy boundary only. Not a router or provider."""

from core.cost_guard.guard import CostGuard, cost_guard
from core.cost_guard.hosts import hostname_from_url, is_loopback_host, is_loopback_url
from core.cost_guard.invoke import (
    authorize_llm_provider,
    guarded_llm_generate,
    health_check_authorized,
    llm_request_for_provider,
)
from core.cost_guard.registry import DEFAULT_REGISTRY, ResourceAttestation
from core.cost_guard.types import (
    CostClass,
    CostDecision,
    CostDecisionAction,
    CostGuardBlockedError,
    CostPolicyMode,
    CostReason,
    ResourceRequest,
    ResourceType,
)

__all__ = [
    "CostGuard",
    "cost_guard",
    "CostClass",
    "CostDecision",
    "CostDecisionAction",
    "CostGuardBlockedError",
    "CostPolicyMode",
    "CostReason",
    "ResourceRequest",
    "ResourceType",
    "ResourceAttestation",
    "DEFAULT_REGISTRY",
    "authorize_llm_provider",
    "guarded_llm_generate",
    "health_check_authorized",
    "llm_request_for_provider",
    "is_loopback_host",
    "is_loopback_url",
    "hostname_from_url",
]
