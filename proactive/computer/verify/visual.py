"""Bounded visual evidence. Capture and remote image analysis are not wired."""

from __future__ import annotations

from typing import Any, Dict

from proactive.computer.verify.types import VerificationStatus


def visual_evidence(_request: Any = None) -> Dict[str, Any]:
    """Supplementary visual slot. Screen capture and CV libraries are not used."""
    return {
        "available": False,
        "local": False,
        "dependency": "",
        "cloud": False,
        "status": VerificationStatus.VERIFICATION_UNAVAILABLE.value,
        "failure_code": "VISUAL_RUNTIME_UNAVAILABLE",
    }
