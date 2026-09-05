"""
DOOM V3 — Unified State Machine
Defines consistent system states across voice, terminal, WebSocket, and Web HUD.
"""

from enum import Enum
import time
from typing import Callable, List, Optional, Dict, Any


class DoomState(str, Enum):
    OFFLINE = "OFFLINE"
    STARTING = "STARTING"
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    PLANNING = "PLANNING"
    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class StateMachine:
    """Thread-safe state manager for DOOM V3 with event listener notifications."""

    def __init__(self):
        self._current_state: DoomState = DoomState.IDLE
        self._state_message: str = "Awaiting command, Boss."
        self._active_task_id: Optional[str] = None
        self._listeners: List[Callable[[DoomState, str, Optional[str]], None]] = []
        self._last_changed: float = time.time()

    @property
    def current_state(self) -> DoomState:
        return self._current_state

    @property
    def state_message(self) -> str:
        return self._state_message

    @property
    def active_task_id(self) -> Optional[str]:
        return self._active_task_id

    def transition_to(
        self,
        new_state: DoomState,
        message: Optional[str] = None,
        task_id: Optional[str] = None
    ) -> None:
        """Transitions to a new state and alerts all registered listeners."""
        self._current_state = new_state
        if message:
            self._state_message = message
        if task_id is not None:
            self._active_task_id = task_id
        elif new_state in [DoomState.IDLE, DoomState.COMPLETED, DoomState.ERROR]:
            if new_state == DoomState.IDLE:
                self._active_task_id = None

        self._last_changed = time.time()
        for listener in list(self._listeners):
            try:
                listener(self._current_state, self._state_message, self._active_task_id)
            except Exception:
                pass

    def add_listener(self, listener: Callable[[DoomState, str, Optional[str]], None]) -> None:
        """Registers a callback for state changes."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[DoomState, str, Optional[str]], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def get_status_payload(self) -> Dict[str, Any]:
        """Returns structured state payload for REST / WebSocket broadcasts."""
        return {
            "state": self._current_state.value,
            "message": self._state_message,
            "task_id": self._active_task_id,
            "timestamp": time.strftime("%H:%M:%S")
        }


# Global State Machine instance
state_machine = StateMachine()
