"""Deterministic V8.5 failure classifier. Structured ExecutionResult only."""

from __future__ import annotations

from typing import Any

from orchestration.executor import ExecutionResult
from orchestration.executor_errors import ExecutionStatus
from orchestration.recovery.errors import RecoveryFailureClass
from orchestration.recovery.types import RecoveryFailure

_RECOVERABLE = frozenset({
    RecoveryFailureClass.TIMEOUT,
    RecoveryFailureClass.PRECONDITION_FAILED,
    RecoveryFailureClass.NOT_VERIFIED,
    RecoveryFailureClass.VERIFICATION_FAILED,
})

_STATUS_MAP = {
    ExecutionStatus.SUCCESS: RecoveryFailureClass.SUCCESS,
    ExecutionStatus.TIMEOUT: RecoveryFailureClass.TIMEOUT,
    ExecutionStatus.PRECONDITION_FAILED: RecoveryFailureClass.PRECONDITION_FAILED,
    ExecutionStatus.NOT_VERIFIED: RecoveryFailureClass.NOT_VERIFIED,
    ExecutionStatus.STEP_FAILED: RecoveryFailureClass.STEP_FAILED,
    ExecutionStatus.BLOCKED: RecoveryFailureClass.BLOCKED,
    ExecutionStatus.ABORTED: RecoveryFailureClass.ABORTED,
    ExecutionStatus.CANCELLED: RecoveryFailureClass.ABORTED,
    ExecutionStatus.EMERGENCY_STOPPED: RecoveryFailureClass.EMERGENCY_STOPPED,
    ExecutionStatus.APPROVAL_REQUIRED: RecoveryFailureClass.APPROVAL_REQUIRED,
    ExecutionStatus.PLAN_HASH_MISMATCH: RecoveryFailureClass.PLAN_HASH_MISMATCH,
    ExecutionStatus.INVALID_PLAN: RecoveryFailureClass.INVALID_PLAN,
    ExecutionStatus.CAPABILITY_UNAVAILABLE: RecoveryFailureClass.CAPABILITY_UNAVAILABLE,
    ExecutionStatus.ACTION_UNAVAILABLE: RecoveryFailureClass.ACTION_UNAVAILABLE,
    ExecutionStatus.SESSION_UNAVAILABLE: RecoveryFailureClass.SESSION_UNAVAILABLE,
    ExecutionStatus.V8_DISABLED: RecoveryFailureClass.V8_DISABLED,
    ExecutionStatus.FAILED: RecoveryFailureClass.UNKNOWN,
}


def classify_failure(execution_result: Any) -> RecoveryFailure:
    """Map a V8.4 ExecutionResult to a typed failure class. No screenshots or LLM."""
    if type(execution_result) is not ExecutionResult:
        return RecoveryFailure(
            failure_class=RecoveryFailureClass.UNKNOWN,
            reason_code="MALFORMED_RESULT",
            recoverable=False,
        )
    klass = _STATUS_MAP.get(execution_result.status, RecoveryFailureClass.UNKNOWN)
    if klass is RecoveryFailureClass.NOT_VERIFIED:
        vstate = str(execution_result.verification_state or "")
        if vstate and vstate not in ("", "NOT_VERIFIED", "VERIFIED"):
            klass = RecoveryFailureClass.VERIFICATION_FAILED
    return RecoveryFailure(
        failure_class=klass,
        reason_code=klass.value,
        failed_step_id=str(execution_result.failed_step_id or ""),
        recoverable=klass in _RECOVERABLE,
    )
