from typing import Optional, List, Dict, Any
from models import (
    BaseLLMProvider, LLMResponse, GroqProvider, OpenAIProvider,
    GeminiProvider, OllamaProvider, BedrockProvider, FallbackProvider, NIMProvider,
    ProviderCostTier, ProviderDeploymentMode
)


from enum import Enum


class ModelCapability(str, Enum):
    CODING = "coding"
    REASONING = "reasoning"
    MULTI_STEP = "multi_step"
    VISION = "vision"
    WEB_RESEARCH = "web_research"
    FAST_CONVERSATION = "fast_conversation"
    GENERAL = "general"
    OFFLINE = "offline"


class NoCapableProviderError(Exception):
    """Raised when no provider with required capability is available."""
    def __init__(self, task_type: str, available_providers: List[str]):
        self.task_type = task_type
        self.available_providers = available_providers
        super().__init__(f"No capable provider for '{task_type}'. Available: {available_providers}")


class CapabilityFailoverManager:
    """Helper for capability-preserving provider failover."""
    def __init__(self, router: Optional['ModelRouter'] = None):
        self._router = router

    @property
    def router(self) -> 'ModelRouter':
        return self._router or model_router

    def get_next_provider(self, capability: Any, exclude: Optional[List[str]] = None) -> BaseLLMProvider:
        cap_str = capability.value if hasattr(capability, 'value') else str(capability)
        exclude = exclude or []
        capable = [p for p in self.router._get_capable_providers(cap_str) if p not in exclude]
        if not capable:
            raise NoCapableProviderError(cap_str, [p for p in self.router.providers.keys() if p not in exclude])
        return self.router.providers[capable[0]]


class ModelRouter:
    """
    Intelligent Capability-Based Model Router for DOOM.
    Matches task requirements to model capabilities with automatic capability-preserving failover.
    Routing decision order:
    1. Task/capability requirements
    2. Provider enabled state
    3. Provider configuration/authentication
    4. Provider/model availability
    5. Provider health/circuit state
    6. Context-window compatibility
    7. Multimodal/tool-calling/streaming requirements
    8. Task suitability
    9. Cost tier (tiebreaker)
    10. Latency/reliability
    11. Configured preference
    """

    def __init__(self):
        # Initialize all providers (registered but may be disabled)
        self.providers: Dict[str, BaseLLMProvider] = {
            "ollama": OllamaProvider(),      # LOCAL - runs locally
            "nim": NIMProvider(),            # FREE_TIER - NVIDIA hosted free endpoint
            "groq": GroqProvider(),          # FREE_TIER - Groq cloud free tier
            "openai": OpenAIProvider(),      # PAID - OpenAI
            "gemini": GeminiProvider(),      # PAID - Google
            "bedrock": BedrockProvider(),    # PAID - AWS Bedrock (DISABLED BY DEFAULT)
            "fallback": FallbackProvider(),  # LOCAL - zero-config rule engine
        }

        # Capability requirements per task type
        self.capability_requirements = {
            "coding": ["tool_calling", "code_generation"],
            "reasoning": ["reasoning", "tool_calling"],
            "multi_step": ["tool_calling", "reasoning", "code_generation"],
            "vision": ["vision", "tool_calling"],
            "web_research": ["web_search", "tool_calling"],
            "fast_conversation": ["fast_inference"],
            "general": ["tool_calling"],
            "offline": ["offline"],
        }

        # Provider capabilities (from provider metadata)
        self.provider_capabilities = {
            "ollama": ["code_generation", "reasoning", "offline"],
            "nim": ["tool_calling", "code_generation", "reasoning", "coding"],
            "groq": ["tool_calling", "code_generation", "reasoning", "fast_inference", "coding"],
            "bedrock": ["tool_calling", "code_generation", "reasoning", "vision", "coding"],
            "openai": ["tool_calling", "code_generation", "reasoning", "vision", "web_search", "coding"],
            "gemini": ["code_generation", "reasoning", "vision", "web_search", "coding"],
            "fallback": ["deterministic_dispatch"],  # No actual LLM capabilities
        }

        # Cost tier ordering (lower = preferred) - used as tiebreaker
        self.cost_tier_order = {
            "LOCAL": 0,
            "FREE_TIER": 1,
            "LOW_COST": 2,
            "PAID": 3,
            "DISABLED": 99,
        }

        # Task-aware capability priorities (ordered by suitability, not just cost)
        self.capability_priorities = {
            "coding": ["nim", "groq", "ollama", "openai", "gemini"],
            "reasoning": ["nim", "groq", "ollama", "openai", "gemini"],
            "multi_step": ["nim", "groq", "ollama", "openai", "gemini"],
            "vision": ["gemini", "openai", "bedrock"],
            "web_research": ["gemini", "groq", "openai"],
            "fast_conversation": ["groq", "nim", "ollama", "bedrock", "openai", "gemini"],
            "general": ["nim", "groq", "ollama", "openai", "gemini"],
            "offline": ["ollama"],
        }

        # Initialize provider capabilities from metadata
        for name, p in self.providers.items():
            caps = getattr(p, 'capabilities', self.provider_capabilities.get(name, []))
            setattr(p, "capabilities", caps)

    def _get_provider_cost_tier(self, name: str) -> int:
        """Get cost tier order for a provider (lower = more preferred)."""
        provider = self.providers.get(name)
        if provider:
            cost_tier = getattr(provider, 'cost_tier', 'PAID')
            return self.cost_tier_order.get(cost_tier, 99)
        return 99

    def _has_capability(self, provider_name: str, required_capabilities: List[str]) -> bool:
        """Check if a provider has all required capabilities."""
        provider_caps = self.provider_capabilities.get(provider_name, [])
        return all(cap in provider_caps for cap in required_capabilities)

    def _get_capable_providers(self, task_type: str) -> List[str]:
        """Get list of enabled, available providers that have the required capability."""
        key = (task_type or "general").lower()
        required_caps = self.capability_requirements.get(key, ["tool_calling"])
        priorities = self.capability_priorities.get(key, self.capability_priorities["general"])

        capable = []
        for name in priorities:
            p = self.providers.get(name)
            if not p:
                continue
            # Check enabled state first
            if not p.is_enabled():
                continue
            # Check availability (configured + authenticated + healthy)
            if not p.is_available():
                continue
            # Check capability
            if not self._has_capability(name, required_caps):
                continue
            capable.append(name)
        return capable

    def _sort_by_cost_tier(self, providers: List[str]) -> List[str]:
        """Sort providers by cost tier (lower cost first) as tiebreaker."""
        return sorted(providers, key=lambda name: self._get_provider_cost_tier(name))

    def _get_routable_providers(self, task_type: str) -> List[str]:
        """Get all routable providers for a task type, sorted by capability priority then cost tier."""
        capable = self._get_capable_providers(task_type)
        # Sort by priority first, then by cost tier
        priorities = self.capability_priorities.get(task_type, self.capability_priorities["general"])
        # Filter to only capable providers, maintaining priority order
        prioritized = [name for name in priorities if name in capable]
        # Then sort by cost tier within same priority (stable sort)
        return self._sort_by_cost_tier(prioritized)

    def route(self, task_type: str = "general") -> BaseLLMProvider:
        """Finds the optimal online provider based on task capabilities."""
        from core.reliability.circuit_breaker import provider_circuit_breaker
        key = (task_type or "general").lower()
        routable = self._get_routable_providers(key)
        for name in routable:
            p = self.providers.get(name)
            if p and p.is_available() and provider_circuit_breaker.can_attempt(name):
                return p
        # Fallback probe if all breakers open
        for name in routable:
            p = self.providers.get(name)
            if p and p.is_available():
                return p
        return self.providers["fallback"]

    def select_provider(self, prompt: str) -> BaseLLMProvider:
        """Select best provider based on prompt analysis (for IDE compatibility)."""
        # Simple heuristic: coding-related prompts -> coding providers
        prompt_lower = prompt.lower()
        if any(kw in prompt_lower for kw in ["code", "python", "function", "class", "debug", "script", "api"]):
            return self.route("coding")
        elif any(kw in prompt_lower for kw in ["reason", "analyze", "think", "logic", "solve"]):
            return self.route("reasoning")
        elif any(kw in prompt_lower for kw in ["image", "vision", "visual", "screenshot", "photo"]):
            return self.route("vision")
        elif any(kw in prompt_lower for kw in ["search", "web", "news", "weather", "stock"]):
            return self.route("web_research")
        else:
            return self.route("general")

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        task_type: str = "general",
        provider_override: Optional[str] = None
    ) -> LLMResponse:
        """
        Executes generation with capability-preserving failover and circuit breaker protection.
        If the primary routed provider throws an exception, tries the next CAPABLE provider.
        Raises NoCapableProviderError if no capable provider is available.
        """
        from core.reliability.circuit_breaker import provider_circuit_breaker
        key = (task_type or "general").lower()
        required_caps = self.capability_requirements.get(key, ["tool_calling"])

        if provider_override and provider_override in self.providers and self.providers[provider_override].is_available():
            cascade = [provider_override] + [p for p in self._get_routable_providers(key) if p != provider_override]
        else:
            cascade = self._get_routable_providers(key)

        # Filter cascade to only capable providers
        capable_cascade = [name for name in cascade if self._has_capability(name, required_caps)]

        for name in capable_cascade:
            provider = self.providers.get(name)
            if not provider or not provider.is_available():
                continue
            if not provider_circuit_breaker.can_attempt(name):
                print(f"[MODEL ROUTER] Circuit open for '{name}' -> skipping to next provider.")
                continue
            try:
                response = provider.generate(prompt=prompt, system_prompt=system_prompt, tools=tools)
                if response and (response.text or response.tool_calls):
                    provider_circuit_breaker.record_success(name)
                    return response
            except Exception as e:
                provider_circuit_breaker.record_failure(name)
                print(f"[MODEL ROUTER] Provider '{name}' failed with {e}. Failing over...")
                print(f"[MODEL ROUTER] Provider '{name}' failed ({e}). Attempting capability-preserving failover...")
                continue

        # No capable provider available - raise exception for orchestrator to handle
        available_providers = [name for name in self.capability_priorities.get(key, [])
                               if self.providers.get(name) and self.providers[name].is_available()]
        if available_providers:
            # Providers available but none with required capability
            raise NoCapableProviderError(task_type, available_providers)

        # No providers available at all - this is a hard outage
        raise NoCapableProviderError(task_type, [])

    def get_provider_status(self) -> Dict[str, bool]:
        return {name: p.is_available() for name, p in self.providers.items()}

    def get_provider_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Get rich metadata for all registered providers."""
        metadata = {}
        for name, p in self.providers.items():
            metadata[name] = p.get_metadata()
        return metadata

    def get_intelligence_matrix(self) -> List[Dict[str, Any]]:
        """Returns comprehensive capability matrix for the System / Intelligence UI."""
        metadata = {
            "ollama": {"model": "Local LLaMA 3", "role": "Private Offline Reasoning", "tier": 1, "cost_tier": "LOCAL"},
            "nim": {"model": "NVIDIA Nemotron 3 Ultra", "role": "High-Precision Reasoning & Logic", "tier": 2, "cost_tier": "FREE_TIER"},
            "groq": {"model": "LLaMA 3.3 70B Versatile", "role": "Ultra-Fast Voice & Reflexes (~500 t/s)", "tier": 3, "cost_tier": "FREE_TIER"},
            "bedrock": {"model": "Claude 4.6 Sonnet / Haiku", "role": "Autonomous Coding & Architecture", "tier": 4, "cost_tier": "PAID"},
            "openai": {"model": "GPT-4o Omnimodal", "role": "Complex Problem Solving", "tier": 5, "cost_tier": "PAID"},
            "gemini": {"model": "Gemini 2.0 Flash", "role": "Multimodal Vision & Web Research", "tier": 5, "cost_tier": "PAID"},
            "fallback": {"model": "DOOM Rule Engine", "role": "Zero-Config Deterministic Dispatch", "tier": 6, "cost_tier": "LOCAL"}
        }
        matrix = []
        for name, p in self.providers.items():
            meta = metadata.get(name, {"model": name, "role": "Provider", "tier": 99, "cost_tier": "UNKNOWN"})
            matrix.append({
                "key": name,
                "name": p.name,
                "model": meta["model"],
                "role": meta["role"],
                "tier": meta["tier"],
                "cost_tier": meta["cost_tier"],
                "is_available": p.is_available(),
                "is_enabled": p.is_enabled(),
                "is_enabled_for_routing": p.is_enabled() and p.is_available(),
            })
        return sorted(matrix, key=lambda x: x["tier"])

    def get_bedrock_status(self) -> Dict[str, Any]:
        bedrock = self.providers.get("bedrock")
        if bedrock and hasattr(bedrock, "get_status"):
            return bedrock.get_status()
        return {}


model_router = ModelRouter()