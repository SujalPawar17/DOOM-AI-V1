"""
DOOM V4 — Cognitive Core Package
Public interface for the 9-stage cognitive architecture.
"""

from core.cognition.schemas import (
    CognitiveState,
    CognitiveIntent,
    CognitiveDecisionType,
    CognitiveStep,
    CognitiveObservation,
    CognitiveReflection,
    EvaluationOutcome,
    CognitiveTelemetry
)
from core.cognition.engine import cognitive_engine, CognitiveEngine
from core.cognition.understanding import understanding_engine, UnderstandingEngine
from core.cognition.reasoning import reasoning_engine, ReasoningEngine
from core.cognition.decision import cognitive_decision_engine, CognitiveDecisionEngine
from core.cognition.planner import cognitive_planner, CognitivePlanner
from core.cognition.observation import observation_engine, ObservationEngine
from core.cognition.reflection import reflection_engine, ReflectionEngine
from core.cognition.replanning import cognitive_replanner, CognitiveReplanner

__all__ = [
    "cognitive_engine",
    "CognitiveEngine",
    "CognitiveState",
    "CognitiveIntent",
    "CognitiveDecisionType",
    "CognitiveStep",
    "CognitiveObservation",
    "CognitiveReflection",
    "EvaluationOutcome",
    "CognitiveTelemetry",
    "understanding_engine",
    "UnderstandingEngine",
    "reasoning_engine",
    "ReasoningEngine",
    "cognitive_decision_engine",
    "CognitiveDecisionEngine",
    "cognitive_planner",
    "CognitivePlanner",
    "observation_engine",
    "ObservationEngine",
    "reflection_engine",
    "ReflectionEngine",
    "cognitive_replanner",
    "CognitiveReplanner"
]
