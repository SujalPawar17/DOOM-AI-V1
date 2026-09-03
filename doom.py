from core.listen import listen_for_wake_word, listen_for_command, is_wake_phrase, listen_for_command_multilingual
from core.cinematic_voice import speak, setup_jarvis_voice, stop_speaking, start_stop_hotkey_listener, set_language, get_current_language, get_current_language_name
from core.commands import handle_command
from core.ui_effects import show_jarvis_startup, show_listening, show_processing, show_wake_word_detected
from core.sound_detector import sound_detector
from core.language_manager import get_language_manager
import os
import time
from datetime import datetime

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

is_busy = False
current_lang = None

def on_clap_awakening():
    """Triggered when acoustic double-clap is detected"""
    global is_busy, current_lang
    if is_busy:
        return
    is_busy = True
    try:
        show_wake_word_detected()
        speak("Acoustic wake signal detected. Systems energized. I am listening, Sujal.", lang=current_lang)
        cmd = listen_for_command()
        if cmd:
            show_processing(cmd)
            handle_command(cmd, current_lang)
        else:
            speak("Standing by, Sujal.", lang=current_lang)
    finally:
        is_busy = False
        show_listening()

def startup_sequence():
    """JARVIS-like startup sequence"""
    global current_lang
    lm = get_language_manager()
    current_lang = lm.get_tts_language()
    set_language(current_lang)
    
    show_jarvis_startup()
    setup_jarvis_voice()
    start_stop_hotkey_listener()
    
    # Start acoustic double-clap sensor in background
    try:
        sound_detector.start_background_detector(on_clap_awakening)
    except Exception:
        pass
        
    lang_name = get_current_language_name()
    startup_message = f"DOOM is online and fully operational in {lang_name}. How may I assist you, Sujal?"
    speak(startup_message)

def main_loop():
    """Main command processing loop - continuous listening with multilingual support"""
    global is_busy, current_lang
    lm = get_language_manager()
    consecutive_errors = 0
    max_errors = 5
    first_interaction = True
    
    show_listening()
    
    while True:
        try:
            if not is_busy:
                if first_interaction:
                    # First interaction - listen directly for immediate command (multilingual)
                    command = listen_for_command_multilingual()
                    if command:
                        # Detect language from command
                        detected_lang = lm.detect_language_from_text(command)
                        if detected_lang != current_lang:
                            current_lang = detected_lang
                            set_language(current_lang)
                            lang_name = lm.get_language_name(current_lang)
                            speak(f"Language switched to {lang_name}.", lang=current_lang)
                        
                        is_busy = True
                        try:
                            stop_speaking()
                            show_processing(command)
                            handle_command(command, current_lang)
                        finally:
                            is_busy = False
                        consecutive_errors = 0
                    first_interaction = False
                    show_listening()
                else:
                    # Subsequent interactions - listen for wake word or direct command
                    # Use multilingual listening
                    command = listen_for_command_multilingual()
                    if command and not is_busy:
                        # Detect language from command
                        detected_lang = lm.detect_language_from_text(command)
                        if detected_lang != current_lang:
                            current_lang = detected_lang
                            set_language(current_lang)
                            lang_name = lm.get_language_name(current_lang)
                            speak(f"Language switched to {lang_name}.", lang=current_lang)
                        
                        is_busy = True
                        try:
                            stop_speaking()
                            # Check for wake phrase in detected language
                            if is_wake_phrase(command, current_lang):
                                show_wake_word_detected()
                                speak("Yes Sujal, I am listening.", lang=current_lang)
                                cleaned_command = command
                                wake_tokens = lm.get_wake_phrases(current_lang)
                                for w in wake_tokens:
                                    cleaned_command = cleaned_command.replace(w, "").strip()
                                if cleaned_command:
                                    show_processing(cleaned_command)
                                    handle_command(cleaned_command, current_lang)
                                    consecutive_errors = 0
                                else:
                                    speak("What would you like me to do, Sujal?", lang=current_lang)
                            else:
                                show_processing(command)
                                handle_command(command, current_lang)
                                consecutive_errors = 0
                        finally:
                            is_busy = False
                        show_listening()
                        
            time.sleep(0.1)
            
        except KeyboardInterrupt:
            print("\nShutdown requested by user.")
            sound_detector.stop_detector()
            speak("Shutting down DOOM. Goodbye, Sujal.")
            break
            
        except Exception as e:
            is_busy = False
            consecutive_errors += 1
            error_msg = f"Error in main loop (attempt {consecutive_errors}/{max_errors}): {e}"
            print(error_msg)
            
            if consecutive_errors >= max_errors:
                print("Too many consecutive errors. Restarting DOOM...")
                speak("I'm experiencing technical difficulties. Re-calibrating systems, Sujal.")
                consecutive_errors = 0
                time.sleep(2)
            else:
                time.sleep(1)

if __name__ == "__main__":
    try:
        startup_sequence()
        main_loop()
    except Exception as e:
        print(f"Critical error during startup: {e}")
        speak("Critical system failure. Please restart DOOM manually.")
