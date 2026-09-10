"""V7.3 bounded browser control. No generic execute(command)."""

from __future__ import annotations

from proactive.computer.browser.kernel import (
    close_browser_session,
    execute_browser_action,
    open_browser_session,
)
from proactive.computer.browser.types import (
    BrowserActionRequest,
    BrowserActionResult,
    BrowserActionType,
    BrowserSessionState,
    BrowserTarget,
    Status,
)

__all__ = [
    "BrowserActionRequest",
    "BrowserActionResult",
    "BrowserActionType",
    "BrowserSessionState",
    "BrowserTarget",
    "Status",
    "close_browser_session",
    "execute_browser_action",
    "open_browser_session",
]
