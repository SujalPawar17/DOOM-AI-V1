from core.orchestrator import doom_core
from core.cinematic_voice import speak, speak_immediate, voice_effects, stop_speaking, get_current_language
from core.translate import translate
from core.memory import remember, recall
from core.language_manager import get_language_manager


def submit_user_input(command: str, lang: str = None, *, source: str = "text"):
    """Canonical user-text entry for typed input and voice STT transcripts.

    source is metadata only (e.g. 'text' or 'voice'). This does not execute
    tools, shell, or computer actions. It forwards plain text to
    doom_core.process_request — the existing policy, risk, approval,
    Cost Guard, and routing pipeline.
    """
    if not command:
        return None
    text = str(command).strip()
    if not text:
        return None
    stop_speaking()
    if lang is None:
        lm = get_language_manager()
        lang = lm.detect_language_from_text(text)
    try:
        response = doom_core.process_request(
            text, lang, context={"input_source": source}
        )
        speak(response, lang=lang)
        return response
    except Exception as e:
        print(f"[ERROR]: DOOM Core exception: {e}")
        speak("I encountered an anomaly in the core orchestrator, Sujal.", lang=lang)
        return None


def handle_command(command: str, lang: str = None):
    """DOOM V2 Master Command Dispatcher — routes through DOOM Core Orchestrator"""
    return submit_user_input(command, lang, source="text")


def handle_command_with_language(command: str):
    """Handle command with automatic language detection"""
    lm = get_language_manager()
    lang = lm.detect_language_from_text(command)
    return submit_user_input(command, lang, source="text")
