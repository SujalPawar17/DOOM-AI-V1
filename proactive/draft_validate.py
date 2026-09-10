"""V6.2.8 deterministic draft validation. Fail closed. No repair."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from proactive.draft_contract import (
    BODY_MAX,
    BODY_NEWLINES_MAX,
    DRAFT_TYPES,
    ITEM_MAX,
    LIST_MAX,
    OUTPUT_KEYS,
    REFS_MAX,
    SUMMARY_MAX,
    TITLE_MAX,
    extract_json_object,
)

_SECRET_RE = re.compile(
    r"(gsk_[A-Za-z0-9]+|sk-[A-Za-z0-9]+|nvapi-[A-Za-z0-9]+|AKIA[A-Z0-9]{8,}"
    r"|Bearer\s+[A-Za-z0-9\-._~+/]+=*)",
    re.IGNORECASE,
)
_EXEC_RE = re.compile(
    r"(subprocess|os\.system|rm\s+-rf|DROP\s+TABLE|eval\s*\(|new\s+Function|"
    r"tool_calls|Popen\()",
    re.IGNORECASE,
)
_HTTP_WRITE_RE = re.compile(
    r"(['\"]method['\"]\s*:\s*['\"](POST|PUT|PATCH|DELETE)['\"])",
    re.IGNORECASE,
)
_AUTH_ASSIGN_RE = re.compile(
    r"\b(owner_id|param_hash|binding_hash|action_type|risk_class|privacy_class)\s*=",
    re.IGNORECASE,
)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _str(v: Any, cap: int) -> Optional[str]:
    if not isinstance(v, str):
        return None
    s = _CTRL_RE.sub("", v)
    if len(s) > cap:
        return None
    return s


def _str_list(v: Any, nmax: int, cap: int) -> Optional[List[str]]:
    if not isinstance(v, list):
        return None
    if len(v) > nmax:
        return None
    out: List[str] = []
    for item in v:
        if not isinstance(item, str):
            return None
        s = _CTRL_RE.sub("", item)
        if len(s) > cap:
            return None
        out.append(s)
    return out


def _unsafe_blob(s: str) -> Optional[str]:
    if _SECRET_RE.search(s):
        return "SECRET"
    if _EXEC_RE.search(s):
        return "CONTENT"
    if _HTTP_WRITE_RE.search(s):
        return "CONTENT"
    if _AUTH_ASSIGN_RE.search(s):
        return "CONTENT"
    return None


def validate_draft(
    text: str,
    expected_draft_type: str,
    evidence_ids: List[str],
    tool_calls: Optional[list] = None,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    if tool_calls:
        return False, None, "TOOL_CALLS"
    if not (text or "").strip():
        return False, None, "EMPTY"
    obj = extract_json_object(text)
    if obj is None:
        return False, None, "NOT_JSON"
    if set(obj.keys()) != set(OUTPUT_KEYS):
        extra = set(obj.keys()) - set(OUTPUT_KEYS)
        if extra:
            return False, None, "SCHEMA"
        return False, None, "SCHEMA"
    dt = obj.get("draft_type")
    if dt not in DRAFT_TYPES:
        return False, None, "ENUM"
    if dt != expected_draft_type:
        return False, None, "ENUM"
    title = _str(obj.get("title"), TITLE_MAX)
    summary = _str(obj.get("summary"), SUMMARY_MAX)
    body = _str(obj.get("body"), BODY_MAX)
    if title is None or summary is None or body is None:
        return False, None, "SIZE"
    if title.count("\n") > 0:
        return False, None, "SIZE"
    if body.count("\n") > BODY_NEWLINES_MAX:
        return False, None, "SIZE"
    warnings = _str_list(obj.get("warnings"), LIST_MAX, ITEM_MAX)
    uncertainties = _str_list(obj.get("uncertainties"), LIST_MAX, ITEM_MAX)
    refs = _str_list(obj.get("source_refs"), REFS_MAX, 64)
    if warnings is None or uncertainties is None or refs is None:
        return False, None, "SIZE"
    allowed = set(str(x) for x in (evidence_ids or []))
    for r in refs:
        if r not in allowed:
            return False, None, "PROVENANCE"
    blob = "\n".join([title, summary, body] + warnings + uncertainties)
    why = _unsafe_blob(blob)
    if why:
        return False, None, why
    payload = {
        "draft_type": dt,
        "title": title,
        "summary": summary,
        "body": body,
        "warnings": warnings,
        "uncertainties": uncertainties,
        "source_refs": refs,
    }
    return True, payload, ""
