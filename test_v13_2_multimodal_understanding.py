#!/usr/bin/env python
"""DOOM V13.2 — Real multimodal understanding tests (local fixtures; no cloud; HARD $0)."""

import io
import os
import sys
import unittest
from unittest.mock import patch
import zipfile
import zlib

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from core.cost_guard.types import CostClass, CostDecision, CostDecisionAction, CostReason, ResourceRequest, ResourceType  # noqa: E402
from core.v12.multimodal import PerceptionNormalizer, UnifiedPerceptualContext, MAX_ITEMS_PER_SESSION  # noqa: E402
from core.v13.multimodal_understanding import (  # noqa: E402
    DocumentUnderstanding, ImageUnderstanding, LocalOllamaVisionProvider, Status, VisionProvider, VisionStatus,
    document_percept, image_percept,
)

OWNER, SESSION = "mm13_owner", "mm13_sess"


def png(color=(30, 60, 200), size=(320, 200)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def checkerboard(size=256, cell=16):
    arr = np.indices((size, size)).sum(axis=0) // cell % 2 * 255
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(buf, "PNG")
    return buf.getvalue()


def qr_png(text):
    import cv2
    mat = cv2.QRCodeEncoder.create().encode(text)
    img = Image.fromarray(mat).resize((mat.shape[1] * 8, mat.shape[0] * 8), Image.NEAREST)
    padded = Image.new("L", (img.width + 80, img.height + 80), 255)
    padded.paste(img, (40, 40))
    buf = io.BytesIO()
    padded.save(buf, "PNG")
    return buf.getvalue()


def pdf(text_lines, compress=True, extra=b""):
    content = b"BT /F1 12 Tf " + b" ".join(b"(" + t.replace("(", "\\(").replace(")", "\\)").encode("latin-1") + b") Tj"
                                          for t in text_lines) + b" ET"
    body = zlib.compress(content) if compress else content
    filt = b"/Filter /FlateDecode " if compress else b""
    return (b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n2 0 obj << " + filt + b"/Length "
            + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream\nendobj\n" + extra + b"%%EOF")


def docx(paragraphs):
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


class FakeVision(VisionProvider):
    name = "ollama"
    model = "test-vision"

    def __init__(self, reply="A blue rectangle.", exc=None):
        self.reply, self.exc, self.prompts = reply, exc, []

    def available(self):
        return True

    def ask(self, image_bytes, prompt):
        self.prompts.append(prompt)
        if self.exc:
            raise self.exc
        return self.reply


class NoVision(VisionProvider):
    pass


class TestImages(unittest.TestCase):

    def test_1_metadata(self):
        a = ImageUnderstanding(vision=NoVision()).analyze(png(size=(320, 200)))
        self.assertEqual((a.status, a.format, a.width, a.height), (Status.OK, "png", 320, 200))
        self.assertEqual(a.properties["orientation"], "landscape")

    def test_2_low_level_visual_description_is_measured_and_labelled(self):
        blue = ImageUnderstanding(vision=NoVision()).analyze(png((30, 60, 200)))
        self.assertIn("blue", blue.properties["dominant_colors"])
        self.assertTrue(blue.low_level_description.startswith("low-level analysis:"))
        self.assertIn("very little detail", blue.low_level_description)
        board = ImageUnderstanding(vision=NoVision()).analyze(checkerboard())
        self.assertEqual(set(board.properties["dominant_colors"]), {"black", "white"})
        self.assertIn("grayscale", board.low_level_description)
        self.assertGreater(board.properties["edge_density"], blue.properties["edge_density"])
        self.assertEqual(blue, ImageUnderstanding(vision=NoVision()).analyze(png((30, 60, 200))), "deterministic")

    def test_2b_semantic_description_only_via_local_vision_model(self):
        none = ImageUnderstanding(vision=NoVision()).analyze(png(), describe=True)
        self.assertEqual(none.vision_status, VisionStatus.VISION_MODEL_UNAVAILABLE)
        self.assertEqual(none.vision_description, "")
        fake = FakeVision("A plain blue rectangle.")
        got = ImageUnderstanding(vision=fake).analyze(png(), describe=True)
        self.assertEqual((got.vision_status, got.vision_description), (VisionStatus.ANSWERED, "A plain blue rectangle."))
        self.assertIn("A plain blue rectangle.", got.summary())

    def test_3_question_answering(self):
        none = ImageUnderstanding(vision=NoVision()).analyze(png(), question="What colour is it?")
        self.assertEqual((none.vision_status, none.answer), (VisionStatus.VISION_MODEL_UNAVAILABLE, ""))
        fake = FakeVision("Blue.")
        got = ImageUnderstanding(vision=fake).analyze(png(), question="What colour is it?")
        self.assertEqual(got.answer, "Blue.")
        self.assertIn("What colour is it?", fake.prompts[0])

    def test_real_environment_reports_capability_truthfully(self):
        provider = LocalOllamaVisionProvider()
        has_vision = provider.available()
        result = ImageUnderstanding(vision=provider).analyze(png(), question="what is this?") if not has_vision else None
        if not has_vision:
            self.assertEqual(result.vision_status, VisionStatus.VISION_MODEL_UNAVAILABLE)
        remote = LocalOllamaVisionProvider(base_url="http://example.com:11434")
        self.assertFalse(remote.available(), "non-loopback vision endpoints are refused")
        remote.model = "forced"
        with patch("requests.post") as post, self.assertRaises(RuntimeError):
            remote.ask(png(), "describe")
        post.assert_not_called()

    def test_4_unsupported_and_5_corrupted(self):
        iu = ImageUnderstanding(vision=NoVision())
        self.assertEqual(iu.analyze(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>").status, Status.UNSUPPORTED)
        self.assertEqual(iu.analyze(png()[:40]).status, Status.CORRUPT)
        self.assertEqual(iu.analyze(b"").status, Status.CORRUPT)

    def test_qr_decoding_with_redaction(self):
        a = ImageUnderstanding(vision=NoVision()).analyze(qr_png("Meeting room 4B at 3pm"))
        self.assertEqual(a.qr_payloads, ("Meeting room 4B at 3pm",))
        secret = ImageUnderstanding(vision=NoVision()).analyze(qr_png("token=abcdef1234567890SECRET"))
        self.assertTrue(secret.qr_payloads)
        self.assertNotIn("abcdef1234567890SECRET", secret.qr_payloads[0])

    def test_12_failure_containment(self):
        failing = ImageUnderstanding(vision=FakeVision(exc=RuntimeError("model crashed"))).analyze(png(), describe=True)
        self.assertEqual((failing.status, failing.vision_status), (Status.OK, VisionStatus.FAILED))
        self.assertTrue(failing.low_level_description, "measured analysis survives a model failure")
        blocked = CostDecision(CostDecisionAction.BLOCK, CostClass.PAID, CostReason.PAID_PROVIDER_BLOCKED,
                               ResourceRequest(resource_type=ResourceType.LLM, provider="ollama"))

        class Guard:
            def decision(self, request):
                return blocked
        fake = FakeVision()
        cost = ImageUnderstanding(vision=fake, cost_guard=Guard()).analyze(png(), describe=True)
        self.assertEqual(cost.vision_status, VisionStatus.COST_BLOCKED)
        self.assertEqual(fake.prompts, [], "model never called when Cost Guard blocks")
        huge = Image.new("L", (8000, 6000))
        buf = io.BytesIO()
        huge.save(buf, "PNG")
        self.assertEqual(ImageUnderstanding(vision=NoVision()).analyze(buf.getvalue()).status, Status.TOO_LARGE)


class TestDocuments(unittest.TestCase):

    def test_6_metadata_and_7_text_extraction(self):
        du = DocumentUnderstanding()
        d = du.extract("plan.docx", docx(["Quarterly plan", "Ship V13 safely"]))
        self.assertEqual((d.status, d.doc_type, d.words), (Status.OK, "docx", 5))
        self.assertIn("Ship V13 safely", d.text)
        p = du.extract("report.pdf", pdf(["Revenue grew 12%", "Costs (fixed) stayed flat"]))
        self.assertEqual((p.status, p.pages), (Status.OK, 1))
        self.assertIn("Costs (fixed) stayed flat", p.text)
        raw = du.extract("raw.pdf", pdf(["Uncompressed text"], compress=False))
        self.assertIn("Uncompressed text", raw.text)
        t = du.extract("notes.md", b"# Notes\nbuy milk\n")
        self.assertEqual(t.status, Status.OK)

    def test_unsupported_documents_are_reported_not_faked(self):
        du = DocumentUnderstanding()
        scanned = du.extract("scan.pdf", b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n%%EOF")
        self.assertEqual(scanned.status, Status.UNSUPPORTED)
        self.assertIn("no OCR", scanned.detail)
        self.assertEqual(du.extract("secret.pdf", pdf(["x"], extra=b"/Encrypt 5 0 R\n")).status, Status.UNSUPPORTED)
        self.assertEqual(du.extract("x.xlsx", b"PK\x03\x04junk").status, Status.UNSUPPORTED)
        self.assertEqual(du.extract("bad.docx", b"not a zip").status, Status.CORRUPT)

    def test_11_secret_filtering_in_documents(self):
        d = DocumentUnderstanding().extract("cfg.docx", docx(["api_key=sk_live_ABCDEF1234567890", "normal line"]))
        self.assertNotIn("sk_live_ABCDEF1234567890", d.text)
        self.assertIn("normal line", d.text)

    def test_extraction_is_bounded(self):
        d = DocumentUnderstanding().extract("big.txt", ("word " * 20000).encode())
        self.assertLessEqual(len(d.text), 20000)
        self.assertEqual(DocumentUnderstanding().extract("huge.txt", b"x" * (11 * 1024 * 1024)).status, Status.TOO_LARGE)


class TestPerceptionIntegration(unittest.TestCase):

    def setUp(self):
        self.n = PerceptionNormalizer()
        self.ctx = UnifiedPerceptualContext()

    def test_8_9_owner_and_session_isolation_and_10_bounds(self):
        data = png()
        a = ImageUnderstanding(vision=FakeVision("A blue card.")).analyze(data, describe=True)
        self.ctx.add(image_percept(self.n, OWNER, SESSION, data, a))
        doc_bytes = docx(["Agenda: launch review"])
        ext = DocumentUnderstanding().extract("agenda.docx", doc_bytes)
        self.ctx.add(document_percept(self.n, OWNER, SESSION, ext, doc_bytes))
        context = self.ctx.to_context(OWNER, SESSION)
        joined = " ".join(str(v) for v in context.values())
        self.assertIn("A blue card.", joined)
        self.assertIn("Agenda: launch review", joined)
        self.assertEqual(self.ctx.to_context("intruder", SESSION), {})
        self.assertEqual(self.ctx.to_context(OWNER, "other"), {})
        for i in range(MAX_ITEMS_PER_SESSION + 8):
            b = png((i * 5 % 255, 10, 10))
            self.ctx.add(image_percept(self.n, OWNER, SESSION, b, ImageUnderstanding(vision=NoVision()).analyze(b)))
        self.assertLessEqual(len(self.ctx.items(OWNER, SESSION)), MAX_ITEMS_PER_SESSION)

    def test_screenshots_keep_privacy_boundary(self):
        item = self.n.screenshot(OWNER, SESSION, png())
        self.ctx.add(item)
        self.assertIn("content withheld", self.ctx.to_context(OWNER, SESSION)["percept_1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
