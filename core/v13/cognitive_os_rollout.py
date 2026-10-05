"""V13.1 Production Cognitive OS — controlled rollout of the unified DoomOS.

Modes (DOOM_COGNITIVE_OS):
    off  (default)  -> V8 core (doom_core.process_request), unchanged behaviour
    on              -> unified V12 DoomOS

Rollout guarantees:
- Lazy, single-instance initialization (thread-safe). Initialization latency is measured.
- Initialization failure -> DEGRADED state and safe fallback to the V8 core for that
  request (nothing ran in DoomOS yet, so falling back cannot duplicate anything).
  Re-initialization is attempted at most once per backoff window (no init storms).
- A failure *during* a DoomOS request never falls back to V8: the request may already
  have acted, and re-running it elsewhere could execute twice. The user gets a truthful
  error instead.
- Exactly one response is produced per input; the caller speaks it once (DoomOS itself
  is constructed without a speak callable).
- DoomOS's proactive runtime/monitor is NOT started here; production keeps its existing
  monitors (no duplicate monitor).
- Clean shutdown (registered once with atexit) and explicit restart.
"""

from __future__ import annotations

import atexit
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

INIT_RETRY_BACKOFF_S = 60.0
SAFE_FAILURE_TEXT = "I couldn't complete that safely, and I haven't retried it. Please try again."


class RolloutMode(str, Enum):
    OFF = "off"
    ON = "on"


class OSState(str, Enum):
    DISABLED = "DISABLED"
    NOT_INITIALIZED = "NOT_INITIALIZED"
    READY = "READY"
    DEGRADED = "DEGRADED"
    SHUT_DOWN = "SHUT_DOWN"


@dataclass(frozen=True)
class RoutedResponse:
    text: str
    route: str          # "v8" | "cognitive" | "v8_fallback" | "cognitive_error"
    detail: str = ""


def rollout_mode() -> RolloutMode:
    raw = os.getenv("DOOM_COGNITIVE_OS", "").strip().lower()
    return RolloutMode.ON if raw in ("1", "true", "yes", "on") else RolloutMode.OFF


class CognitiveOSRollout:

    def __init__(self, factory: Optional[Callable[[], Any]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 backoff_s: float = INIT_RETRY_BACKOFF_S):
        self._factory = factory or _default_factory
        self._clock = clock
        self._backoff_s = backoff_s
        self._lock = threading.Lock()
        self._os: Any = None
        self._state = OSState.NOT_INITIALIZED
        self._last_error = ""
        self._last_attempt = -1e18
        self._init_ms: Optional[float] = None
        self._init_count = 0
        self._atexit_registered = False
        self.stats: Dict[str, int] = {"cognitive": 0, "v8": 0, "v8_fallback": 0, "cognitive_error": 0}

    # -- lifecycle ---------------------------------------------------------------------

    def _ensure_os(self) -> Tuple[Any, str]:
        with self._lock:
            if self._os is not None:
                return self._os, ""
            now = self._clock()
            if self._state is OSState.DEGRADED and now - self._last_attempt < self._backoff_s:
                return None, self._last_error
            self._last_attempt = now
            t0 = time.perf_counter()
            try:
                self._os = self._factory()
            except Exception as exc:  # init failure -> degraded, safe fallback
                self._state = OSState.DEGRADED
                self._last_error = type(exc).__name__
                return None, self._last_error
            self._init_ms = (time.perf_counter() - t0) * 1000
            self._init_count += 1
            self._state = OSState.READY
            self._last_error = ""
            if not self._atexit_registered:
                atexit.register(self.shutdown)
                self._atexit_registered = True
            return self._os, ""

    def shutdown(self) -> None:
        with self._lock:
            doom_os, self._os = self._os, None
            if self._state is not OSState.DISABLED:
                self._state = OSState.SHUT_DOWN
        if doom_os is not None:
            try:
                doom_os.shutdown()
            except Exception:
                pass
            if self._factory is _default_factory:
                _default_reset()

    def restart(self) -> None:
        self.shutdown()
        with self._lock:
            self._state = OSState.NOT_INITIALIZED
            self._last_attempt = -1e18

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {"mode": rollout_mode().value, "state": self._state.value, "last_error": self._last_error,
                    "init_ms": round(self._init_ms, 1) if self._init_ms is not None else None,
                    "init_count": self._init_count, "routes": dict(self.stats)}

    # -- routing --------------------------------------------------------------------------

    def _count(self, route: str) -> None:
        with self._lock:
            self.stats[route] += 1

    def route(self, text: str, lang: Optional[str], source: str,
              v8_handler: Callable[[str, Optional[str], str], str]) -> RoutedResponse:
        if rollout_mode() is RolloutMode.OFF:
            self._count("v8")
            return RoutedResponse(v8_handler(text, lang, source), "v8")
        doom_os, error = self._ensure_os()
        if doom_os is None:
            # Nothing ran in DoomOS: falling back cannot duplicate an action.
            self._count("v8_fallback")
            return RoutedResponse(v8_handler(text, lang, source), "v8_fallback", error)
        try:
            reply = doom_os.handle_text(text, lang, source=source)
        except Exception as exc:
            # The request may already have acted; never re-run it on another pipeline.
            self._count("cognitive_error")
            return RoutedResponse(SAFE_FAILURE_TEXT, "cognitive_error", type(exc).__name__)
        self._count("cognitive")
        return RoutedResponse(reply.text or SAFE_FAILURE_TEXT, "cognitive")


def _default_factory() -> Any:
    # The process-wide V12 singleton, constructed without a speak callable:
    # the caller speaks exactly once.
    from core.v12.doom_os import get_doom_os
    return get_doom_os()


def _default_reset() -> None:
    from core.v12.doom_os import reset_doom_os
    reset_doom_os()


_ROLLOUT_LOCK = threading.Lock()
_ROLLOUT: Optional[CognitiveOSRollout] = None


def get_rollout() -> CognitiveOSRollout:
    global _ROLLOUT
    with _ROLLOUT_LOCK:
        if _ROLLOUT is None:
            _ROLLOUT = CognitiveOSRollout()
        return _ROLLOUT
