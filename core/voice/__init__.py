"""DOOM Voice Engine — modular TTS architecture."""

from core.voice.engine import VoiceEngine, AudioStatus
from core.voice.config import VoiceEngineConfig
from core.voice.backends import TTSBackend, BackendStatus, Pyttsx3Backend, EdgeTTSBackend
from core.voice.outputs import AudioOutput, OutputStatus, PygameOutput

__all__ = [
    "VoiceEngine",
    "VoiceEngineConfig",
    "TTSBackend",
    "BackendStatus",
    "Pyttsx3Backend",
    "EdgeTTSBackend",
    "AudioOutput",
    "OutputStatus",
    "PygameOutput",
    "AudioStatus",
]