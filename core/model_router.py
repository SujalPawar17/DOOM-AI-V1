from typing import Optional, List, Dict, Any
from models import (
    BaseLLMProvider, LLMResponse, GroqProvider, OpenAIProvider,
    GeminiProvider, OllamaProvider, BedrockProvider, FallbackProvider, NIMProvider
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
    Intelligent Capability-Based Model Router for DOOM V3.3.
    Matches task requirements to model capabilities with automatic capability-preserving failover.
    """
    def __init__(self):
        self.providers: Dict[str, BaseLLMProvider] = {
            "groq": GroqProvider(),         # LLaMA 3.3 70B (ultra-fast ~500 t/s)
            "nim": NIMProvider(),           # NVIDIA NIM (Nemotron 3 Ultra, Llama 3.1)
            "bedrock": BedrockProvider(),   # Amazon Bedrock (Claude 4.6 Sonnet, Nova, Haiku)
            "openai": OpenAIProvider(),     # OpenAI GPT-4o
            "gemini": GeminiProvider(),     # Google Gemini 2.0 Flash
            "ollama": OllamaProvider(),     # Local Ollama
            "fallback": FallbackProvider()  # Zero-config local rule engine
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

        # Provider capabilities
        self.provider_capabilities = {
            "groq": ["tool_calling", "code_generation", "reasoning", "fast_inference", "coding"],
            "nim": ["tool_calling", "code_generation", "reasoning", "coding"],
            "bedrock": ["tool_calling", "code_generation", "reasoning", "vision", "coding"],
            "openai": ["tool_calling", "code_generation", "reasoning", "vision", "web_search", "coding"],
            "gemini": ["tool_calling", "code_generation", "reasoning", "vision", "web_search", "coding"],
            "ollama": ["tool_calling", "code_generation", "reasoning", "offline", "coding"],
            "fallback": ["deterministic_dispatch"],  # No actual LLM capabilities
        }

        for name, p in self.providers.items():
            setattr(p, "capabilities", self.provider_capabilities.get(name, []))

        self.capability_priorities = {
            "coding": ["groq", "bedrock", "openai", "gemini", "ollama"],
            "reasoning": ["groq", "bedrock", "openai", "gemini", "ollama"],
            "multi_step": ["groq", "bedrock", "openai", "gemini"],
            "vision": ["gemini", "openai", "bedrock", "groq"],
            "web_research": ["gemini", "groq", "openai", "bedrock"],
            "fast_conversation": ["groq", "gemini", "openai", "bedrock", "ollama"],
            "general": ["groq", "bedrock", "openai", "gemini", "ollama"],
            "offline": ["ollama"],
        }

    def _has_capability(self, provider_name: str, required_capabilities: List[str]) -> bool:
        """Check if a provider has all required capabilities."""
        provider_caps = self.provider_capabilities.get(provider_name, [])
        return all(cap in provider_caps for cap in required_capabilities)

    def _get_capable_providers(self, task_type: str) -> List[str]:
        """Get list of available providers that have the required capability."""
        key = (task_type or "general").lower()
        required_caps = self.capability_requirements.get(key, ["tool_calling"])
        priorities = self.capability_priorities.get(key, self.capability_priorities["general"])
        
        capable = []
        for name in priorities:
            p = self.providers.get(name)
            if p and p.is_available() and self._has_capability(name, required_caps):
                capable.append(name)
        return capable

    def route(self, task_type: str = "general") -> BaseLLMProvider:
        """Finds the optimal online provider based on task capabilities."""
        from core.reliability.circuit_breaker import provider_circuit_breaker
        key = (task_type or "general").lower()
        priorities = self.capability_priorities.get(key, self.capability_priorities["general"])
        for name in priorities:
            p = self.providers.get(name)
            if p and p.is_available() and provider_circuit_breaker.can_attempt(name):
                return p
        # Fallback probe if all breakers open
        for name in priorities:
            p = self.providers.get(name)
            if p and p.is_available():
                return p
        return self.providers["fallback"]

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
            cascade = [provider_override] + [p for p in self.capability_priorities.get(key, self.capability_priorities["general"]) if p != provider_override]
        else:
            cascade = self.capability_priorities.get(key, self.capability_priorities["general"])
        
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

    def get_intelligence_matrix(self) -> List[Dict[str, Any]]:
        """Returns comprehensive capability matrix for the System / Intelligence UI."""
        metadata = {
            "groq": {"model": "LLaMA 3.3 70B Versatile", "role": "Ultra-Fast Voice & Reflexes (~500 t/s)", "tier": 1},
            "nim": {"model": "NVIDIA Nemotron 3 Ultra", "role": "High-Precision Reasoning & Logic", "tier": 2},
            "bedrock": {"model": "Claude 4.6 Sonnet / Haiku", "role": "Autonomous Coding & Architecture", "tier": 3},
            "openai": {"model": "GPT-4o Omnimodal", "role": "Complex Problem Solving", "tier": 4},
            "gemini": {"model": "Gemini 2.0 Flash", "role": "Multimodal Vision & Web Research", "tier": 5},
            "ollama": {"model": "Local LLaMA 3", "role": "Private Offline Reasoning", "tier": 6},
            "fallback": {"model": "DOOM Rule Engine", "role": "Zero-Config Deterministic Dispatch", "tier": 7}
        }
        matrix = []
        for name, p in self.providers.items():
            meta = metadata.get(name, {"model": name, "role": "Provider", "tier": 99})
            matrix.append({
                "key": name,
                "name": p.name,
                "model": meta["model"],
                "role": meta["role"],
                "tier": meta["tier"],
                "is_available": p.is_available()
            })
        return sorted(matrix, key=lambda x: x["tier"])

    def get_bedrock_status(self) -> Dict[str, Any]:
        bedrock = self.providers.get("bedrock")
        if bedrock and hasattr(bedrock, "get_status"):
            return bedrock.get_status()
        return {}


model_router = ModelRouter()

