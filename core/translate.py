import requests
from urllib.parse import quote
from core.cinematic_voice import speak, stop_speaking

def translate(text: str, dest_language: str = 'hi') -> str:
    """Free, robust translation using Google Translate endpoint via requests"""
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl={dest_language}&dt=t&q={quote(text)}"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            translated_text = "".join([part[0] for part in data[0] if part[0]])
            stop_speaking()
            speak(f"In {dest_language}: {translated_text}")
            return translated_text
    except Exception:
        pass
    stop_speaking()
    speak("Translation could not be completed, Sujal.")
    return text
