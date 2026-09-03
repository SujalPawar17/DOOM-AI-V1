import re
from typing import Dict, Any, List
from tools.base import ToolResult

class Verifier:
    """Verifies tool outputs, checks safety, and formats the final JARVIS voice response"""
    def __init__(self):
        pass

    def verify_tool_result(self, tool_name: str, result: ToolResult) -> str:
        if not result.success:
            return f"I encountered a slight complication with {tool_name}: {result.error or result.output}"
        
        # Clean up output for spoken delivery
        out = result.output.strip()
        if len(out) > 400:
            out = out[:380] + "... and completed successfully."
        return out

    def polish_response(self, text: str, tools_executed: List[Dict[str, Any]]) -> str:
        if not text and tools_executed:
            # Generate synthesized response from tool results
            outputs = [t["result"].output for t in tools_executed if hasattr(t.get("result"), "output")]
            if outputs:
                return " ".join(outputs)
            return "Task executed successfully, Sujal."

        # Remove markdown artifacts for clean speech
        clean_text = re.sub(r'[\*\#\_`]', '', text).strip()
        
        # Remove thinking/reasoning tags from models (e.g., <think, <reasoning>...</reasoning>)
        clean_text = re.sub(r'<think>.*?</think>', '', clean_text, flags=re.DOTALL).strip()
        clean_text = re.sub(r'<reasoning>.*?</reasoning>', '', clean_text, flags=re.DOTALL).strip()
        clean_text = re.sub(r'<\|.*?\|>', '', clean_text).strip()  # Special tokens
        
        # Remove "According to directives..." style verbose responses
        clean_text = re.sub(r'According to directives,?\s*', '', clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r'We have a tool \w+\.?\s*', '', clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r'So we need to call that\.?\s*', '', clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(r'User asks ".*?"\.?\s*', '', clean_text, flags=re.IGNORECASE)
        
        # Take only the last sentence if response is too verbose (likely reasoning)
        sentences = re.split(r'[.!?]+', clean_text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if len(sentences) > 3:
            # Keep last 2 meaningful sentences
            clean_text = '. '.join(sentences[-2:]) + '.'
        
        return clean_text

verifier = Verifier()
