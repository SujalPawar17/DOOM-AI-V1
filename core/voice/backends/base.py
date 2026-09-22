"""TTSBackend protocol — minimal abstraction for TTS backends."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable
from enum import Enum


class BackendStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


@runtime_checkable
class TTSBackend(Protocol):
    """Protocol for TTS backends. Minimal, extensible for future backends."""

    @property
    def name(self) -> str:
        ...

    def is_available(self) -> bool:
        ...

    def is_allowed(self, lang: Optional[str] = None) -> bool:
        ...

    def synthesize(self, text: str, lang: Optional[str] = None) -> BackendStatus:
        ...

    def stop(self) -> None:
        ...

    def get_voice_for_language(self, lang: str) -> Optional[str]:
        ...