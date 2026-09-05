"""
DOOM V4 — Cognitive Observation & Evaluation Layer
Normalizes empirical tool execution results into structured evidence and evaluates objective fulfillment.
"""

import time
from typing import Dict, Any, List, Optional
from tools.base import CanonicalToolResult
from core.cognition.schemas import CognitiveObservation, CognitiveStep, EvaluationOutcome


class ObservationEngine:
    """
    Observation Layer:
    Normalizes empirical results and evaluates step achievement based strictly on evidence.
    """

    def observe(self, raw_result: Any, step: CognitiveStep) -> CognitiveObservation:
        """Transforms ToolResult or CanonicalToolResult into a normalized CognitiveObservation."""
        if raw_result is None:
            success = False
            output = "No result returned from tool execution."
            stdout = ""
            stderr = "No result returned from tool execution."
            exit_code = 1
            action = step.action
            tool = step.tool_name or "unknown"
            duration_ms = 0.0
            raw_art = None
        else:
            success = getattr(raw_result, "success", True)
            output = getattr(raw_result, "output", str(raw_result)) or ""
            stdout = getattr(raw_result, "stdout", "") or ""
            stderr = getattr(raw_result, "stderr", "") or ""
            exit_code = getattr(raw_result, "exit_code", 0 if success else 1)
            action = getattr(raw_result, "action", "") or step.action
            tool = getattr(raw_result, "tool", "") or step.tool_name or "unknown"
            duration_ms = getattr(raw_result, "duration_ms", 0.0)
            raw_art = getattr(raw_result, "artifact", None)
        artifacts = [raw_art] if raw_art and isinstance(raw_art, dict) else []
        if not artifacts and hasattr(raw_result, "data") and isinstance(raw_result.data, dict) and "path" in raw_result.data:
            artifacts = [raw_result.data]

        # Auto-resolve artifact for create_file / patch_file if file exists on disk
        if (step.action in ("create_file", "patch_file")) and not artifacts:
            fname = step.tool_args.get("file_name") or step.tool_args.get("file_path") or step.tool_args.get("code_or_file")
            if fname:
                try:
                    from core.path_resolver import canonical_path
                    cp = canonical_path(fname)
                    if cp.exists:
                        artifacts = [{
                            "path": cp.absolute_path,
                            "relative_path": cp.relative_path,
                            "name": cp.filename,
                            "size_bytes": os.path.getsize(cp.absolute_path),
                            "exists": True
                        }]
                except Exception:
                    pass

        return CognitiveObservation(
            action=action,
            tool=tool,
            success=success,
            stdout=stdout,
            stderr=stderr,
            output=output,
            exit_code=exit_code,
            artifacts=artifacts,
            duration_ms=duration_ms,
            verification_relevance="Direct execution artifact" if artifacts else "Process execution output"
        )

    def evaluate(self, step: CognitiveStep, observation: CognitiveObservation) -> EvaluationOutcome:
        """
        Evaluates whether an action actually achieved its objective based on physical evidence.
        """
        if not observation.success:
            if "blocked" in (observation.stderr or observation.output).lower() or observation.exit_code == -2:
                return EvaluationOutcome.BLOCKED
            return EvaluationOutcome.FAILED

        # For file creation: require non-empty artifact
        if step.action == "create_file":
            if observation.artifacts:
                return EvaluationOutcome.SUCCESS
            return EvaluationOutcome.PARTIAL_SUCCESS

        # For file execution: require exit_code == 0
        if step.action == "execute_file":
            if observation.exit_code == 0:
                return EvaluationOutcome.SUCCESS
            elif observation.exit_code is not None and observation.exit_code != 0:
                return EvaluationOutcome.FAILED
            return EvaluationOutcome.SUCCESS if observation.success else EvaluationOutcome.FAILED

        return EvaluationOutcome.SUCCESS if observation.success else EvaluationOutcome.FAILED


observation_engine = ObservationEngine()
