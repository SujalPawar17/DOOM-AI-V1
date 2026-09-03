from typing import List, Dict, Any
from datetime import datetime

class ShortTermMemory:
    """Memory 2.0: Multi-turn conversation buffer with automatic sliding-window history"""
    def __init__(self, max_turns: int = 15):
        self.max_turns = max_turns
        self.turns: List[Dict[str, Any]] = []

    def add_user_turn(self, text: str):
        self.turns.append({
            "role": "user",
            "content": text,
            "timestamp": datetime.now().isoformat()
        })
        self._trim()

    def add_assistant_turn(self, text: str, tools_used: List[str] = None):
        self.turns.append({
            "role": "assistant",
            "content": text,
            "tools_used": tools_used or [],
            "timestamp": datetime.now().isoformat()
        })
        self._trim()

    def _trim(self):
        if len(self.turns) > self.max_turns * 2:
            self.turns = self.turns[-(self.max_turns * 2):]

    def get_messages(self) -> List[Dict[str, str]]:
        return [{"role": t["role"], "content": t["content"]} for t in self.turns]

    def get_recent_context(self, limit: int = 5) -> str:
        if not self.turns:
            return ""
        recent = self.turns[-(limit * 2):]
        lines = []
        for t in recent:
            role_label = "Sujal" if t["role"] == "user" else "DOOM"
            lines.append(f"{role_label}: {t['content']}")
        return "\n".join(lines)

    def clear(self):
        self.turns = []

short_term_memory = ShortTermMemory()
