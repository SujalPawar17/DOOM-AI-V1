"""Edge-TTS backend implementation — cloud neural TTS (free but Cost Guard gated)."""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from typing import Optional
from core.voice.backends.base import TTSBackend, BackendStatus
from core.voice.outputs.base import AudioOutput
from core.cost_guard import ResourceRequest, ResourceType, cost_guard

EDGE_TTS_AVAILABLE = False
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    edge_tts = None  # type: ignore


class EdgeTTSBackend:
    """Edge-TTS backend — Microsoft neural TTS with multilingual support."""

    name = "edge_tts"

    def __init__(self, language_manager, audio_output: AudioOutput):
        self.lang_manager = language_manager
        self.audio_output = audio_output
        self._stop_event = threading.Event()

    def is_available(self) -> bool:
        return EDGE_TTS_AVAILABLE

    def is_allowed(self, lang: Optional[str] = None) -> bool:
        """Check Cost Guard authorization for edge_tts."""
        decision = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.TTS,
            provider="edge_tts",
            capability="tts",
        ))
        return decision.is_allow

    def synthesize(self, text: str, lang: Optional[str] = None) -> BackendStatus:
        """Synthesize text using Edge-TTS and play via audio output."""
        if not text:
            return BackendStatus.UNAVAILABLE

        if self._stop_event.is_set():
            return BackendStatus.FAILED

        if not self.is_allowed(lang):
            return BackendStatus.BLOCKED

        voice = self._get_edge_voice(lang)

        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
            temp_path = tmp_file.name

        try:
            async def _generate_audio():
                communicate = edge_tts.Communicate(text, voice, rate="+5%", pitch="+0Hz")
                await communicate.save(temp_path)

            asyncio.run(_generate_audio())

            if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
                return BackendStatus.FAILED

            status = self.audio_output.play(temp_path)
            if status == OutputStatus.ERROR:
                return BackendStatus.FAILED
            return BackendStatus.AVAILABLE
        except Exception as e:
            print(f"[VOICE] Edge-TTS synthesis failed: {e}")
            return BackendStatus.FAILED
        finally:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception:
                pass

    def stop(self) -> None:
        """Stop current speech."""
        self._stop_event.set()
        self.audio_output.stop()

    def get_voice_for_language(self, lang: str) -> Optional[str]:
        return self.lang_manager.get_tts_voice(lang)

    def _get_edge_voice(self, lang: Optional[str] = None) -> str:
        return self.lang_manager.get_tts_voice(lang)


# Need to import OutputStatus for the comparison
from core.voice.outputs.base import OutputStatus