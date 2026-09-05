from typing import Optional, List, Dict, Any
from models import (
    BaseLLMProvider, LLMResponse, GroqProvider, OpenAIProvider,
    GeminiProvider, OllamaProvider, BedrockProvider, FallbackProvider, NIMProvider
)


class ModelRouter:
    """
    Intelligent Capability-Based Model Router for DOOM V3.
    Matches task requirements to model capabilities with automatic multi-tier failover.
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

        self.capability_priorities = {
            "coding": ["groq", "bedrock", "openai", "gemini", "ollama", "fallback"],
            "reasoning": ["groq", "bedrock", "openai", "gemini", "ollama", "fallback"],
            "multi_step": ["groq", "bedrock", "openai", "gemini", "fallback"],
            "vision": ["gemini", "openai", "bedrock", "groq", "fallback"],
            "web_research": ["gemini", "groq", "openai", "bedrock", "fallback"],
            "fast_conversation": ["groq", "gemini", "openai", "bedrock", "ollama", "fallback"],
            "general": ["groq", "bedrock", "openai", "gemini", "ollama", "fallback"],
            "offline": ["ollama", "fallback"]
        }

    def route(self, task_type: str = "general") -> BaseLLMProvider:
        """Finds the optimal online provider based on task capabilities."""
        key = (task_type or "general").lower()
        priorities = self.capability_priorities.get(key, self.capability_priorities["general"])
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
        Executes generation with automatic failover.
        If the primary routed provider throws an exception, tries the next in the cascade.
        """
        key = (task_type or "general").lower()
        if provider_override and provider_override in self.providers and self.providers[provider_override].is_available():
            cascade = [provider_override] + [p for p in self.capability_priorities.get(key, self.capability_priorities["general"]) if p != provider_override]
        else:
            cascade = self.capability_priorities.get(key, self.capability_priorities["general"])

        for name in cascade:
            provider = self.providers.get(name)
            if not provider or not provider.is_available():
                continue
            try:
                response = provider.generate(prompt=prompt, system_prompt=system_prompt, tools=tools)
                if response and (response.text or response.tool_calls):
                    return response
            except Exception as e:
                print(f"[MODEL ROUTER] Provider '{name}' failed ({e}). Attempting failover...")
                continue

        # Last resort: guaranteed local fallback
        return self.providers["fallback"].generate(prompt=prompt, system_prompt=system_prompt, tools=tools)

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

