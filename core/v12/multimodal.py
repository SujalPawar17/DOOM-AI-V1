"""V12.4 Multimodal Context.

    Text | Voice transcript | Image | Screenshot | Document | Structured data | System event
      -> normalizers (deterministic, local, no models)
      -> PerceptualItem (modality, provenance, timestamp, privacy, deterministic id)
      -> UnifiedPerceptualContext (per owner+session, bounded, deduplicated, TTL)
      -> caller-context keys (percept_*) -> Context Fusion -> cognitive pipeline

Design rules:
- No live camera or microphone. STT is frozen: voice arrives only as a transcript
  produced by the existing STT, passed in by the caller.
- No vision/OCR model: images contribute metadata (format, dimensions) parsed from
  headers, plus an optional caller-supplied caption. Screenshots are always SENSITIVE
  and never contribute pixel-derived content.
- Payloads are bounded; summaries are bounded and secret-redacted; IDs are
  deterministic (identical content in the same owner+session deduplicates).
- Owner/session isolation: items are only ever retrieved for their own owner+session.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

MAX_PAYLOAD_BYTES = 5 * 1024 * 1024
MAX_SUMMARY_CHARS = 240
MAX_ITEMS_PER_SESSION = 32
ITEM_TTL_SECONDS = 3600.0
MAX_CONTEXT_PERCEPTS = 3
TEXT_DOCUMENT_TYPES = {".txt", ".md", ".csv", ".json", ".log", ".py", ".yaml", ".yml", ".ini"}


class Modality(str, Enum):
    TEXT = "TEXT"
    VOICE = "VOICE"
    IMAGE = "IMAGE"
    SCREENSHOT = "SCREENSHOT"
    DOCUMENT = "DOCUMENT"
    STRUCTURED_DATA = "STRUCTURED_DATA"
    SYSTEM_EVENT = "SYSTEM_EVENT"


class Privacy(str, Enum):
    PUBLIC = "PUBLIC"
    SENSITIVE = "SENSITIVE"


class PerceptionError(ValueError):
    pass


@dataclass(frozen=True)
class PerceptualItem:
    item_id: str
    owner_id: str
    session_id: str
    modality: Modality
    source: str
    timestamp: float
    summary: str
    content_digest: str
    size_bytes: int
    metadata: Tuple[Tuple[str, Any], ...]
    privacy: Privacy
    provenance: str

    def meta(self) -> Dict[str, Any]:
        return dict(self.metadata)


def _redact(text: str) -> str:
    from orchestration.conversation.respond import _redact as v8_redact
    return v8_redact(str(text or ""))


def _bounded_text(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = " ".join(_redact(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _scalar_meta(meta: Dict[str, Any]) -> Tuple[Tuple[str, Any], ...]:
    out = []
    for k, v in sorted((meta or {}).items())[:12]:
        if v is None or isinstance(v, (bool, int, float)):
            out.append((str(k)[:32], v))
        else:
            out.append((str(k)[:32], _bounded_text(str(v), 80)))
    return tuple(out)


# --- image header parsing (no decoding of pixel data) --------------------------

def image_info(data: bytes) -> Dict[str, Any]:
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return {"format": "png", "width": width, "height": height}
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return {"format": "gif", "width": width, "height": height}
    if data[:2] == b"\xff\xd8":
        i, n = 2, min(len(data), 1024 * 1024)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return {"format": "jpeg", "width": width, "height": height}
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + max(2, length)
        return {"format": "jpeg"}
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return {"format": "webp"}
    raise PerceptionError("unsupported or corrupt image")


# --- normalization -------------------------------------------------------------

class PerceptionNormalizer:
    """Turns raw local inputs into PerceptualItems deterministically."""

    def __init__(self, clock=time.time):
        self._clock = clock

    def _item(self, owner_id: str, session_id: str, modality: Modality, source: str, summary: str,
              digest_material: bytes, size: int, meta: Dict[str, Any], privacy: Privacy,
              timestamp: Optional[float]) -> PerceptualItem:
        if not owner_id or not session_id:
            raise PerceptionError("owner and session are required")
        if size > MAX_PAYLOAD_BYTES:
            raise PerceptionError("payload too large")
        digest = hashlib.sha256(digest_material).hexdigest()
        item_id = hashlib.sha256(
            f"{owner_id}|{session_id}|{modality.value}|{source}|{digest}".encode("utf-8")).hexdigest()[:24]
        return PerceptualItem(
            item_id=item_id, owner_id=owner_id, session_id=session_id, modality=modality,
            source=_bounded_text(source, 48) or "unknown", timestamp=float(timestamp or self._clock()),
            summary=summary, content_digest=digest, size_bytes=size, metadata=_scalar_meta(meta),
            privacy=privacy, provenance=f"v12.4:{modality.value.lower()}:{source[:24]}")

    def text(self, owner_id, session_id, text: str, source="user", timestamp=None) -> PerceptualItem:
        raw = str(text or "")
        if not raw.strip():
            raise PerceptionError("empty text")
        return self._item(owner_id, session_id, Modality.TEXT, source, _bounded_text(raw),
                          raw.encode("utf-8"), len(raw.encode("utf-8")), {"chars": len(raw)},
                          Privacy.PUBLIC, timestamp)

    def voice_transcript(self, owner_id, session_id, transcript: str, language: str = "",
                         confidence: Optional[float] = None, source="stt", timestamp=None) -> PerceptualItem:
        """Accepts a transcript produced by the existing (frozen) STT. Never records audio."""
        raw = str(transcript or "")
        if not raw.strip():
            raise PerceptionError("empty transcript")
        meta = {"language": language or "", "confidence": round(float(confidence), 3) if confidence is not None else None}
        return self._item(owner_id, session_id, Modality.VOICE, source, _bounded_text(raw),
                          raw.encode("utf-8"), len(raw.encode("utf-8")), meta, Privacy.PUBLIC, timestamp)

    def image(self, owner_id, session_id, data: bytes, caption: str = "", source="upload",
              timestamp=None) -> PerceptualItem:
        return self._image(owner_id, session_id, data, caption, source, Modality.IMAGE, timestamp)

    def screenshot(self, owner_id, session_id, data: bytes, source="screen", timestamp=None) -> PerceptualItem:
        """Screens may show private data: metadata only, always SENSITIVE, never captioned."""
        return self._image(owner_id, session_id, data, "", source, Modality.SCREENSHOT, timestamp)

    def _image(self, owner_id, session_id, data, caption, source, modality, timestamp) -> PerceptualItem:
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise PerceptionError("image bytes required")
        if len(data) > MAX_PAYLOAD_BYTES:
            raise PerceptionError("payload too large")
        info = image_info(bytes(data))
        dims = f" {info['width']}x{info['height']}" if "width" in info else ""
        label = "screenshot" if modality is Modality.SCREENSHOT else "image"
        summary = f"{label} ({info['format']}{dims})"
        if caption and modality is Modality.IMAGE:
            summary += f": {_bounded_text(caption, 160)}"
        privacy = Privacy.SENSITIVE if modality is Modality.SCREENSHOT else Privacy.PUBLIC
        return self._item(owner_id, session_id, modality, source, summary, bytes(data), len(data), info,
                          privacy, timestamp)

    def document(self, owner_id, session_id, name: str, data: bytes, source="file", timestamp=None,
                 extracted_text: Optional[str] = None) -> PerceptualItem:
        if not isinstance(data, (bytes, bytearray)):
            raise PerceptionError("document bytes required")
        if len(data) > MAX_PAYLOAD_BYTES:
            raise PerceptionError("payload too large")
        name = str(name or "document")[:120]
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        meta: Dict[str, Any] = {"name": name, "type": ext.lstrip(".") or "unknown"}
        if extracted_text is not None:
            # V13.2: text extracted by a local extractor (DOCX / PDF / text types)
            meta["words"] = len(extracted_text.split())
            meta["extracted"] = True
            summary = f"document {name} ({meta['words']} words): {_bounded_text(extracted_text, 160)}"
        elif ext in TEXT_DOCUMENT_TYPES:
            text = bytes(data).decode("utf-8", errors="replace")
            meta["words"] = len(text.split())
            meta["lines"] = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
            summary = f"document {name} ({meta['words']} words): {_bounded_text(text, 160)}"
        elif ext == ".pdf" or bytes(data[:5]) == b"%PDF-":
            meta["pages"] = len(re.findall(rb"/Type\s*/Page\b", bytes(data)))
            summary = f"document {name} (pdf, {meta['pages']} pages; content not extracted)"
        else:
            summary = f"document {name} ({len(data)} bytes; binary content not extracted)"
        return self._item(owner_id, session_id, Modality.DOCUMENT, source, summary, bytes(data), len(data),
                          meta, Privacy.PUBLIC, timestamp)

    def structured(self, owner_id, session_id, data: Any, label: str = "data", source="app",
                   timestamp=None) -> PerceptualItem:
        try:
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))  # strict JSON
        except (TypeError, ValueError) as exc:
            raise PerceptionError("structured data must be JSON-serializable") from exc
        size = len(canonical.encode("utf-8"))
        if isinstance(data, dict):
            shape = f"object with keys {', '.join(sorted(map(str, data))[:8])}"
        elif isinstance(data, list):
            shape = f"list of {len(data)} items"
        else:
            shape = type(data).__name__
        return self._item(owner_id, session_id, Modality.STRUCTURED_DATA, source,
                          _bounded_text(f"{label}: {shape}"), canonical.encode("utf-8"), size,
                          {"label": label}, Privacy.PUBLIC, timestamp)

    def system_event(self, owner_id, session_id, event_type: str, attributes: Optional[Dict[str, Any]] = None,
                     source="system", timestamp=None) -> PerceptualItem:
        attrs = {k: v for k, v in sorted((attributes or {}).items())}
        canonical = json.dumps({"type": event_type, "attrs": attrs}, sort_keys=True, default=str)
        detail = ", ".join(f"{k}={v}" for k, v in list(attrs.items())[:4])
        return self._item(owner_id, session_id, Modality.SYSTEM_EVENT, source,
                          _bounded_text(f"event {event_type}" + (f" ({detail})" if detail else "")),
                          canonical.encode("utf-8"), len(canonical), dict(attrs, event_type=event_type),
                          Privacy.PUBLIC, timestamp)


# --- unified perceptual context ------------------------------------------------

class UnifiedPerceptualContext:
    """Bounded, deduplicated, TTL-limited perceptual buffer per (owner, session)."""

    def __init__(self, clock=time.time, max_items: int = MAX_ITEMS_PER_SESSION, ttl: float = ITEM_TTL_SECONDS):
        self._clock = clock
        self._max = max_items
        self._ttl = ttl
        self._lock = threading.Lock()
        self._items: Dict[Tuple[str, str], Dict[str, PerceptualItem]] = {}

    def add(self, item: PerceptualItem) -> bool:
        """Returns False when the identical item is already present (deduplicated)."""
        key = (item.owner_id, item.session_id)
        with self._lock:
            bucket = self._items.setdefault(key, {})
            self._expire_locked(bucket)
            if item.item_id in bucket:
                return False
            bucket[item.item_id] = item
            while len(bucket) > self._max:
                oldest = min(bucket.values(), key=lambda i: (i.timestamp, i.item_id))
                del bucket[oldest.item_id]
            return True

    def _expire_locked(self, bucket: Dict[str, PerceptualItem]) -> None:
        now = self._clock()
        for item_id in [k for k, v in bucket.items() if now - v.timestamp > self._ttl]:
            del bucket[item_id]

    def items(self, owner_id: str, session_id: str) -> List[PerceptualItem]:
        with self._lock:
            bucket = self._items.get((owner_id, session_id), {})
            self._expire_locked(bucket)
            return sorted(bucket.values(), key=lambda i: (i.timestamp, i.item_id))

    def clear(self, owner_id: str, session_id: str) -> None:
        with self._lock:
            self._items.pop((owner_id, session_id), None)

    def to_context(self, owner_id: str, session_id: str, limit: int = MAX_CONTEXT_PERCEPTS) -> Dict[str, Any]:
        """Scalar caller-context entries for the most recent percepts. Sensitive items
        contribute only their modality and format, never content."""
        recent = self.items(owner_id, session_id)[-max(0, limit):]
        if not recent:
            return {}
        out: Dict[str, Any] = {"percept_count": len(self.items(owner_id, session_id))}
        for n, item in enumerate(reversed(recent), start=1):
            if item.privacy is Privacy.SENSITIVE:
                fmt = item.meta().get("format", "")
                out[f"percept_{n}"] = f"{item.modality.value.lower()} ({fmt}) [sensitive: content withheld]"
            else:
                out[f"percept_{n}"] = f"{item.modality.value.lower()}: {item.summary}"
        return out


def compose_caller_context(primary: Optional[Dict[str, Any]], perceptual: Dict[str, Any],
                           learned: Dict[str, Any], limit: int = 16) -> Dict[str, Any]:
    """Merge caller-context sources by priority so caller/monitoring keys can never be
    crowded out of the 16-key caller-context budget: primary > perceptual > learned."""
    out: Dict[str, Any] = {}
    for source in (primary or {}, perceptual or {}, learned or {}):
        for k, v in source.items():
            if len(out) >= limit:
                return out
            out.setdefault(k, v)
    return out
