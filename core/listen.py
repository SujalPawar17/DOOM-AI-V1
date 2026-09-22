import os
import speech_recognition as sr
from typing import Optional, List
from core.language_manager import get_language_manager
from core.stt.local_whisper import (
    STT_UNAVAILABLE_LOCAL_MODEL_MISSING,
    local_stt,
)

_recognizer = None


def _safe_print(message: str) -> None:
    try:
        print(message)
    except Exception:
        try:
            print(message.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def _google_stt_allowed() -> bool:
    from core.cost_guard import ResourceRequest, ResourceType, cost_guard
    return cost_guard.authorize(ResourceRequest(
        resource_type=ResourceType.STT,
        provider="google_web_speech",
        capability="stt",
        host="www.google.com",
    )).is_allow


def get_recognizer():
    global _recognizer
    if _recognizer is None:
        _recognizer = sr.Recognizer()
        _recognizer.energy_threshold = 280
        _recognizer.dynamic_energy_threshold = True
        _recognizer.dynamic_energy_adjustment_damping = 0.15
        _recognizer.dynamic_energy_ratio = 1.5
        _recognizer.pause_threshold = 1.0
        _recognizer.non_speaking_duration = 0.5
    return _recognizer


def open_microphone():
    """Local capture, no AGC. Uses the device native sample rate. Optional DOOM_STT_MIC_INDEX."""
    kwargs = {"chunk_size": 1024}
    raw = (os.getenv("DOOM_STT_MIC_INDEX") or "").strip()
    if raw.isdigit():
        kwargs["device_index"] = int(raw)
    return sr.Microphone(**kwargs)

def get_wake_phrases(lang: Optional[str] = None) -> List[str]:
    """Get wake phrases for a specific language"""
    lm = get_language_manager()
    return lm.get_wake_phrases(lang)

def is_wake_phrase(text: str, lang: Optional[str] = None) -> bool:
    """Fuzzy and phonetic wake phrase recognition with multilingual support"""
    if not text:
        return False
    text = text.lower().strip()
    wake_words = get_wake_phrases(lang)
    return any(w in text for w in wake_words)


def _transcribe_local(audio, language: Optional[str] = None) -> Optional[str]:
    """Local STT only. Never falls back to Google or any cloud recognizer."""
    result = local_stt.transcribe_audio(audio, language=language)
    if result.ok and result.text:
        return result.text
    code = result.code or STT_UNAVAILABLE_LOCAL_MODEL_MISSING
    _safe_print(f"[STT]: {code}")
    return None


def listen_for_wake_word(lang: Optional[str] = None) -> bool:
    lm = get_language_manager()
    stt_lang = lang or lm.get_stt_language()
    
    r = get_recognizer()
    
    with open_microphone() as source:
        try:
            audio = r.listen(source, timeout=5, phrase_time_limit=5)
        except sr.WaitTimeoutError:
            return False
        except Exception:
            return False

    try:
        text = _transcribe_local(audio, language=stt_lang)
        if not text:
            return False
        text = text.lower()
        _safe_print(f"[HEARD]: {text}")
        return is_wake_phrase(text, lang)
    except Exception:
        return False

def listen_for_command(prompt_user: bool = False, lang: Optional[str] = None) -> Optional[str]:
    lm = get_language_manager()
    stt_lang = lang or lm.get_stt_language()
    
    r = get_recognizer()
    
    with open_microphone() as source:
        try:
            _safe_print(f"\nListening for command (speak naturally in {lm.get_language_name(lang)})...")
            audio = r.listen(source, timeout=8, phrase_time_limit=8)
        except sr.WaitTimeoutError:
            return None
        except Exception:
            return None

    try:
        text = _transcribe_local(audio, language=stt_lang)
        if not text:
            return None
        print(f"[YOU SAID]: {text}")
        return text
    except Exception as e:
        _safe_print(f"[AUDIO NOTE]: {e}")
        return None

def listen_for_command_multilingual() -> Optional[str]:
    """Recognize speech locally (language auto-detected by Whisper when possible)."""
    r = get_recognizer()
    
    with open_microphone() as source:
        try:
            _safe_print("\nListening for command (multilingual)...")
            audio = r.listen(source, timeout=8, phrase_time_limit=8)
        except sr.WaitTimeoutError:
            return None
        except Exception:
            return None

    try:
        text = _transcribe_local(audio, language=None)
        if not text:
            return None
        _safe_print(f"[YOU SAID]: {text}")
        return text
    except Exception:
        return None
