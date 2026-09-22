"""Prosody Controller for DOOM Voice.

Deterministic controller that converts DOOM's speaking mode and personality
into a concrete SpeechStyle. No TTS engine logic, no audio processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.voice.personality import (
    VoicePersonality,
    SpeechStyle,
    SpeakingMode,
    DEFAULT_VOICE_PERSONALITY,
)
from core.voice.delivery import DeliveryProfile, apply_delivery_profile


# Mode-specific adjustment factors (multiplicative for rate, additive for pitch/pauses)
@dataclass(frozen=True, slots=True)
class _ModeAdjustments:
    """Deterministic adjustments for each speaking mode."""
    rate_mult: float = 1.0
    pitch_shift: float = 0.0       # semitones
    volume_mult: float = 1.0
    pause_before_add: float = 0.0  # seconds
    pause_after_add: float = 0.0
    sentence_pause_mult: float = 1.0
    clause_pause_mult: float = 1.0
    emphasis_mult: float = 1.0
    keyword_boost_mult: float = 1.0


# Deterministic mode adjustments — these define the qualitative behavior
# Values are chosen to stay within V9.3.1 validation bounds
_MODE_ADJUSTMENTS: dict[SpeakingMode, _ModeAdjustments] = {
    SpeakingMode.NORMAL: _ModeAdjustments(
        rate_mult=1.0,
        pitch_shift=0.0,
        volume_mult=1.0,
        pause_before_add=0.0,
        pause_after_add=0.0,
        sentence_pause_mult=1.0,
        clause_pause_mult=1.0,
        emphasis_mult=1.0,
        keyword_boost_mult=1.0,
    ),
    SpeakingMode.CONFIDENT: _ModeAdjustments(
        rate_mult=0.92,          # slightly slower
        pitch_shift=-0.5,        # slightly lower
        volume_mult=1.0,
        pause_before_add=0.05,
        pause_after_add=0.05,
        sentence_pause_mult=1.15,
        clause_pause_mult=1.1,
        emphasis_mult=1.15,
        keyword_boost_mult=1.1,
    ),
    SpeakingMode.COMMAND: _ModeAdjustments(
        rate_mult=0.95,
        pitch_shift=-0.3,
        volume_mult=1.05,
        pause_before_add=0.0,
        pause_after_add=0.0,
        sentence_pause_mult=0.85,
        clause_pause_mult=0.9,
        emphasis_mult=1.25,
        keyword_boost_mult=1.2,
    ),
    SpeakingMode.ALERT: _ModeAdjustments(
        rate_mult=1.15,          # faster
        pitch_shift=0.5,
        volume_mult=1.0,
        pause_before_add=0.0,
        pause_after_add=0.0,
        sentence_pause_mult=0.75,
        clause_pause_mult=0.8,
        emphasis_mult=1.1,
        keyword_boost_mult=1.05,
    ),
    SpeakingMode.URGENT: _ModeAdjustments(
        rate_mult=1.25,          # faster than ALERT
        pitch_shift=0.8,
        volume_mult=1.05,
        pause_before_add=0.0,
        pause_after_add=0.0,
        sentence_pause_mult=0.6,
        clause_pause_mult=0.65,
        emphasis_mult=1.15,
        keyword_boost_mult=1.1,
    ),
    SpeakingMode.THINKING: _ModeAdjustments(
        rate_mult=0.85,          # slower
        pitch_shift=-0.3,
        volume_mult=0.95,
        pause_before_add=0.1,
        pause_after_add=0.1,
        sentence_pause_mult=1.3,
        clause_pause_mult=1.25,
        emphasis_mult=0.85,
        keyword_boost_mult=0.9,
    ),
    SpeakingMode.HUMOR: _ModeAdjustments(
        rate_mult=1.05,
        pitch_shift=0.3,
        volume_mult=1.0,
        pause_before_add=0.02,
        pause_after_add=0.05,
        sentence_pause_mult=0.95,
        clause_pause_mult=0.95,
        emphasis_mult=1.05,
        keyword_boost_mult=1.05,
    ),
    SpeakingMode.CRITICAL: _ModeAdjustments(
        rate_mult=0.88,          # deliberate
        pitch_shift=-0.8,
        volume_mult=1.1,
        pause_before_add=0.1,
        pause_after_add=0.1,
        sentence_pause_mult=1.2,
        clause_pause_mult=1.15,
        emphasis_mult=1.3,
        keyword_boost_mult=1.25,
    ),
}


def _clamp_rate(value: float) -> float:
    return max(0.25, min(3.0, value))


def _clamp_pitch(value: float) -> float:
    return max(-24.0, min(24.0, value))


def _clamp_volume(value: float) -> float:
    return max(0.0, min(2.0, value))


def _clamp_emphasis(value: float) -> float:
    return max(0.0, min(3.0, value))


def _clamp_keyword_boost(value: float) -> float:
    return max(0.0, min(3.0, value))


def _clamp_pause_seconds(value: float) -> float:
    return max(0.0, min(5.0, value))


class ProsodyController:
    """
    Deterministic prosody controller for DOOM voice.
    
    Converts a VoicePersonality + SpeakingMode into a SpeechStyle
    through pure parameter transformation. No side effects, no mutation.
    """
    
    def __init__(self, personality: Optional[VoicePersonality] = None):
        self._personality = personality or DEFAULT_VOICE_PERSONALITY
    
    @property
    def personality(self) -> VoicePersonality:
        return self._personality
    
    def for_mode(self, mode: SpeakingMode = SpeakingMode.NORMAL) -> SpeechStyle:
        """
        Generate a SpeechStyle for the given speaking mode.
        
        Returns a new SpeechStyle instance — never mutates inputs.
        """
        adj = _MODE_ADJUSTMENTS.get(mode, _MODE_ADJUSTMENTS[SpeakingMode.NORMAL])
        
        # Base rate from personality, then apply mode multiplier
        base_rate = self._personality.base_rate
        rate = _clamp_rate(base_rate * adj.rate_mult)
        
        # Base pitch from personality, then apply mode shift
        base_pitch = self._personality.base_pitch
        pitch = _clamp_pitch(base_pitch + adj.pitch_shift)
        
        # Base volume from personality, then apply mode multiplier
        base_volume = self._personality.base_volume
        volume = _clamp_volume(base_volume * adj.volume_mult)
        
        # Pauses: personality values in ms, convert to seconds, apply adjustments
        sentence_pause = _clamp_pause_seconds(
            (self._personality.sentence_pause_ms / 1000.0) * adj.sentence_pause_mult + adj.pause_before_add
        )
        clause_pause = _clamp_pause_seconds(
            (self._personality.clause_pause_ms / 1000.0) * adj.clause_pause_mult + adj.pause_after_add
        )
        
        # Emphasis and keyword boost
        emphasis = _clamp_emphasis(
            self._personality.emphasis_strength * adj.emphasis_mult
        )
        keyword_boost = _clamp_keyword_boost(
            self._personality.keyword_boost * adj.keyword_boost_mult
        )
        
        return SpeechStyle(
            rate=rate,
            pitch=pitch,
            volume=volume,
            pause_before=adj.pause_before_add,
            pause_after=adj.pause_after_add,
            sentence_pause=sentence_pause,
            clause_pause=clause_pause,
            emphasis=emphasis,
            keyword_boost=keyword_boost,
            mode=mode,
        )
    
    def for_modes(self, *modes: SpeakingMode) -> dict[SpeakingMode, SpeechStyle]:
        """Generate styles for multiple modes at once (convenience)."""
        return {mode: self.for_mode(mode) for mode in modes}
    
    def all_modes(self) -> dict[SpeakingMode, SpeechStyle]:
        """Generate styles for all speaking modes."""
        return self.for_modes(*SpeakingMode)

    def for_delivery(
        self,
        mode: SpeakingMode = SpeakingMode.NORMAL,
        delivery_profile: Optional[DeliveryProfile] = None,
        length_category: str = "medium",
    ) -> SpeechStyle:
        """
        Generate a SpeechStyle with delivery profile adjustments.
        
        Combines base mode style with contextual delivery profile.
        """
        base_style = self.for_mode(mode)
        
        if delivery_profile is None:
            return base_style
        
        return apply_delivery_profile(base_style, delivery_profile, length_category)