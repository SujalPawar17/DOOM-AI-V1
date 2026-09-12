"""Deterministic V8.9 explanations. Derived from reason codes. Not LLM-generated."""

from __future__ import annotations

from orchestration.audit.codes import AuditEventCode, AuditReasonCode
from orchestration.audit.redaction import redact_text

MAX_EXPLANATION_CHARS = 1000

_TEMPLATES = {
    AuditReasonCode.APPROVAL_REQUIRED: (
        "Execution was blocked because the current plan requires authorization."
    ),
    AuditReasonCode.PLAN_HASH_MISMATCH: (
        "Execution was blocked because the supplied plan hash did not match the validated plan."
    ),
    AuditReasonCode.NOT_VERIFIED: (
        "The action completed, but the required verification did not establish the requested state."
    ),
    AuditReasonCode.VERIFICATION_FAILED: (
        "Verification returned a failed result for the requested state."
    ),
    AuditReasonCode.VERIFICATION_PASSED: (
        "Verification returned a passed result for the requested state."
    ),
    AuditReasonCode.DEPENDENCY_FAILED: (
        "This step was blocked because a required earlier step failed."
    ),
    AuditReasonCode.EMERGENCY_STOP: (
        "Execution was aborted by the emergency-stop boundary."
    ),
    AuditReasonCode.STALE_IN_FLIGHT: (
        "The persisted task was marked aborted after restart because execution state could not be safely resumed."
    ),
    AuditReasonCode.CANCELLED: (
        "Future work was stopped. External effects already performed were not rolled back."
    ),
    AuditReasonCode.V8_DISABLED: (
        "The V8 orchestration flag is disabled, so this stage did not proceed."
    ),
    AuditReasonCode.CAPABILITY_UNAVAILABLE: (
        "Validation or execution was blocked because the requested capability is unavailable."
    ),
    AuditReasonCode.INVALID_PLAN: (
        "The plan was rejected because it failed structural validation."
    ),
    AuditReasonCode.PLAN_VALIDATION_FAILED: (
        "Validation rejected the proposed plan."
    ),
    AuditReasonCode.TIMEOUT: (
        "The stage ended because a timeout bound was reached."
    ),
    AuditReasonCode.PRECONDITION_FAILED: (
        "The stage was blocked because a required precondition was not met."
    ),
    AuditReasonCode.ABORTED: (
        "The task was aborted. This is not a successful completion."
    ),
    AuditReasonCode.FAILED: (
        "The authoritative result was FAILED."
    ),
    AuditReasonCode.SUCCESS: (
        "The authoritative result was SUCCESS."
    ),
    AuditReasonCode.BLOCKED: (
        "The stage was blocked by an existing policy or availability check."
    ),
    AuditReasonCode.CONTEXT_UNAVAILABLE: (
        "Context retrieval was unavailable. No fabricated context was used."
    ),
    AuditReasonCode.CONTEXT_EMPTY: (
        "Context retrieval returned no historical items."
    ),
    AuditReasonCode.CONTEXT_OK: (
        "Bounded historical context was retrieved as data only."
    ),
    AuditReasonCode.LEDGER_ERROR: (
        "A ledger persistence error was reported. This does not authorize retry or replay."
    ),
    AuditReasonCode.SESSION_UNAVAILABLE: (
        "The session bound to the plan was unavailable or did not match."
    ),
    AuditReasonCode.OWNER_MISMATCH: (
        "The owner bound to the request did not match the authoritative record."
    ),
    AuditReasonCode.UNKNOWN_INTENT: (
        "The goal was rejected because the intent could not be classified."
    ),
    AuditReasonCode.AMBIGUOUS_INTENT: (
        "The goal was rejected because the intent was ambiguous."
    ),
    AuditReasonCode.UNSUPPORTED_INTENT: (
        "Planning was unavailable for this intent class."
    ),
    AuditReasonCode.INVALID_GOAL: (
        "The goal record was invalid."
    ),
    AuditReasonCode.APPROVAL_MISMATCH: (
        "Authorization did not match the validated plan hash."
    ),
    AuditReasonCode.ACTION_UNAVAILABLE: (
        "The requested action is not available on the capability."
    ),
    AuditReasonCode.STEP_FAILED: (
        "A plan step failed according to the execution result."
    ),
    AuditReasonCode.RECOVERY_PROPOSED: (
        "A recovery proposal was recorded. New authorization is still required for any new plan."
    ),
    AuditReasonCode.RECOVERY_BLOCKED: (
        "Recovery was not eligible or was blocked."
    ),
    AuditReasonCode.RECOVERY_COMPLETED: (
        "A recovery execution result was recorded. This does not grant future authorization."
    ),
    AuditReasonCode.NONE: (
        "No additional structured reason was supplied."
    ),
}


_FORBIDDEN_SUCCESS_REASONS = frozenset({
    AuditReasonCode.FAILED,
    AuditReasonCode.NOT_VERIFIED,
    AuditReasonCode.VERIFICATION_FAILED,
    AuditReasonCode.ABORTED,
    AuditReasonCode.CANCELLED,
    AuditReasonCode.EMERGENCY_STOP,
    AuditReasonCode.STALE_IN_FLIGHT,
    AuditReasonCode.TIMEOUT,
    AuditReasonCode.PRECONDITION_FAILED,
    AuditReasonCode.BLOCKED,
    AuditReasonCode.PLAN_HASH_MISMATCH,
    AuditReasonCode.APPROVAL_REQUIRED,
})


def explain(
    event_code: AuditEventCode,
    reason_code: AuditReasonCode,
    outcome: str,
    custom: str = "",
) -> str:
    """Build a factual explanation from structured codes. Does not decide outcomes."""
    outcome_u = str(outcome or "").upper()
    if event_code is AuditEventCode.NOT_VERIFIED or reason_code is AuditReasonCode.NOT_VERIFIED:
        text = _TEMPLATES[AuditReasonCode.NOT_VERIFIED]
    elif event_code is AuditEventCode.TASK_CANCELLED or reason_code is AuditReasonCode.CANCELLED:
        text = _TEMPLATES[AuditReasonCode.CANCELLED]
    elif reason_code is AuditReasonCode.STALE_IN_FLIGHT:
        text = _TEMPLATES[AuditReasonCode.STALE_IN_FLIGHT]
    else:
        text = _TEMPLATES.get(reason_code, _TEMPLATES[AuditReasonCode.NONE])
    extra = str(custom or "").strip()
    if extra:
        redacted, _ = redact_text(extra, MAX_EXPLANATION_CHARS)
        text = "%s %s" % (text, redacted)
    if outcome_u in ("SUCCESS", "COMPLETED") and reason_code in _FORBIDDEN_SUCCESS_REASONS:
        text = _TEMPLATES.get(reason_code, text)
    if "rollback" in text.lower() and reason_code is not AuditReasonCode.CANCELLED:
        text = text.replace("rolled back", "not rolled back")
    redacted, _ = redact_text(text, MAX_EXPLANATION_CHARS)
    return redacted
