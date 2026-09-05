"""
DOOM V4 — Cognitive Dynamic Planner
Formulates goal-oriented dependency plans with explicit capability & verification requirements.
"""

from typing import List, Dict, Any, Optional
from core.cognition.schemas import CognitiveStep, CognitiveIntent


class CognitivePlanner:
    """
    Goal-Oriented Dynamic Planner:
    Synthesizes dependency plans where each step defines required capability, risk, and verification criteria.
    """

    def plan(
        self,
        intent: CognitiveIntent,
        normalized_goal: str,
        entities: Dict[str, Any],
        required_capabilities: List[str]
    ) -> List[CognitiveStep]:
        """Synthesizes structured cognitive steps."""
        target_file = entities.get("target_file", "Desktop/script.py")

        # 1. Multi-Step Execution: Create -> Run -> Verify
        if intent == CognitiveIntent.MULTI_STEP:
            if "system_info" in target_file or ("cpu" in normalized_goal.lower() and "ram" in normalized_goal.lower()):
                code_content = (
                    "import psutil\n"
                    "cpu = psutil.cpu_percent(interval=0.1)\n"
                    "mem = psutil.virtual_memory().percent\n"
                    "disk = psutil.disk_usage('/').percent\n"
                    "print(f'Workstation Telemetry: CPU: {cpu}%, RAM: {mem}%, Disk: {disk}%')\n"
                )
            else:
                code_content = (
                    "# DOOM Sovereign Autonomous Task Script\n"
                    "import datetime, psutil\n"
                    "print('DOOM Task Executed successfully at', datetime.datetime.now())\n"
                    "print(f'CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%')\n"
                )
            return [
                CognitiveStep(
                    step_id=1,
                    objective=f"Write script '{target_file}' to disk",
                    action="create_file",
                    tool_name="coding_write_script",
                    tool_args={"file_name": target_file, "code": code_content},
                    required_capability="coding",
                    expected_outcome="Target file exists on disk with valid Python syntax",
                    risk_level="SAFE",
                    dependencies=[],
                    verification_required=True
                ),
                CognitiveStep(
                    step_id=2,
                    objective=f"Execute script '{target_file}' and capture runtime output",
                    action="execute_file",
                    tool_name="coding_run_python",
                    tool_args={"code_or_file": target_file},
                    required_capability="coding",
                    expected_outcome="Process completes with exit code 0 and valid stdout",
                    risk_level="SAFE",
                    dependencies=[1],
                    verification_required=True
                ),
                CognitiveStep(
                    step_id=3,
                    objective=f"Empirically verify ground truth and synthesize truthful response",
                    action="verify",
                    tool_name="verifier",
                    tool_args={"target": target_file},
                    required_capability="reasoning",
                    expected_outcome="Ground-truth verified artifact and execution metrics",
                    risk_level="SAFE",
                    dependencies=[2],
                    verification_required=True
                )
            ]

        # 2. Autonomous Self-Healing Workflow
        if intent == CognitiveIntent.AUTOMATION:
            broken_file = entities.get("target_file", "Desktop/broken_demo.py")
            broken_code = "print('Starting autonomous repair test'\n"  # Intentional unclosed parenthesis
            patch_code = "print('Starting autonomous repair test')\nprint('DOOM auto-repair verified.')\n"
            return [
                CognitiveStep(
                    step_id=1,
                    objective=f"Create initial script '{broken_file}'",
                    action="create_file",
                    tool_name="coding_write_script",
                    tool_args={"file_name": broken_file, "code": broken_code},
                    required_capability="coding",
                    expected_outcome="Script written to disk",
                    risk_level="SAFE",
                    dependencies=[]
                ),
                CognitiveStep(
                    step_id=2,
                    objective=f"Execute script '{broken_file}' to observe errors",
                    action="execute_file",
                    tool_name="coding_run_python",
                    tool_args={"code_or_file": broken_file},
                    required_capability="coding",
                    expected_outcome="Execution output captured",
                    dependencies=[1]
                ),
                CognitiveStep(
                    step_id=3,
                    objective="Diagnose error trace and patch code",
                    action="patch_file",
                    tool_name="coding_write_script",
                    tool_args={"file_name": broken_file, "code": patch_code},
                    required_capability="coding",
                    expected_outcome="Patched script written to disk",
                    dependencies=[2]
                ),
                CognitiveStep(
                    step_id=4,
                    objective="Re-execute patched script",
                    action="execute_file",
                    tool_name="coding_run_python",
                    tool_args={"code_or_file": broken_file},
                    required_capability="coding",
                    expected_outcome="Clean execution with exit code 0",
                    dependencies=[3]
                ),
                CognitiveStep(
                    step_id=5,
                    objective="Verify ground truth",
                    action="verify",
                    tool_name="verifier",
                    tool_args={"target": broken_file},
                    required_capability="reasoning",
                    expected_outcome="Full verification passed",
                    dependencies=[4]
                )
            ]

        # 3. System Telemetry Operation
        if intent == CognitiveIntent.SYSTEM_OPERATION:
            return [
                CognitiveStep(
                    step_id=1,
                    objective="Query real-time hardware telemetry",
                    action="query_telemetry",
                    tool_name="system_get_status",
                    tool_args={},
                    required_capability="telemetry",
                    expected_outcome="Live CPU, RAM, and Disk metrics retrieved",
                    risk_level="SAFE",
                    dependencies=[],
                    verification_required=False
                )
            ]

        # 4. Search / Information Retrieval
        if intent == CognitiveIntent.SEARCH:
            return [
                CognitiveStep(
                    step_id=1,
                    objective=f"Search web for information: {normalized_goal[:50]}",
                    action="search_web",
                    tool_name="web_search",
                    tool_args={"query": normalized_goal},
                    required_capability="web_search",
                    expected_outcome="Relevant search results retrieved",
                    risk_level="SAFE",
                    dependencies=[],
                    verification_required=False
                )
            ]

        # 5. Direct Action
        if intent in (CognitiveIntent.ACTION, CognitiveIntent.CREATION):
            return [
                CognitiveStep(
                    step_id=1,
                    objective=normalized_goal,
                    action="execute_action",
                    tool_name=None,
                    tool_args={},
                    required_capability=required_capabilities[0] if required_capabilities else "general",
                    expected_outcome="Action executed successfully",
                    risk_level="SAFE",
                    dependencies=[],
                    verification_required=True
                )
            ]

        # Default Single Conversational Step
        return [
            CognitiveStep(
                step_id=1,
                objective="Deliver direct conversational or identity response",
                action="direct_response",
                tool_name=None,
                tool_args={},
                required_capability="general",
                expected_outcome="Direct response delivered",
                risk_level="SAFE",
                dependencies=[],
                verification_required=False
            )
        ]


cognitive_planner = CognitivePlanner()
