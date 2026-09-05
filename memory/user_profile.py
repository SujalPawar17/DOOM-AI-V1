import os
import json
from datetime import datetime
from typing import Dict, Any, List

class UserProfile:
    """Memory 2.0: Stores persistent profile for Sujal (preferences, projects, role, habits)"""
    def __init__(self, storage_path: str = "memory_profile.json"):
        self.storage_path = storage_path
        self.data: Dict[str, Any] = self._load_profile()

    def _load_profile(self) -> Dict[str, Any]:
        default_profile = {
            "name": "Sujal",
            "role": "Creator, Boss, and Lead AI Engineer",
            "title": "Sir",
            "assistant_name": "DOOM",
            "preferences": {
                "voice_accent": "British (Ryan Neural)",
                "communication_style": "Concise, loyal, highly intelligent, like JARVIS",
                "favorite_topics": ["AI", "Autonomous Agents", "Robotics", "Software Engineering"],
                "default_editor": "VS Code",
                "default_browser": "Chrome"
            },
            "projects": [
                {
                    "name": "DOOM Personal AI OS",
                    "status": "Active Development",
                    "goal": "Build an Iron Man JARVIS-level autonomous desktop OS"
                }
            ],
            "custom_notes": {},
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat()
        }
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    # Merge defaults with saved
                    default_profile.update(saved)
            except Exception:
                pass
        
        # Try syncing from PostgreSQL if available
        try:
            from database.postgres_db import postgres_manager
            db_profile = postgres_manager.load_user_profile(user_id="sujal")
            if db_profile:
                default_profile.update(db_profile)
        except Exception:
            pass

        return default_profile

    def save(self):
        try:
            self.data["last_updated"] = datetime.now().isoformat()
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            print(f"[MEMORY ERROR] Could not save user profile: {e}")

        # Dual-persistence: Sync to PostgreSQL
        try:
            from database.postgres_db import postgres_manager
            postgres_manager.save_user_profile(self.data, user_id="sujal")
        except Exception:
            pass

    def get_name(self) -> str:
        return self.data.get("name", "Sujal")

    def get_role(self) -> str:
        return self.data.get("role", "Boss and Creator")

    def get_title(self) -> str:
        return self.data.get("title", "Sir")

    def get_access_level(self) -> str:
        return self.data.get("access_level", "Root / Level 10")

    def get_preferences(self) -> Dict[str, Any]:
        return self.data.get("preferences", {})

    def get_projects(self) -> List[Dict[str, Any]]:
        return self.data.get("projects", [])

    def get_custom_notes(self) -> Dict[str, Any]:
        return self.data.get("custom_notes", {})

    def set_preference(self, key: str, value: Any):
        self.data["preferences"][key] = value
        self.save()

    def get_preference(self, key: str, default: Any = None) -> Any:
        return self.data.get("preferences", {}).get(key, default)

    def set_note(self, key: str, value: str):
        self.data["custom_notes"][key] = {
            "value": value,
            "timestamp": datetime.now().isoformat()
        }
        self.save()

    def get_note(self, key: str) -> str:
        note_obj = self.data.get("custom_notes", {}).get(key)
        if isinstance(note_obj, dict):
            return note_obj.get("value", "")
        return str(note_obj) if note_obj else ""

    def get_context_summary(self) -> str:
        return (f"User: {self.get_name()} ({self.get_role()})\n"
                f"Assistant: {self.data.get('assistant_name', 'DOOM')}\n"
                f"Active Projects: {', '.join([p['name'] for p in self.data.get('projects', [])])}\n"
                f"Communication Style: {self.get_preference('communication_style')}")

user_profile = UserProfile()
