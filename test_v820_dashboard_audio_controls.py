"""V8.20 PART B — dashboard mic/speaker gating (static verification)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = ROOT / "dashboard" / "static" / "index.html"
JS = ROOT / "dashboard" / "static" / "js" / "app.js"
CSS = ROOT / "dashboard" / "static" / "css" / "style.css"
VOICE_PY = (
    ROOT / "core" / "listen.py",
    ROOT / "core" / "cinematic_voice.py",
    ROOT / "core" / "stt" / "local_whisper.py",
)


class TestV820DashboardAudioControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML.read_text(encoding="utf-8")
        cls.js = JS.read_text(encoding="utf-8")
        cls.css = CSS.read_text(encoding="utf-8")

    def test_01_header_has_speaker_and_mic_controls(self):
        self.assertIn('id="btn-voice-toggle"', self.html)
        self.assertIn('id="btn-mic-toggle"', self.html)
        # Mic should appear near speaker in header-right.
        voice_i = self.html.index('id="btn-voice-toggle"')
        mic_i = self.html.index('id="btn-mic-toggle"')
        self.assertLess(abs(voice_i - mic_i), 800)

    def test_02_defaults_on(self):
        self.assertIn("voiceEnabled: true", self.js)
        self.assertIn("handsFree: true", self.js)

    def test_03_speaker_gate_and_labels(self):
        self.assertIn("function setSpeakerEnabled", self.js)
        self.assertIn("DOOM speech enabled", self.js)
        self.assertIn("DOOM speech muted", self.js)
        self.assertIn("if (!state.voiceEnabled || !text) return", self.js)
        self.assertIn('localStorage.setItem(LS_SPEAKER', self.js)

    def test_04_mic_gate_and_labels(self):
        self.assertIn("function setMicEnabled", self.js)
        self.assertIn("DOOM hands-free listening enabled", self.js)
        self.assertIn("DOOM hands-free listening disabled", self.js)
        self.assertIn("micGeneration", self.js)
        self.assertIn('localStorage.setItem(LS_MIC', self.js)
        self.assertIn("btn-mic-toggle", self.js)

    def test_05_mic_off_blocks_speech_submission(self):
        self.assertIn("if (!state.handsFree) return", self.js)
        self.assertIn("late callback after mute", self.js)
        self.assertIn("genAtCallback !== micGeneration", self.js)
        # Push-to-talk also gated when header mic off.
        self.assertRegex(
            self.js,
            r"if \(!state\.handsFree\) \{\s*// Header MIC OFF gates all speech input",
        )

    def test_06_speaker_independent_of_mic(self):
        self.assertIn("setSpeakerEnabled(!state.voiceEnabled)", self.js)
        self.assertIn("setMicEnabled(!state.handsFree)", self.js)
        # Early gate of speakText depends only on voiceEnabled.
        m = re.search(
            r"function speakText\(text\) \{\s*if \(!state\.voiceEnabled \|\| !text\) return;",
            self.js,
        )
        self.assertIsNotNone(m)
        # Mic mute must not require speaker mute and vice versa.
        self.assertIn("function setSpeakerEnabled(on)", self.js)
        self.assertIn("function setMicEnabled(on)", self.js)
        set_spk = self.js.split("function setSpeakerEnabled", 1)[1].split("function ", 1)[0]
        set_mic = self.js.split("function setMicEnabled", 1)[1].split("function ", 1)[0]
        self.assertNotIn("handsFree", set_spk)
        self.assertNotIn("voiceEnabled", set_mic)

    def test_07_typed_commands_not_gated_by_mic(self):
        form = self.js.split('commandForm.addEventListener("submit"', 1)[1][:400]
        self.assertIn("executeGoal(commandInput.value)", form)
        self.assertNotIn("handsFree", form)

    def test_08_busy_and_overlap_guards(self):
        self.assertIn("commandInFlight", self.js)
        self.assertIn('state.coreState === "EXECUTING"', self.js)
        self.assertIn("Ignoring speech while typed input is active", self.js)

    def test_09_css_muted_state(self):
        self.assertIn(".header-icon-btn.muted", self.css)
        self.assertIn(".header-icon-btn.active-listen", self.css)

    def test_10_voice_engines_untouched(self):
        # PART B must not modify STT/TTS engine modules.
        # Compare against presence of core listen/cinematic/whisper files only
        # (content change detection is via git status in the report; here we
        # assert dashboard gating lives in app.js, not in engine modules).
        for path in VOICE_PY:
            src = path.read_text(encoding="utf-8")
            self.assertNotIn("btn-mic-toggle", src)
            self.assertNotIn("doom_dashboard_mic_enabled", src)
            self.assertNotIn("setMicEnabled", src)

    def test_11_no_security_coupling(self):
        # Audio prefs must not touch ASK / identity / plan hash.
        audio_block = self.js.split("LS_SPEAKER", 1)[1].split("// 8.", 1)[0]
        for needle in (
            "owner_id",
            "plan_hash",
            "authorized_plan_hash",
            "ExecutionIdentity",
            "PROACTIVE_COMPUTER_BROWSER",
            "subprocess",
        ):
            self.assertNotIn(needle, audio_block)


if __name__ == "__main__":
    unittest.main()
