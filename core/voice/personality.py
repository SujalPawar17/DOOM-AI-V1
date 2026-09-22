"""Voice Personality & Speech Style Contracts for DOOM.

Immutable dataclasses defining DOOM's original voice identity and speaking style.
No TTS engine logic, no audio processing, no external dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SpeakingMode(Enum):
    """Future speaking modes for V9.3.2 ProsodyController consumption."""
    NORMAL = "normal"
    CONFIDENT = "confident"
    COMMAND = "command"
    ALERT = "alert"
    URGENT = "urgent"
    THINKING = "thinking"
    HUMOR = "humor"
    CRITICAL = "critical"


# Validation bounds (documented safe ranges)
_MIN_RATE = 0.25
_MAX_RATE = 3.0

_MIN_PITCH = -24.0  # semitones
_MAX_PITCH = 24.0

_MIN_VOLUME = 0.0
_MAX_VOLUME = 2.0

_MIN_EMPHASIS = 0.0
_MAX_EMPHASIS = 3.0

_MIN_KEYWORD_BOOST = 0.0
_MAX_KEYWORD_BOOST = 3.0

_MIN_PAUSE_MS = 0
_MAX_PAUSE_MS = 5000

# Tactical DSP post-processing bounds (V9.3.8 validated)
_MIN_EQ_BOOST_DB = -12.0
_MAX_EQ_BOOST_DB = 12.0

_MIN_COMPRESSION_RATIO = 1.0
_MAX_COMPRESSION_RATIO = 10.0

_MIN_COMPRESSION_THRESHOLD_DB = -60.0
_MAX_COMPRESSION_THRESHOLD_DB = 0.0

_MIN_SATURATION = 0.0
_MAX_SATURATION = 1.0

_MIN_SYNTHETIC = 0.0
_MAX_SYNTHETIC = 1.0

_MIN_SPECTRAL_TILT_DB = -12.0
_MAX_SPECTRAL_TILT_DB = 12.0

_MIN_HF_AIR_DB = -12.0
_MAX_HF_AIR_DB = 12.0


def _validate_rate(value: float) -> float:
    if not (_MIN_RATE <= value <= _MAX_RATE):
        raise ValueError(f"rate must be in [{_MIN_RATE}, {_MAX_RATE}], got {value}")
    return value


def _validate_pitch(value: float) -> float:
    if not (_MIN_PITCH <= value <= _MAX_PITCH):
        raise ValueError(f"pitch must be in [{_MIN_PITCH}, {_MAX_PITCH}] semitones, got {value}")
    return value


def _validate_volume(value: float) -> float:
    if not (_MIN_VOLUME <= value <= _MAX_VOLUME):
        raise ValueError(f"volume must be in [{_MIN_VOLUME}, {_MAX_VOLUME}], got {value}")
    return value


def _validate_emphasis(value: float) -> float:
    if not (_MIN_EMPHASIS <= value <= _MAX_EMPHASIS):
        raise ValueError(f"emphasis must be in [{_MIN_EMPHASIS}, {_MAX_EMPHASIS}], got {value}")
    return value


def _validate_keyword_boost(value: float) -> float:
    if not (_MIN_KEYWORD_BOOST <= value <= _MAX_KEYWORD_BOOST):
        raise ValueError(f"keyword_boost must be in [{_MIN_KEYWORD_BOOST}, {_MAX_KEYWORD_BOOST}], got {value}")
    return value


def _validate_pause_ms(value: int) -> int:
    if not (_MIN_PAUSE_MS <= value <= _MAX_PAUSE_MS):
        raise ValueError(f"pause must be in [{_MIN_PAUSE_MS}, {_MAX_PAUSE_MS}] ms, got {value}")
    return value


def _validate_eq_boost_db(value: float) -> float:
    if not (_MIN_EQ_BOOST_DB <= value <= _MAX_EQ_BOOST_DB):
        raise ValueError(f"eq_boost_db must be in [{_MIN_EQ_BOOST_DB}, {_MAX_EQ_BOOST_DB}] dB, got {value}")
    return value


def _validate_compression_ratio(value: float) -> float:
    if not (_MIN_COMPRESSION_RATIO <= value <= _MAX_COMPRESSION_RATIO):
        raise ValueError(f"compression_ratio must be in [{_MIN_COMPRESSION_RATIO}, {_MAX_COMPRESSION_RATIO}], got {value}")
    return value


def _validate_compression_threshold_db(value: float) -> float:
    if not (_MIN_COMPRESSION_THRESHOLD_DB <= value <= _MAX_COMPRESSION_THRESHOLD_DB):
        raise ValueError(f"compression_threshold_db must be in [{_MIN_COMPRESSION_THRESHOLD_DB}, {_MAX_COMPRESSION_THRESHOLD_DB}] dB, got {value}")
    return value


def _validate_saturation(value: float) -> float:
    if not (_MIN_SATURATION <= value <= _MAX_SATURATION):
        raise ValueError(f"saturation must be in [{_MIN_SATURATION}, {_MAX_SATURATION}], got {value}")
    return value


def _validate_synthetic(value: float) -> float:
    if not (_MIN_SYNTHETIC <= value <= _MAX_SYNTHETIC):
        raise ValueError(f"synthetic must be in [{_MIN_SYNTHETIC}, {_MAX_SYNTHETIC}], got {value}")
    return value


def _validate_spectral_tilt_db(value: float) -> float:
    if not (_MIN_SPECTRAL_TILT_DB <= value <= _MAX_SPECTRAL_TILT_DB):
        raise ValueError(f"spectral_tilt_db must be in [{_MIN_SPECTRAL_TILT_DB}, {_MAX_SPECTRAL_TILT_DB}] dB, got {value}")
    return value


def _validate_hf_air_db(value: float) -> float:
    if not (_MIN_HF_AIR_DB <= value <= _MAX_HF_AIR_DB):
        raise ValueError(f"hf_air_db must be in [{_MIN_HF_AIR_DB}, {_MAX_HF_AIR_DB}] dB, got {value}")
    return value


# Public clamp functions for use by other modules (e.g., delivery layer)
def clamp_rate(value: float) -> float:
    """Clamp rate to valid range [0.25, 3.0]."""
    return max(_MIN_RATE, min(_MAX_RATE, value))


def clamp_pitch(value: float) -> float:
    """Clamp pitch to valid range [-24.0, 24.0] semitones."""
    return max(_MIN_PITCH, min(_MAX_PITCH, value))


def clamp_volume(value: float) -> float:
    """Clamp volume to valid range [0.0, 2.0]."""
    return max(_MIN_VOLUME, min(_MAX_VOLUME, value))


def clamp_emphasis(value: float) -> float:
    """Clamp emphasis to valid range [0.0, 3.0]."""
    return max(_MIN_EMPHASIS, min(_MAX_EMPHASIS, value))


def clamp_keyword_boost(value: float) -> float:
    """Clamp keyword_boost to valid range [0.0, 3.0]."""
    return max(_MIN_KEYWORD_BOOST, min(_MAX_KEYWORD_BOOST, value))


def clamp_pause_seconds(value: float) -> float:
    """Clamp pause to valid range [0.0, 5.0] seconds."""
    return max(0.0, min(5.0, value))


@dataclass(frozen=True, slots=True)
class VoicePersonality:
    """
    DOOM's stable voice identity — immutable configuration.
    
    Represents the core vocal character: deep, mature, authoritative, calm,
    deliberate, slightly intimidating, sophisticated, theatrical, precise.
    """
    
    # Identity
    name: str = "DOOM"
    gender: str = "male"
    timbre_profile: str = "deep_resonant"
    
    # Base voice characteristics
    base_rate: float = 0.85      # slower than neutral (1.0)
    base_pitch: float = -2.0     # slightly lower (semitones)
    base_volume: float = 1.0
    
    # Pause architecture
    sentence_pause_ms: int = 350
    clause_pause_ms: int = 180
    dramatic_pause_ms: int = 800
    
    # Emphasis
    emphasis_strength: float = 1.25
    keyword_boost: float = 1.15
    
    # Post-processing intent metadata ONLY (no DSP implementation)
    # These are configuration/intention for future V9.3.5+ post-processing
    eq_low_boost_db: float = 2.0
    saturation_amount: float = 0.08
    ring_mod_depth: float = 0.02
    
    # Tactical DSP fields (V9.3.8 production profile)
    low_mid_boost_db: float = 2.0
    compression_ratio: float = 1.0
    compression_threshold_db: float = -20.0
    spectral_tilt_db: float = 0.0
    hf_air_db: float = 0.0
    synthetic_pct: float = 0.0
    
    def __post_init__(self) -> None:
        # Validate all numeric fields
        object.__setattr__(self, "base_rate", _validate_rate(self.base_rate))
        object.__setattr__(self, "base_pitch", _validate_pitch(self.base_pitch))
        object.__setattr__(self, "base_volume", _validate_volume(self.base_volume))
        object.__setattr__(self, "sentence_pause_ms", _validate_pause_ms(self.sentence_pause_ms))
        object.__setattr__(self, "clause_pause_ms", _validate_pause_ms(self.clause_pause_ms))
        object.__setattr__(self, "dramatic_pause_ms", _validate_pause_ms(self.dramatic_pause_ms))
        object.__setattr__(self, "emphasis_strength", _validate_emphasis(self.emphasis_strength))
        object.__setattr__(self, "keyword_boost", _validate_keyword_boost(self.keyword_boost))
        # Post-processing fields - validate reasonable bounds
        if not (-12.0 <= self.eq_low_boost_db <= 12.0):
            raise ValueError(f"eq_low_boost_db must be in [-12, 12] dB, got {self.eq_low_boost_db}")
        if not (0.0 <= self.saturation_amount <= 1.0):
            raise ValueError(f"saturation_amount must be in [0, 1], got {self.saturation_amount}")
        if not (0.0 <= self.ring_mod_depth <= 0.5):
            raise ValueError(f"ring_mod_depth must be in [0, 0.5], got {self.ring_mod_depth}")
        # Tactical DSP fields
        object.__setattr__(self, "low_mid_boost_db", _validate_eq_boost_db(self.low_mid_boost_db))
        object.__setattr__(self, "compression_ratio", _validate_compression_ratio(self.compression_ratio))
        object.__setattr__(self, "compression_threshold_db", _validate_compression_threshold_db(self.compression_threshold_db))
        object.__setattr__(self, "spectral_tilt_db", _validate_spectral_tilt_db(self.spectral_tilt_db))
        object.__setattr__(self, "hf_air_db", _validate_hf_air_db(self.hf_air_db))
        object.__setattr__(self, "synthetic_pct", _validate_synthetic(self.synthetic_pct))
        if self.gender not in ("male", "female", "neutral"):
            raise ValueError(f"gender must be 'male', 'female', or 'neutral', got {self.gender}")


@dataclass(frozen=True, slots=True)
class SpeechStyle:
    """
    Per-response speaking style — immutable style parameters.
    
    Consumed by V9.3.2 ProsodyController to apply contextual variations
    on top of the base VoicePersonality.
    """
    
    # Prosody overrides (relative to VoicePersonality base)
    rate: float = 1.0
    pitch: float = 0.0
    volume: float = 1.0
    
    # Pause overrides
    pause_before: float = 0.0      # seconds
    pause_after: float = 0.0
    sentence_pause: float = 0.35   # seconds
    clause_pause: float = 0.18
    
    # Emphasis overrides
    emphasis: float = 1.0
    keyword_boost: float = 1.0
    
    # Speaking mode
    mode: SpeakingMode = SpeakingMode.NORMAL
    
    def __post_init__(self) -> None:
        object.__setattr__(self, "rate", _validate_rate(self.rate))
        object.__setattr__(self, "pitch", _validate_pitch(self.pitch))
        object.__setattr__(self, "volume", _validate_volume(self.volume))
        object.__setattr__(self, "emphasis", _validate_emphasis(self.emphasis))
        object.__setattr__(self, "keyword_boost", _validate_keyword_boost(self.keyword_boost))
        
        # Pause validation (in seconds, convert to ms for bounds check)
        if self.pause_before < 0 or self.pause_before > 5.0:
            raise ValueError(f"pause_before must be in [0, 5.0] seconds, got {self.pause_before}")
        if self.pause_after < 0 or self.pause_after > 5.0:
            raise ValueError(f"pause_after must be in [0, 5.0] seconds, got {self.pause_after}")
        if self.sentence_pause < 0 or self.sentence_pause > 5.0:
            raise ValueError(f"sentence_pause must be in [0, 5.0] seconds, got {self.sentence_pause}")
        if self.clause_pause < 0 or self.clause_pause > 5.0:
            raise ValueError(f"clause_pause must be in [0, 5.0] seconds, got {self.clause_pause}")


# Default DOOM personality instance
DEFAULT_VOICE_PERSONALITY = VoicePersonality()

# Tactical Voice personality (V9.3.8 production profile)
# pitch: -3.0 st, low-mid: +2.2 dB, compression: 2.5:1 @ -20 dB, saturation: 5%, synthetic: 0%, hf_air: 0 dB, spectral_tilt: -1.0 dB
TACTICAL_VOICE_PERSONALITY = VoicePersonality(
    name="DOOM Tactical",
    base_pitch=-3.0,
    low_mid_boost_db=2.2,
    compression_ratio=2.5,
    compression_threshold_db=-20.0,
    saturation_amount=0.05,
    spectral_tilt_db=-1.0,
    hf_air_db=0.0,
    synthetic_pct=0.0,
    ring_mod_depth=0.0,
)

# Default neutral speech style
DEFAULT_SPEECH_STYLE = SpeechStyle()