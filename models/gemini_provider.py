import os
import json
import requests
from typing import List, Dict, Any, Optional
from models.base_provider import BaseLLMProvider, LLMResponse, ProviderCostTier, ProviderDeploymentMode, ProviderRateLimitError, ProviderModelNotFoundError, ProviderAuthError


class GeminiProvider(BaseLLMProvider):
    name = "gemini"
    display_name = "Google Gemini 2.0 Flash"
    cost_tier = ProviderCostTier.PAID.value
    deployment_mode = ProviderDeploymentMode.HOSTED_CLOUD.value
    billing_possible = True
    capabilities = ["code_generation", "reasoning", "vision", "web_search", "coding"]
    models = ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"]
    context_limit = 1048576
    streaming = True
    tool_calling = False  # Not implemented in current version
    multimodal = True

    def __init__(self, model: str = "gemini-2.0-flash"):
        self.model = model
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
        self._enabled = True

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def is_authenticated(self) -> bool:
        return self.is_available()

    def is_available(self) -> bool:
        if not self._enabled:
            return False
        if not self.is_configured():
            return False
        return True

    def is_healthy(self) -> bool:
        return self.is_available()

    def _generate(self,
                 prompt: str,
                 system_prompt: str = "",
                 tools: Optional[List[Dict[str, Any]]] = None,
                 temperature: float = 0.7,
                 **kwargs) -> LLMResponse:
        if not self.is_configured():
            raise ProviderAuthError("Gemini API key not configured", self.name)

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": f"System Directive: {system_prompt}"}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": 800
            }
        }

        try:
            res = requests.post(url, json=payload, timeout=20)
            if res.status_code == 429:
                raise ProviderRateLimitError("Gemini rate limit exceeded", self.name)
            elif res.status_code == 401:
                raise ProviderAuthError("Gemini authentication failed", self.name)
            elif res.status_code == 404:
                raise ProviderModelNotFoundError(f"Gemini model not found: {self.model}", self.name, self.model)
            res.raise_for_status()
            data = res.json()

            try:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                text = ""

            return LLMResponse(
                text=text,
                tool_calls=[],
                model_name=f"gemini/{self.model}"
            )
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderUnavailableError(f"Gemini request failed: {e}", self.name)

    def disable(self):
        self._enabled = False

    def enable(self):
        self._enabled = True

    def is_enabled(self) -> bool:
        return self._enabled