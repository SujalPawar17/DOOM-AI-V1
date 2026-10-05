"""V13.2 Real Multimodal Understanding (local only, HARD $0).

What this module really does, and nothing more:

Images (Pillow / OpenCV, deterministic):
  - safe decode with decompression-bomb guard; corrupt and unsupported inputs are reported
  - measured visual properties: size, orientation, colour profile, dominant colour names,
    brightness, contrast, detail level (edge density) -> a labelled *low-level* description
  - QR-code decoding (OpenCV) with secret redaction
  - semantic description and visual question answering ONLY through a local vision model
    (Ollama model whose capabilities include "vision"), guarded by Cost Guard and loopback-
    only. If no such model is installed the capability is reported as unavailable; nothing
    is guessed. Models are never downloaded by this module.

Documents (stdlib only):
  - text types (.txt .md .csv .json .log .py ...), DOCX (zip + WordprocessingML), and simple
    PDFs (uncompressed or Flate content streams, Tj/TJ text operators). Encrypted PDFs,
    CID/custom-encoded fonts and scanned PDFs (no OCR available) are reported as partial or
    unsupported.

Results enter the cognitive pipeline as V12.4 perceptual items (bounded, owner/session
scoped). Screenshots are never semantically analysed (privacy boundary kept from V12.4).
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_DOC_BYTES = 10 * 1024 * 1024
MAX_EXTRACTED_CHARS = 20_000
MAX_QR_CHARS = 200
TEXT_TYPES = {".txt", ".md", ".csv", ".json", ".log", ".py", ".yaml", ".yml", ".ini", ".xml", ".html"}


class Status(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    CORRUPT = "CORRUPT"
    TOO_LARGE = "TOO_LARGE"


class VisionStatus(str, Enum):
    ANSWERED = "ANSWERED"
    VISION_MODEL_UNAVAILABLE = "VISION_MODEL_UNAVAILABLE"
    COST_BLOCKED = "COST_BLOCKED"
    FAILED = "FAILED"
    NOT_REQUESTED = "NOT_REQUESTED"


def _redact(text: str) -> str:
    from orchestration.conversation.respond import _redact as v8_redact
    return v8_redact(str(text or ""))


# --- local vision model boundary ---------------------------------------------------------

class VisionProvider:
    """Interface: answer a prompt about an image, or raise."""
    name = "none"
    model = ""

    def available(self) -> bool:
        return False

    def ask(self, image_bytes: bytes, prompt: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class LocalOllamaVisionProvider(VisionProvider):
    """Uses a locally installed Ollama model that declares the 'vision' capability.
    Loopback only; never pulls/downloads models."""

    name = "ollama"

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout_s: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.model = ""
        self._checked = False

    def _loopback(self) -> bool:
        from core.cost_guard.hosts import hostname_from_url, is_loopback_host
        return is_loopback_host(hostname_from_url(self.base_url))

    def available(self) -> bool:
        if self._checked:
            return bool(self.model)
        self._checked = True
        if not self._loopback():
            return False
        try:
            import requests
            tags = requests.get(f"{self.base_url}/api/tags", timeout=2).json().get("models", [])
            for entry in tags:
                caps = entry.get("capabilities")
                if caps is None:
                    caps = requests.post(f"{self.base_url}/api/show", json={"model": entry["name"]},
                                         timeout=3).json().get("capabilities", [])
                if "vision" in (caps or []):
                    self.model = entry["name"]
                    return True
        except Exception:
            return False
        return False

    def ask(self, image_bytes: bytes, prompt: str) -> str:
        if not self._loopback() or not self.model:
            raise RuntimeError("no local vision model (loopback only)")
        import requests
        payload = {"model": self.model, "prompt": prompt, "stream": False,
                   "images": [base64.b64encode(image_bytes).decode("ascii")],
                   "options": {"temperature": 0.2, "num_predict": 200}}
        res = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout_s)
        res.raise_for_status()
        return str(res.json().get("response", ""))


# --- image analysis ----------------------------------------------------------------------------

_COLOR_NAMES = {
    "black": (0, 0, 0), "white": (255, 255, 255), "gray": (128, 128, 128), "red": (220, 30, 30),
    "orange": (240, 140, 20), "yellow": (240, 220, 30), "green": (40, 170, 60), "cyan": (40, 200, 210),
    "blue": (40, 70, 210), "purple": (130, 50, 170), "pink": (240, 130, 180), "brown": (130, 80, 40),
}


def _color_name(rgb) -> str:
    r, g, b = (int(c) for c in rgb)
    return min(_COLOR_NAMES, key=lambda n: sum((a - c) ** 2 for a, c in zip((r, g, b), _COLOR_NAMES[n])))


@dataclass(frozen=True)
class ImageAnalysis:
    status: Status
    detail: str = ""
    format: str = ""
    width: int = 0
    height: int = 0
    properties: Dict[str, Any] = field(default_factory=dict)
    low_level_description: str = ""
    qr_payloads: Tuple[str, ...] = ()
    vision_status: VisionStatus = VisionStatus.NOT_REQUESTED
    vision_description: str = ""
    answer: str = ""

    def summary(self) -> str:
        if self.status not in (Status.OK, Status.PARTIAL):
            return f"image ({self.status.value.lower()})"
        parts = [self.vision_description or self.low_level_description]
        if self.qr_payloads:
            parts.append("QR code: " + "; ".join(self.qr_payloads))
        return " | ".join(p for p in parts if p)[:240]


_IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*")


def _has_image_signature(data: bytes) -> bool:
    return data.startswith(_IMAGE_SIGNATURES) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")


class ImageUnderstanding:

    def __init__(self, vision: Optional[VisionProvider] = None, cost_guard: Any = None):
        self.vision = vision if vision is not None else LocalOllamaVisionProvider()
        if cost_guard is None:
            from core.cost_guard.guard import cost_guard as default_cost_guard
            cost_guard = default_cost_guard
        self.cost_guard = cost_guard

    def analyze(self, data: bytes, question: str = "", describe: bool = False) -> ImageAnalysis:
        if not isinstance(data, (bytes, bytearray)) or not data:
            return ImageAnalysis(Status.CORRUPT, "no image bytes")
        if len(data) > MAX_IMAGE_BYTES:
            return ImageAnalysis(Status.TOO_LARGE, f"image exceeds {MAX_IMAGE_BYTES} bytes")
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(bytes(data))) as probe:
                fmt = (probe.format or "").lower()
                width, height = probe.size
                if width * height > MAX_IMAGE_PIXELS:
                    return ImageAnalysis(Status.TOO_LARGE, "image has too many pixels", fmt, width, height)
                probe.verify()
            img = Image.open(io.BytesIO(bytes(data)))
            img.load()
        except UnidentifiedImageError:
            if _has_image_signature(bytes(data)):
                return ImageAnalysis(Status.CORRUPT, "recognised image signature but undecodable data")
            return ImageAnalysis(Status.UNSUPPORTED, "not a supported image format")
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            return ImageAnalysis(Status.TOO_LARGE, "decompression bomb refused")
        except Exception as exc:
            return ImageAnalysis(Status.CORRUPT, f"image could not be decoded ({type(exc).__name__})")
        try:
            props, description = self._properties(img)
            qr = self._qr(img)
        finally:
            img.close()
        analysis = ImageAnalysis(Status.OK, "", fmt, width, height, props, description, qr)
        if describe or question:
            analysis = self._vision(analysis, bytes(data), question, describe)
        return analysis

    @staticmethod
    def _properties(img) -> Tuple[Dict[str, Any], str]:
        import numpy as np
        rgb = img.convert("RGB")
        small = rgb.copy()
        small.thumbnail((256, 256))
        arr = np.asarray(small, dtype=np.float32)
        gray = arr.mean(axis=2)
        brightness = float(gray.mean() / 255.0)
        contrast = float(gray.std() / 128.0)
        sat = float((arr.max(axis=2) - arr.min(axis=2)).mean() / 255.0)
        quant = (arr // 64).astype(int).reshape(-1, 3)
        keys, counts = np.unique(quant, axis=0, return_counts=True)
        order = counts.argsort()[::-1]
        dominant = []
        for idx in order[:4]:
            name = _color_name(keys[idx] * 64 + 32)
            share = counts[idx] / counts.sum()
            if name not in [d[0] for d in dominant] and share >= 0.08:
                dominant.append((name, round(float(share), 2)))
        try:
            import cv2
            edges = cv2.Canny(gray.astype(np.uint8), 80, 160)
            edge_density = float((edges > 0).mean())
        except Exception:
            edge_density = 0.0
        w, h = img.size
        orientation = "square" if abs(w - h) <= 0.05 * max(w, h) else ("landscape" if w > h else "portrait")
        tone = "dark" if brightness < 0.3 else ("bright" if brightness > 0.7 else "medium-brightness")
        colourfulness = "grayscale" if sat < 0.06 else ("muted" if sat < 0.2 else "colourful")
        detail = "very little detail" if edge_density < 0.01 else ("moderate detail" if edge_density < 0.08 else "a lot of detail")
        props = {"orientation": orientation, "brightness": round(brightness, 3), "contrast": round(contrast, 3),
                 "saturation": round(sat, 3), "edge_density": round(edge_density, 4),
                 "dominant_colors": [d[0] for d in dominant], "mode": img.mode}
        colours = ", ".join(d[0] for d in dominant[:3]) or "mixed colours"
        description = (f"low-level analysis: a {tone}, {colourfulness} {orientation} image ({w}x{h}) "
                       f"dominated by {colours}, with {detail}")
        return props, description

    @staticmethod
    def _qr(img) -> Tuple[str, ...]:
        try:
            import cv2
            import numpy as np
            arr = np.asarray(img.convert("RGB"))[:, :, ::-1].copy()
            text, points, _ = cv2.QRCodeDetector().detectAndDecode(arr)
        except Exception:
            return ()
        if not text:
            return ()
        return (_redact(text)[:MAX_QR_CHARS],)

    def _vision(self, analysis: ImageAnalysis, data: bytes, question: str, describe: bool) -> ImageAnalysis:
        from dataclasses import replace
        from core.cost_guard.types import ResourceRequest, ResourceType
        if not self.vision.available():
            return replace(analysis, vision_status=VisionStatus.VISION_MODEL_UNAVAILABLE)
        host = "localhost" if getattr(self.vision, "name", "") == "ollama" else ""
        decision = self.cost_guard.decision(ResourceRequest(resource_type=ResourceType.LLM,
                                                            provider=self.vision.name, host=host,
                                                            capability="v13_vision"))
        if not decision.is_allow:
            return replace(analysis, vision_status=VisionStatus.COST_BLOCKED)
        try:
            description = analysis.vision_description
            if describe:
                description = _redact(self.vision.ask(data, "Describe this image briefly and factually. "
                                                            "If unsure, say so."))[:600]
            answer = ""
            if question:
                answer = _redact(self.vision.ask(data, f"Answer briefly about this image: {question[:300]} "
                                                       "If the image does not show it, say you can't tell."))[:600]
        except Exception as exc:
            return replace(analysis, vision_status=VisionStatus.FAILED, detail=type(exc).__name__)
        return replace(analysis, vision_status=VisionStatus.ANSWERED,
                       vision_description=description.strip(), answer=answer.strip())


# --- document extraction ---------------------------------------------------------------------

@dataclass(frozen=True)
class DocumentExtraction:
    status: Status
    name: str
    doc_type: str
    text: str = ""
    pages: int = 0
    words: int = 0
    detail: str = ""


_PDF_STREAM = re.compile(rb"<<(.*?)>>\s*stream\r?\n(.*?)\r?\nendstream", re.S)
_PDF_TEXT_OP = re.compile(rb"\((?:\\.|[^\\)])*\)\s*Tj|\[(?:.*?)\]\s*TJ", re.S)
_PDF_LITERAL = re.compile(rb"\(((?:\\.|[^\\)])*)\)")


def _pdf_unescape(raw: bytes) -> str:
    out, i = bytearray(), 0
    while i < len(raw):
        c = raw[i]
        if c == 0x5C and i + 1 < len(raw):
            n = raw[i + 1]
            mapping = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("("): b"(", ord(")"): b")", 0x5C: b"\\"}
            if n in mapping:
                out += mapping[n]
                i += 2
                continue
            if 48 <= n <= 55:
                j, digits = i + 1, b""
                while j < len(raw) and len(digits) < 3 and 48 <= raw[j] <= 55:
                    digits += bytes([raw[j]])
                    j += 1
                out.append(int(digits, 8) & 0xFF)
                i = j
                continue
            i += 1
            continue
        out.append(c)
        i += 1
    return out.decode("latin-1")


class DocumentUnderstanding:

    def extract(self, name: str, data: bytes) -> DocumentExtraction:
        name = str(name or "document")[:120]
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        if not isinstance(data, (bytes, bytearray)):
            return DocumentExtraction(Status.CORRUPT, name, ext, detail="no bytes")
        if len(data) > MAX_DOC_BYTES:
            return DocumentExtraction(Status.TOO_LARGE, name, ext, detail="document too large")
        data = bytes(data)
        try:
            if ext in TEXT_TYPES:
                return self._finish(name, ext, data.decode("utf-8", errors="replace"), 1, Status.OK)
            if ext == ".docx" or data[:2] == b"PK" and ext in ("", ".docx"):
                return self._docx(name, data)
            if ext == ".pdf" or data[:5] == b"%PDF-":
                return self._pdf(name, data)
        except Exception as exc:
            return DocumentExtraction(Status.CORRUPT, name, ext, detail=type(exc).__name__)
        return DocumentExtraction(Status.UNSUPPORTED, name, ext or "unknown",
                                  detail="no local extractor for this format")

    def _finish(self, name: str, ext: str, text: str, pages: int, status: Status, detail: str = "") -> DocumentExtraction:
        text = _redact(" ".join(text.replace("\x00", " ").split()) if ext != ".txt" else text.replace("\x00", ""))
        text = text[:MAX_EXTRACTED_CHARS]
        return DocumentExtraction(status, name, ext.lstrip(".") or "unknown", text, pages, len(text.split()), detail)

    def _docx(self, name: str, data: bytes) -> DocumentExtraction:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                info = z.getinfo("word/document.xml")
                if info.file_size > 20 * 1024 * 1024:
                    return DocumentExtraction(Status.TOO_LARGE, name, "docx", detail="document.xml too large")
                xml = z.read(info)
        except (zipfile.BadZipFile, KeyError):
            return DocumentExtraction(Status.CORRUPT, name, "docx", detail="not a valid DOCX package")
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        root = ElementTree.fromstring(xml)
        paragraphs = []
        for para in root.iter(f"{ns}p"):
            paragraphs.append("".join(t.text or "" for t in para.iter(f"{ns}t")))
        text = "\n".join(p for p in paragraphs if p.strip())
        return self._finish(name, ".docx", text, 0, Status.OK)

    def _pdf(self, name: str, data: bytes) -> DocumentExtraction:
        if b"/Encrypt" in data:
            return DocumentExtraction(Status.UNSUPPORTED, name, "pdf", detail="encrypted PDF")
        pages = len(re.findall(rb"/Type\s*/Page\b", data))
        chunks, undecodable = [], 0
        for header, body in _PDF_STREAM.findall(data):
            if b"/FlateDecode" in header:
                try:
                    body = zlib.decompress(body)
                except zlib.error:
                    undecodable += 1
                    continue
            elif b"/Filter" in header:
                undecodable += 1
                continue
            for op in _PDF_TEXT_OP.findall(body):
                chunks.append("".join(_pdf_unescape(lit) for lit in _PDF_LITERAL.findall(op)))
        text = "\n".join(c for c in chunks if c.strip())
        if not text.strip():
            return DocumentExtraction(Status.UNSUPPORTED, name, "pdf", pages=pages,
                                      detail="no extractable text (scanned, image-only or custom-encoded PDF; no OCR)")
        status = Status.PARTIAL if undecodable else Status.OK
        return self._finish(name, ".pdf", text, pages, status,
                            detail=f"{undecodable} streams not decodable" if undecodable else "")


# --- pipeline integration ---------------------------------------------------------------------

def image_percept(normalizer: Any, owner_id: str, session_id: str, data: bytes, analysis: ImageAnalysis,
                  source: str = "upload"):
    """V12.4 perceptual item whose caption is the (real) analysis summary."""
    return normalizer.image(owner_id, session_id, data, caption=analysis.summary(), source=source)


def document_percept(normalizer: Any, owner_id: str, session_id: str, extraction: DocumentExtraction,
                     data: bytes, source: str = "file"):
    """V12.4 document item carrying the extracted (redacted, bounded) text."""
    return normalizer.document(owner_id, session_id, extraction.name, data, source=source,
                               extracted_text=extraction.text if extraction.status in (Status.OK, Status.PARTIAL) else None)
