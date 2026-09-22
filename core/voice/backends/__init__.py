"""Voice backends package."""

from core.voice.backends.base import TTSBackend, BackendStatus
from core.voice.backends.pyttsx3_backend import Pyttsx3Backend
from core.voice.backends.edge_tts_backend import EdgeTTSBackend
from core.voice.backends.kokoro_backend import KokoroBackend, create_kokoro_backend
from core.voice.backends.piper_backend import PiperBackend, create_piper_backend

__all__ = [
    "TTSBackend",
    "BackendStatus",
    "Pyttsx3Backend",
    "EdgeTTSBackend",
    "KokoroBackend",
    "create_kokoro_backend",
    "PiperBackend",
    "create_piper_backend",
]