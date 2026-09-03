from datetime import datetime
from memory import user_profile, short_term_memory, episodic_memory, semantic_memory
from core.advanced_automation import get_system_info

class ContextManager:
    """Assembles rich context combining User Profile, Memory 2.0, and System Telemetry"""
    def __init__(self):
        pass

    def build_system_prompt(self) -> str:
        name = user_profile.get_name()
        role = user_profile.get_role()
        now_str = datetime.now().strftime("%A, %B %d, %Y %I:%M %p")
        
        # System telemetry snippet
        telemetry = ""
        try:
            info = get_system_info()
            if "error" not in info:
                telemetry = f"Workstation Telemetry: CPU {info['cpu_percent']}%, RAM {info['memory'].percent}%, Disk {info['disk'].percent}%."
        except Exception:
            pass

        recent_episodes = episodic_memory.get_context_summary(limit=2)

        return (
            f"You are DOOM V2, the high-tech Personal AI Operating System and loyal companion for {name} ({role}).\n"
            f"Current Timestamp: {now_str}.\n"
            f"{telemetry}\n\n"
            f"Personality & Directives:\n"
            f"- Address {name} respectfully, loyally, and concisely as DOOM. We are DOOM.\n"
            f"- When a user goal requires taking action on the computer, invoke the appropriate tool from your Tool Registry.\n"
            f"- Keep final spoken answers crisp, high-impact, and informative.\n\n"
            f"Recent Memory Context:\n"
            f"{recent_episodes}\n"
        )

    def get_conversation_history(self) -> str:
        return short_term_memory.get_recent_context(limit=4)

context_manager = ContextManager()
