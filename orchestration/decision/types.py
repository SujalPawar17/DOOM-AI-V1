"""V8.22 typed decision model. Informational only — never authorizes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Tuple


class DecisionConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class DecisionStatus(str, Enum):
    OK = "OK"
    CLARIFY = "CLARIFY"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    UNAVAILABLE = "UNAVAILABLE"


# Hard bounds (chars / counts).
MAX_QUESTION_CHARS = 500
MAX_FACTS = 6
MAX_FACT_CHARS = 200
MAX_CONSTRAINTS = 6
MAX_CONSTRAINT_CHARS = 160
MAX_OPTIONS = 6
MAX_OPTION_CHARS = 120
MAX_PREFERENCES = 4
MAX_PREFERENCE_CHARS = 160
MAX_ASSUMPTIONS = 4
MAX_ASSUMPTION_CHARS = 160
MAX_REASONS = 6
MAX_REASON_CHARS = 200
MAX_TRADEOFFS = 6
MAX_TRADEOFF_CHARS = 200


@dataclass(frozen=True)
class DecisionSourceFlags:
    conversation: bool = False
    memory: bool = False
    system: bool = False
    situation: bool = False


@dataclass(frozen=True)
class DecisionInput:
    """Bounded decision package. No identity / auth / secrets."""

    question: str = ""
    facts: Tuple[str, ...] = ()
    constraints: Tuple[str, ...] = ()
    options: Tuple[str, ...] = ()
    preferences: Tuple[str, ...] = ()
    situation_factors: Dict[str, str] = field(default_factory=dict)
    assumptions_seed: Tuple[str, ...] = ()
    source_flags: DecisionSourceFlags = field(default_factory=DecisionSourceFlags)


@dataclass(frozen=True)
class DecisionResult:
    status: DecisionStatus = DecisionStatus.UNAVAILABLE
    recommendation: str = ""
    reasons: Tuple[str, ...] = ()
    tradeoffs: Tuple[str, ...] = ()
    confidence: DecisionConfidence = DecisionConfidence.LOW
    assumptions: Tuple[str, ...] = ()
    clarification: str = ""
    options_considered: Tuple[str, ...] = ()
