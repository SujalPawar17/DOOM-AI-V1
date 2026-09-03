#!/usr/bin/env pythonw
"""
DOOM Silent Background Service
Listens continuously for double-claps and voice wake words without taking terminal focus.
"""

import sys
import os
import time
import traceback

workspace_dir = os.path.dirname(os.path.abspath(__file__))
if workspace_dir not in sys.path:
    sys.path.insert(0, workspace_dir)

log_file = os.path.join(workspace_dir, "doom_bg.log")

def log(msg):
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            f.flush()
    except Exception:
        pass

# Redirect stdout/stderr to log file
try:
    log_fp = open(log_file, "a", encoding="utf-8")
    sys.stdout = log_fp
    sys.stderr = log_fp
except Exception:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')

log("Starting DOOM background service...")

try:
    from core.sound_detector import sound_detector
    from core.listen import listen_for_command, listen_for_wake_word
    from core.cinematic_voice import speak, setup_jarvis_voice, stop_speaking, start_stop_hotkey_listener
    from core.commands import handle_command
    log("All core modules imported successfully.")
except Exception as e:
    log(f"Import error: {traceback.format_exc()}")
    sys.exit(1)

is_processing = False

def on_clap():
    global is_processing
    if is_processing:
        return
    is_processing = True
    try:
        stop_speaking()
        log("Double-clap detected!")
        speak("Acoustic wake signal detected. Systems energized. I am listening, Sujal.", "greeting")
        command = listen_for_command()
        if command:
            log(f"Handling command: {command}")
            handle_command(command)
        else:
            speak("Standing by, Sujal.")
    except Exception as e:
        log(f"Clap processing error: {traceback.format_exc()}")
    finally:
        is_processing = False

def main():
    try:
        setup_jarvis_voice()
        start_stop_hotkey_listener()
        log("Voice setup complete.")
        
        # 1. Start acoustic clap detector in background thread
        try:
            sound_detector.start_background_detector(on_clap)
            log("Acoustic sound detector thread started.")
        except Exception as e:
            log(f"Acoustic detector error: {e}")
        
        global is_processing
        log("Entering main wake word listener loop...")
        
        # 2. Main loop listens for voice wake words ("Hey DOOM", "Jarvis")
        while True:
            try:
                if not is_processing:
                    is_wake = listen_for_wake_word()
                    if is_wake and not is_processing:
                        is_processing = True
                        try:
                            stop_speaking()
                            log("Voice wake word detected!")
                            speak("Yes Sujal, I am listening.", "greeting")
                            command = listen_for_command()
                            if command:
                                log(f"Handling command: {command}")
                                handle_command(command)
                            else:
                                speak("Standing by, Sujal.")
                        except Exception as e:
                            log(f"Voice command processing error: {traceback.format_exc()}")
                        finally:
                            is_processing = False
                time.sleep(0.1)
            except Exception as e:
                log(f"Loop iteration exception: {traceback.format_exc()}")
                time.sleep(1)
    except Exception as e:
        log(f"Fatal error in main: {traceback.format_exc()}")

if __name__ == "__main__":
    main()
