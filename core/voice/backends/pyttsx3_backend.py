"""pyttsx3 TTS backend implementation — offline, local, always allowed."""

from __future__ import annotations

import threading
from typing import Optional
from core.voice.backends.base import TTSBackend, BackendStatus
from core.cost_guard import ResourceRequest, ResourceType, cost_guard

PYTTSX3_AVAILABLE = False
try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    pyttsx3 = None  # type: ignore


class Pyttsx3Backend:
    """pyttsx3 backend — offline TTS, always available when installed."""

    name = "pyttsx3"

    def __init__(self):
        self.engine = None
        self._lock = threading.Lock()
        self._init_engine()

    def _init_engine(self) -> None:
        """Initialize pyttsx3 engine with JARVIS-like voice settings."""
        if not PYTTSX3_AVAILABLE:
            return
        with self._lock:
            if self.engine is not None:
                return
            try:
                import pyttsx3
                self.engine = pyttsx3.init()
                self._setup_voice()
            except Exception as e:
                print(f"[VOICE] pyttsx3 init failed: {e}")
                self.engine = None

    def _setup_voice(self) -> None:
        """Configure JARVIS-like voice settings (rate, volume, male voice preference)."""
        if not self.engine:
            return
        try:
            self.engine.setProperty('rate', 180)
            self.engine.setProperty('volume', 0.9)
            voices = self.engine.getProperty('voices')
            if voices:
                male_voice_found = False
                for voice in voices:
                    voice_name_lower = voice.name.lower()
                    if any(male_name in voice_name_lower for male_name in ['david', 'mark', 'george', 'ryan', 'male', 'zira']):
                        self.engine.setProperty('voice', voice.id)
                        male_voice_found = True
                        break
                if not male_voice_found:
                    for voice in voices:
                        if 'desktop' in voice.name.lower() and 'english' in voice.name.lower():
                            self.engine.setProperty('voice', voice.id)
                            break
        except Exception:
            pass

    def is_available(self) -> bool:
        return PYTTSX3_AVAILABLE and self.engine is not None

    def is_allowed(self, lang: Optional[str] = None) -> bool:
        """Check Cost Guard authorization for pyttsx3."""
        decision = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.TTS,
            provider="pyttsx3",
            capability="tts",
        ))
        return decision.is_allow

    def synthesize(self, text: str, lang: Optional[str] = None) -> BackendStatus:
        """Synthesize and play text using pyttsx3."""
        if not text:
            return BackendStatus.UNAVAILABLE

        if not self.is_allowed(lang):
            return BackendStatus.BLOCKED

        if not self.engine:
            self._init_engine()
        if not self.engine:
            return BackendStatus.FAILED

        try:
            with self._lock:
                self.engine.say(text)
                self.engine.runAndWait()
            return BackendStatus.AVAILABLE
        except Exception as e:
            print(f"[VOICE] pyttsx3 synthesis failed: {e}")
            return BackendStatus.FAILED

    def stop(self) -> None:
        """Stop current speech."""
        if self.engine:
            try:
                self.engine.stop()
            except Exception:
                pass

    def get_voice_for_language(self, lang: str) -> Optional[str]:
        """pyttsx3 has limited multilingual support."""
        return None