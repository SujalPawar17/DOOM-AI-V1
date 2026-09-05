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
        """Transforms tool result or CanonicalToolResult into a normalized CognitiveObservation."""
        if isinstance(raw_result, CanonicalToolResult):
            return CognitiveObservation(
                action=raw_result.action or step.action,
                tool=raw_result.tool or step.tool_name or "unknown",
                success=raw_result.success,
                stdout=raw_result.stdout or "",
                stderr=raw_result.stderr or "",
                output=raw_result.output or "",
                exit_code=raw_result.exit_code,
                artifacts=[raw_result.artifact] if raw_result.artifact else [],
                duration_ms=raw_result.duration_ms,
                verification_relevance="Direct execution artifact" if raw_result.artifact else "Process execution output"
            )

        # Fallback for plain dictionary or object
        success = getattr(raw_result, "success", True) if hasattr(raw_result, "success") else True
        output = getattr(raw_result, "output", str(raw_result)) if hasattr(raw_result, "output") else str(raw_result)
        return CognitiveObservation(
            action=step.action,
            tool=step.tool_name or "unknown",
            success=success,
            output=output,
            verification_relevance="Unstructured tool execution"
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
