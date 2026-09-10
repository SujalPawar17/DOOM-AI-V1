"""V7.6 structured verification. Visual is unavailable. No planner."""

from __future__ import annotations

from proactive.computer.verify.kernel import execute_verification
from proactive.computer.verify.types import (
    VerificationRequest,
    VerificationResult,
    VerificationSpec,
    VerificationStatus,
    VerificationType,
)
from proactive.computer.verify.visual import visual_evidence

__all__ = [
    "VerificationRequest",
    "VerificationResult",
    "VerificationSpec",
    "VerificationStatus",
    "VerificationType",
    "execute_verification",
    "visual_evidence",
]
