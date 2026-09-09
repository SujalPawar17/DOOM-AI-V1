import os
import re
import json
from typing import List, Dict, Any, Optional
from models.base_provider import BaseLLMProvider, LLMResponse, ProviderCostTier, ProviderDeploymentMode, ProviderRateLimitError, ProviderModelNotFoundError, ProviderAuthError


class GroqProvider(BaseLLMProvider):
    name = "groq"
    display_name = "Groq LPU (LLaMA / GPT-OSS)"
    cost_tier = ProviderCostTier.FREE_TIER.value
    deployment_mode = ProviderDeploymentMode.HOSTED_CLOUD.value
    billing_possible = False
    capabilities = ["tool_calling", "code_generation", "reasoning", "fast_inference", "coding"]
    models = ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile"]
    context_limit = 8192
    streaming = True
    tool_calling = True
    multimodal = False

    def __init__(self, model: Optional[str] = None):
        self._model = model
        self._client = None
        self._enabled = True

    @property
    def model(self) -> str:
        if self._model:
            return self._model
        return os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()

    @property
    def api_key(self) -> str:
        return os.getenv("GROQ_API_KEY", "").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.startswith("gsk_"))

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

    def _get_client(self):
        if not self.is_available():
            return None
        from groq import Groq
        return Groq(api_key=self.api_key)

    def generate(self,
                 prompt: str,
                 system_prompt: str = "",
                 tools: Optional[List[Dict[str, Any]]] = None,
                 temperature: float = 0.7) -> LLMResponse:
        if not self.is_configured():
            raise ProviderAuthError("Groq API key not configured", self.name)

        client = self._get_client()
        if not client:
            return LLMResponse(
                text="Groq Cloud is not configured. Standing by on local engine.",
                tool_calls=[],
                model_name="groq/unavailable"
            )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1000
        }

        if tools:
            clean_tools = []
            for t in tools:
                if "type" in t and "function" in t:
                    clean_tools.append(t)
                elif "name" in t:
                    clean_tools.append({
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": t.get("parameters", {"type": "object", "properties": {}})
                        }
                    })
            if clean_tools:
                kwargs["tools"] = clean_tools
                kwargs["tool_choice"] = "auto"

        try:
            response = client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            raw_text = choice.message.content or ""

            if not raw_text.strip() and hasattr(choice.message, "reasoning") and choice.message.reasoning:
                raw_text = choice.message.reasoning

            cleaned_text = re.sub(r"\[thinking\].*?\[/thinking\]", "", raw_text, flags=re.DOTALL).strip()
            if not cleaned_text and raw_text:
                cleaned_text = raw_text.strip()

            tool_calls = []
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                    except Exception:
                        args = {}
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": args or {}
                    })

            usage_dict = None
            if response.usage:
                usage_dict = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                    "total_tokens": getattr(response.usage, "total_tokens", 0)
                }

            return LLMResponse(
                text=cleaned_text,
                tool_calls=tool_calls,
                model_name=f"groq/{self.model}",
                usage=usage_dict
            )

        except Exception as e:
            err_str = str(e).lower()
            if "rate limit" in err_str or "429" in err_str:
                raise ProviderRateLimitError(f"Groq rate limit: {e}", self.name)
            elif "401" in err_str or "unauthorized" in err_str or "authentication" in err_str:
                raise ProviderAuthError(f"Groq authentication failed: {e}", self.name)
            elif "not found" in err_str or "404" in err_str:
                raise ProviderModelNotFoundError(f"Groq model not found: {self.model}", self.name, self.model)

            print(f"[GROQ ERROR] {e}")
            fallback_models = ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
            for fb_model in fallback_models:
                if self.model != fb_model:
                    try:
                        kwargs["model"] = fb_model
                        fallback_res = client.chat.completions.create(**kwargs)
                        choice = fallback_res.choices[0]
                        text = choice.message.content or ""
                        fallback_tools = []
                        if choice.message.tool_calls:
                            for tc in choice.message.tool_calls:
                                try:
                                    args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                                except Exception:
                                    args = {}
                                fallback_tools.append({
                                    "id": tc.id,
                                    "name": tc.function.name,
                                    "arguments": args or {}
                                })
                        return LLMResponse(
                            text=text,
                            tool_calls=fallback_tools,
                            model_name=f"groq/{fb_model}"
                        )
                    except Exception:
                        continue

            return LLMResponse(
                text="",
                tool_calls=[],
                model_name=f"groq/error"
            )

    def disable(self):
        self._enabled = False

    def enable(self):
        self._enabled = True

    def is_enabled(self) -> bool:
        return self._enabled