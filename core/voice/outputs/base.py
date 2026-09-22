"""AudioOutput protocol — minimal abstraction for audio playback."""

from __future__ import annotations

from typing import Protocol, Iterator, Optional
from enum import Enum


class OutputStatus(str, Enum):
    PLAYING = "PLAYING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class AudioOutput(Protocol):
    """Protocol for audio output backends."""

    def play(self, audio_path: str) -> OutputStatus:
        ...

    def stop(self) -> None:
        ...

    def get_status(self) -> OutputStatus:
        ...

    def play_stream(
        self,
        audio_chunks: Iterator[bytes],
        sample_rate: int = 24000,
        channels: int = 1,
    ) -> OutputStatus:
        """Play streaming audio chunks. Optional method for streaming-capable backends."""
        ...