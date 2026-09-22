"""Kokoro ONNX TTS backend -- local neural TTS."""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Optional, Iterator
import numpy as np

from core.voice.backends.base import TTSBackend, BackendStatus
from core.voice.outputs.base import AudioOutput, OutputStatus
from core.cost_guard import ResourceRequest, ResourceType, cost_guard
from core.voice.personality import VoicePersonality, TACTICAL_VOICE_PERSONALITY
from core.voice.dsp import process_tactical_chain, float32_to_int16, SAMPLE_RATE


KOKORO_AVAILABLE = False
try:
    import kokoro_onnx
    KOKORO_AVAILABLE = True
except ImportError:
    kokoro_onnx = None  # type: ignore


# Module-level singleton for Kokoro model to ensure single initialization
_kokoro_singleton: Optional["kokoro_onnx.Kokoro"] = None
_kokoro_singleton_lock = threading.Lock()
_kokoro_singleton_init_error: Optional[str] = None
_kokoro_singleton_model_path: Optional[str] = None
_kokoro_singleton_voices_path: Optional[str] = None


def _get_kokoro_singleton(
    model_path: str,
    voices_path: str,
    espeak_lib_path: Optional[str] = None,
    espeak_data_path: Optional[str] = None,
) -> Optional["kokoro_onnx.Kokoro"]:
    """Get or create the Kokoro singleton instance.

    Ensures model is loaded only once per process with given paths.
    """
    global _kokoro_singleton, _kokoro_singleton_init_error, _kokoro_singleton_model_path, _kokoro_singleton_voices_path

    if not KOKORO_AVAILABLE:
        return None

    # Check if paths match existing singleton
    with _kokoro_singleton_lock:
        if (_kokoro_singleton is not None
            and _kokoro_singleton_model_path == model_path
            and _kokoro_singleton_voices_path == voices_path):
            return _kokoro_singleton

        # Different paths or not initialized - (re)initialize
        _kokoro_singleton = None
        _kokoro_singleton_model_path = model_path
        _kokoro_singleton_voices_path = voices_path

        if not os.path.exists(model_path):
            _kokoro_singleton_init_error = f"Model file not found: {model_path}"
            return None
        if not os.path.exists(voices_path):
            _kokoro_singleton_init_error = f"Voices file not found: {voices_path}"
            return None

        try:
            espeak_config = None
            if espeak_lib_path or espeak_data_path:
                espeak_config = kokoro_onnx.EspeakConfig(
                    lib_path=espeak_lib_path,
                    data_path=espeak_data_path,
                )

            _kokoro_singleton = kokoro_onnx.Kokoro(
                model_path=model_path,
                voices_path=voices_path,
                espeak_config=espeak_config,
            )
            _kokoro_singleton_init_error = None
            return _kokoro_singleton
        except Exception as e:
            _kokoro_singleton_init_error = f"Kokoro initialization failed: {e}"
            _kokoro_singleton = None
            return None


class KokoroBackend:
    """
    Kokoro ONNX TTS backend -- local neural TTS.

    Uses kokoro-onnx with ONNX Runtime for local synthesis.
    Supports streaming via async generator.
    Supports Tactical DSP post-processing when configured with Tactical voice profile.
    """

    name = "kokoro"

    def __init__(
        self,
        model_path: Optional[str] = None,
        voices_path: Optional[str] = None,
        voice: str = "bm_george",
        speed: float = 1.0,
        lang_code: str = "en-us",
        espeak_lib_path: Optional[str] = None,
        espeak_data_path: Optional[str] = None,
        audio_output: Optional[AudioOutput] = None,
        voice_profile: Optional[VoicePersonality] = None,
    ):
        self.model_path = model_path
        self.voices_path = voices_path
        self.voice = voice
        self.speed = speed
        self.lang_code = lang_code
        self.espeak_lib_path = espeak_lib_path
        self.espeak_data_path = espeak_data_path
        self.audio_output = audio_output
        self.voice_profile = voice_profile

        self._kokoro: Optional["kokoro_onnx.Kokoro"] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._init_error: Optional[str] = None
        self._initialization_attempted = False

        # Determine if Tactical DSP should be applied
        self._use_tactical_dsp = (
            voice_profile is not None
            and voice_profile.name == "DOOM Tactical"
        )

    def _initialize(self) -> bool:
        """Initialize Kokoro model using singleton. Returns True on success."""
        if not KOKORO_AVAILABLE:
            self._init_error = "kokoro-onnx package not installed"
            return False

        with self._lock:
            if self._initialization_attempted:
                return self._kokoro is not None

            self._initialization_attempted = True

            if not self.model_path or not os.path.exists(self.model_path):
                self._init_error = f"Model file not found: {self.model_path}"
                return False

            if not self.voices_path or not os.path.exists(self.voices_path):
                self._init_error = f"Voices file not found: {self.voices_path}"
                return False

            # Use module-level singleton to ensure single model load
            self._kokoro = _get_kokoro_singleton(
                self.model_path,
                self.voices_path,
                self.espeak_lib_path,
                self.espeak_data_path,
            )

            if self._kokoro is None:
                self._init_error = _kokoro_singleton_init_error or "Kokoro initialization failed"
                return False

            self._init_error = None
            return True

    def is_available(self) -> bool:
        """Return True if Kokoro is installed and model files exist."""
        if not KOKORO_AVAILABLE:
            return False
        if self._kokoro is not None:
            return True
        return self._initialize()

    def get_init_error(self) -> Optional[str]:
        """Return initialization error message if any."""
        if not self.is_available():
            if self._init_error:
                return self._init_error
            if not KOKORO_AVAILABLE:
                return "kokoro-onnx not installed"
            if not self.model_path or not os.path.exists(self.model_path):
                return f"Model file not found: {self.model_path}"
            if not self.voices_path or not os.path.exists(self.voices_path):
                return f"Voices file not found: {self.voices_path}"
        return None

    def is_allowed(self, lang: Optional[str] = None) -> bool:
        """Check Cost Guard authorization for kokoro."""
        decision = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.TTS,
            provider="kokoro",
            capability="tts",
        ))
        return decision.is_allow

    def get_voice_for_language(self, lang: str) -> Optional[str]:
        """Return configured voice for language."""
        if not self.is_available():
            return None
        try:
            voices = self._kokoro.get_voices()
            lang_prefix = lang.split('-')[0] if '-' in lang else lang
            for v in voices:
                if v.startswith(lang_prefix):
                    return v
            return self.voice
        except Exception:
            return self.voice

    def _float32_to_bytes(self, audio: np.ndarray) -> bytes:
        """Convert float32 numpy array to bytes for SoundDeviceOutput."""
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        return audio.tobytes()

    def _apply_tactical_dsp(self, audio: np.ndarray) -> np.ndarray:
        """Apply Tactical DSP chain to audio if Tactical profile is active."""
        if not self._use_tactical_dsp or self.voice_profile is None:
            return audio

        vp = self.voice_profile
        return process_tactical_chain(
            audio,
            sample_rate=SAMPLE_RATE,
            pitch_semitones=vp.base_pitch,
            low_mid_boost_db=vp.low_mid_boost_db,
            hf_air_db=vp.hf_air_db,
            spectral_tilt_db=vp.spectral_tilt_db,
            compression_ratio=vp.compression_ratio,
            compression_threshold_db=vp.compression_threshold_db,
            saturation_amount=vp.saturation_amount,
            synthetic_pct=vp.synthetic_pct,
        )

    def _synthesize_blocking(self, text: str, lang: Optional[str] = None) -> np.ndarray:
        """Generate complete audio using blocking API."""
        if not self._kokoro:
            return np.array([], dtype=np.float32)

        effective_lang = lang or self.lang_code
        voice = self.get_voice_for_language(effective_lang) or self.voice

        audio, sr = self._kokoro.create(
            text=text,
            voice=voice,
            speed=self.speed,
            lang=effective_lang,
            is_phonemes=False,
            trim=True,
            sentence_pause=0.25,
            clause_pause=0.1,
            continuous=False,
        )

        # Apply Tactical DSP if configured
        if self._use_tactical_dsp:
            audio = self._apply_tactical_dsp(audio)

        return audio

    def _synthesize_streaming(self, text: str, lang: Optional[str] = None):
        """Generate audio chunks using Kokoro's streaming API.

        Note: Tactical DSP requires complete utterance for pitch shifting.
        For streaming, we synthesize the full audio first, apply DSP, then chunk it.
        """
        if not self._kokoro:
            return
            yield  # Make this a generator

        self._stop_event.clear()  # Clear stop event for new synthesis

        effective_lang = lang or self.lang_code
        voice = self.get_voice_for_language(effective_lang) or self.voice

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _generate_chunks():
                stream = self._kokoro.create_stream(
                    text=text,
                    voice=voice,
                    speed=self.speed,
                    lang=effective_lang,
                    is_phonemes=False,
                    trim=True,
                    sentence_pause=0.25,
                    clause_pause=0.1,
                )
                async for chunk_audio, chunk_sr in stream:
                    if self._stop_event.is_set():
                        break
                    if chunk_audio is not None and len(chunk_audio) > 0:
                        yield chunk_audio

            # Collect all chunks for DSP processing if Tactical is enabled
            if self._use_tactical_dsp:
                chunks = []
                gen = _generate_chunks()
                while True:
                    try:
                        chunk = loop.run_until_complete(gen.__anext__())
                        chunks.append(chunk)
                    except StopAsyncIteration:
                        break
                    except Exception as e:
                        print(f"[VOICE] Kokoro streaming error: {e}")
                        break

                if chunks:
                    # Concatenate and apply DSP
                    full_audio = np.concatenate(chunks)
                    processed = self._apply_tactical_dsp(full_audio)
                    # Re-chunk for streaming output (yield in reasonable chunks)
                    chunk_size = 1024
                    for i in range(0, len(processed), chunk_size):
                        yield self._float32_to_bytes(processed[i:i + chunk_size])
            else:
                # Original streaming behavior without DSP
                gen = _generate_chunks()
                while True:
                    try:
                        chunk = loop.run_until_complete(gen.__anext__())
                        yield self._float32_to_bytes(chunk)
                    except StopAsyncIteration:
                        break
                    except Exception as e:
                        print(f"[VOICE] Kokoro streaming error: {e}")
                        break
        finally:
            loop.close()

    def synthesize(self, text: str, lang: Optional[str] = None) -> BackendStatus:
        """Synthesize and play text using Kokoro."""
        if not text:
            return BackendStatus.UNAVAILABLE

        if not self.is_available():
            print(f"[VOICE] Kokoro unavailable: {self.get_init_error()}")
            return BackendStatus.FAILED

        if not self.is_allowed(lang):
            return BackendStatus.BLOCKED

        self._stop_event.clear()

        try:
            if self.audio_output and hasattr(self.audio_output, 'play_stream'):
                chunks = self._synthesize_streaming(text, lang)
                status = self.audio_output.play_stream(
                    chunks,
                    sample_rate=SAMPLE_RATE,
                    channels=1,
                )
                if status == OutputStatus.ERROR:
                    return BackendStatus.FAILED
                return BackendStatus.AVAILABLE
            else:
                audio = self._synthesize_blocking(text, lang)
                if len(audio) == 0:
                    return BackendStatus.FAILED
                if self.audio_output:
                    audio_bytes = self._float32_to_bytes(audio)
                    chunks = iter([audio_bytes])
                    status = self.audio_output.play_stream(
                        chunks,
                        sample_rate=SAMPLE_RATE,
                        channels=1,
                    )
                    if status == OutputStatus.ERROR:
                        return BackendStatus.FAILED
                    return BackendStatus.AVAILABLE
                return BackendStatus.FAILED
        except Exception as e:
            print(f"[VOICE] Kokoro synthesis failed: {e}")
            return BackendStatus.FAILED
        finally:
            self._stop_event.set()

    def stop(self) -> None:
        """Stop current synthesis/playback."""
        self._stop_event.set()
        if self.audio_output:
            try:
                self.audio_output.stop()
            except Exception:
                pass

    def reset(self) -> None:
        """Reset backend state for reinitialization (testing)."""
        with self._lock:
            self._kokoro = None
            self._init_error = None
            self._initialization_attempted = False
            self._stop_event.clear()


def create_kokoro_backend(
    model_path: Optional[str] = None,
    voices_path: Optional[str] = None,
    voice: Optional[str] = None,
    speed: Optional[float] = None,
    lang_code: Optional[str] = None,
    audio_output: Optional[AudioOutput] = None,
    voice_profile: Optional[VoicePersonality] = None,
) -> KokoroBackend:
    """Factory function to create KokoroBackend with optional model paths from env."""
    model_path = model_path or os.getenv("DOOM_KOKORO_MODEL_PATH")
    voices_path = voices_path or os.getenv("DOOM_KOKORO_VOICES_PATH")
    voice = voice or os.getenv("DOOM_KOKORO_VOICE", "bm_george")
    speed = float(speed) if speed is not None else float(os.getenv("DOOM_KOKORO_SPEED", "1.0"))
    lang_code = lang_code or os.getenv("DOOM_KOKORO_LANG_CODE", "en-us")

    return KokoroBackend(
        model_path=model_path,
        voices_path=voices_path,
        voice=voice,
        speed=speed,
        lang_code=lang_code,
        audio_output=audio_output,
        voice_profile=voice_profile,
    )