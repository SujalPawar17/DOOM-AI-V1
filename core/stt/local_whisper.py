"""
Local Whisper STT. Inference is in-process only.

Weights are never downloaded automatically. Missing models return
STT_UNAVAILABLE_LOCAL_MODEL_MISSING. Cloud STT is not a fallback.
"""
from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass
from typing import Any, Optional

from core.cost_guard import ResourceRequest, ResourceType, cost_guard

STT_UNAVAILABLE_LOCAL_MODEL_MISSING = "STT_UNAVAILABLE_LOCAL_MODEL_MISSING"
STT_UNAVAILABLE_RUNTIME_MISSING = "STT_UNAVAILABLE_RUNTIME_MISSING"
STT_UNAVAILABLE_BLOCKED = "STT_UNAVAILABLE_BLOCKED"
STT_UNAVAILABLE_FAILED = "STT_UNAVAILABLE_FAILED"

_DEFAULT_MODEL = "base"
_HF_SLUG_PREFIX = "models--Systran--faster-whisper-"


@dataclass(frozen=True)
class TranscribeResult:
    ok: bool
    text: str = ""
    code: str = ""
    language: str = ""


class LocalSTTProvider:
    """faster-whisper (optional) with local_files_only. No API key. No HTTP."""

    name = "local_whisper"

    def __init__(self, model: Optional[str] = None):
        self._model_name = (model or os.getenv("DOOM_STT_MODEL") or _DEFAULT_MODEL).strip()
        self._engine: Any = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    def requires_api_key(self) -> bool:
        return False

    def local_weights_present(self) -> bool:
        slug = _HF_SLUG_PREFIX + self._model_name
        explicit = (os.getenv("DOOM_STT_MODEL_DIR") or "").strip()
        roots = [
            explicit,
            os.environ.get("HF_HOME") or "",
            os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub"),
            os.path.join(os.path.expanduser("~"), ".cache", "huggingface"),
            os.path.join(os.path.expanduser("~"), ".cache", "faster-whisper"),
        ]
        for root in roots:
            if not root:
                continue
            if os.path.isfile(root):
                return True
            direct = os.path.join(root, slug)
            if os.path.isdir(direct):
                return True
            if os.path.isdir(root):
                try:
                    names = os.listdir(root)
                except OSError:
                    continue
                if slug in names:
                    return True
                if self._model_name in names and os.path.isdir(os.path.join(root, self._model_name)):
                    return True
        return False

    def runtime_available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception:
            return False

    def is_ready(self) -> bool:
        return self.runtime_available() and self.local_weights_present()

    def _authorize_inference(self) -> bool:
        d = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.STT,
            provider="local_whisper",
            capability="stt",
            model=self._model_name,
        ))
        return d.is_allow

    def _authorize_download(self) -> bool:
        d = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.STT_DOWNLOAD,
            provider="huggingface",
            capability="stt",
            model=self._model_name,
            host="huggingface.co",
        ))
        return d.is_allow

    def _ensure_engine(self) -> Optional[str]:
        if self._engine is not None:
            return None
        if not self.runtime_available():
            return STT_UNAVAILABLE_RUNTIME_MISSING
        if not self.local_weights_present():
            self._authorize_download()
            return STT_UNAVAILABLE_LOCAL_MODEL_MISSING
        if not self._authorize_inference():
            return STT_UNAVAILABLE_BLOCKED
        with self._lock:
            if self._engine is not None:
                return None
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            try:
                from faster_whisper import WhisperModel
                kwargs = {
                    "device": "cpu",
                    "compute_type": "int8",
                    "local_files_only": True,
                }
                model_dir = (os.getenv("DOOM_STT_MODEL_DIR") or "").strip()
                if model_dir and os.path.isdir(model_dir):
                    kwargs["download_root"] = model_dir
                self._engine = WhisperModel(self._model_name, **kwargs)
            except Exception:
                self._engine = None
                return STT_UNAVAILABLE_LOCAL_MODEL_MISSING
        return None

    def transcribe_wav_path(self, path: str, language: Optional[str] = None) -> TranscribeResult:
        if not path or not os.path.isfile(path):
            return TranscribeResult(ok=False, code=STT_UNAVAILABLE_FAILED)
        err = self._ensure_engine()
        if err:
            return TranscribeResult(ok=False, code=err)
        if self._engine is None:
            return TranscribeResult(ok=False, code=STT_UNAVAILABLE_FAILED)
        lang = _whisper_lang(language)
        try:
            segments, info = self._engine.transcribe(
                path,
                language=lang,
                vad_filter=False,
            )
            parts = []
            for seg in segments:
                piece = (getattr(seg, "text", None) or "").strip()
                if piece:
                    parts.append(piece)
            text = " ".join(parts).strip()
            detected = ""
            if info is not None:
                detected = str(getattr(info, "language", "") or "")
            if not text:
                return TranscribeResult(ok=False, code=STT_UNAVAILABLE_FAILED, language=detected)
            return TranscribeResult(ok=True, text=text, language=detected)
        except Exception:
            return TranscribeResult(ok=False, code=STT_UNAVAILABLE_FAILED)

    def transcribe_wav_bytes(self, wav_bytes: bytes, language: Optional[str] = None) -> TranscribeResult:
        if not wav_bytes:
            return TranscribeResult(ok=False, code=STT_UNAVAILABLE_FAILED)
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                tmp.write(wav_bytes)
                tmp_path = tmp.name
            return self.transcribe_wav_path(tmp_path, language=language)
        except Exception:
            return TranscribeResult(ok=False, code=STT_UNAVAILABLE_FAILED)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def transcribe_audio(self, audio: Any, language: Optional[str] = None) -> TranscribeResult:
        if audio is None:
            return TranscribeResult(ok=False, code=STT_UNAVAILABLE_FAILED)
        try:
            wav_bytes = audio.get_wav_data()
        except Exception:
            return TranscribeResult(ok=False, code=STT_UNAVAILABLE_FAILED)
        return self.transcribe_wav_bytes(wav_bytes, language=language)


def _whisper_lang(language: Optional[str]) -> Optional[str]:
    if not language:
        return None
    code = language.replace("_", "-").split("-")[0].strip().lower()
    return code or None


local_stt = LocalSTTProvider()
