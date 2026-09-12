"""V8.5 verification-aware bounded recovery.

Recovery is not an agent. A failed V8.4 plan is never rewritten and never
inherits approval. At most one recovery attempt may produce a NEW GoalPlan
with a NEW plan_hash and a NEW authorization bind, then execute only through
V8.4 execute_plan(). V7 remains the exclusive computer-control boundary.

NOT_VERIFIED is never SUCCESS without a subsequent V7.6 verification result.
No LLM, network, learning, nested recovery, or V8.6+ task state.
"""

from orchestration.recovery.classifier import classify_failure
from orchestration.recovery.engine import recover_execution, reset_recovery_attempts_for_tests
from orchestration.recovery.errors import (
    MAX_RECOVERY_ATTEMPTS,
    RecoveryAction,
    RecoveryFailureClass,
    RecoveryStatus,
)
from orchestration.recovery.planner import propose_recovery
from orchestration.recovery.types import RecoveryExecutionResult, RecoveryFailure, RecoveryProposal

__all__ = [
    "MAX_RECOVERY_ATTEMPTS",
    "RecoveryAction",
    "RecoveryExecutionResult",
    "RecoveryFailure",
    "RecoveryFailureClass",
    "RecoveryProposal",
    "RecoveryStatus",
    "classify_failure",
    "propose_recovery",
    "recover_execution",
    "reset_recovery_attempts_for_tests",
]
