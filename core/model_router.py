from typing import Optional, List, Dict, Any
from models import (
    BaseLLMProvider, GroqProvider, OpenAIProvider,
    GeminiProvider, OllamaProvider, BedrockProvider, FallbackProvider, NIMProvider
)

class ModelRouter:
    """
    Intelligent Model Router for DOOM V2.
    Priority Order: Groq -> NIM (Nemotron) -> Bedrock -> OpenAI -> Gemini -> Ollama -> Fallback
    """
    def __init__(self):
        self.providers: Dict[str, BaseLLMProvider] = {
            "bedrock": BedrockProvider(),   # 1st: Amazon Bedrock (Claude 4.6, GPT-5.6, Grok, Nova)
            "groq": GroqProvider(),         # 2nd: Groq LLaMA 3.3 70B (ultra-fast)
            "nim": NIMProvider(),           # 3rd: NVIDIA NIM (Nemotron 3 Ultra, Llama 3.1)
            "openai": OpenAIProvider(),     # 4th: OpenAI GPT-4o
            "gemini": GeminiProvider(),     # 5th: Gemini 2.0 Flash
            "ollama": OllamaProvider(),     # 6th: Local Ollama
            "fallback": FallbackProvider()  # 7th: Zero-config local rule engine
        }

    def route(self, task_type: str = "general") -> BaseLLMProvider:
        """
        Routing Strategy:
        - 'coding' / 'multi_step' / 'reasoning': Bedrock (Claude Sonnet 4.6) -> Groq -> OpenAI -> Fallback
        - 'fast' / 'query' / 'conversation': Bedrock (Claude Haiku 3) -> Groq -> Gemini -> Fallback
        - 'offline': Fallback provider
        """
        # Priority: Groq (Ultra-fast active) -> NIM -> Bedrock -> OpenAI -> Gemini -> Ollama -> Fallback
        for name in ["groq", "nim", "bedrock", "openai", "gemini", "ollama"]:
            p = self.providers[name]
            if p.is_available():
                return p

        # Guaranteed fallback — always works, no API key needed
        return self.providers["fallback"]

    def get_provider_status(self) -> Dict[str, bool]:
        return {name: p.is_available() for name, p in self.providers.items()}

    def get_bedrock_status(self) -> Dict[str, Any]:
        bedrock = self.providers.get("bedrock")
        if bedrock and hasattr(bedrock, "get_status"):
            return bedrock.get_status()
        return {}

model_router = ModelRouter()
