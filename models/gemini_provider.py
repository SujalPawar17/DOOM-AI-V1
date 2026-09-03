import os
import json
import requests
from typing import List, Dict, Any, Optional
from models.base_provider import BaseLLMProvider, LLMResponse

class GeminiProvider(BaseLLMProvider):
    name = "gemini"

    def __init__(self, model: str = "gemini-2.0-flash"):
        self.model = model
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self,
                 prompt: str,
                 system_prompt: str = "",
                 tools: Optional[List[Dict[str, Any]]] = None,
                 temperature: float = 0.7) -> LLMResponse:
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

        res = requests.post(url, json=payload, timeout=20)
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
