"""Guarded LLM invoke. Application code must not call provider._generate directly."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.cost_guard.guard import cost_guard
from core.cost_guard.hosts import hostname_from_url
from core.cost_guard.types import (
    CostGuardBlockedError,
    ResourceRequest,
    ResourceType,
)


def llm_request_for_provider(
    provider: Any,
    *,
    capability: str = "",
    privacy_class: str = "",
    correlation_id: str = "",
) -> ResourceRequest:
    name = str(getattr(provider, "name", "") or "").strip().lower()
    model = ""
    try:
        raw = getattr(provider, "model", "")
        model = str(raw() if callable(raw) else raw or "")[:120]
    except Exception:
        model = ""
    endpoint = str(getattr(provider, "base_url", "") or "")
    host = hostname_from_url(endpoint)
    return ResourceRequest(
        resource_type=ResourceType.LLM,
        provider=name,
        capability=capability,
        model=model,
        endpoint=endpoint,
        host=host,
        privacy_class=privacy_class,
        correlation_id=correlation_id,
    )


def authorize_llm_provider(provider: Any, *, capability: str = "", privacy_class: str = "") -> Any:
    req = llm_request_for_provider(provider, capability=capability, privacy_class=privacy_class)
    return cost_guard.authorize(req)


def health_check_authorized(provider: Any, *, capability: str = "health") -> bool:
    """True only if Cost Guard ALLOW before any external availability probe."""
    return bool(authorize_llm_provider(provider, capability=capability).is_allow)


def guarded_llm_generate(
    provider: Any,
    prompt: str,
    system_prompt: str = "",
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.7,
    *,
    capability: str = "",
    privacy_class: str = "",
    **kwargs: Any,
):
    decision = authorize_llm_provider(
        provider, capability=capability, privacy_class=privacy_class
    )
    if not decision.allows_invoke:
        raise CostGuardBlockedError(decision)
    impl = getattr(provider, "_generate", None)
    if impl is None:
        raise CostGuardBlockedError(decision)
    return impl(
        prompt=prompt,
        system_prompt=system_prompt,
        tools=tools,
        temperature=temperature,
        **kwargs,
    )
