import os
from typing import Optional, Dict
from dotenv import load_dotenv

load_dotenv()

class LanguageManager:
    def __init__(self):
        self.stt_language = os.getenv("STT_LANGUAGE", "en-US")
        self.tts_language = os.getenv("TTS_LANGUAGE", "en")
        self.auto_detect = os.getenv("AUTO_DETECT_LANGUAGE", "false").lower() == "true"
        
        self.tts_voice_map: Dict[str, str] = {
            "en": os.getenv("TTS_VOICE_EN", "en-GB-RyanNeural"),
            "hi": os.getenv("TTS_VOICE_HI", "hi-IN-MadhurNeural"),
            "mr": os.getenv("TTS_VOICE_MR", "mr-IN-AarohiNeural"),
            "ta": os.getenv("TTS_VOICE_TA", "ta-IN-PallaviNeural"),
            "te": os.getenv("TTS_VOICE_TE", "te-IN-MohanNeural"),
            "kn": os.getenv("TTS_VOICE_KN", "kn-IN-GaganNeural"),
            "ml": os.getenv("TTS_VOICE_ML", "ml-IN-MidhunNeural"),
            "gu": os.getenv("TTS_VOICE_GU", "gu-IN-DhwaniNeural"),
            "bn": os.getenv("TTS_VOICE_BN", "bn-IN-TanishaaNeural"),
            "pa": os.getenv("TTS_VOICE_PA", "pa-IN-GaganNeural"),
            "ur": os.getenv("TTS_VOICE_UR", "ur-IN-AsadNeural"),
        }
        
        self.stt_language_map: Dict[str, str] = {
            "en": "en-US",
            "hi": "hi-IN",
            "mr": "mr-IN",
            "ta": "ta-IN",
            "te": "te-IN",
            "kn": "kn-IN",
            "ml": "ml-IN",
            "gu": "gu-IN",
            "bn": "bn-IN",
            "pa": "pa-IN",
            "ur": "ur-IN",
        }
        
        self.language_names: Dict[str, str] = {
            "en": "English",
            "hi": "Hindi (हिन्दी)",
            "mr": "Marathi (मराठी)",
            "ta": "Tamil (தமிழ்)",
            "te": "Telugu (తెలుగు)",
            "kn": "Kannada (ಕನ್ನಡ)",
            "ml": "Malayalam (മലയാളം)",
            "gu": "Gujarati (ગુજરાતી)",
            "bn": "Bengali (বাংলা)",
            "pa": "Punjabi (ਪੰਜਾਬੀ)",
            "ur": "Urdu (اردو)",
        }
        
        self.wake_phrases: Dict[str, list] = {
            "en": ["hey doom", "hello doom", "hey do", "hey dum", "hey dom", "doom", "jarvis", "hey jarvis", "hello jarvis", "wake up"],
            "hi": ["हे डूम", "हैलो डूम", "डूम", "जार्विस", "हे जार्विस", "उठो"],
            "mr": ["हे डूम", "हॅलो डूम", "डूम", "जार्विस", "हे जार्विस", "उठा"],
            "ta": ["ஹே டூம்", "ஹலோ டூம்", "டூம்", "ஜார்விஸ்", "ஹே ஜார்விஸ்", "எழுந்து"],
            "te": ["హే డూమ్", "హలో డూమ్", "డూమ్", "జార్విస్", "హే జార్విస్", "ఉచ్ఛవసించు"],
            "kn": ["ಹೇ ಡೂಮ್", "ಹಲೋ ಡೂಮ್", "ಡೂಮ್", "ಜಾರ್ವಿಸ್", "ಹೇ ಜಾರ್ವಿಸ್", "ಎದ್ದöffnung"],
            "ml": ["ഹേ ഡൂം", "ഹലോ ഡൂം", "ഡൂം", "ജാർവിസ്", "ഹേ ജാർവിസ്", "ഉയർന്നു"],
            "gu": ["હે ડૂમ", "હેલો ડૂમ", "ડૂમ", "જાર્વિસ", "હે જાર્વિસ", "ઉઠો"],
            "bn": ["হে ডুম", "হ্যালো ডুম", "ডুম", "জারভিস", "হে জারভিস", "উঠো"],
            "pa": ["ਹੇ ਡੂਮ", "ਹੈਲੋ ਡੂਮ", "ਡੂਮ", "ਜਾਰਵਿਸ", "ਹੇ ਜਾਰਵਿਸ", "ਜਾਗੋ"],
            "ur": ["ہے ڈوم", "ہیلو ڈوم", "ڈوم", "جاروس", "ہے جاروس", "اٹھو"],
        }

    def get_stt_language(self) -> str:
        return self.stt_language

    def get_tts_language(self) -> str:
        return self.tts_language

    def get_tts_voice(self, lang: Optional[str] = None) -> str:
        lang = lang or self.tts_language
        return self.tts_voice_map.get(lang, self.tts_voice_map["en"])

    def get_stt_language_code(self, lang: Optional[str] = None) -> str:
        lang = lang or self.tts_language
        return self.stt_language_map.get(lang, "en-US")

    def get_language_name(self, lang: Optional[str] = None) -> str:
        lang = lang or self.tts_language
        return self.language_names.get(lang, "English")

    def get_wake_phrases(self, lang: Optional[str] = None) -> list:
        lang = lang or self.tts_language
        return self.wake_phrases.get(lang, self.wake_phrases["en"])

    def set_language(self, lang_code: str) -> bool:
        if lang_code in self.tts_voice_map:
            self.tts_language = lang_code
            self.stt_language = self.stt_language_map.get(lang_code, "en-US")
            return True
        return False

    def detect_language_from_text(self, text: str) -> str:
        """Simple heuristic to detect language from text"""
        text_lower = text.lower()
        
        devanagari_chars = sum(1 for c in text if '\u0900' <= c <= '\u097F')
        tamil_chars = sum(1 for c in text if '\u0B80' <= c <= '\u0BFF')
        telugu_chars = sum(1 for c in text if '\u0C00' <= c <= '\u0C7F')
        kannada_chars = sum(1 for c in text if '\u0C80' <= c <= '\u0CFF')
        malayalam_chars = sum(1 for c in text if '\u0D00' <= c <= '\u0D7F')
        gujarati_chars = sum(1 for c in text if '\u0A80' <= c <= '\u0AFF')
        bengali_chars = sum(1 for c in text if '\u0980' <= c <= '\u09FF')
        punjabi_chars = sum(1 for c in text if '\u0A00' <= c <= '\u0A7F')
        arabic_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
        
        script_counts = {
            "hi": devanagari_chars,
            "mr": devanagari_chars,
            "ta": tamil_chars,
            "te": telugu_chars,
            "kn": kannada_chars,
            "ml": malayalam_chars,
            "gu": gujarati_chars,
            "bn": bengali_chars,
            "pa": punjabi_chars,
            "ur": arabic_chars,
        }
        
        max_lang = max(script_counts, key=script_counts.get)
        if script_counts[max_lang] > 0:
            return max_lang
        return "en"

    def get_all_supported_languages(self) -> Dict[str, str]:
        return self.language_names.copy()


_language_manager = None

def get_language_manager() -> LanguageManager:
    global _language_manager
    if _language_manager is None:
        _language_manager = LanguageManager()
    return _language_manager