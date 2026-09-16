"""V8.17 owner-scoped explicit personal memory. Deterministic. No embeddings."""

from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

MAX_CONTENT_CHARS = 280
MAX_MEMORIES_PER_OWNER = 40
MAX_RECALL_ITEMS = 4
MAX_FACT_KEY_CHARS = 96

_SECRETISH = re.compile(
    r"(?i)("
    r"api[_-]?key|bearer\b|password|passwd|\bpwd\b|"
    r"cookie|csrf|private[_ -]?key|authorization|"
    r"session_id|session_id_hash|ask_session|doom_ask|"
    r"executionidentity|owner_id\s*[:=]|plan_hash|"
    r"authorized_plan_hash|computer_session|browser_session|\bbws_|"
    r"csrf_token|postgres|database.{0,12}(user|pass|cred)|"
    r"-----BEGIN|hidden system prompt|internal (security )?policy|"
    r"\bsecrets?\b|supersecret|sk-[a-z0-9]{8,}"
    r")"
)
_SQLISH = re.compile(r"(?i)\b(select|insert|update|delete)\b.+\bfrom\b|\bcreate table\b")
_SAVE_PREFIX = re.compile(
    r"^\s*(please\s+)?(remember|save|store)\s+(that\s+|this\s*:?\s*|the\s+fact\s+that\s+)?",
    re.IGNORECASE,
)
_STOP = frozenset({
    "a", "an", "the", "is", "are", "am", "was", "were", "be", "been", "being",
    "my", "me", "i", "you", "your", "about", "that", "this", "to", "of", "and",
    "or", "for", "in", "on", "with", "do", "did", "what", "which", "who",
    "favorite", "favourite", "please", "remember", "save", "store",
})

_LOCK = threading.Lock()
_TEST_ROWS: Dict[str, List[Dict[str, Any]]] = {}
_USE_TEST_STORE = False


@dataclass(frozen=True)
class PersonalMemoryHit:
    content: str
    relevance: float
    fact_key: str


def use_test_personal_memory_store(enabled: bool = True) -> None:
    global _USE_TEST_STORE
    with _LOCK:
        _USE_TEST_STORE = bool(enabled)
        if enabled:
            _TEST_ROWS.clear()


def reset_personal_memory_for_tests() -> None:
    with _LOCK:
        _TEST_ROWS.clear()


def is_sensitive_memory_content(text: str) -> bool:
    blob = str(text or "")
    if not blob.strip():
        return True
    if _SECRETISH.search(blob):
        return True
    if _SQLISH.search(blob):
        return True
    if "\x00" in blob:
        return True
    return False


def extract_memory_content(raw_intent: str) -> str:
    text = " ".join(str(raw_intent or "").replace("\x00", "").split()).strip()
    if not text:
        return ""
    cleaned = _SAVE_PREFIX.sub("", text, count=1).strip()
    if cleaned.lower().startswith("that "):
        cleaned = cleaned[5:].strip()
    # Normalize trailing punctuation; keep one sentence-final period.
    cleaned = cleaned.rstrip(" .!?")
    if cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    if cleaned:
        cleaned = cleaned[: max(0, MAX_CONTENT_CHARS - 1)].rstrip() + "."
    return cleaned[:MAX_CONTENT_CHARS]


_PREF_IS = re.compile(
    r"\b(?:my\s+)?favou?rite\s+(.+?)\s+is\s+",
    re.IGNORECASE,
)
_PREFER = re.compile(r"\bi prefer\s+(.+)$", re.IGNORECASE)
_BUILDING = re.compile(r"\bi(?:'m| am)\s+building\b", re.IGNORECASE)
_PROJECT_CALLED = re.compile(r"\bmy project is called\b", re.IGNORECASE)


def derive_fact_key(content: str) -> str:
    text = str(content or "").strip()
    low = text.lower()
    pref = _PREF_IS.search(text)
    if pref:
        topic = re.sub(r"[^a-z0-9\s]+", " ", pref.group(1).lower())
        tokens = [t for t in topic.split() if t and t not in _STOP]
        if tokens:
            return ("favorite_" + "_".join(tokens[:6]))[:MAX_FACT_KEY_CHARS]
    prefer = _PREFER.search(text)
    if prefer:
        topic = re.sub(r"[^a-z0-9\s]+", " ", prefer.group(1).lower())
        tokens = [t for t in topic.split() if t and t not in _STOP]
        if tokens:
            return ("prefer_" + "_".join(tokens[:6]))[:MAX_FACT_KEY_CHARS]
    if _BUILDING.search(text):
        return "building_project"
    if _PROJECT_CALLED.search(text):
        return "project_name"
    text = re.sub(r"[^a-z0-9\s]+", " ", low)
    tokens = [t for t in text.split() if t and t not in _STOP]
    if not tokens:
        digest = hashlib.sha256(low.encode("utf-8")).hexdigest()[:16]
        return f"fact:{digest}"
    key = "_".join(tokens[:8])
    return key[:MAX_FACT_KEY_CHARS]


def _tokenize(text: str) -> List[str]:
    raw = re.sub(r"[^a-z0-9\s]+", " ", str(text or "").lower())
    return [t for t in raw.split() if t and t not in _STOP]


def score_memory(query: str, content: str) -> float:
    q = _tokenize(query)
    c = _tokenize(content)
    if not c:
        return 0.0
    if not q:
        return 0.15
    qset = set(q)
    cset = set(c)
    overlap = len(qset & cset)
    if overlap == 0:
        # Phrase containment for short recalls.
        ql = " ".join(q)
        cl = " ".join(c)
        if ql and ql in cl:
            return 0.55
        return 0.0
    return min(1.0, overlap / max(len(qset), 1))


def ensure_personal_memory_schema() -> bool:
    try:
        from database.postgres_db import postgres_manager
        if not postgres_manager.is_connected():
            return False
        with postgres_manager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS v8_personal_memories (
                        memory_id VARCHAR(64) PRIMARY KEY,
                        owner_id VARCHAR(64) NOT NULL,
                        fact_key VARCHAR(96) NOT NULL,
                        content TEXT NOT NULL,
                        created_at DOUBLE PRECISION NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL,
                        UNIQUE (owner_id, fact_key)
                    );
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_v8_pm_owner_updated "
                    "ON v8_personal_memories (owner_id, updated_at DESC);"
                )
            conn.commit()
        return True
    except Exception:
        return False


def count_memories(owner_id: str) -> int:
    owner = str(owner_id or "").strip()[:64]
    if not owner:
        return 0
    if _USE_TEST_STORE:
        with _LOCK:
            return len(_TEST_ROWS.get(owner, []))
    try:
        ensure_personal_memory_schema()
        from database.postgres_db import postgres_manager
        with postgres_manager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM v8_personal_memories WHERE owner_id = %s",
                    (owner,),
                )
                row = cur.fetchone()
                return int(row[0] if row else 0)
    except Exception:
        return 0


def _confirmation_message(content: str) -> str:
    body = str(content or "").rstrip(".").strip()
    if not body:
        return "Understood. I'll remember that."
    # Keep capital "I ..." ; otherwise drop leading capital for natural "that …".
    if body.startswith("I ") or body.startswith("I'm "):
        phrase = body
    else:
        phrase = body[0].lower() + body[1:]
    return f"Understood. I'll remember that {phrase}."


def lookup_personal_memory_id(owner_id: str, fact_key: str) -> str:
    """Return memory_id for owner+fact_key, or empty string. Read-only."""
    owner = str(owner_id or "").strip()[:64]
    key = str(fact_key or "").strip()[:MAX_FACT_KEY_CHARS]
    if not owner or not key:
        return ""
    if _USE_TEST_STORE:
        with _LOCK:
            for row in _TEST_ROWS.get(owner, []):
                if str(row.get("fact_key") or "") == key:
                    return str(row.get("memory_id") or "")
        return ""
    try:
        rows = _load_owner_rows(owner)
        for row in rows:
            if str(row.get("fact_key") or "") == key:
                return str(row.get("memory_id") or "")
    except Exception:
        return ""
    return ""


def save_personal_memory(owner_id: str, raw_intent: str) -> Tuple[bool, str, str]:
    """Return (ok, code, safe_message). Never returns secrets."""
    owner = str(owner_id or "").strip()[:64]
    if not owner:
        return False, "IDENTITY_REQUIRED", "Your memory could not be saved."
    content = extract_memory_content(raw_intent)
    if not content:
        return False, "EMPTY_MEMORY", "Your memory could not be saved."
    if is_sensitive_memory_content(content) or is_sensitive_memory_content(raw_intent):
        return False, "SENSITIVE_REJECTED", (
            "I cannot store passwords, API keys, session material, or other sensitive credentials."
        )
    fact_key = derive_fact_key(content)
    now = time.time()
    confirm = _confirmation_message(content)

    if _USE_TEST_STORE:
        with _LOCK:
            rows = _TEST_ROWS.setdefault(owner, [])
            for row in rows:
                if row["fact_key"] == fact_key:
                    row["content"] = content
                    row["updated_at"] = now
                    return True, "UPDATED", confirm
            if len(rows) >= MAX_MEMORIES_PER_OWNER:
                return False, "MEMORY_LIMIT", (
                    "Your memory limit has been reached. I did not save this entry."
                )
            rows.append({
                "memory_id": f"pm_{uuid.uuid4().hex[:16]}",
                "owner_id": owner,
                "fact_key": fact_key,
                "content": content,
                "created_at": now,
                "updated_at": now,
            })
        return True, "SAVED", confirm

    try:
        ensure_personal_memory_schema()
        from database.postgres_db import postgres_manager
        with postgres_manager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT memory_id FROM v8_personal_memories "
                    "WHERE owner_id = %s AND fact_key = %s",
                    (owner, fact_key),
                )
                existing = cur.fetchone()
                if existing:
                    cur.execute(
                        "UPDATE v8_personal_memories SET content = %s, updated_at = %s "
                        "WHERE owner_id = %s AND fact_key = %s",
                        (content, now, owner, fact_key),
                    )
                    conn.commit()
                    return True, "UPDATED", confirm
                cur.execute(
                    "SELECT COUNT(*) FROM v8_personal_memories WHERE owner_id = %s",
                    (owner,),
                )
                count = int(cur.fetchone()[0] or 0)
                if count >= MAX_MEMORIES_PER_OWNER:
                    return False, "MEMORY_LIMIT", (
                        "Your memory limit has been reached. I did not save this entry."
                    )
                mid = f"pm_{uuid.uuid4().hex[:16]}"
                cur.execute(
                    "INSERT INTO v8_personal_memories "
                    "(memory_id, owner_id, fact_key, content, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (mid, owner, fact_key, content, now, now),
                )
            conn.commit()
        return True, "SAVED", confirm
    except Exception:
        return False, "STORE_FAILED", "Your memory could not be saved."


def list_personal_memories(owner_id: str, *, limit: int = MAX_RECALL_ITEMS) -> Tuple[PersonalMemoryHit, ...]:
    owner = str(owner_id or "").strip()[:64]
    if not owner:
        return ()
    lim = max(1, min(int(limit or MAX_RECALL_ITEMS), MAX_RECALL_ITEMS))
    rows = _load_owner_rows(owner)
    rows = sorted(rows, key=lambda r: float(r.get("updated_at") or 0), reverse=True)
    out: List[PersonalMemoryHit] = []
    for row in rows[:lim]:
        content = str(row.get("content") or "")[:MAX_CONTENT_CHARS]
        if is_sensitive_memory_content(content):
            continue
        out.append(PersonalMemoryHit(content=content, relevance=1.0, fact_key=str(row.get("fact_key") or "")[:MAX_FACT_KEY_CHARS]))
    return tuple(out)


def search_personal_memories(
    owner_id: str,
    query: str,
    *,
    limit: int = MAX_RECALL_ITEMS,
) -> Tuple[PersonalMemoryHit, ...]:
    owner = str(owner_id or "").strip()[:64]
    if not owner:
        return ()
    lim = max(1, min(int(limit or MAX_RECALL_ITEMS), MAX_RECALL_ITEMS))
    q = str(query or "")[:512]
    # Broad recall prompts: return recent memories.
    low = q.lower()
    if any(p in low for p in (
        "what do you remember",
        "what did i ask you to remember",
        "what have you remembered",
        "remember about me",
    )):
        return list_personal_memories(owner, limit=lim)

    scored: List[PersonalMemoryHit] = []
    for row in _load_owner_rows(owner):
        content = str(row.get("content") or "")[:MAX_CONTENT_CHARS]
        if is_sensitive_memory_content(content):
            continue
        rel = score_memory(q, content)
        if rel <= 0:
            continue
        scored.append(PersonalMemoryHit(
            content=content,
            relevance=rel,
            fact_key=str(row.get("fact_key") or "")[:MAX_FACT_KEY_CHARS],
        ))
    scored.sort(key=lambda h: (-h.relevance, h.fact_key, h.content))
    return tuple(scored[:lim])


# Narrow personal-fact questions only — not general conversation bypass.
_SIMPLE_PERSONAL_FACT_Q = re.compile(
    r"(?ix)^\s*"
    r"(?:"
    r"(?:what|which)\s+(?:programming\s+)?language\s+do\s+i\s+prefer"
    r"|what(?:'s|\s+is)\s+my\s+favou?rite\s+(?:programming\s+)?language"
    r"|which\s+(?:programming\s+)?language\s+do\s+i\s+(?:prefer|like)"
    r"|what(?:'s|\s+is)\s+my\s+favou?rite\s+(?:colour|color|theme|ui)"
    r"|what\s+(?:colour|color|theme|ui)\s+do\s+i\s+prefer"
    r"|what\s+am\s+i\s+building"
    r"|what(?:'s|\s+is)\s+my\s+(?:project(?:\s+name)?|favou?rite\s+project)"
    r"|what\s+project\s+am\s+i\s+(?:building|working\s+on)"
    r"|do\s+you\s+remember\s+what\s+i(?:'m|\s+am)\s+building"
    r")"
    r"\s*[?.!]?\s*$"
)

_FACT_KEY_DIRECT = (
    "favorite_",
    "favourite_",
    "prefer_",
    "building_",
    "project_",
)


def _user_facing_fact(content: str) -> str:
    """Rewrite stored first-person owner facts for a natural user reply."""
    text = " ".join(str(content or "").replace("\x00", "").split()).strip()
    if not text:
        return ""
    if is_sensitive_memory_content(text):
        return ""
    for pat, rep in (
        (r"(?i)^I am\b", "You are"),
        (r"(?i)^I'm\b", "You're"),
        (r"(?i)^I prefer\b", "You prefer"),
        (r"(?i)^My\b", "Your"),
    ):
        text = re.sub(pat, rep, text, count=1)
    return text[:MAX_CONTENT_CHARS]


def is_simple_personal_fact_question(query: str) -> bool:
    q = " ".join(str(query or "").split())
    return bool(q and _SIMPLE_PERSONAL_FACT_Q.match(q))


def try_direct_personal_fact_answer(owner_id: str, query: str) -> Optional[str]:
    """
    Narrow deterministic reply for exact personal-fact questions when an
    owner-scoped memory hit already answers them. Returns None to use Ollama.
    Never returns IDs, keys, or implementation details.
    """
    if not is_simple_personal_fact_question(query):
        return None
    owner = str(owner_id or "").strip()[:64]
    if not owner:
        return None
    hits = search_personal_memories(owner, query, limit=1)
    if not hits:
        return None
    hit = hits[0]
    key = str(hit.fact_key or "")
    if hit.relevance < 0.35 and not any(key.startswith(p) for p in _FACT_KEY_DIRECT):
        return None
    return _user_facing_fact(hit.content) or None


def _load_owner_rows(owner: str) -> List[Dict[str, Any]]:
    if _USE_TEST_STORE:
        with _LOCK:
            return [dict(r) for r in _TEST_ROWS.get(owner, [])]
    try:
        ensure_personal_memory_schema()
        from database.postgres_db import postgres_manager
        with postgres_manager.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT memory_id, owner_id, fact_key, content, created_at, updated_at "
                    "FROM v8_personal_memories WHERE owner_id = %s",
                    (owner,),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        return []
