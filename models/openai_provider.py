import os
import json
import requests
from typing import List, Dict, Any, Optional
from models.base_provider import BaseLLMProvider, LLMResponse

class OpenAIProvider(BaseLLMProvider):
    name = "openai"

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self,
                 prompt: str,
                 system_prompt: str = "",
                 tools: Optional[List[Dict[str, Any]]] = None,
                 temperature: float = 0.7) -> LLMResponse:
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

        res = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=20)
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
