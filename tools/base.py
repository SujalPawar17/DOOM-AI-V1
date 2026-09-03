from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

@dataclass
class ToolResult:
    success: bool
    output: str
    data: Optional[Any] = None
    error: Optional[str] = None

    def __str__(self) -> str:
        if self.success:
            return self.output
        return f"Tool Error: {self.error or self.output}"

class BaseTool(ABC):
    """Standardized Tool Interface for DOOM V2 Personal AI OS"""
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}
    permission_level: str = "safe"  # safe, moderate, sensitive, dangerous

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """Execute the tool action and return a standardized ToolResult"""
        pass

    def to_json_schema(self) -> Dict[str, Any]:
        """Convert tool to OpenAI/Groq/Gemini JSON function calling format"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }
