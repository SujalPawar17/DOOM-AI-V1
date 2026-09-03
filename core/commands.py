from core.orchestrator import doom_core
from core.cinematic_voice import speak, speak_immediate, voice_effects, stop_speaking, get_current_language
from core.translate import translate
from core.memory import remember, recall
from core.language_manager import get_language_manager

def handle_command(command: str, lang: str = None):
    """DOOM V2 Master Command Dispatcher — routes through DOOM Core Orchestrator"""
    if not command:
        return
    stop_speaking()  # Stop any ongoing speech before processing new command
    
    # Detect language if not provided
    if lang is None:
        lm = get_language_manager()
        lang = lm.detect_language_from_text(command)
    
    try:
        response = doom_core.process_request(command, lang)
        speak(response, lang=lang)
    except Exception as e:
        print(f"[ERROR]: DOOM Core exception: {e}")
        speak("I encountered an anomaly in the core orchestrator, Sujal.", lang=lang)

def handle_command_with_language(command: str):
    """Handle command with automatic language detection"""
    lm = get_language_manager()
    lang = lm.detect_language_from_text(command)
    handle_command(command, lang)
