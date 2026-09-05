import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from core.path_resolver import canonical_path


@dataclass
class PlanStep:
    id: int
    action: str
    tool: str
    description: str
    expected_outcome: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionPlan:
    goal: str
    type: str  # DIRECT, QUERY, ACTION, MULTI_STEP, AUTONOMOUS
    steps: List[PlanStep] = field(default_factory=list)
    target_path: Optional[str] = None
    target_app: Optional[str] = None
    is_code_generation: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def task_type(self) -> str:
        """Backward compatibility for orchestrator and router."""
        return self.type

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "type": self.type,
            "steps": [s.to_dict() for s in self.steps],
            "target_path": self.target_path,
            "is_code_generation": self.is_code_generation,
            "metadata": self.metadata
        }


class Planner:
    """
    DOOM V3.1 Intent Classifier and Plan Synthesizer.
    Accurately classifies requests into:
      - DIRECT: Profile lookups, who am I, greetings, direct responses (0 external tools)
      - QUERY: Status, telemetry, web search, read-only questions (distinguished from code generation)
      - ACTION: Single operations (open app, screenshot, media)
      - MULTI_STEP: Sequential multi-stage operations (create + run + verify)
      - AUTONOMOUS: Complex pipelines and self-healing autonomous workflows
    """
    def __init__(self):
        pass

    def classify_and_plan(self, user_goal: str) -> ExecutionPlan:
        goal = user_goal.strip()
        lower = goal.lower()

        # ─────────────────────────────────────────────────────────────────────
        # 1. DIRECT INTENT (Who am I, Identity, Greetings, Persona)
        # ─────────────────────────────────────────────────────────────────────
        direct_patterns = [
            r"\bwho am i\b", r"\bwho are you\b", r"\bwhat is my name\b",
            r"\bmy profile\b", r"\bwhat can you do\b", r"\bhello\b",
            r"\bhi\b", r"\bhey doom\b", r"\bgood (morning|afternoon|evening)\b",
            r"\bhow are you\b", r"\bwhat is your name\b"
        ]
        if any(re.search(pat, lower) for pat in direct_patterns) and not any(w in lower for w in ["create", "write", "run", "search", "open"]):
            return ExecutionPlan(
                goal=goal,
                type="DIRECT",
                steps=[PlanStep(1, "respond_direct", "direct_response", "Direct profile or conversational answer", "Answer delivered")],
                is_code_generation=False
            )

        # ─────────────────────────────────────────────────────────────────────
        # 2. AUTONOMOUS (Complex multi-action goals, debugging loops, self-healing)
        # ─────────────────────────────────────────────────────────────────────
        if any(p in lower for p in ["syntax error", "fix it", "fix the error", "run it again", "debug and fix", "build a complete", "develop an entire"]):
            return ExecutionPlan(
                goal=goal,
                type="AUTONOMOUS",
                steps=[
                    PlanStep(1, "create_initial", "coding_write_script", "Generate initial code", "Code on disk"),
                    PlanStep(2, "execute_test", "coding_run_python", "Execute and detect errors", "Execution result"),
                    PlanStep(3, "diagnose_and_fix", "coding_write_script", "Analyze trace and patch code", "Patched code"),
                    PlanStep(4, "re_execute", "coding_run_python", "Re-run patched code", "Clean execution"),
                    PlanStep(5, "verify_completion", "verifier", "Verify final ground truth", "Verified")
                ],
                is_code_generation=True
            )

        # ─────────────────────────────────────────────────────────────────────
        # 3. CODE GENERATION / MULTI-STEP INTENTS (create + run + check)
        # ─────────────────────────────────────────────────────────────────────
        is_create_code = any(p in lower for p in [
            "create a python", "create python", "write a python", "write python",
            "generate a python", "generate script", "create a script", "write a script",
            "create script", "build a script", "make a python file", "make a script",
            "code a python", "create a file called", "create a file on my desktop"
        ]) or (("create" in lower or "write" in lower) and (".py" in lower or "python" in lower))

        has_execution = any(p in lower for p in ["run it", "execute it", "test it", "check that it works", "check if it works", "run and", "and run"])
        has_verification = any(p in lower for p in ["verify", "check that", "tell me the result", "show me the result", "make sure it works"])

        # Extract target file path if mentioned
        file_match = re.search(r'([a-zA-Z0-9_\-\\\/\.]+\.py)', goal)
        target_file = file_match.group(1) if file_match else None
        if target_file and "desktop" in lower and "desktop" not in target_file.lower():
            target_file = f"Desktop/{target_file}"

        if is_create_code or (target_file and ("create" in lower or "write" in lower)):
            if has_execution or has_verification or "and" in lower:
                # Multi-Step: create -> run -> verify
                steps = [
                    PlanStep(1, "create_file", "coding_write_script", f"Create Python script '{target_file or 'script.py'}'", "File created on disk"),
                    PlanStep(2, "execute_file", "coding_run_python", f"Execute '{target_file or 'script.py'}' and capture output", "Exit code 0 and nominal output"),
                    PlanStep(3, "verify", "verifier", "Verify file syntax, ground truth, and execution results", "Full ground truth verified")
                ]
                return ExecutionPlan(
                    goal=goal,
                    type="MULTI_STEP",
                    steps=steps,
                    target_path=target_file,
                    is_code_generation=True,
                    metadata={"target_file": target_file}
                )
            else:
                # Single creation step
                return ExecutionPlan(
                    goal=goal,
                    type="ACTION",
                    steps=[PlanStep(1, "create_file", "coding_write_script", f"Create Python script '{target_file or 'script.py'}'", "File created")],
                    target_path=target_file,
                    is_code_generation=True
                )

        # ─────────────────────────────────────────────────────────────────────
        # 4. INFORMATION REQUEST / QUERY (Telemetry, Status, Read-only)
        # CRITICAL: "Show my CPU, RAM and disk" must NEVER create Python files!
        # ─────────────────────────────────────────────────────────────────────
        is_telemetry_query = any(p in lower for p in [
            "cpu usage", "ram usage", "disk usage", "cpu, ram", "cpu and ram",
            "show my cpu", "show cpu", "show ram", "show disk", "check cpu",
            "check ram", "check system", "system status", "telemetry",
            "memory usage", "battery level", "hardware status", "system diagnostics"
        ])
        if is_telemetry_query and not is_create_code:
            return ExecutionPlan(
                goal=goal,
                type="QUERY",
                steps=[PlanStep(1, "query_telemetry", "system_get_status", "Query current system telemetry directly", "Hardware telemetry captured")],
                is_code_generation=False,
                metadata={"category": "system_telemetry"}
            )

        # General queries (knowledge, search)
        if any(lower.startswith(w) for w in ["what ", "who ", "where ", "when ", "why ", "how ", "is ", "are ", "can you explain", "search for"]):
            return ExecutionPlan(
                goal=goal,
                type="QUERY",
                steps=[PlanStep(1, "search_or_query", "web_search", f"Retrieve information for '{goal[:40]}'", "Information retrieved")],
                is_code_generation=False
            )

        # ─────────────────────────────────────────────────────────────────────
        # 5. ACTION (Single direct operations)
        # ─────────────────────────────────────────────────────────────────────
        if any(lower.startswith(w) for w in ["open ", "launch ", "start ", "bring up "]):
            app_name = re.sub(r'^(open|launch|start|bring up)\s+', '', lower).strip()
            return ExecutionPlan(
                goal=goal,
                type="ACTION",
                steps=[PlanStep(1, "open_application", "computer_open_app", f"Launch application '{app_name}'", "App opened")],
                target_app=app_name
            )

        if any(lower.startswith(w) for w in ["play ", "stream "]) or "music" in lower:
            return ExecutionPlan(
                goal=goal,
                type="ACTION",
                steps=[PlanStep(1, "stream_media", "computer_stream_youtube", "Stream requested audio/video", "Media playing")]
            )

        if "screenshot" in lower or "capture screen" in lower:
            return ExecutionPlan(
                goal=goal,
                type="ACTION",
                steps=[PlanStep(1, "take_screenshot", "system_take_screenshot", "Capture screen image", "Screenshot saved")]
            )

        if any(w in lower for w in ["lock workstation", "lock computer", "lock screen", "lockdown"]):
            return ExecutionPlan(
                goal=goal,
                type="ACTION",
                steps=[PlanStep(1, "lock_workstation", "system_lock_workstation", "Lock Windows session", "Workstation locked")]
            )

        # Default multi-step fallback
        return ExecutionPlan(
            goal=goal,
            type="QUERY",
            steps=[PlanStep(1, "process_intent", "llm_agent", "Process goal through autonomous agent loop", "Goal satisfied")]
        )


planner = Planner()
