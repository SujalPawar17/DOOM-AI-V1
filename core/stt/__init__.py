"""Local speech-to-text. Cloud STT is not part of this package."""

from core.stt.local_whisper import (
    STT_UNAVAILABLE_LOCAL_MODEL_MISSING,
    LocalSTTProvider,
    TranscribeResult,
    local_stt,
)

__all__ = [
    "STT_UNAVAILABLE_LOCAL_MODEL_MISSING",
    "LocalSTTProvider",
    "TranscribeResult",
    "local_stt",
]
