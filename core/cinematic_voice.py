import time
import random
import threading
import os
import tempfile
import asyncio
from typing import Optional

# Try imports for various high-tech voice backends
try:
    import edge_tts
    import pygame
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

try:
    from gtts import gTTS
    import pygame
    GTTS_AVAILABLE = True
except ImportError:
    GTTS_AVAILABLE = False

try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    PYTTSX3_AVAILABLE = False

from core.language_manager import get_language_manager


class DOOMCinematicVoice:
    def __init__(self):
        self.engine = None
        self.is_speaking = False
        self.speech_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.current_mixer = None
        self.elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "")
        self.elevenlabs_voice_id = os.getenv("ELEVENLABS_VOICE_ID", "")
        self._preferred_backend = self._detect_preferred_backend()
        
        # Language manager for multilingual support
        self.lang_manager = get_language_manager()
        
        # JARVIS personality responses for Sujal (multilingual)
        self.personality_responses = {
            'acknowledgment': {
                'en': ["Certainly, Sujal.", "Right away, Sujal.", "I'm on it, Sujal.", "Immediately, Sujal.", "Of course, Sujal."],
                'hi': ["जरूर, सुजल।", "तुरंत, सुजल।", "मैं कर रहा हूँ, सुजल।", "फौरन, सुजल।", "बिल्कुल, सुजल।"],
                'mr': ["नक्की, सुजल।", "लवकर, सुजल।", "मी करत आहे, सुजल।", "तात्काळ, सुजल।", "खरंच, सुजल।"],
                'ta': ["நிச்சயமாக, சூஜல்.", "உடனே, சூஜல்.", "நான் செய்கிறேன், சூஜல்.", "உடனடி, சூஜல்.", "நிச்சயமாக, சூஜல்."],
                'te': ["అవశ్యం, సుజల్.", "త్వరితంగా, సుజల్.", "నేను చేయుతున్నాను, సుజల్.", "క్షణ 안으로, సుజల్.", "ఖచ్చితంగా, సుజల్."],
            },
            'thinking': {
                'en': ["Let me analyze that...", "Running calculations...", "Accessing mainframe...", "One moment, Sujal...", "Processing parameters..."],
                'hi': ["मैं इसका विश्लेषण करता हूँ...", "गणना चल रही है...", "मेनफ्रेम एक्सेस कर रहा हूँ...", "एक पल, सुजल...", "पैरामीटर्स प्रोसेस हो रहे हैं..."],
                'mr': ["मी याचे विश्लेषण करतो...", "गणना चालू आहे...", "मेनफ्रेम एक्सेस करत आहे...", "एक क्षण, सुजल...", "पॅरामीटर्स प्रोसेस होत आहेत..."],
                'ta': ["இதை বিশ্লேஷிக்கிறேன்...", "கணக்கீடுகள் நடக்கின்றன...", "மெயின் ஃப்ரேమ్ அணுகப்படுகிறது...", "ஒரு கணம், சூஜல்...", "அம்சங்கள் செயலாக்கப்படுகின்றன..."],
                'te': ["నేను విశ్లేషిస్తున్నాను...", "లెక్కింపులు நடిస్తున్నాయి...", "మెయిన్ ఫ్రేమ్ యాక్సెస్ చేస్తోంది...", "ఒక నిమిషం, సుజల్...", "ప్యారామೀటర్లు ప్రాసెస్ అవుతున్నాయి..."],
            },
            'completion': {
                'en': ["Task completed, Sujal.", "Done, Sujal.", "Mission accomplished, Sujal.", "All systems nominal, Sujal.", "Execution finished, Sujal."],
                'hi': ["कार्य पूर्ण, सुजल।", "हो गया, सुजल।", "मिशन पूरा, सुजल।", "सभी सिस्टम सामान्य, सुजल।", "निष्पादन समाप्त, सुजल।"],
                'mr': ["कार्य पूर्ण, सुजल।", "झाले, सुजल।", "मिशन पूर्ण, सुजल।", "सर्व सिस्टम्स सामान्य, सुजल।", "अमल पूर्ण, सुजल।"],
                'ta': ["வேலை முடிந்தது, சூஜல்.", "முடிந்தது, சூஜல்.", "மேஷன் நிருவர்த்தப்பட்டது, சூஜல்.", "அனைத்து அமைப்புகளும் சரியாக உள்ளன, சூஜல்.", "நிர்வாகம் முடிந்தது, சூஜல்."],
                'te': ["పని पूरा అయింది, సుజల్.", "అయింది, సుజల్.", "మిషన్ పూర్తైంది, సుజల్.", "అన్ని సిస్టమ్స్ సాధారణంగా ఉన్నాయి, సుజల్.", "ఎగ్జిక్యూషన్ పూర్తైంది, సుజల్."],
            },
            'error': {
                'en': ["I apologize, Sujal, but", "I encountered an anomaly:", "Unfortunately,", "System alert:"],
                'hi': ["मुझे खेद है, सुजल, लेकिन", "मुझे एक विसंगति मिली:", "दुर्भाग्य से,", "सिस्टम अलर्ट:"],
                'mr': ["मला खेद आहे, सुजल, परंतु", "मला एक विसंगती आढळली:", "दुर्दैवाने,", "सिस्टम सावध:"],
                'ta': ["மன்னிக்கவும், சூஜல், ஆனால்", "ஒரு விதிமேற்றம் கண்டுபிடிக்கப்பட்டது:", "துரococcமாக,", "நிலையத்திரவ அறிவிப்பு:"],
                'te': ["క్షమాపణాలు, సుజల్, కానీ", "ఒక అనọమలి ఎదురായി:", "దురద్యోగంగా,", "సిస్టమ్ అలెర్ట్:"],
            },
            'greeting': {
                'en': ["Good day, Sujal. Systems are online and ready.", "At your service, Sujal. What are our objectives today?", "DOOM online. How may I assist you, Sujal?", "Standing by for your command, Sujal."],
                'hi': ["नमस्ते, सुजल। सिस्टम्स ऑनलाइन और तैयार हैं।", "आपकी सेवा में, सुजल। आज हमारे उद्देश्य क्या हैं?", "डूम ऑनलाइन। मैं आपकी कैसे सहायता कर सकता हूँ, सुजल?", "आपके आदेश की प्रतीक्षा में, सुजल।"],
                'mr': ["नमस्कार, सुजल। सिस्टम्स ऑनलाइन आणि तयार आहेत।", "तुमची सेवा, सुजल। आज आमचे उद्दिष्ट काय आहेत?", "डूम ऑनलाइन। मी तुमची कशी मदत करू शकतो, सुजल?", "तुमच्या आदेशाची प्रतीक्षा, सुजल।"],
                'ta': ["நலம், சூஜல். அமைப்புகள் ஆன்லைனில் உள்ளன மற்றும் தயாராக உள்ளன.", "உங்கள் சேவையில், சூஜல். இன்று எங்கள் இலக்குகள் என்ன?", "டூம் ஆன்லைன். நான் உங்களுக்கு எப்படி உதவ முடியும், சூஜல்?", "உங்கள் கட்டளைக்காக காத்திருக்கிறேன், சூஜல்."],
                'te': ["శుభోదయం, సుజల్. సిస్టమ్స్ ఆన్లైన్లో ఉన్నాయి మరియు సిద్ధంగా ఉన్నాయి.", "మీ సేవలో, సుజల్. ఈరోజు మా లక్ష్యాలు ఏమిటి?", "డూమ్ ఆన్లైన్. నేను మీకు ఎలా సహాయపడగలను, సుజల్?", "మీ ఆదేశానికి వేలుస్తున్నాను, సుజల్."],
            }
        }
        
        # Initialize voice system
        self._init_voice()
    
    def _detect_preferred_backend(self) -> str:
        """Detect and return the preferred TTS backend (only ONE active at a time)"""
        if self.elevenlabs_key and self.elevenlabs_voice_id:
            return "elevenlabs"
        if EDGE_TTS_AVAILABLE:
            return "edge_tts"
        if PYTTSX3_AVAILABLE:
            return "pyttsx3"
        if GTTS_AVAILABLE:
            return "gtts"
        return "none"

    def _init_voice(self):
        """Initialize pyttsx3 engine safely for offline fallback"""
        if PYTTSX3_AVAILABLE and self.engine is None:
            try:
                self.engine = pyttsx3.init()
                self.setup_jarvis_voice()
            except Exception as e:
                self.engine = None

    def setup_jarvis_voice(self):
        """Configure offline JARVIS-like voice settings"""
        if not self.engine:
            return
        try:
            self.engine.setProperty('rate', 180)
            self.engine.setProperty('volume', 0.9)
            voices = self.engine.getProperty('voices')
            if voices:
                male_voice_found = False
                for voice in voices:
                    voice_name_lower = voice.name.lower()
                    if any(male_name in voice_name_lower for male_name in ['david', 'mark', 'george', 'ryan', 'male', 'zira']):
                        self.engine.setProperty('voice', voice.id)
                        male_voice_found = True
                        break
                if not male_voice_found:
                    for voice in voices:
                        if 'desktop' in voice.name.lower() and 'english' in voice.name.lower():
                            self.engine.setProperty('voice', voice.id)
                            break
        except Exception:
            pass

    def stop_speaking(self):
        """Immediately stop ALL ongoing speech across ALL backends"""
        self.stop_event.set()
        
        # Stop pygame mixer (used by Edge-TTS, ElevenLabs, gTTS)
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.quit()
        except Exception:
            pass
        
        # Stop pyttsx3 engine
        if self.engine:
            try:
                self.engine.stop()
            except Exception:
                pass
        
        self.is_speaking = False
        self.stop_event.clear()
        print("[VOICE] Stopped all ongoing speech")

    def speak(self, text: str, context: str = "", lang: Optional[str] = None):
        """Main speaking function with JARVIS personality - interrupts previous speech"""
        if not text:
            return
        self.stop_speaking()  # Stop any ongoing speech before starting new
        processed_text = self.add_personality_to_text(text, context, lang)
        self.speak_immediate(processed_text, lang)

    def speak_immediate(self, text: str, lang: Optional[str] = None):
        """Speak using ONLY the preferred backend (single voice)"""
        if not text:
            return

        try:
            print(f"\n[DOOM]: {text}")
        except UnicodeEncodeError:
            print(f"\n[DOOM]: {text.encode('ascii', 'replace').decode('ascii')}")

        with self.speech_lock:
            self.is_speaking = True
            try:
                if self._preferred_backend == "elevenlabs":
                    self._speak_with_elevenlabs(text, lang)
                elif self._preferred_backend == "edge_tts":
                    self._speak_with_edge_tts(text, lang)
                elif self._preferred_backend == "pyttsx3":
                    self._speak_with_pyttsx3(text, lang)
                elif self._preferred_backend == "gtts":
                    self._speak_with_gtts(text, lang)
            finally:
                self.is_speaking = False

    def _get_edge_voice(self, lang: Optional[str] = None) -> str:
        """Get Edge-TTS voice for language"""
        return self.lang_manager.get_tts_voice(lang)

    def _get_gtts_lang(self, lang: Optional[str] = None) -> str:
        """Get gTTS language code"""
        lang = lang or self.lang_manager.get_tts_language()
        gtts_map = {
            "en": "en",
            "hi": "hi",
            "mr": "mr",
            "ta": "ta",
            "te": "te",
            "kn": "kn",
            "ml": "ml",
            "gu": "gu",
            "bn": "bn",
            "pa": "pa",
            "ur": "ur",
        }
        return gtts_map.get(lang, "en")

    def _speak_with_edge_tts(self, text: str, lang: Optional[str] = None) -> bool:
        """Speak using Microsoft Edge Neural TTS with multilingual support"""
        if self.stop_event.is_set():
            return False
            
        voice = self._get_edge_voice(lang)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
            temp_path = tmp_file.name

        try:
            async def _generate_audio():
                communicate = edge_tts.Communicate(text, voice, rate="+5%", pitch="+0Hz")
                await communicate.save(temp_path)

            asyncio.run(_generate_audio())

            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                import pygame
                pygame.mixer.init()
                self.current_mixer = pygame.mixer
                pygame.mixer.music.load(temp_path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy() and not self.stop_event.is_set():
                    time.sleep(0.05)
                pygame.mixer.music.stop()
                pygame.mixer.quit()
                return True
        except Exception as e:
            print(f"[EDGE_TTS ERROR]: {e}")
            return False
        finally:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except Exception:
                pass
        return False

    def _speak_with_elevenlabs(self, text: str, lang: Optional[str] = None) -> bool:
        """Speak using ElevenLabs API (multilingual v2 model)"""
        if self.stop_event.is_set():
            return False
            
        import requests
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self.elevenlabs_voice_id}"
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.elevenlabs_key
        }
        # Use multilingual model for non-English
        model_id = "eleven_multilingual_v2" if lang and lang != "en" else "eleven_monolingual_v1"
        data = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}
        }
        response = requests.post(url, json=data, headers=headers, timeout=10)
        if response.status_code == 200:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
                temp_path = tmp_file.name
                tmp_file.write(response.content)

            try:
                import pygame
                pygame.mixer.init()
                self.current_mixer = pygame.mixer
                pygame.mixer.music.load(temp_path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy() and not self.stop_event.is_set():
                    time.sleep(0.05)
                pygame.mixer.music.stop()
                pygame.mixer.quit()
                return True
            finally:
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
        return False

    def _speak_with_gtts(self, text: str, lang: Optional[str] = None):
        """Speak using gTTS with multilingual support"""
        if self.stop_event.is_set():
            return
            
        gtts_lang = self._get_gtts_lang(lang)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
            temp_path = tmp_file.name
        try:
            tts = gTTS(text=text, lang=gtts_lang, slow=False)
            tts.save(temp_path)
            import pygame
            pygame.mixer.init()
            self.current_mixer = pygame.mixer
            pygame.mixer.music.load(temp_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy() and not self.stop_event.is_set():
                time.sleep(0.05)
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass

    def _speak_with_pyttsx3(self, text: str, lang: Optional[str] = None):
        """Offline pyttsx3 speak (limited multilingual support)"""
        if not self.engine:
            self._init_voice()
        if not self.engine:
            return
        try:
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception:
            pass

    def add_personality_to_text(self, text: str, context: str = "", lang: Optional[str] = None) -> str:
        """Add JARVIS personality to responses in the appropriate language"""
        lang = lang or self.lang_manager.get_tts_language()
        if context and context in self.personality_responses:
            lang_responses = self.personality_responses[context].get(lang, self.personality_responses[context].get('en', []))
            if lang_responses:
                prefix = random.choice(lang_responses)
                return f"{prefix} {text}"
        return text

    def set_language(self, lang_code: str) -> bool:
        """Change the active language"""
        return self.lang_manager.set_language(lang_code)

    def get_current_language(self) -> str:
        return self.lang_manager.get_tts_language()

    def get_current_language_name(self) -> str:
        return self.lang_manager.get_language_name()


# Global voice instance
_voice_instance = None

def get_voice_instance() -> DOOMCinematicVoice:
    global _voice_instance
    if _voice_instance is None:
        _voice_instance = DOOMCinematicVoice()
    return _voice_instance

def speak(text: str, context: str = "", lang: Optional[str] = None):
    voice = get_voice_instance()
    voice.speak(text, context, lang)

def speak_immediate(text: str, lang: Optional[str] = None):
    voice = get_voice_instance()
    voice.speak_immediate(text, lang)

def stop_speaking():
    """Global stop function - call this to immediately halt all speech"""
    voice = get_voice_instance()
    voice.stop_speaking()

def setup_jarvis_voice():
    voice = get_voice_instance()
    voice.setup_jarvis_voice()

def voice_effects(text: str) -> str:
    return text

def set_language(lang_code: str) -> bool:
    """Set the active language for TTS"""
    voice = get_voice_instance()
    return voice.set_language(lang_code)

def get_current_language() -> str:
    voice = get_voice_instance()
    return voice.get_current_language()

def get_current_language_name() -> str:
    voice = get_voice_instance()
    return voice.get_current_language_name()


# Global hotkey for stop speaking
_hotkey_listener_started = False
_hotkey_thread = None

def start_stop_hotkey_listener():
    """Start global hotkey listener for Ctrl+Shift+S to stop speech"""
    global _hotkey_listener_started, _hotkey_thread
    if _hotkey_listener_started:
        return
    try:
        import keyboard
        def on_stop_hotkey():
            stop_speaking()
            print("\n[HOTKEY] Stop speech triggered (Ctrl+Shift+S)")
        
        keyboard.add_hotkey('ctrl+shift+s', on_stop_hotkey)
        _hotkey_listener_started = True
        print("[HOTKEY] Global stop hotkey registered: Ctrl+Shift+S")
    except ImportError:
        print("[HOTKEY] 'keyboard' module not available - install with: pip install keyboard")
    except Exception as e:
        print(f"[HOTKEY] Failed to register hotkey: {e}")

def stop_hotkey_listener():
    """Stop the global hotkey listener"""
    global _hotkey_listener_started
    if _hotkey_listener_started:
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        _hotkey_listener_started = False