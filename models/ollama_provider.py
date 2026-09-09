import os
import requests
import json
from typing import List, Dict, Any, Optional
from models.base_provider import BaseLLMProvider, LLMResponse, ProviderCostTier, ProviderDeploymentMode


class OllamaProvider(BaseLLMProvider):
    name = "ollama"
    display_name = "Ollama (Local)"
    cost_tier = ProviderCostTier.LOCAL.value
    deployment_mode = ProviderDeploymentMode.LOCAL.value
    billing_possible = False
    capabilities = ["code_generation", "reasoning", "offline"]
    models = ["llama3", "llama3.1", "mistral", "codellama", "phi3"]
    context_limit = 8192
    streaming = True
    tool_calling = False  # Ollama /api/generate does not support tool calling
    multimodal = False

    def __init__(self, model: str = "llama3", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url
        self._enabled = True

    def is_configured(self) -> bool:
        return True  # No API key needed

    def is_authenticated(self) -> bool:
        return self.is_available()

    def is_available(self) -> bool:
        if not self._enabled:
            return False
        try:
            res = requests.get(f"{self.base_url}/api/tags", timeout=1)
            return res.status_code == 200
        except Exception:
            return False

    def is_healthy(self) -> bool:
        return self.is_available()

    def generate(self,
                 prompt: str,
                 system_prompt: str = "",
                 tools: Optional[List[Dict[str, Any]]] = None,
                 temperature: float = 0.7) -> LLMResponse:
        url = f"{self.base_url}/api/generate"
        full_prompt = f"{system_prompt}\n\nUser: {prompt}\nDOOM:" if system_prompt else prompt
        
        payload = {
            "model": self.model,
            "prompt": full_prompt,
            "stream": False,
            "options": {"temperature": temperature}
        }
        res = requests.post(url, json=payload, timeout=30)
        res.raise_for_status()
        data = res.json()

        return LLMResponse(
            text=data.get("response", ""),
            tool_calls=[],
            model_name=f"ollama/{self.model}"
        )

    def disable(self):
        self._enabled = False

    def enable(self):
        self._enabled = True

    def is_enabled(self) -> bool:
        return self._enabled
