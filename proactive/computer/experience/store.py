"""In-process V7.7 experience envelope. Not V5 memory_records. Not durable."""

from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

from proactive.computer.experience.types import ExperienceRecord

_LOCK = threading.Lock()
_RECORDS: Dict[str, ExperienceRecord] = {}
_ORDER: List[str] = []
_STREAM_TIP: Dict[str, str] = {}
_CONTENT_INDEX: Dict[str, str] = {}
_MAX = 200


def reset_experience_store_for_tests() -> None:
    with _LOCK:
        _RECORDS.clear()
        _ORDER.clear()
        _STREAM_TIP.clear()
        _CONTENT_INDEX.clear()


def last_hash(stream_id: str) -> str:
    with _LOCK:
        return str(_STREAM_TIP.get(str(stream_id or ""), "") or "")


def find_by_content_hash(content_hash: str) -> Optional[ExperienceRecord]:
    with _LOCK:
        eid = _CONTENT_INDEX.get(str(content_hash or ""))
        if not eid:
            return None
        return _RECORDS.get(eid)


def get_experience(experience_id: str) -> Optional[ExperienceRecord]:
    with _LOCK:
        return _RECORDS.get(str(experience_id or ""))


def list_experiences(sequence_id: str = "") -> Tuple[ExperienceRecord, ...]:
    with _LOCK:
        rows = [_RECORDS[i] for i in _ORDER if i in _RECORDS]
    if sequence_id:
        rows = [r for r in rows if r.sequence_id == sequence_id]
    return tuple(rows)


def put_experience(record: ExperienceRecord, stream_id: str) -> None:
    with _LOCK:
        _RECORDS[record.experience_id] = record
        _ORDER.append(record.experience_id)
        if stream_id:
            _STREAM_TIP[str(stream_id)] = record.experience_hash
        if record.content_hash and record.content_hash not in _CONTENT_INDEX:
            _CONTENT_INDEX[record.content_hash] = record.experience_id
        while len(_ORDER) > _MAX:
            old = _ORDER.pop(0)
            rec = _RECORDS.pop(old, None)
            if rec and _CONTENT_INDEX.get(rec.content_hash) == old:
                _CONTENT_INDEX.pop(rec.content_hash, None)
