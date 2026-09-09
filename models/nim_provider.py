import os
import json
import time
import requests
from typing import List, Dict, Any, Optional
from models.base_provider import BaseLLMProvider, LLMResponse, ProviderCostTier, ProviderDeploymentMode, ProviderRateLimitError, ProviderModelNotFoundError, ProviderAuthError


class NIMProvider(BaseLLMProvider):
    name = "nim"
    display_name = "NVIDIA NIM (Nemotron 3 Ultra)"
    cost_tier = ProviderCostTier.FREE_TIER.value
    deployment_mode = ProviderDeploymentMode.PARTNER_HOSTED.value
    billing_possible = False
    capabilities = ["tool_calling", "code_generation", "reasoning", "coding"]
    models = ["nvidia/nemotron-3-ultra-550b-a55b"]
    context_limit = 8192
    streaming = False
    tool_calling = True
    multimodal = False

    def __init__(self, model: Optional[str] = None):
        self._model = model
        self.api_key = os.getenv("NVIDIA_API_KEY", "").strip()
        self.base_url = os.getenv("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1").strip()
        self._verified: Optional[bool] = None
        self._verified_at: float = 0.0
        self._enabled = True

    @property
    def model(self) -> str:
        if self._model:
            return self._model
        return os.getenv("NVIDIA_NIM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def is_authenticated(self) -> bool:
        return self.is_available()

    def is_available(self) -> bool:
        if not self._enabled:
            return False
        if not self.api_key:
            return False

        now = time.time()
        if self._verified is not None and (now - self._verified_at) < 3600:
            return self._verified

        try:
            resp = requests.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=2
            )
            self._verified = resp.status_code in (200, 401)
            self._verified_at = now
            if self._verified:
                print(f"[NIM] Endpoint reachable (HTTP {resp.status_code}).")
            else:
                print(f"[NIM] Endpoint returned {resp.status_code} — caching as unavailable for 1hr.")
            return self._verified
        except Exception as e:
            print(f"[NIM] Availability probe failed ({e}) — caching as unavailable for 1hr.")
            self._verified = False
            self._verified_at = now
            return False

    def is_healthy(self) -> bool:
        return self.is_available()

    def generate(self,
                 prompt: str,
                 system_prompt: str = "",
                 tools: Optional[List[Dict[str, Any]]] = None,
                 temperature: float = 0.7) -> LLMResponse:
        if not self.api_key:
            raise ProviderAuthError("NVIDIA API key not configured", self.name)

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
            "max_tokens": 1000
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            res = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=30)
            if res.status_code == 429:
                raise ProviderRateLimitError("NIM rate limit exceeded", self.name)
            elif res.status_code == 401:
                raise ProviderAuthError("NIM authentication failed", self.name)
            elif res.status_code == 404:
                raise ProviderModelNotFoundError(f"NIM model not found: {self.model}", self.name, self.model)
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
                model_name=f"nim/{self.model}",
                usage=data.get("usage")
            )
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderUnavailableError(f"NIM request failed: {e}", self.name)

    def disable(self):
        self._enabled = False

    def enable(self):
        self._enabled = True

    def is_enabled(self) -> bool:
        return self._enabled