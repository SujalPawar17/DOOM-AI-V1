"""DOOM Cost Guard — authoritative spend policy. Fail closed. HARD $0 default."""

from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

from core.cost_guard.hosts import hostname_from_url, is_loopback_host, is_loopback_url
from core.cost_guard.registry import (
    DEFAULT_REGISTRY,
    ResourceAttestation,
    lookup_attestation,
)
from core.cost_guard.types import (
    CostClass,
    CostDecision,
    CostDecisionAction,
    CostPolicyMode,
    CostReason,
    ResourceRequest,
    ResourceType,
    SECRET_FIELD_NAMES,
)


class CostGuard:
    """Answers: may this resource be used under the current cost policy?"""

    def __init__(
        self,
        registry: Optional[Dict[Tuple[str, str], ResourceAttestation]] = None,
        policy: CostPolicyMode = CostPolicyMode.HARD_ZERO,
    ):
        self._registry = registry if registry is not None else DEFAULT_REGISTRY
        self._policy = policy
        self._lock = threading.Lock()
        self._decisions: List[CostDecision] = []

    @property
    def policy(self) -> CostPolicyMode:
        return self._policy

    def classify(self, request: ResourceRequest) -> CostClass:
        provider = (request.provider or "").strip().lower()
        if not provider:
            return CostClass.UNKNOWN

        host = (request.host or "").strip().lower() or hostname_from_url(request.endpoint)

        if request.resource_type == ResourceType.STT and provider == "local_whisper":
            endpoint = request.endpoint or host
            if endpoint and not is_loopback_url(endpoint) and not is_loopback_host(host or endpoint):
                return CostClass.UNKNOWN
            if host and not is_loopback_host(host) and not is_loopback_url(request.endpoint or host):
                return CostClass.UNKNOWN
            return CostClass.LOCAL_FREE

        if request.resource_type == ResourceType.LLM and provider == "ollama":
            endpoint = request.endpoint or host
            if endpoint and not is_loopback_url(endpoint) and not is_loopback_host(host or endpoint):
                return CostClass.UNKNOWN
            if host and not is_loopback_host(host) and not is_loopback_url(request.endpoint or host):
                return CostClass.UNKNOWN
            return CostClass.LOCAL_FREE

        if request.resource_type == ResourceType.DATABASE and provider == "postgres":
            target = host or (request.endpoint or "").strip().lower()
            if target and not is_loopback_host(target):
                return CostClass.UNKNOWN
            return CostClass.LOCAL_FREE

        if request.resource_type == ResourceType.HTTP:
            if host and is_loopback_host(host):
                return CostClass.LOCAL_FREE
            att = lookup_attestation(self._registry, request.resource_type, provider)
            if att:
                return att.cost_class
            return CostClass.UNKNOWN

        att = lookup_attestation(self._registry, request.resource_type, provider)
        if att is None:
            return CostClass.UNKNOWN

        if att.cost_class == CostClass.FREE_TIER and att.models_required:
            model = (request.model or "").strip()
            if not model or model not in att.allowed_models:
                return CostClass.UNKNOWN

        return att.cost_class

    def decision(self, request: ResourceRequest) -> CostDecision:
        cost_class = self.classify(request)
        att = lookup_attestation(self._registry, request.resource_type, request.provider)

        if request.resource_type in (ResourceType.LLM,) and att and att.models_required:
            model = (request.model or "").strip()
            if not model or (att.allowed_models and model not in att.allowed_models):
                return CostDecision(
                    action=CostDecisionAction.BLOCK,
                    cost_class=CostClass.UNKNOWN,
                    reason=CostReason.UNKNOWN_MODEL_BLOCKED,
                    request=request,
                )

        if cost_class == CostClass.LOCAL_FREE:
            return CostDecision(
                action=CostDecisionAction.ALLOW,
                cost_class=cost_class,
                reason=CostReason.LOCAL_RESOURCE_ALLOWED,
                request=request,
            )
        if cost_class == CostClass.FREE_TIER:
            if att is None or not att.verified_free_tier:
                return CostDecision(
                    action=CostDecisionAction.BLOCK,
                    cost_class=cost_class,
                    reason=CostReason.FREE_TIER_NOT_VERIFIED,
                    request=request,
                )
            if att.models_required:
                model = (request.model or "").strip()
                if not model or model not in att.allowed_models:
                    return CostDecision(
                        action=CostDecisionAction.BLOCK,
                        cost_class=CostClass.UNKNOWN,
                        reason=CostReason.UNKNOWN_MODEL_BLOCKED,
                        request=request,
                    )
            return CostDecision(
                action=CostDecisionAction.ALLOW,
                cost_class=cost_class,
                reason=CostReason.FREE_TIER_ALLOWED,
                request=request,
            )
        if cost_class == CostClass.PAID:
            return CostDecision(
                action=CostDecisionAction.BLOCK,
                cost_class=cost_class,
                reason=CostReason.PAID_PROVIDER_BLOCKED,
                request=request,
            )
        if cost_class == CostClass.UNAVAILABLE:
            return CostDecision(
                action=CostDecisionAction.WAIT,
                cost_class=cost_class,
                reason=CostReason.RESOURCE_UNAVAILABLE,
                request=request,
            )
        if request.endpoint and not provider_known(self._registry, request) and request.resource_type == ResourceType.HTTP:
            return CostDecision(
                action=CostDecisionAction.BLOCK,
                cost_class=CostClass.UNKNOWN,
                reason=CostReason.UNKNOWN_ENDPOINT_BLOCKED,
                request=request,
            )
        reason = CostReason.UNKNOWN_PROVIDER_BLOCKED
        if (request.model or "").strip() and att and att.models_required:
            reason = CostReason.UNKNOWN_MODEL_BLOCKED
        if request.resource_type == ResourceType.HTTP:
            reason = CostReason.UNKNOWN_ENDPOINT_BLOCKED
        return CostDecision(
            action=CostDecisionAction.BLOCK,
            cost_class=CostClass.UNKNOWN,
            reason=reason,
            request=request,
        )

    def authorize(self, request: ResourceRequest) -> CostDecision:
        decided = self.decision(request)
        return self.record(decided)

    def record(self, decision: CostDecision) -> CostDecision:
        stored = CostDecision(
            action=decision.action,
            cost_class=decision.cost_class,
            reason=decision.reason,
            request=decision.request,
            detail=decision.detail,
            recorded=True,
        )
        with self._lock:
            self._decisions.append(stored)
        _emit_decision(stored)
        return stored

    def recent_decisions(self) -> List[CostDecision]:
        with self._lock:
            return list(self._decisions)

    def reset_records_for_tests(self) -> None:
        with self._lock:
            self._decisions.clear()


def provider_known(registry: Dict[Tuple[str, str], ResourceAttestation], request: ResourceRequest) -> bool:
    return lookup_attestation(registry, request.resource_type, request.provider) is not None


def _emit_decision(decision: CostDecision) -> None:
    try:
        from observability.telemetry import emit
        req = decision.request
        attrs = {
            "provider": (req.provider or "")[:80],
            "model": (req.model or "")[:80],
            "capability": (req.capability or "")[:80],
            "resource_type": req.resource_type.value,
            "classification": decision.cost_class.value,
            "cost_decision": decision.action.value,
            "cost_reason": decision.reason.value,
            "privacy_class": (req.privacy_class or "")[:40],
            "risk_class": (req.risk_class or "")[:40],
        }
        for k in list(attrs.keys()):
            if k.lower() in SECRET_FIELD_NAMES:
                attrs.pop(k, None)
        status = "ok" if decision.is_allow else "skipped"
        emit(
            "cost_guard.decision",
            "cost",
            status=status,
            component="cost_guard",
            operation="authorize",
            attributes=attrs,
        )
    except Exception:
        pass


cost_guard = CostGuard()
