import os
import re
import json
from typing import List, Dict, Any, Optional
from models.base_provider import BaseLLMProvider, LLMResponse

class GroqProvider(BaseLLMProvider):
    name = "groq"

    def __init__(self, model: Optional[str] = None):
        self._model = model
        self._client = None

    @property
    def model(self) -> str:
        if self._model:
            return self._model
        return os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()

    @property
    def api_key(self) -> str:
        return os.getenv("GROQ_API_KEY", "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.startswith("gsk_"))

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
        client = self._get_client()
        if not client:
            return LLMResponse(
                text="Groq Cloud is not configured. Standing by on local engine, Sujal.",
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

        # Format tools if provided
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

            # If content is empty but model reasoning exists (reasoning models)
            if not raw_text.strip() and hasattr(choice.message, "reasoning") and choice.message.reasoning:
                raw_text = choice.message.reasoning

            # Strip thinking tags if any
            cleaned_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
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
            print(f"[GROQ ERROR] {e}")
            # Fallback attempt to fast model if 120B errors
            if self.model != "openai/gpt-oss-20b":
                try:
                    kwargs["model"] = "openai/gpt-oss-20b"
                    fallback_res = client.chat.completions.create(**kwargs)
                    text = fallback_res.choices[0].message.content or ""
                    return LLMResponse(
                        text=text,
                        tool_calls=[],
                        model_name="groq/openai/gpt-oss-20b"
                    )
                except Exception:
                    pass

            return LLMResponse(
                text="",
                tool_calls=[],
                model_name=f"groq/error"
            )
