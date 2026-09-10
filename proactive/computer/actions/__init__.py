"""V7.2 UIA click/type kernel. Observe remains V7.1. No coordinate automation."""

from __future__ import annotations

from proactive.computer.actions.kernel import execute_computer_action
from proactive.computer.actions.types import (
    ActionType,
    ApprovalState,
    ComputerActionRequest,
    ComputerActionResult,
    Status,
    TargetIdentity,
)

__all__ = [
    "ActionType",
    "ApprovalState",
    "ComputerActionRequest",
    "ComputerActionResult",
    "Status",
    "TargetIdentity",
    "execute_computer_action",
]
