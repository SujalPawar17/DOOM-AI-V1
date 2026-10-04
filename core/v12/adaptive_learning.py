"""V12.2 Adaptive Learning.

    experience / explicit statement
      -> outcome analysis -> pattern extraction -> candidate learning
      -> validation (evidence, confidence, sensitivity, secrets, bounds)
      -> learned-knowledge store (owner-scoped)  -> future retrieval (Context Fusion)

Builds on (does not replace) the V8.27 User Model, V8.28 Goal Experience store and
V11.4 Advanced Memory. Learned knowledge is kept in its own owner-scoped store with
explicit provenance, because the V8.27 User Model has no provenance for inferences.
It reaches cognition through the V11.8 caller-context channel (lowest precedence,
scalar, privacy-filtered).

Guarantees:
- owner-scoped and bounded (items per owner, evidence per item)
- never stores arbitrary conversation text: only bounded values extracted by
  explicit patterns, or structural experience signatures
- never infers or stores sensitive attributes; never stores secrets
- explainable (evidence + explanation per item) and reversible (forget)
- confidence-aware: inferences are promoted only with sufficient evidence;
  low-confidence inferences are never retrieved
- deterministic for a given input sequence and clock
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

MAX_ITEMS_PER_OWNER = 64
MAX_EVIDENCE_PER_ITEM = 8
MAX_VALUE_CHARS = 60
MAX_KEY_CHARS = 48
MAX_RETRIEVED = 6
DAY = 86400.0
EXPLICIT_STALE_AFTER = 180 * DAY
INFERRED_STALE_AFTER = 30 * DAY
PROMOTE_CONFIDENCE = 0.6
MIN_EVIDENCE = {"WORKFLOW": 3, "HABIT": 3, "GOAL_PATTERN": 2}
HABIT_MIN_SHARE = 0.7


class KnowledgeKind(str, Enum):
    FACT = "FACT"
    PREFERENCE = "PREFERENCE"
    HABIT = "HABIT"
    WORKFLOW = "WORKFLOW"
    GOAL_PATTERN = "GOAL_PATTERN"
    TEMPORARY_CONTEXT = "TEMPORARY_CONTEXT"
    LOW_CONFIDENCE_INFERENCE = "LOW_CONFIDENCE_INFERENCE"  # classification of unpromoted inferences


class KnowledgeStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    PROMOTED = "PROMOTED"
    SUPERSEDED = "SUPERSEDED"
    STALE = "STALE"
    FORGOTTEN = "FORGOTTEN"


class KnowledgeSource(str, Enum):
    USER_EXPLICIT = "USER_EXPLICIT"
    EXPERIENCE_INFERENCE = "EXPERIENCE_INFERENCE"


@dataclass(frozen=True)
class LearnedItem:
    owner_id: str
    kind: KnowledgeKind
    key: str
    value: str
    source: KnowledgeSource
    status: KnowledgeStatus
    confidence: float
    evidence: Tuple[str, ...]
    first_seen: float
    last_seen: float
    explanation: str
    session_id: str = ""
    expires_at: Optional[float] = None

    @property
    def classification(self) -> KnowledgeKind:
        if self.source is KnowledgeSource.EXPERIENCE_INFERENCE and self.status is KnowledgeStatus.CANDIDATE:
            return KnowledgeKind.LOW_CONFIDENCE_INFERENCE
        return self.kind

    def to_json(self) -> Dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["source"] = self.source.value
        data["status"] = self.status.value
        data["evidence"] = list(self.evidence)
        return data

    @staticmethod
    def from_json(data: Dict[str, Any]) -> "LearnedItem":
        return LearnedItem(
            owner_id=str(data["owner_id"]), kind=KnowledgeKind(data["kind"]), key=str(data["key"]),
            value=str(data["value"]), source=KnowledgeSource(data["source"]),
            status=KnowledgeStatus(data["status"]), confidence=float(data["confidence"]),
            evidence=tuple(data.get("evidence") or ()), first_seen=float(data["first_seen"]),
            last_seen=float(data["last_seen"]), explanation=str(data.get("explanation") or ""),
            session_id=str(data.get("session_id") or ""), expires_at=data.get("expires_at"),
        )


# --- safety filters ----------------------------------------------------------

_SENSITIVE = re.compile(
    r"(?i)\b("
    r"health|medical|medication|diagnos\w*|illness|disease|disorder|pregnan\w*|disabilit\w*|"
    r"therap\w*|mental|depress\w*|anxiety|religio\w*|church|mosque|temple|faith|"
    r"politic\w*|vote|voting|party|sexual\w*|gay|lesbian|bisexual|transgender|orientation|"
    r"ethnic\w*|race|racial|caste|nationality|immigra\w*|citizenship|criminal|arrest\w*|"
    r"salary|income|debt|loan|bank|credit|ssn|social security|passport|license number|"
    r"address|home address|phone|email|birthday|date of birth|dob|age|"
    r"password|passcode|pin|otp|token|secret|api[_ -]?key|cookie|csrf|credential"
    r")\b")
_KEY_SAFE = re.compile(r"[^a-z0-9_]+")


def _is_sensitive(*texts: str) -> bool:
    blob = " ".join(str(t or "") for t in texts)
    if _SENSITIVE.search(blob):
        return True
    try:
        from orchestration.user_model.policy import is_sensitive_profile_content
        return bool(is_sensitive_profile_content(blob))
    except Exception:
        return False


def _clean_value(text: str) -> str:
    value = " ".join(str(text or "").strip().strip(".!?,;:'\"").split())
    return value[:MAX_VALUE_CHARS]


def _key(*parts: str) -> str:
    raw = "_".join(str(p or "").lower() for p in parts if p)
    return _KEY_SAFE.sub("_", raw).strip("_")[:MAX_KEY_CHARS]


def _ref(prefix: str, text: str) -> str:
    """Evidence reference: a short digest, never the text itself."""
    return f"{prefix}:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"


# --- explicit-statement extraction -------------------------------------------

_V = r"[A-Za-z0-9][\w .+#/-]{0,58}?"
_FAVORITE = re.compile(rf"(?i)^\s*my\s+favou?rite\s+(?P<cat>[a-z][a-z ]{{1,30}}?)\s+is\s+(?P<val>{_V})\s*[.!]?\s*$")
_PREFER_OVER = re.compile(rf"(?i)^\s*i\s+(?:really\s+)?prefer\s+(?P<a>{_V})\s+(?:over|to|rather\s+than)\s+(?P<b>{_V})\s*[.!]?\s*$")
_PREFER_FOR = re.compile(rf"(?i)^\s*i\s+(?:really\s+)?prefer\s+(?:using\s+)?(?P<a>{_V})\s+for\s+(?P<d>{_V})\s*[.!]?\s*$")
_LIKE = re.compile(rf"(?i)^\s*i\s+(?:really\s+)?(?P<verb>like|love|enjoy|don'?t\s+like|do\s+not\s+like|dislike|hate)\s+(?P<a>{_V})\s*[.!]?\s*$")
_FACT = re.compile(rf"(?i)^\s*(?:remember\s+that\s+)?my\s+(?P<attr>[a-z][a-z ]{{1,24}}?)\s+is\s+(?P<val>{_V})\s*[.!]?\s*$")
_TEMPORARY = re.compile(
    rf"(?i)^\s*(?P<when>today|right\s+now|currently|this\s+week)\s*,?\s*i(?:'m|\s+am)\s+"
    rf"(?:working\s+on|focusing\s+on|busy\s+with)\s+(?P<val>{_V})\s*[.!]?\s*$")


def extract_statements(text: str) -> List[Tuple[KnowledgeKind, str, str, Optional[float]]]:
    """(kind, key, value, ttl_seconds) from explicit first-person statements only."""
    out: List[Tuple[KnowledgeKind, str, str, Optional[float]]] = []
    text = str(text or "")
    if len(text) > 240:
        return out
    m = _TEMPORARY.match(text)
    if m:
        ttl = 7 * DAY if m.group("when").lower().startswith("this") else 0.5 * DAY
        out.append((KnowledgeKind.TEMPORARY_CONTEXT, "current_focus", _clean_value(m.group("val")), ttl))
        return out
    m = _FAVORITE.match(text)
    if m:
        out.append((KnowledgeKind.PREFERENCE, _key("favorite", m.group("cat")), _clean_value(m.group("val")), None))
        return out
    m = _PREFER_OVER.match(text)
    if m:
        a, b = _clean_value(m.group("a")), _clean_value(m.group("b"))
        pair = sorted([a.lower(), b.lower()])
        out.append((KnowledgeKind.PREFERENCE, _key("choice", pair[0], "vs", pair[1]), a, None))
        return out
    m = _PREFER_FOR.match(text)
    if m:
        out.append((KnowledgeKind.PREFERENCE, _key("for", m.group("d")), _clean_value(m.group("a")), None))
        return out
    m = _LIKE.match(text)
    if m:
        verb = m.group("verb").lower()
        liked = not ("not" in verb or "n't" in verb or verb in ("dislike", "hate"))
        obj = _clean_value(m.group("a"))
        out.append((KnowledgeKind.PREFERENCE, _key("likes", obj), "yes" if liked else "no", None))
        return out
    m = _FACT.match(text)
    if m:
        out.append((KnowledgeKind.FACT, _key(m.group("attr")), _clean_value(m.group("val")), None))
    return out


# --- store -------------------------------------------------------------------

class LearningStore:
    """Thread-safe owner-scoped store; optional atomic JSON persistence (local file)."""

    def __init__(self, path: Optional[str] = None):
        self._lock = threading.RLock()
        self._items: Dict[str, List[LearnedItem]] = {}
        self._path = path
        if path and os.path.exists(path):
            self._load()

    def _load(self) -> None:
        with open(self._path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        for owner, rows in (raw.get("owners") or {}).items():
            self._items[owner] = [LearnedItem.from_json(r) for r in rows][:MAX_ITEMS_PER_OWNER]

    def _save_locked(self) -> None:
        if not self._path:
            return
        payload = {"schema": 1, "owners": {o: [i.to_json() for i in items] for o, items in self._items.items()}}
        directory = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".learning_", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, sort_keys=True)
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def items(self, owner_id: str) -> List[LearnedItem]:
        with self._lock:
            return list(self._items.get(owner_id, []))

    def put_all(self, owner_id: str, items: Iterable[LearnedItem]) -> None:
        with self._lock:
            kept = sorted(items, key=lambda i: (i.status is KnowledgeStatus.FORGOTTEN, -i.last_seen))
            self._items[owner_id] = _bounded(kept)
            self._save_locked()

    def owners(self) -> List[str]:
        with self._lock:
            return list(self._items)


def _bounded(items: List[LearnedItem]) -> List[LearnedItem]:
    """Keep at most MAX_ITEMS_PER_OWNER: active items first, most recent first."""
    rank = {KnowledgeStatus.PROMOTED: 0, KnowledgeStatus.CANDIDATE: 1, KnowledgeStatus.STALE: 2,
            KnowledgeStatus.SUPERSEDED: 3, KnowledgeStatus.FORGOTTEN: 4}
    ordered = sorted(items, key=lambda i: (rank[i.status], -i.last_seen))
    return ordered[:MAX_ITEMS_PER_OWNER]


# --- learning ----------------------------------------------------------------

def _bucket(ts: float) -> str:
    hour = time.localtime(ts).tm_hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def _signature(step_summary: Iterable[str]) -> str:
    steps = []
    for s in step_summary or ():
        parts = [p for p in str(s).split() if not p.startswith("status:")]
        if parts:
            steps.append(" ".join(parts[:2]))
    return " > ".join(steps)


class AdaptiveLearning:

    def __init__(self, store: Optional[LearningStore] = None, clock=time.time):
        if store is None:
            store = LearningStore(os.getenv("DOOM_LEARNING_STORE_PATH") or None)
        self.store = store
        self._clock = clock
        self._lock = threading.RLock()

    # -- explicit statements --------------------------------------------------

    def observe_utterance(self, owner_id: str, session_id: str, text: str) -> List[LearnedItem]:
        """Learn only from explicit first-person statements. Returns items changed."""
        if not owner_id:
            return []
        now = self._clock()
        changed: List[LearnedItem] = []
        with self._lock:
            items = self.store.items(owner_id)
            for kind, key, value, ttl in extract_statements(text):
                if not key or not value or _is_sensitive(key, value, text):
                    continue
                ref = _ref("utt", f"{owner_id}|{key}|{value.lower()}")
                if any(i.status is KnowledgeStatus.FORGOTTEN and i.key == key and i.value.lower() == value.lower()
                       and i.kind is kind for i in items):
                    # Forgotten explicitly: only a fresh, explicit restatement re-learns it.
                    items = [i for i in items if not (i.status is KnowledgeStatus.FORGOTTEN and i.key == key)]
                same = [i for i in items if i.kind is kind and i.key == key
                        and i.status in (KnowledgeStatus.PROMOTED, KnowledgeStatus.CANDIDATE, KnowledgeStatus.STALE)
                        and (kind is not KnowledgeKind.TEMPORARY_CONTEXT or i.session_id == session_id)]
                for old in same:
                    if old.value.lower() == value.lower():
                        continue
                    items[items.index(old)] = replace(
                        old, status=KnowledgeStatus.SUPERSEDED, last_seen=now,
                        explanation=f"{old.explanation} Superseded by a newer explicit statement.")
                existing = next((i for i in items if i.kind is kind and i.key == key
                                 and i.value.lower() == value.lower()
                                 and i.status is not KnowledgeStatus.SUPERSEDED
                                 and (kind is not KnowledgeKind.TEMPORARY_CONTEXT or i.session_id == session_id)), None)
                evidence = tuple(dict.fromkeys(((existing.evidence if existing else ()) + (ref,))))[-MAX_EVIDENCE_PER_ITEM:]
                item = LearnedItem(
                    owner_id=owner_id, kind=kind, key=key, value=value,
                    source=KnowledgeSource.USER_EXPLICIT,
                    status=KnowledgeStatus.PROMOTED, confidence=0.9, evidence=evidence,
                    first_seen=existing.first_seen if existing else now, last_seen=now,
                    explanation=("Stated explicitly by the user"
                                 + (f" ({len(evidence)} times)." if len(evidence) > 1 else ".")),
                    session_id=session_id if kind is KnowledgeKind.TEMPORARY_CONTEXT else "",
                    expires_at=(now + ttl) if ttl else None,
                )
                if existing:
                    items[items.index(existing)] = item
                else:
                    items.append(item)
                changed.append(item)
            if changed:
                self.store.put_all(owner_id, self._reconcile(items, now))
        return changed

    # -- experiences ----------------------------------------------------------

    def observe_experiences(self, owner_id: str, experiences: Iterable[Any]) -> List[LearnedItem]:
        """Re-derive inferred knowledge from the owner's (bounded) experiences."""
        if not owner_id:
            return []
        now = self._clock()
        from orchestration.experience.policy import normalize_owner_id
        owner_norm = normalize_owner_id(owner_id)
        groups: Dict[str, Dict[str, Any]] = {}
        seen_ids = set()
        for exp in experiences or ():
            if normalize_owner_id(getattr(exp, "owner_id", owner_id) or owner_id) != owner_norm:
                continue  # owner isolation even if a caller passes foreign records
            exp_id = str(getattr(exp, "experience_id", "") or "")
            if exp_id:
                if exp_id in seen_ids:
                    continue  # the same experience is one piece of evidence, not several
                seen_ids.add(exp_id)
            sig = _signature(getattr(exp, "step_summary", ()) or ())
            if not sig or _is_sensitive(sig):
                continue
            goal_tags = [t for t in (getattr(exp, "tags", ()) or ()) if str(t).startswith("goal_")]
            outcome = str(getattr(exp, "outcome", "") or "").upper()
            g = groups.setdefault(sig, {"n": 0, "ok": 0, "refs": [], "ts": [], "goal": set(), "blockers": []})
            g["n"] += 1
            g["ok"] += 1 if outcome.endswith("COMPLETED") else 0
            g["refs"].append("exp:" + str(getattr(exp, "experience_id", "") or "")[:24])
            g["ts"].append(float(getattr(exp, "created_at", 0) or 0))
            g["goal"].update(goal_tags)
            g["blockers"].extend(str(b) for b in (getattr(exp, "blockers", ()) or ()))

        inferred: List[LearnedItem] = []
        for sig, g in groups.items():
            refs = tuple(dict.fromkeys(g["refs"]))[-MAX_EVIDENCE_PER_ITEM:]
            first, last = min(g["ts"] or [now]), max(g["ts"] or [now])
            n, rate = g["n"], g["ok"] / g["n"]
            reliable = rate >= 0.67
            unreliable = rate <= 0.34
            if reliable or unreliable:
                value = "reliable" if reliable else "unreliable"
                detail = f"{g['ok']}/{n} succeeded"
                if unreliable and g["blockers"]:
                    detail += f"; usual blocker: {max(set(g['blockers']), key=g['blockers'].count)[:40]}"
                inferred.append(self._inferred(owner_id, KnowledgeKind.WORKFLOW, _key("workflow", sig), value,
                                               n, rate, refs, first, last, f"Workflow '{sig}': {detail}."))
            goal_key = sorted(g["goal"])[0] if g["goal"] else sig
            inferred.append(self._inferred(owner_id, KnowledgeKind.GOAL_PATTERN, _key("recurring", goal_key),
                                           "recurring", n, 1.0, refs, first, last,
                                           f"'{goal_key}' was pursued {n} times."))
            if g["ts"]:
                buckets = [_bucket(t) for t in g["ts"] if t > 0]
                if buckets:
                    top = max(set(buckets), key=buckets.count)
                    share = buckets.count(top) / len(buckets)
                    if share >= HABIT_MIN_SHARE:
                        inferred.append(self._inferred(
                            owner_id, KnowledgeKind.HABIT, _key("habit", goal_key), f"usually in the {top}",
                            len(buckets), share, refs, first, last,
                            f"'{goal_key}' happened in the {top} {buckets.count(top)}/{len(buckets)} times."))

        with self._lock:
            items = self.store.items(owner_id)
            forgotten = {(i.kind, i.key) for i in items if i.status is KnowledgeStatus.FORGOTTEN}
            keep = [i for i in items if i.source is not KnowledgeSource.EXPERIENCE_INFERENCE
                    or i.status is KnowledgeStatus.FORGOTTEN]
            fresh = []
            for item in inferred:
                if (item.kind, item.key) in forgotten:
                    continue
                prior = next((i for i in items if i.source is KnowledgeSource.EXPERIENCE_INFERENCE
                              and i.kind is item.kind and i.key == item.key
                              and i.status is not KnowledgeStatus.FORGOTTEN), None)
                if prior is not None:
                    item = replace(item, first_seen=min(prior.first_seen, item.first_seen))
                fresh.append(item)
            self.store.put_all(owner_id, self._reconcile(keep + fresh, now))
        return fresh

    def _inferred(self, owner_id, kind, key, value, n, consistency, refs, first, last, explanation) -> LearnedItem:
        confidence = round(min(0.95, 0.4 + 0.15 * (n - 1)) * consistency, 3)
        promoted = n >= MIN_EVIDENCE.get(kind.value, 3) and confidence >= PROMOTE_CONFIDENCE
        return LearnedItem(
            owner_id=owner_id, kind=kind, key=key, value=value,
            source=KnowledgeSource.EXPERIENCE_INFERENCE,
            status=KnowledgeStatus.PROMOTED if promoted else KnowledgeStatus.CANDIDATE,
            confidence=confidence, evidence=refs, first_seen=first, last_seen=last,
            explanation=explanation + ("" if promoted else " (insufficient evidence; not used)"),
        )

    # -- reconciliation, retrieval, transparency ----------------------------------

    def _reconcile(self, items: List[LearnedItem], now: float) -> List[LearnedItem]:
        out = []
        for i in items:
            if i.status in (KnowledgeStatus.PROMOTED, KnowledgeStatus.CANDIDATE):
                limit = EXPLICIT_STALE_AFTER if i.source is KnowledgeSource.USER_EXPLICIT else INFERRED_STALE_AFTER
                expired = i.expires_at is not None and now >= float(i.expires_at)
                if expired or now - i.last_seen > limit:
                    i = replace(i, status=KnowledgeStatus.STALE,
                                explanation=f"{i.explanation} Stale: not reaffirmed recently.")
            out.append(i)
        return _bounded(out)

    def retrieve(self, owner_id: str, session_id: str = "", query: str = "",
                 limit: int = MAX_RETRIEVED) -> Dict[str, Any]:
        """Scalar context entries (prefixed learned_) for promoted, non-stale knowledge."""
        if not owner_id:
            return {}
        now = self._clock()
        with self._lock:
            items = self._reconcile(self.store.items(owner_id), now)
        words = {w for w in re.findall(r"[a-z0-9]+", str(query or "").lower()) if len(w) > 2}
        usable = [i for i in items if i.status is KnowledgeStatus.PROMOTED
                  and (i.kind is not KnowledgeKind.TEMPORARY_CONTEXT or i.session_id == session_id)]

        def score(i: LearnedItem) -> Tuple[float, float]:
            overlap = len(words & set(re.findall(r"[a-z0-9]+", f"{i.key} {i.value}".lower())))
            return (overlap + i.confidence, i.last_seen)

        ranked = sorted(usable, key=score, reverse=True)[:max(0, min(limit, MAX_RETRIEVED))]
        return {f"learned_{i.kind.value.lower()}_{i.key}"[:64]: i.value for i in ranked}

    def explain(self, owner_id: str) -> List[Dict[str, Any]]:
        now = self._clock()
        with self._lock:
            items = self._reconcile(self.store.items(owner_id), now)
        return [{
            "classification": i.classification.value, "kind": i.kind.value, "key": i.key, "value": i.value,
            "status": i.status.value, "source": i.source.value, "confidence": i.confidence,
            "evidence_count": len(i.evidence), "explanation": i.explanation,
        } for i in items if i.status is not KnowledgeStatus.FORGOTTEN]

    def forget(self, owner_id: str, key: Optional[str] = None) -> int:
        """Reversible-by-user removal: forgotten knowledge is never retrieved or re-inferred."""
        now = self._clock()
        with self._lock:
            items = self.store.items(owner_id)
            count = 0
            for idx, i in enumerate(items):
                if i.status is not KnowledgeStatus.FORGOTTEN and (key is None or i.key == key):
                    items[idx] = replace(i, status=KnowledgeStatus.FORGOTTEN, last_seen=now,
                                         explanation="Forgotten at the user's request.")
                    count += 1
            if count:
                self.store.put_all(owner_id, items)
        return count
