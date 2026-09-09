import os
import json
import requests
from typing import List, Dict, Any, Optional
from models.base_provider import BaseLLMProvider, LLMResponse, ProviderCostTier, ProviderDeploymentMode, ProviderRateLimitError, ProviderModelNotFoundError, ProviderAuthError


class OpenAIProvider(BaseLLMProvider):
    name = "openai"
    display_name = "OpenAI GPT-4o"
    cost_tier = ProviderCostTier.PAID.value
    deployment_mode = ProviderDeploymentMode.HOSTED_CLOUD.value
    billing_possible = True
    capabilities = ["tool_calling", "code_generation", "reasoning", "vision", "web_search", "coding"]
    models = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]
    context_limit = 128000
    streaming = True
    tool_calling = True
    multimodal = True

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
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

    def generate(self,
                 prompt: str,
                 system_prompt: str = "",
                 tools: Optional[List[Dict[str, Any]]] = None,
                 temperature: float = 0.7) -> LLMResponse:
        if not self.is_configured():
            raise ProviderAuthError("OpenAI API key not configured", self.name)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 800
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            res = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=20)
            if res.status_code == 429:
                raise ProviderRateLimitError("OpenAI rate limit exceeded", self.name)
            elif res.status_code == 401:
                raise ProviderAuthError("OpenAI authentication failed", self.name)
            elif res.status_code == 404:
                raise ProviderModelNotFoundError(f"OpenAI model not found: {self.model}", self.name, self.model)
            res.raise_for_status()
            data = res.json()
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
            tool_calls = []

            if "tool_calls" in choice["message"] and choice["message"]["tool_calls"]:
                for tc in choice["message"]["tool_calls"]:
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except Exception:
                        args = {}
                    tool_calls.append({
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "arguments": args
                    })

            return LLMResponse(
                text=text,
                tool_calls=tool_calls,
                model_name=f"openai/{self.model}",
                usage=data.get("usage")
            )
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderUnavailableError(f"OpenAI request failed: {e}", self.name)

    def disable(self):
        self._enabled = False

    def enable(self):
        self._enabled = True

    def is_enabled(self) -> bool:
        return self._enabled