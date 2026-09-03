import requests
import json
from typing import List, Dict, Any, Optional
from models.base_provider import BaseLLMProvider, LLMResponse

class OllamaProvider(BaseLLMProvider):
    name = "ollama"

    def __init__(self, model: str = "llama3", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    def is_available(self) -> bool:
        try:
            res = requests.get(f"{self.base_url}/api/tags", timeout=1)
            return res.status_code == 200
        except Exception:
            return False

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
