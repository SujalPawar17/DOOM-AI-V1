"""Pygame audio output implementation."""

from __future__ import annotations

import os
import time
import threading
from core.voice.outputs.base import AudioOutput, OutputStatus


class PygameOutput:
    """Pygame mixer-based audio output. Preserves existing behavior."""

    def __init__(
        self,
        frequency: int = 24000,
        size: int = -16,
        channels: int = 2,
        buffer: int = 2048,
    ):
        self.frequency = frequency
        self.size = size
        self.channels = channels
        self.buffer = buffer
        self._mixer = None
        self._lock = threading.Lock()
        self._status = OutputStatus.STOPPED

    def _ensure_mixer(self) -> bool:
        """Initialize pygame mixer if not already initialized."""
        with self._lock:
            if self._mixer is not None:
                return True
            try:
                import pygame
                if not pygame.mixer.get_init():
                    pygame.mixer.init(
                        frequency=self.frequency,
                        size=self.size,
                        channels=self.channels,
                        buffer=self.buffer,
                    )
                self._mixer = pygame.mixer
                return True
            except Exception as e:
                print(f"[VOICE] Audio hardware unavailable ({e}).")
                self._mixer = None
                return False

    def play(self, audio_path: str) -> OutputStatus:
        """Play an audio file. Blocking until complete or stopped."""
        if not self._ensure_mixer():
            self._status = OutputStatus.ERROR
            return self._status

        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            self._status = OutputStatus.ERROR
            return self._status

        try:
            import pygame
            self._mixer.music.load(audio_path)
            self._mixer.music.play()
            self._status = OutputStatus.PLAYING

            # Blocking wait - preserves existing behavior
            while self._mixer.music.get_busy():
                time.sleep(0.05)
                if self._status == OutputStatus.STOPPED:
                    break

            self._mixer.music.stop()
            self._status = OutputStatus.STOPPED
            return OutputStatus.STOPPED
        except Exception as e:
            print(f"[VOICE] Audio playback error: {e}")
            self._status = OutputStatus.ERROR
            return OutputStatus.ERROR

    def stop(self) -> None:
        """Stop current playback."""
        self._status = OutputStatus.STOPPED
        try:
            if self._mixer is not None:
                import pygame
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
        except Exception:
            pass

    def get_status(self) -> OutputStatus:
        return self._status