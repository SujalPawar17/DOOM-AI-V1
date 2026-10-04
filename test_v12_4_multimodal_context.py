#!/usr/bin/env python
"""DOOM V12.4 — Multimodal Context tests (deterministic local fixtures; no camera/mic)."""

import ast
import os
import struct
import sys
import unittest
import zlib

os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.v12.multimodal import (  # noqa: E402
    Modality, Privacy, PerceptionError, PerceptionNormalizer, UnifiedPerceptualContext,
    compose_caller_context, MAX_ITEMS_PER_SESSION, MAX_SUMMARY_CHARS, MAX_PAYLOAD_BYTES,
)

OWNER, SESSION = "mm_owner", "mm_sess"


def png(width=800, height=600):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 10)) + chunk(b"IEND", b"")


def jpeg(width=640, height=480):
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


GIF = b"GIF89a" + struct.pack("<HH", 32, 16) + b"\x00" * 20
PDF = b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n2 0 obj << /Type /Page >> endobj\n3 0 obj << /Type /Pages >> endobj\n%%EOF"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class TestNormalization(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.n = PerceptionNormalizer(clock=self.clock)

    def test_text_and_voice(self):
        t = self.n.text(OWNER, SESSION, "Remind me about the budget review")
        self.assertEqual((t.modality, t.privacy), (Modality.TEXT, Privacy.PUBLIC))
        v = self.n.voice_transcript(OWNER, SESSION, "open my calendar", language="en", confidence=0.91)
        self.assertEqual(v.modality, Modality.VOICE)
        self.assertEqual(v.meta()["language"], "en")
        self.assertEqual(v.meta()["confidence"], 0.91)

    def test_images(self):
        self.assertEqual(self.n.image(OWNER, SESSION, png()).meta(), {"format": "png", "width": 800, "height": 600})
        self.assertEqual(self.n.image(OWNER, SESSION, jpeg()).meta()["width"], 640)
        self.assertEqual(self.n.image(OWNER, SESSION, GIF).meta()["height"], 16)
        captioned = self.n.image(OWNER, SESSION, png(), caption="whiteboard sketch of the plan")
        self.assertEqual(captioned.summary, "image (png 800x600): whiteboard sketch of the plan")

    def test_screenshot_is_sensitive_and_never_captioned(self):
        s = self.n.screenshot(OWNER, SESSION, png(1920, 1080))
        self.assertEqual(s.privacy, Privacy.SENSITIVE)
        self.assertEqual(s.summary, "screenshot (png 1920x1080)")

    def test_documents(self):
        doc = self.n.document(OWNER, SESSION, "notes.md", b"# Plan\nship v12\nreview tests\n")
        self.assertEqual(doc.meta()["words"], 6)
        self.assertEqual(doc.meta()["lines"], 3)
        pdf = self.n.document(OWNER, SESSION, "report.pdf", PDF)
        self.assertEqual(pdf.meta()["pages"], 2)
        self.assertIn("content not extracted", pdf.summary)
        binary = self.n.document(OWNER, SESSION, "blob.bin", b"\x00\x01\x02")
        self.assertIn("binary content not extracted", binary.summary)

    def test_structured_and_system_event(self):
        d = self.n.structured(OWNER, SESSION, {"cpu": 12, "tasks": [1, 2]}, label="metrics")
        self.assertEqual(d.summary, "metrics: object with keys cpu, tasks")
        e = self.n.system_event(OWNER, SESSION, "battery_low", {"percent": 9})
        self.assertEqual(e.summary, "event battery_low (percent=9)")
        self.assertEqual(e.meta()["event_type"], "battery_low")

    def test_deterministic_ids(self):
        a = self.n.text(OWNER, SESSION, "same")
        self.clock.t += 50
        b = self.n.text(OWNER, SESSION, "same")
        self.assertEqual(a.item_id, b.item_id, "ids ignore timestamps")
        self.assertNotEqual(a.item_id, self.n.text(OWNER, "other", "same").item_id)
        self.assertNotEqual(a.item_id, self.n.text("other", SESSION, "same").item_id)

    def test_bounds_and_errors(self):
        long = self.n.text(OWNER, SESSION, "word " * 1000)
        self.assertLessEqual(len(long.summary), MAX_SUMMARY_CHARS)
        with self.assertRaises(PerceptionError):
            self.n.text(OWNER, SESSION, "x" * (MAX_PAYLOAD_BYTES + 1))
        with self.assertRaises(PerceptionError):
            self.n.image(OWNER, SESSION, b"not an image at all")
        with self.assertRaises(PerceptionError):
            self.n.text(OWNER, SESSION, "   ")
        with self.assertRaises(PerceptionError):
            self.n.structured(OWNER, SESSION, {1, 2})  # a set is not JSON
        with self.assertRaises(PerceptionError):
            self.n.text("", SESSION, "hi")

    def test_secrets_are_redacted_from_summaries(self):
        t = self.n.text(OWNER, SESSION, "deploy with api_key=sk_live_abcdef123456 today")
        self.assertNotIn("sk_live_abcdef123456", t.summary)
        doc = self.n.document(OWNER, SESSION, "config.txt", b"password: hunter2hunter2\n")
        self.assertNotIn("hunter2hunter2", doc.summary)


class TestUnifiedContext(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.n = PerceptionNormalizer(clock=self.clock)
        self.ctx = UnifiedPerceptualContext(clock=self.clock)

    def test_dedup_and_ordering(self):
        self.assertTrue(self.ctx.add(self.n.text(OWNER, SESSION, "first")))
        self.assertFalse(self.ctx.add(self.n.text(OWNER, SESSION, "first")), "no duplicate context")
        self.clock.t += 1
        self.ctx.add(self.n.image(OWNER, SESSION, png()))
        self.assertEqual([i.modality for i in self.ctx.items(OWNER, SESSION)], [Modality.TEXT, Modality.IMAGE])
        context = self.ctx.to_context(OWNER, SESSION)
        self.assertEqual(context["percept_count"], 2)
        self.assertEqual(context["percept_1"], "image: image (png 800x600)")

    def test_bounded_and_ttl(self):
        for i in range(MAX_ITEMS_PER_SESSION + 10):
            self.clock.t += 1
            self.ctx.add(self.n.text(OWNER, SESSION, f"item {i}"))
        items = self.ctx.items(OWNER, SESSION)
        self.assertEqual(len(items), MAX_ITEMS_PER_SESSION)
        self.assertEqual(items[0].summary, "item 10", "oldest evicted first")
        self.clock.t += 3601
        self.assertEqual(self.ctx.items(OWNER, SESSION), [])

    def test_owner_and_session_isolation(self):
        self.ctx.add(self.n.text(OWNER, SESSION, "private plan"))
        self.assertEqual(self.ctx.to_context("intruder", SESSION), {})
        self.assertEqual(self.ctx.to_context(OWNER, "other_session"), {})

    def test_sensitive_content_withheld(self):
        self.ctx.add(self.n.screenshot(OWNER, SESSION, png(1280, 720)))
        self.assertEqual(self.ctx.to_context(OWNER, SESSION)["percept_1"],
                         "screenshot (png) [sensitive: content withheld]")

    def test_context_priority_keeps_monitoring_keys(self):
        monitoring = {"monitoring_trigger": True, "event_id": "e1", "event_type": "x", "priority": "HIGH",
                      "source": "monitor", "description": "d"}
        perceptual = {f"percept_{i}": "p" for i in range(1, 5)}
        learned = {f"learned_k{i}": "v" for i in range(10)}
        merged = compose_caller_context(monitoring, perceptual, learned)
        self.assertEqual(len(merged), 16)
        for k in monitoring:
            self.assertIn(k, merged)
        for k in perceptual:
            self.assertIn(k, merged)

    def test_no_capture_or_stt_dependencies(self):
        tree = ast.parse(open(os.path.join(PROJECT_ROOT, "core", "v12", "multimodal.py"), encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for banned in ("cv2", "sounddevice", "pyaudio", "PIL", "pyautogui", "mss", "core.stt", "speech_recognition"):
            self.assertFalse(any(m == banned or m.startswith(banned + ".") for m in imported), banned)


# --- pipeline integration -------------------------------------------------------

from test_v11_8_end_to_end_integration import V118TestBase  # noqa: E402
from test_v12_1_response_intelligence import FakeLocalModel  # noqa: E402
from core.v12.cognitive_orchestrator import V12CognitiveOrchestrator  # noqa: E402
from core.v12.adaptive_learning import AdaptiveLearning, LearningStore  # noqa: E402
from orchestration.conversation.respond import (  # noqa: E402
    use_respond_provider_for_tests, reset_respond_provider_for_tests,
)


class TestPipelineIntegration(V118TestBase):

    def setUp(self):
        super().setUp()
        self.orchestrator = V12CognitiveOrchestrator(learning=AdaptiveLearning(store=LearningStore()))
        use_respond_provider_for_tests(FakeLocalModel(["Noted."] * 10))

    def tearDown(self):
        reset_respond_provider_for_tests()
        super().tearDown()

    def test_percepts_reach_context_fusion(self):
        n = self.orchestrator.normalizer
        owner, session = "test_owner_v118", "test_sess_v118"
        self.orchestrator.perceive(n.document(owner, session, "agenda.md", b"standup at 10\n"))
        self.orchestrator.perceive(n.image(owner, session, png(), caption="team photo"))
        result = self.run_cycle("What did I just share?")
        fused = result["stages"]["context_fusion"]["fused_context"]
        self.assertEqual(fused.context.get("percept_count"), 2)
        self.assertIn("team photo", fused.context.get("percept_1", ""))
        self.assertEqual(fused.provenance["percept_1"].source, "caller_context")
        self.assertEqual(result["stages"]["perception"]["percept_keys"], ["percept_1", "percept_2", "percept_count"])
        # Another session sees none of it.
        other = self.run_cycle("What did I just share?", session_id="other_session")
        self.assertNotIn("percept_count", other["stages"]["context_fusion"]["fused_context"].context)

    def test_monitoring_keys_survive_with_full_context_budget(self):
        n = self.orchestrator.normalizer
        owner, session = "test_owner_v118", "test_sess_v118"
        for i in range(5):
            self.orchestrator.perceive(n.text(owner, session, f"note {i}"))
        for i in range(8):
            self.orchestrator.learning.observe_utterance(owner, session, f"My favorite thing{i} is value{i}")
        result = self.run_cycle("status", context={"monitoring_trigger": True, "event_id": "evt_mm"})
        fused = result["stages"]["context_fusion"]["fused_context"]
        self.assertIs(fused.context.get("monitoring_trigger"), True)
        self.assertEqual(fused.context.get("event_id"), "evt_mm")


if __name__ == "__main__":
    unittest.main(verbosity=2)
