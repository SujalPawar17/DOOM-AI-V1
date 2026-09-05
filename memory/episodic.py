import os
import json
from datetime import datetime
from typing import List, Dict, Any

class EpisodicMemory:
    """Memory 2.0: Logs past actions, tool calls, decisions, and outcomes over time"""
    def __init__(self, storage_path: str = "memory_episodes.json"):
        self.storage_path = storage_path
        self.episodes: List[Dict[str, Any]] = self._load_episodes()

    def _load_episodes(self) -> List[Dict[str, Any]]:
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def record_episode(self, goal: str, plan_steps: List[str], tools_called: List[Dict[str, Any]], outcome: str, success: bool = True):
        episode = {
            "id": f"ep_{int(datetime.now().timestamp())}",
            "timestamp": datetime.now().isoformat(),
            "goal": goal,
            "plan_steps": plan_steps,
            "tools_called": tools_called,
            "outcome": outcome,
            "success": success
        }
        self.episodes.append(episode)
        if len(self.episodes) > 100:
            self.episodes = self.episodes[-100:]
        self._save()

        # Dual-persistence: Sync episode to PostgreSQL
        try:
            from database.postgres_db import postgres_manager
            postgres_manager.record_episode(
                episode_id=episode["id"],
                goal=goal,
                plan_steps=plan_steps,
                tools_called=tools_called,
                outcome=outcome,
                success=success
            )
        except Exception:
            pass

    def _save(self):
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.episodes, f, indent=2, default=str)
        except Exception as e:
            print(f"[MEMORY ERROR] Could not save episodic memory: {e}")

    def get_recent_episodes(self, limit: int = 5) -> List[Dict[str, Any]]:
        return self.episodes[-limit:]

    def get_context_summary(self, limit: int = 3) -> str:
        recent = self.get_recent_episodes(limit)
        if not recent:
            return "No previous episodic actions recorded."
        lines = []
        for ep in recent:
            lines.append(f"- [{ep['timestamp'][:16]}] Goal: '{ep['goal']}' -> Outcome: {ep['outcome'][:80]}")
        return "\n".join(lines)

episodic_memory = EpisodicMemory()
