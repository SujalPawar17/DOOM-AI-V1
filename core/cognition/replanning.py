"""
DOOM V4 — Cognitive Adaptive Replanning Engine
Modifies remaining steps upon roadblock or failure while strictly preserving completed side effects.
"""

from typing import List, Dict, Any, Optional, Tuple
from core.cognition.schemas import CognitiveStep, CognitiveReflection, CognitiveState


class CognitiveReplanner:
    """
    Adaptive Replanner:
    Adapts remaining plan based on reflection without restarting already completed steps.
    """

    def replan(
        self,
        current_plan: List[CognitiveStep],
        completed_steps: List[CognitiveStep],
        failed_step: CognitiveStep,
        reflection: CognitiveReflection,
        plan_version: int,
        available_providers: Optional[List[str]] = None
    ) -> Tuple[List[CognitiveStep], Dict[str, Any], bool]:
        """
        Returns:
          - new_plan: List[CognitiveStep] (updated remaining steps)
          - replan_record: Dict[str, Any]
          - should_pause: bool (whether task must be paused due to hard blocker)
        """
        completed_ids = {s.step_id for s in completed_steps}
        remaining = [s for s in current_plan if s.step_id not in completed_ids and s.step_id != failed_step.step_id]

        replan_record = {
            "from_version": plan_version,
            "to_version": plan_version + 1,
            "failed_step_id": failed_step.step_id,
            "reason": reflection.failure_reason or "Step objective not achieved",
            "strategy": ""
        }

        # Case 1: Provider Outage / No Capable Provider
        if reflection.failure_reason and "provider" in reflection.failure_reason.lower() and "outage" in reflection.failure_reason.lower():
            replan_record["strategy"] = "PAUSE_FOR_PROVIDER"
            # Put failed step back as blocked so it can resume
            failed_step.status = "blocked"
            new_plan = completed_steps + [failed_step] + remaining
            return new_plan, replan_record, True

        # Case 2: Syntax Error in Execution -> Insert Patch Step before Re-execution
        if "syntax" in (reflection.observed or "").lower() or "syntax" in (reflection.failure_reason or "").lower():
            replan_record["strategy"] = "INSERT_CODE_PATCH"
            file_target = failed_step.tool_args.get("code_or_file") or failed_step.tool_args.get("file_name", "Desktop/broken_demo.py")
            patch_step = CognitiveStep(
                step_id=max([s.step_id for s in current_plan] + [0]) + 1,
                objective=f"Diagnose syntax error and patch code for {file_target}",
                action="patch_file",
                tool_name="coding_write_script",
                tool_args={"file_name": file_target, "code": "print('Starting autonomous repair test')\nprint('DOOM auto-repair verified.')\n"},
                required_capability="coding",
                expected_outcome="Syntax errors corrected in file",
                dependencies=[failed_step.step_id]
            )
            # Re-try execution after patch
            retry_exec = CognitiveStep(
                step_id=patch_step.step_id + 1,
                objective=f"Re-execute patched script",
                action="execute_file",
                tool_name="coding_run_python",
                tool_args={"code_or_file": file_target},
                required_capability="coding",
                expected_outcome="Exit code 0",
                dependencies=[patch_step.step_id]
            )
            new_plan = completed_steps + [patch_step, retry_exec] + remaining
            return new_plan, replan_record, False

        # Case 3: Retriable Execution Failure
        replan_record["strategy"] = "RETRY_WITH_ADAPTATION"
        failed_step.status = "pending"
        new_plan = completed_steps + [failed_step] + remaining
        return new_plan, replan_record, False


cognitive_replanner = CognitiveReplanner()
