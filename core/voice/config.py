"""VoiceEngine configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class VoiceEngineConfig:
    """Configuration for VoiceEngine backend selection and behavior."""

    # Backend selection
    preferred_backend: str = "auto"  # auto, pyttsx3, edge_tts, kokoro, piper
    fallback_backends: List[str] = field(default_factory=lambda: ["pyttsx3"])

    # Voice profile selection
    voice_profile: str = "default"  # default, tactical

    # Audio output parameters (preserve existing defaults)
    output_frequency: int = 24000
    output_size: int = -16
    output_channels: int = 2
    output_buffer: int = 2048

    # Behavior
    interrupt_on_speak: bool = True
    headless_mode: bool = False

    # Neural TTS specific configuration
    kokoro_model_path: Optional[str] = None
    kokoro_voices_path: Optional[str] = None
    kokoro_voice: Optional[str] = None
    kokoro_lang_code: Optional[str] = None
    kokoro_speed: Optional[float] = None

    piper_executable: Optional[str] = None
    piper_model_path: Optional[str] = None
    piper_config_path: Optional[str] = None
    piper_voice: Optional[str] = None
    piper_length_scale: Optional[float] = None

    @classmethod
    def from_env(cls) -> "VoiceEngineConfig":
        """Create config from environment variables."""
        preferred = os.getenv("DOOM_TTS_BACKEND", "auto").strip().lower()
        headless = os.getenv("DOOM_HEADLESS") == "1"
        
        # Voice profile selection (default: default for backward compatibility; set DOOM_VOICE_PROFILE=tactical for production)
        voice_profile = os.getenv("DOOM_VOICE_PROFILE", "default").strip().lower()

        # Parse fallback backends from env if provided
        fallback_str = os.getenv("DOOM_TTS_FALLBACK_BACKENDS", "").strip()
        if fallback_str:
            fallback_backends = [b.strip() for b in fallback_str.split(",") if b.strip()]
        else:
            fallback_backends = ["pyttsx3"]

        return cls(
            preferred_backend=preferred,
            fallback_backends=fallback_backends,
            voice_profile=voice_profile,
            headless_mode=headless,
            kokoro_model_path=os.getenv("DOOM_KOKORO_MODEL_PATH"),
            kokoro_voices_path=os.getenv("DOOM_KOKORO_VOICES_PATH"),
            kokoro_voice=os.getenv("DOOM_KOKORO_VOICE"),
            kokoro_lang_code=os.getenv("DOOM_KOKORO_LANG_CODE"),
            kokoro_speed=os.getenv("DOOM_KOKORO_SPEED") and float(os.getenv("DOOM_KOKORO_SPEED", "1.0")),
            piper_executable=os.getenv("DOOM_PIPER_EXECUTABLE"),
            piper_model_path=os.getenv("DOOM_PIPER_MODEL_PATH"),
            piper_config_path=os.getenv("DOOM_PIPER_CONFIG_PATH"),
            piper_voice=os.getenv("DOOM_PIPER_VOICE"),
            piper_length_scale=os.getenv("DOOM_PIPER_LENGTH_SCALE") and float(os.getenv("DOOM_PIPER_LENGTH_SCALE", "1.0")),
        )

    def get_backend_priority(self) -> List[str]:
        """Get ordered list of backends to try."""
        if self.preferred_backend != "auto":
            # Explicit preference: try it first, then fallbacks
            priority = [self.preferred_backend]
            for fb in self.fallback_backends:
                if fb not in priority:
                    priority.append(fb)
            return priority
        # Auto mode: prefer neural TTS if available (kokoro -> piper -> pyttsx3), then edge_tts
        priority = ["kokoro", "piper", "pyttsx3"]
        if "edge_tts" not in priority:
            priority.append("edge_tts")
        # Add any additional fallbacks
        for fb in self.fallback_backends:
            if fb not in priority:
                priority.append(fb)
        return priority