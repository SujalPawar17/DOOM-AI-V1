"""
DOOM V4 — Cognitive Understanding Layer
Separates literal user phrasing from actual operational intent.
Extracts entities, constraints, required capabilities, and flags ambiguity requiring clarification.
"""

import re
import os
from typing import Dict, Any, List, Optional, Tuple
from core.cognition.schemas import CognitiveIntent, CognitiveState


class UnderstandingEngine:
    """
    Cognitive Understanding Layer:
    Determines underlying goal, entities, constraints, and whether clarification is required.
    """

    def understand(self, user_request: str, context: Optional[Dict[str, Any]] = None) -> Tuple[CognitiveIntent, str, Dict[str, Any], List[str], List[str], bool, Optional[str], float, str]:
        """
        Parses request into:
          - intent: CognitiveIntent
          - normalized_goal: str
          - entities: Dict[str, Any]
          - constraints: List[str]
          - required_capabilities: List[str]
          - needs_clarification: bool
          - clarification_prompt: Optional[str]
          - confidence: float
          - task_type: str (V3.3 compatible)
        """
        raw = user_request.strip()
        lower = raw.lower()
        entities: Dict[str, Any] = {}
        constraints: List[str] = []
        required_capabilities: List[str] = []
        needs_clarification = False
        clarification_prompt = None
        confidence = 0.95

        # 1. Ambiguity & Clarification Detection
        # Check for dangerous or vague destructive actions with missing targets
        vague_destructive_patterns = [
            r"^(delete|remove|destroy|wipe|erase)\s+(the\s+)?(file|folder|directory|item|thing|script|data)(\s+.*)?$",
            r"^(delete|remove|destroy|wipe|erase)\s+(the\s+)?(old|previous|last|recent)\s+(file|folder|directory|thing|item|script)(\s+.*)?$"
        ]
        for pat in vague_destructive_patterns:
            if re.search(pat, lower):
                needs_clarification = True
                clarification_prompt = "Which specific file or directory would you like me to delete, Boss?"
                confidence = 0.4
                return (
                    CognitiveIntent.MODIFICATION,
                    raw,
                    {},
                    ["destructive_action"],
                    ["filesystem"],
                    needs_clarification,
                    clarification_prompt,
                    confidence,
                    "ACTION"
                )

        # 2. Entity Extraction
        # Filename extraction (e.g., system_info.py, script.py)
        file_match = re.search(r'([a-zA-Z0-9_\-\\\/\.]+\.(?:py|txt|json|md|csv|html|js|ts|sh))', raw)
        if file_match:
            extracted_filename = file_match.group(1)
            # Check for Desktop reference
            if "desktop" in lower and "desktop" not in extracted_filename.lower():
                entities["target_file"] = f"Desktop/{extracted_filename}"
                constraints.append("target_location: Desktop")
            else:
                entities["target_file"] = extracted_filename
            entities["filename"] = extracted_filename

        # Application extraction (e.g. open chrome, launch notepad)
        app_match = re.search(r'^(?:open|launch|start|bring up)\s+([a-zA-Z0-9_\-\s]+)$', lower)
        if app_match:
            entities["target_app"] = app_match.group(1).strip()

        # 3. Intent & Capability Identification

        # A. Identity / Direct Conversation
        direct_patterns = [
            r"\bwho am i\b", r"\bwho are you\b", r"\bwhat is my name\b",
            r"\bmy profile\b", r"\bwhat can you do\b", r"\bhello\b",
            r"\bhi\b", r"\bhey doom\b", r"\bgood (morning|afternoon|evening)\b",
            r"\bhow are you\b", r"\bwhat is your name\b"
        ]
        if any(re.search(pat, lower) for pat in direct_patterns) and not any(w in lower for w in ["create", "write", "run", "search", "open", "script"]):
            return (
                CognitiveIntent.CONVERSATION,
                "Provide direct identity, profile, or conversational response",
                entities,
                constraints,
                ["general"],
                False,
                None,
                0.99,
                "DIRECT"
            )

        # B. System Telemetry / Hardware Query
        telemetry_patterns = [
            "cpu usage", "ram usage", "disk usage", "cpu, ram", "cpu and ram",
            "show my cpu", "show cpu", "show ram", "show disk", "check cpu",
            "check ram", "check system", "system status", "telemetry",
            "memory usage", "hardware status", "system diagnostics"
        ]
        is_telemetry = any(p in lower for p in telemetry_patterns)
        is_code_creation = any(p in lower for p in ["create a python", "write a python", "generate a python", "create python", "write python", "make a python"])

        if is_telemetry and not is_code_creation:
            constraints.append("read_only")
            constraints.append("no_code_generation")
            required_capabilities.append("telemetry")
            return (
                CognitiveIntent.SYSTEM_OPERATION,
                "Inspect and return current workstation telemetry (CPU, RAM, Disk)",
                entities,
                constraints,
                required_capabilities,
                False,
                None,
                0.98,
                "QUERY"
            )

        # C. Autonomous Error Detection & Self-Healing Loop
        autonomous_patterns = [
            "syntax error", "fix it", "fix the error", "run it again",
            "debug and fix", "build a complete", "develop an entire"
        ]
        if any(p in lower for p in autonomous_patterns):
            required_capabilities.extend(["coding", "reasoning", "filesystem"])
            constraints.append("self_healing_verification")
            return (
                CognitiveIntent.AUTOMATION,
                f"Autonomously execute, diagnose, patch, and verify: {raw}",
                entities,
                constraints,
                required_capabilities,
                False,
                None,
                0.92,
                "AUTONOMOUS"
            )

        # D. Code Generation / Multi-Step Actions
        has_exec = any(p in lower for p in ["run it", "execute it", "test it", "check that it works", "check if it works", "and run", "run and"])
        has_verify = any(p in lower for p in ["verify", "check that", "tell me the result", "show me the result", "make sure it works"])

        if is_code_creation or (entities.get("target_file", "").endswith(".py") and any(w in lower for w in ["create", "write", "build", "generate"])):
            required_capabilities.extend(["coding", "filesystem"])
            if has_exec or has_verify or "and" in lower:
                required_capabilities.append("reasoning")
                return (
                    CognitiveIntent.MULTI_STEP,
                    f"Create script '{entities.get('target_file', 'script.py')}', execute it, and verify output",
                    entities,
                    constraints,
                    required_capabilities,
                    False,
                    None,
                    0.95,
                    "MULTI_STEP"
                )
            else:
                return (
                    CognitiveIntent.CREATION,
                    f"Create script '{entities.get('target_file', 'script.py')}'",
                    entities,
                    constraints,
                    required_capabilities,
                    False,
                    None,
                    0.95,
                    "ACTION"
                )

        # E. Application Launch
        if entities.get("target_app"):
            required_capabilities.append("os_automation")
            return (
                CognitiveIntent.ACTION,
                f"Launch application '{entities['target_app']}'",
                entities,
                constraints,
                required_capabilities,
                False,
                None,
                0.96,
                "ACTION"
            )

        # F. Knowledge / Web Search vs Direct Informational Query
        # Specific informational queries (like time, date, definition) without search verbs are QUERY
        if any(lower.startswith(w) for w in ["what is the time", "what time is it", "what is the current time", "current time", "what is the date", "what date is today"]):
            required_capabilities.append("system_time")
            return (
                CognitiveIntent.QUERY,
                raw,
                entities,
                constraints,
                required_capabilities,
                False,
                None,
                0.95,
                "QUERY"
            )

        if any(lower.startswith(w) for w in ["search for ", "lookup ", "google ", "find online ", "search the web for "]):
            required_capabilities.append("web_search")
            return (
                CognitiveIntent.SEARCH,
                f"Retrieve information regarding: {raw}",
                entities,
                constraints,
                required_capabilities,
                False,
                None,
                0.90,
                "QUERY"
            )
        elif any(lower.startswith(w) for w in ["who ", "where ", "when ", "why ", "how ", "is ", "are "]):
            required_capabilities.append("web_search")
            return (
                CognitiveIntent.SEARCH,
                f"Retrieve information regarding: {raw}",
                entities,
                constraints,
                required_capabilities,
                False,
                None,
                0.90,
                "QUERY"
            )

        # Default Fallback: Query or general tool use
        required_capabilities.append("general")
        return (
            CognitiveIntent.QUERY,
            raw,
            entities,
            constraints,
            required_capabilities,
            False,
            None,
            0.80,
            "QUERY"
        )


understanding_engine = UnderstandingEngine()
