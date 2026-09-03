import os
import json
from datetime import datetime
from typing import Dict, Any, Optional

class SemanticMemory:
    """Memory 2.0: Knowledge base, permanent facts, and conceptual memory store"""
    def __init__(self, storage_path: str = "memory_semantic.json"):
        self.storage_path = storage_path
        self.facts: Dict[str, Any] = self._load_facts()

    def _load_facts(self) -> Dict[str, Any]:
        default_facts = {
            "creator": "Sujal",
            "assistant": "DOOM",
            "voice": "British Neural (en-GB-RyanNeural)",
            "operating_system": "Windows",
            "project_name": "DOOM Personal AI OS",
            "wake_methods": ["Acoustic Double-Clap", "Hey DOOM", "Jarvis"],
            "capabilities": [
                "Computer Control & App Launching",
                "Dynamic Python Code Execution",
                "Computer Vision & Gesture Recognition",
                "Live Web Intelligence & Scraping",
                "Media Streaming & YouTube Control",
                "System Telemetry & Workstation Lock"
            ]
        }
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    default_facts.update(saved)
                    return default_facts
            except Exception:
                pass
        return default_facts

    def remember_fact(self, key: str, value: Any, category: str = "general"):
        clean_key = key.lower().strip()
        self.facts[clean_key] = {
            "value": value,
            "updated_at": datetime.now().isoformat()
        }
        self._save()

        # Dual-persistence: Sync fact to PostgreSQL
        try:
            from database.postgres_db import postgres_manager
            postgres_manager.save_semantic_fact(key=clean_key, value=value, category=category)
        except Exception:
            pass

    def recall_fact(self, key: str) -> Optional[Any]:
        k = key.lower().strip()
        val = self.facts.get(k)
        if isinstance(val, dict) and "value" in val:
            return val["value"]
        return val

    def search_facts(self, query: str) -> Dict[str, Any]:
        q = query.lower()
        results = {}
        for k, v in self.facts.items():
            if q in k or q in str(v).lower():
                results[k] = v.get("value") if isinstance(v, dict) and "value" in v else v
        return results

    def _save(self):
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.facts, f, indent=2)
        except Exception as e:
            print(f"[MEMORY ERROR] Could not save semantic memory: {e}")

semantic_memory = SemanticMemory()
