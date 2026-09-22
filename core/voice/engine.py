"""VoiceEngine — facade for TTS backends and audio output."""

from __future__ import annotations

import threading
import queue
from typing import Optional, Dict, List
from enum import Enum
from dataclasses import dataclass, field
from core.voice.backends.base import TTSBackend, BackendStatus
from core.voice.outputs.base import AudioOutput
from core.voice.outputs.pygame_output import PygameOutput
from core.voice.outputs.sounddevice_output import SoundDeviceOutput
from core.voice.backends.pyttsx3_backend import Pyttsx3Backend
from core.voice.backends.edge_tts_backend import EdgeTTSBackend
from core.voice.backends.kokoro_backend import KokoroBackend, create_kokoro_backend
from core.voice.backends.piper_backend import PiperBackend, create_piper_backend
from core.voice.config import VoiceEngineConfig
from core.voice.personality import VoicePersonality, DEFAULT_VOICE_PERSONALITY, SpeechStyle, SpeakingMode, TACTICAL_VOICE_PERSONALITY
from core.voice.prosody import ProsodyController
from core.voice.delivery import (
    classify_response_category,
    get_delivery_profile,
    calculate_response_length_category,
    DeliveryProfile,
    ResponseCategory,
)
from core.language_manager import get_language_manager


class AudioStatus(str, Enum):
    """Audio status — matches cinematic_voice.AudioStatus for backward compatibility."""
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    """A queued speech request."""
    text: str
    context: str = ""
    lang: Optional[str] = None
    intent: Optional[str] = None
    is_thinking: bool = False
    is_error: bool = False
    is_warning: bool = False
    priority: int = 0  # Higher = more urgent
    # Generation token for cancellation
    generation: int = 0


class VoiceEngine:
    """
    VoiceEngine facade — manages TTS backends, audio output, and speech coordination.
    
    Responsibilities:
    - Backend registration and selection
    - Cost Guard compliant backend authorization
    - Speech synthesis with fallback
    - Interrupt/stop control
    - Status reporting
    
    Does NOT contain:
    - DOOM cognitive logic
    - Orchestration logic
    - Memory logic
    - STT logic
    - Computer control
    - Browser logic
    """

    def __init__(
        self,
        config: Optional[VoiceEngineConfig] = None,
        language_manager=None,
        audio_output: Optional[AudioOutput] = None,
        personality: Optional[VoicePersonality] = None,
    ):
        self.config = config or VoiceEngineConfig.from_env()
        self.lang_manager = language_manager or get_language_manager()
        self._backends: Dict[str, TTSBackend] = {}
        self._audio_output = audio_output or PygameOutput(
            frequency=self.config.output_frequency,
            size=self.config.output_size,
            channels=self.config.output_channels,
            buffer=self.config.output_buffer,
        )
        self._sounddevice_output: Optional[SoundDeviceOutput] = None
        self._speech_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._is_speaking = False
        self._current_backend: Optional[str] = None
        self._audio_status = AudioStatus.AVAILABLE
        
        # Speech queue for sequential playback
        self._speech_queue: "queue.Queue[SpeechRequest]" = queue.Queue(maxsize=50)
        self._queue_worker: Optional[threading.Thread] = None
        self._queue_running = threading.Event()
        self._current_request: Optional[SpeechRequest] = None
        self._generation = 0  # Monotonic generation counter for cancellation
        
        # Personality and prosody - select based on config voice_profile
        if personality is not None:
            self._personality = personality
        elif getattr(self.config, 'voice_profile', 'default') == 'tactical':
            self._personality = TACTICAL_VOICE_PERSONALITY
        else:
            self._personality = DEFAULT_VOICE_PERSONALITY
        self._prosody = ProsodyController(self._personality)

        self._register_default_backends()
        self._select_backend()
        self._start_queue_worker()

    def _start_queue_worker(self) -> None:
        """Start the background queue worker thread."""
        if self._queue_worker is not None and self._queue_worker.is_alive():
            return
        self._queue_running.set()
        self._queue_worker = threading.Thread(
            target=self._queue_worker_loop,
            daemon=True,
            name="DOOM-SpeechQueue"
        )
        self._queue_worker.start()

    def _queue_worker_loop(self) -> None:
        """Background loop that processes speech requests sequentially with crash recovery."""
        consecutive_errors = 0
        max_consecutive_errors = 5
        
        while self._queue_running.is_set():
            try:
                # Wait for next request with timeout to allow checking _queue_running
                request = self._speech_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            
            self._current_request = request
            try:
                # Synthesize with delivery
                self._synthesize_request(request)
                consecutive_errors = 0  # Reset error count on success
            except Exception as e:
                consecutive_errors += 1
                print(f"[VOICE] Queue worker error: {e}")
                if consecutive_errors >= max_consecutive_errors:
                    print(f"[VOICE] Too many consecutive errors, restarting worker")
                    # Attempt to recover by clearing state
                    consecutive_errors = 0
            finally:
                self._speech_queue.task_done()
                self._current_request = None

    def _synthesize_request(self, request: SpeechRequest) -> AudioStatus:
        """Synthesize a single speech request (internal, no locking)."""
        if self.config.headless_mode:
            return AudioStatus.UNAVAILABLE

        # Check generation token - if a newer request has been queued, cancel this one
        with self._speech_lock:
            if request.generation < self._generation:
                return AudioStatus.UNAVAILABLE  # Superseded by newer request

        # Check stop event
        if self._stop_event.is_set():
            return AudioStatus.UNAVAILABLE

        self._is_speaking = True
        self._stop_event.clear()

        try:
            # Compute delivery style
            style, category, length_category = self._compute_delivery_style(
                text=request.text,
                context=request.context,
                intent=request.intent,
                is_thinking=request.is_thinking,
                is_error=request.is_error,
                is_warning=request.is_warning,
            )
            
            print(f"[VOICE] Category: {category.value} | Length: {length_category} | Rate: {style.rate:.2f} | Pitch: {style.pitch:+.1f}")

            # Check generation again before synthesis
            with self._speech_lock:
                if request.generation < self._generation:
                    return AudioStatus.UNAVAILABLE

            # Try current backend first
            if self._current_backend and self._current_backend in self._backends:
                backend = self._backends[self._current_backend]
                # Apply prosody to backend before synthesis
                self._apply_prosody(backend, request.text, request.lang, style)
                if backend.is_allowed(request.lang):
                    status = backend.synthesize(request.text, request.lang)
                    if status == BackendStatus.AVAILABLE:
                        self._audio_status = AudioStatus.AVAILABLE
                        return AudioStatus.AVAILABLE
                    elif status == BackendStatus.BLOCKED:
                        pass
                    else:
                        pass

            # Try fallback backends
            for name in self.config.fallback_backends:
                if self._stop_event.is_set():
                    return AudioStatus.UNAVAILABLE
                
                # Check generation before each fallback
                with self._speech_lock:
                    if request.generation < self._generation:
                        return AudioStatus.UNAVAILABLE
                
                if name == self._current_backend:
                    continue
                backend = self._backends.get(name)
                if backend and backend.is_available() and backend.is_allowed(request.lang):
                    self._apply_prosody(backend, request.text, request.lang, style)
                    status = backend.synthesize(request.text, request.lang)
                    if status == BackendStatus.AVAILABLE:
                        self._current_backend = name
                        self._audio_status = AudioStatus.AVAILABLE
                        return AudioStatus.AVAILABLE

            self._audio_status = AudioStatus.FAILED
            return AudioStatus.FAILED

        except Exception as e:
            print(f"[VOICE] Speech synthesis error: {e}")
            self._audio_status = AudioStatus.FAILED
            return AudioStatus.FAILED
        finally:
            self._is_speaking = False
        self._start_queue_worker()

    def _get_sounddevice_output(self) -> SoundDeviceOutput:
        """Get or create SoundDeviceOutput for streaming neural TTS."""
        if self._sounddevice_output is None:
            self._sounddevice_output = SoundDeviceOutput(
                sample_rate=24000,
                channels=1,
                blocksize=512,
                dtype="float32",
            )
        return self._sounddevice_output

    def _register_default_backends(self) -> None:
        """Register built-in backends."""
        # pyttsx3 - always register if available
        pyttsx3_backend = Pyttsx3Backend()
        if pyttsx3_backend.is_available():
            self.register_backend(pyttsx3_backend)

        # Edge-TTS - register if available
        edge_tts_backend = EdgeTTSBackend(self.lang_manager, self._audio_output)
        if edge_tts_backend.is_available():
            self.register_backend(edge_tts_backend)

        # Kokoro - register if available (model files must be provisioned)
        # Pass voice_profile for Tactical DSP processing
        voice_profile = self._personality if getattr(self.config, 'voice_profile', 'default') == 'tactical' else None
        kokoro_backend = create_kokoro_backend(
            model_path=self.config.kokoro_model_path,
            voices_path=self.config.kokoro_voices_path,
            voice=self.config.kokoro_voice,
            speed=self.config.kokoro_speed,
            lang_code=self.config.kokoro_lang_code,
            audio_output=self._get_sounddevice_output(),
            voice_profile=voice_profile,
        )
        if kokoro_backend.is_available():
            self.register_backend(kokoro_backend)

        # Piper - register if available (model files must be provisioned)
        piper_backend = create_piper_backend(
            executable=self.config.piper_executable,
            model_path=self.config.piper_model_path,
            config_path=self.config.piper_config_path,
            voice=self.config.piper_voice,
            length_scale=self.config.piper_length_scale,
            audio_output=self._get_sounddevice_output(),
        )
        if piper_backend.is_available():
            self.register_backend(piper_backend)

    def register_backend(self, backend: TTSBackend) -> None:
        """Register a TTS backend."""
        self._backends[backend.name] = backend

    def _select_backend(self) -> None:
        """Select the active backend based on config and Cost Guard."""
        priority = self.config.get_backend_priority()
        for name in priority:
            backend = self._backends.get(name)
            if backend and backend.is_available() and backend.is_allowed():
                self._current_backend = name
                return
        # No allowed backend found
        self._current_backend = None

    def get_current_backend(self) -> Optional[str]:
        return self._current_backend

    def get_available_backends(self) -> List[str]:
        return [name for name, b in self._backends.items() if b.is_available()]

    def _apply_prosody(self, backend: TTSBackend, text: str, lang: Optional[str] = None, style: Optional[SpeechStyle] = None) -> SpeechStyle:
        """Generate and apply SpeechStyle to backend."""
        # Use provided style or default to NORMAL mode
        if style is None:
            style = self._prosody.for_mode(SpeakingMode.NORMAL)
        
        # Apply style to backend where supported
        # Kokoro supports speed (rate)
        if hasattr(backend, 'speed') and style.rate != 1.0:
            backend.speed = style.rate
        
        # Piper supports length_scale (inverse of rate)
        if hasattr(backend, 'length_scale') and style.rate != 1.0:
            backend.length_scale = 1.0 / style.rate
            
        return style

    def _compute_delivery_style(
        self,
        text: str,
        context: str = "",
        intent: Optional[str] = None,
        is_thinking: bool = False,
        is_error: bool = False,
        is_warning: bool = False,
    ) -> tuple[SpeechStyle, ResponseCategory, str]:
        """
        Compute the delivery style for a response.
        
        Returns (style, category, length_category).
        """
        # Classify response category
        category = classify_response_category(
            text=text,
            context=context,
            intent=intent,
            is_thinking=is_thinking,
            is_error=is_error,
            is_warning=is_warning,
        )
        
        # Get delivery profile
        profile = get_delivery_profile(category)
        
        # Determine speaking mode from profile
        mode_map = {
            "NORMAL": SpeakingMode.NORMAL,
            "CONFIDENT": SpeakingMode.CONFIDENT,
            "COMMAND": SpeakingMode.COMMAND,
            "ALERT": SpeakingMode.ALERT,
            "URGENT": SpeakingMode.URGENT,
            "THINKING": SpeakingMode.THINKING,
            "HUMOR": SpeakingMode.HUMOR,
            "CRITICAL": SpeakingMode.CRITICAL,
        }
        speaking_mode = mode_map.get(profile.speaking_mode, SpeakingMode.NORMAL)
        
        # Get base style for mode
        base_style = self._prosody.for_mode(speaking_mode)
        
        # Calculate response length category
        length_category = calculate_response_length_category(text)
        
        # Apply delivery profile adjustments
        final_style = self._prosody.for_delivery(
            mode=speaking_mode,
            delivery_profile=profile,
            length_category=length_category,
        )
        
        return final_style, category, length_category

    def speak_with_delivery(
        self,
        text: str,
        context: str = "",
        lang: Optional[str] = None,
        intent: Optional[str] = None,
        is_thinking: bool = False,
        is_error: bool = False,
        is_warning: bool = False,
        priority: int = 0,
    ) -> AudioStatus:
        """
        Speak with full contextual delivery processing.
        
        This is the main entry point for V9 conversational delivery.
        It classifies the response, computes delivery parameters, and queues for synthesis.
        """
        if not text:
            return AudioStatus.UNAVAILABLE

        if self.config.headless_mode:
            return AudioStatus.UNAVAILABLE

        # Increment generation counter for this new request
        with self._speech_lock:
            self._generation += 1
            current_generation = self._generation
        
        # Create request and queue it with generation token
        request = SpeechRequest(
            text=text,
            context=context,
            lang=lang,
            intent=intent,
            is_thinking=is_thinking,
            is_error=is_error,
            is_warning=is_warning,
            priority=priority,
            generation=current_generation,
        )
        
        try:
            self._speech_queue.put(request, timeout=1.0)
            return AudioStatus.AVAILABLE
        except queue.Full:
            print("[VOICE] Speech queue full, dropping request")
            return AudioStatus.FAILED

    def speak(self, text: str, context: str = "", lang: Optional[str] = None) -> None:
        """
        Main speaking function — applies personality then delegates to speak_immediate.
        
        Personality handling is delegated to the cinematic_voice layer.
        """
        if not text:
            return
        # Note: personality prefix is added by cinematic_voice before calling speak_immediate
        # Use delivery-aware speaking for context-aware prosody
        self.speak_with_delivery(text, context, lang)

    def speak_immediate(self, text: str, lang: Optional[str] = None, context: str = "") -> AudioStatus:
        """
        Synthesize and play text immediately using the selected backend.
        
        Returns AudioStatus for backward compatibility with cinematic_voice API.
        If context is provided, uses delivery-aware prosody (queued).
        Without context, synthesizes synchronously for backward compatibility.
        """
        # If context provided, use delivery-aware speaking (queued)
        if context:
            return self.speak_with_delivery(text, context, lang)
        
        # Backward compatibility: synchronous synthesis without context
        if not text:
            return AudioStatus.UNAVAILABLE

        if self.config.headless_mode:
            return AudioStatus.UNAVAILABLE

        with self._speech_lock:
            self._is_speaking = True
            self._stop_event.clear()

            try:
                # Apply prosody for current mode (default NORMAL)
                style = self._prosody.for_mode(SpeakingMode.NORMAL)
                
                # Try current backend first
                if self._current_backend and self._current_backend in self._backends:
                    backend = self._backends[self._current_backend]
                    # Apply prosody to backend before synthesis
                    self._apply_prosody(backend, text, lang, style)
                    if backend.is_allowed(lang):
                        status = backend.synthesize(text, lang)
                        if status == BackendStatus.AVAILABLE:
                            self._audio_status = AudioStatus.AVAILABLE
                            return AudioStatus.AVAILABLE
                        elif status == BackendStatus.BLOCKED:
                            # Cost Guard blocked - try fallbacks
                            pass
                        else:
                            # Other failure - try fallbacks
                            pass

                # Try fallback backends
                for name in self.config.fallback_backends:
                    if name == self._current_backend:
                        continue
                    backend = self._backends.get(name)
                    if backend and backend.is_available() and backend.is_allowed(lang):
                        # Apply prosody to fallback backend
                        self._apply_prosody(backend, text, lang, style)
                        status = backend.synthesize(text, lang)
                        if status == BackendStatus.AVAILABLE:
                            self._current_backend = name
                            self._audio_status = AudioStatus.AVAILABLE
                            return AudioStatus.AVAILABLE

                # All backends failed or blocked
                self._audio_status = AudioStatus.FAILED
                return AudioStatus.FAILED

            except Exception as e:
                print(f"[VOICE] Speech synthesis error: {e}")
                self._audio_status = AudioStatus.FAILED
                return AudioStatus.FAILED
            finally:
                self._is_speaking = False

    def stop(self) -> None:
        """Stop all ongoing speech and clear the speech queue.
        
        Uses generation-based cancellation to handle race conditions:
        - Increments generation counter to invalidate in-flight requests
        - Clears queue to prevent stale audio
        - Stops all backends and outputs
        """
        # Increment generation to invalidate all pending/in-flight requests
        with self._speech_lock:
            self._generation += 1
        
        # Signal stop to current synthesis
        self._stop_event.set()
        
        # Clear queued requests (prevent stale audio)
        cleared = 0
        while not self._speech_queue.empty():
            try:
                self._speech_queue.get_nowait()
                self._speech_queue.task_done()
                cleared += 1
            except queue.Empty:
                break
        if cleared:
            print(f"[VOICE] Cleared {cleared} queued speech request(s)")
        
        # Stop current backend
        if self._current_backend and self._current_backend in self._backends:
            self._backends[self._current_backend].stop()
        
        # Stop audio outputs
        self._audio_output.stop()
        if self._sounddevice_output:
            self._sounddevice_output.stop()
        
        self._is_speaking = False
        self._stop_event.clear()
        print("[VOICE] Stopped all ongoing speech")

    def get_status(self) -> AudioStatus:
        return self._audio_status

    def is_speaking(self) -> bool:
        return self._is_speaking

    def setup_voice(self) -> None:
        """Configure voice settings (delegates to pyttsx3 backend if available)."""
        pyttsx3_backend = self._backends.get("pyttsx3")
        if pyttsx3_backend and hasattr(pyttsx3_backend, '_setup_voice'):
            pyttsx3_backend._setup_voice()

    def set_language(self, lang_code: str) -> bool:
        """Change the active language."""
        return self.lang_manager.set_language(lang_code)

    def get_current_language(self) -> str:
        return self.lang_manager.get_tts_language()

    def get_current_language_name(self) -> str:
        return self.lang_manager.get_language_name()

    @property
    def personality(self) -> VoicePersonality:
        """Get the current voice personality."""
        return self._personality

    @property
    def prosody(self) -> ProsodyController:
        """Get the prosody controller."""
        return self._prosody

    def shutdown(self) -> None:
        """Gracefully shutdown the voice engine."""
        self._queue_running.clear()
        self.stop()
        if self._queue_worker and self._queue_worker.is_alive():
            self._queue_worker.join(timeout=2.0)
        print("[VOICE] VoiceEngine shutdown complete")

    def _ensure_queue_worker(self) -> None:
        """Ensure queue worker is running (restart if dead)."""
        if self._queue_worker is None or not self._queue_worker.is_alive():
            if self._queue_running.is_set():
                print("[VOICE] Queue worker died, restarting...")
                self._start_queue_worker()

    def speak_with_delivery(
        self,
        text: str,
        context: str = "",
        lang: Optional[str] = None,
        intent: Optional[str] = None,
        is_thinking: bool = False,
        is_error: bool = False,
        is_warning: bool = False,
        priority: int = 0,
    ) -> AudioStatus:
        """
        Speak with full contextual delivery processing.
        
        This is the main entry point for V9 conversational delivery.
        It classifies the response, computes delivery parameters, and queues for synthesis.
        """
        if not text:
            return AudioStatus.UNAVAILABLE

        if self.config.headless_mode:
            return AudioStatus.UNAVAILABLE

        # Ensure queue worker is alive
        self._ensure_queue_worker()
        
        # Increment generation counter for this new request
        with self._speech_lock:
            self._generation += 1
            current_generation = self._generation
        
        # Create request and queue it with generation token
        request = SpeechRequest(
            text=text,
            context=context,
            lang=lang,
            intent=intent,
            is_thinking=is_thinking,
            is_error=is_error,
            is_warning=is_warning,
            priority=priority,
            generation=current_generation,
        )
        
        try:
            self._speech_queue.put(request, timeout=1.0)
            return AudioStatus.AVAILABLE
        except queue.Full:
            print("[VOICE] Speech queue full, dropping request")
            return AudioStatus.FAILED