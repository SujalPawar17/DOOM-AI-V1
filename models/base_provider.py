from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class LLMResponse:
    text: str
    tool_calls: List[Dict[str, Any]]
    model_name: str
    usage: Optional[Dict[str, int]] = None

class BaseLLMProvider(ABC):
    """Abstract interface for all DOOM V2 Model Providers"""
    name: str = ""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if API key or local daemon is ready"""
        pass

    @abstractmethod
    def generate(self,
                 prompt: str,
                 system_prompt: str = "",
                 tools: Optional[List[Dict[str, Any]]] = None,
                 temperature: float = 0.7) -> LLMResponse:
        """Generate response or function tool calls"""
        pass
