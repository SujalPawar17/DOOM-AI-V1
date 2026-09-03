import speech_recognition as sr
import os
import time
from typing import Optional, List
from core.language_manager import get_language_manager

_recognizer = None

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

def listen_for_wake_word(lang: Optional[str] = None) -> bool:
    lm = get_language_manager()
    stt_lang = lang or lm.get_stt_language()
    
    r = get_recognizer()
    
    with sr.Microphone() as source:
        try:
            audio = r.listen(source, timeout=5, phrase_time_limit=5)
        except sr.WaitTimeoutError:
            return False
        except Exception:
            return False

    try:
        text = r.recognize_google(audio, language=stt_lang).lower()
        print(f"[HEARD]: {text}")
        return is_wake_phrase(text, lang)
    except Exception:
        pass
    return False

def listen_for_command(prompt_user: bool = False, lang: Optional[str] = None) -> Optional[str]:
    lm = get_language_manager()
    stt_lang = lang or lm.get_stt_language()
    
    r = get_recognizer()
    
    with sr.Microphone() as source:
        try:
            print(f"\n🎤 Listening for command (speak naturally in {lm.get_language_name(lang)})...")
            audio = r.listen(source, timeout=8, phrase_time_limit=8)
        except sr.WaitTimeoutError:
            return None
        except Exception:
            return None

    try:
        text = r.recognize_google(audio, language=stt_lang)
        print(f"[YOU SAID]: {text}")
        return text
    except sr.UnknownValueError:
        return None
    except Exception as e:
        print(f"[AUDIO NOTE]: {e}")
        return None

def listen_for_command_multilingual() -> Optional[str]:
    """Try to recognize speech in multiple languages sequentially"""
    lm = get_language_manager()
    supported_langs = ["en-US", "hi-IN", "mr-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN", "gu-IN", "bn-IN", "pa-IN", "ur-IN"]
    
    r = get_recognizer()
    
    with sr.Microphone() as source:
        try:
            print("\n🎤 Listening for command (multilingual)...")
            audio = r.listen(source, timeout=8, phrase_time_limit=8)
        except sr.WaitTimeoutError:
            return None
        except Exception:
            return None

    for lang_code in supported_langs:
        try:
            text = r.recognize_google(audio, language=lang_code)
            print(f"[YOU SAID] ({lang_code}): {text}")
            return text
        except sr.UnknownValueError:
            continue
        except Exception:
            continue
    
    return None
