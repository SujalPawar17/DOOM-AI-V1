from typing import List, Dict, Any
from dataclasses import dataclass

@dataclass
class PlanStep:
    step_id: int
    description: str
    tool_name: str
    expected_outcome: str

@dataclass
class ExecutionPlan:
    goal: str
    task_type: str  # "direct_action", "multi_step", "query", "coding"
    steps: List[PlanStep]

class Planner:
    """Classifies user intent and builds execution plans for the DOOM Core Orchestrator"""
    def __init__(self):
        pass

    def classify_and_plan(self, user_goal: str) -> ExecutionPlan:
        goal_lower = user_goal.lower().strip()

        # 1. Coding Tasks
        if any(w in goal_lower for w in ["write code", "write python", "generate script", "create script", "debug", "fix bug"]):
            return ExecutionPlan(
                goal=user_goal,
                task_type="coding",
                steps=[
                    PlanStep(1, "Generate Python code", "coding_write_script", "Source file created"),
                    PlanStep(2, "Test/verify Python code", "coding_run_python", "Execution success")
                ]
            )

        # 2. Multi-Step System Inspection
        if "find out why" in goal_lower or "diagnose" in goal_lower:
            return ExecutionPlan(
                goal=user_goal,
                task_type="multi_step",
                steps=[
                    PlanStep(1, "Inspect filesystem/error logs", "filesystem_search_files", "Logs identified"),
                    PlanStep(2, "Run diagnostics", "terminal_execute", "Diagnostic output captured")
                ]
            )

        # 3. Direct App Launching
        if any(goal_lower.startswith(p) for p in ["open ", "launch ", "start "]):
            return ExecutionPlan(
                goal=user_goal,
                task_type="direct_action",
                steps=[PlanStep(1, "Open application", "computer_open_app", "App launched")]
            )

        # 4. Direct Media
        if goal_lower.startswith("play ") or "music" in goal_lower:
            return ExecutionPlan(
                goal=user_goal,
                task_type="direct_action",
                steps=[PlanStep(1, "Stream media", "computer_stream_youtube", "Media playing")]
            )

        # 5. General Query / Knowledge
        return ExecutionPlan(
            goal=user_goal,
            task_type="query",
            steps=[PlanStep(1, "Process user request with model/tools", "web_search", "Answer retrieved")]
        )

planner = Planner()
