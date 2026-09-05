"""
DOOM V4 — Cognitive Reflection Engine
Performs post-action empirical analysis (expected vs observed, failure diagnosis, next steps).
Never exposes internal chain-of-thought to users or logs.
"""

from typing import Dict, Any, List, Optional
from core.cognition.schemas import CognitiveReflection, CognitiveStep, CognitiveObservation, EvaluationOutcome


class ReflectionEngine:
    """
    Reflection Engine:
    Diagnoses outcome discrepancies and dictates whether dynamic replanning is necessary.
    """

    def reflect(
        self,
        cycle: int,
        step: CognitiveStep,
        observation: CognitiveObservation,
        outcome: EvaluationOutcome
    ) -> CognitiveReflection:
        """
        Synthesizes a structured reflection record.
        """
        expected = step.expected_outcome or f"Successful completion of {step.action}"
        observed = observation.stderr or observation.output or (f"Exit code {observation.exit_code}" if observation.exit_code is not None else "Nominal output")
        worked = (outcome == EvaluationOutcome.SUCCESS)

        failure_reason = None
        assumption_fault = None
        lesson = None
        next_action = ""
        should_replan = False

        if worked:
            next_action = "Advance to subsequent planned step or complete task."
            lesson = f"Step '{step.objective}' completed as expected."
        elif outcome == EvaluationOutcome.BLOCKED:
            failure_reason = observation.stderr or observation.output or "Action blocked by external dependency or safety policy"
            next_action = "Pause task and persist checkpoint until blocker resolves."
            should_replan = True
        else:  # FAILED or PARTIAL_SUCCESS
            failure_reason = observation.stderr or observation.output or "Execution returned non-zero exit code or error"
            assumption_fault = "Assumed code or action was error-free and immediately executable."
            lesson = "Failure detected; diagnose trace and adapt plan."
            next_action = "Trigger adaptive replanner to modify execution strategy."
            should_replan = True

        return CognitiveReflection(
            cycle=cycle,
            expected=expected,
            observed=observed[:250],
            worked=worked,
            failure_reason=failure_reason[:200] if failure_reason else None,
            assumption_fault=assumption_fault,
            lesson=lesson,
            next_action=next_action,
            should_replan=should_replan
        )


reflection_engine = ReflectionEngine()
