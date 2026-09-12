"""V8.3 bounded rule-based planner. Untrusted proposal generator. Zero execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from orchestration.goal.catalog import lookup
from orchestration.goal.plan_errors import PlanValidationError
from orchestration.goal.plan_registry import MAX_DEPENDENCY_DEPTH, MAX_PARAM_CHARS, MAX_STEPS, MAX_TIMEOUT_MS
from orchestration.goal.plan_types import GoalPlan
from orchestration.goal.plan_validator import build_goal_plan
from orchestration.goal.planner_errors import PlannerStatus
from orchestration.goal.types import CapabilityClass, GoalSpec, IntentClass
from proactive.config import (
    is_computer_browser_enabled,
    is_computer_click_enabled,
    is_computer_filesystem_enabled,
    is_computer_observe_enabled,
    is_computer_type_enabled,
    is_v8_enabled,
)

MAX_PLANNING_ATTEMPTS = 1
PLANNER_TIMEOUT_MS = 8000
_TYPE_MAX = 256

_DEFERRED = frozenset({
    IntentClass.WORLD_ACTION,
})

_OBSERVE = re.compile(
    r"what('?s| is) on (my )?screen|\bobserve (the )?(screen|desktop)\b",
    re.I,
)
_CLICK = re.compile(r"\bclick\b|\bpress\b.+\bbutton\b", re.I)
_TYPE = re.compile(r"\btype\b", re.I)
_COORD = re.compile(r"\b(x|y)\s*[:=]\s*\d+\b", re.I)
_DELETE = re.compile(r"\b(delete|unlink|rmdir|rm\s+-rf)\b", re.I)
_WRITE = re.compile(r"\b(write|overwrite|create file|save to)\b", re.I)
_LIST = re.compile(r"\blist\b", re.I)
_READ = re.compile(r"\bread\b", re.I)
_CLICK_NAME = re.compile(
    r"(?:click|press)\s+(?:the\s+)?([A-Za-z0-9][A-Za-z0-9 _-]{0,39})\s+button"
    r"|(?:click|press)\s+([A-Za-z][A-Za-z0-9_]{2,39})\b",
    re.I,
)
_TYPE_TOO_LONG = re.compile(r"\btype\s+[\"'][^\"']{257,}", re.I)
_TYPE_QUOTED = re.compile(r"\btype\s+[\"']([^\"']{1,256})[\"']", re.I)
_TYPE_WORD = re.compile(r"\btype\s+([A-Za-z0-9 .,_-]{1,256}?)(?:\s+into|\s+in\b|$)", re.I)
_URL = re.compile(r"(https?://[^\s<>\"']+)", re.I)
_WIN_PATH = re.compile(r"([A-Za-z]:\\[^\s\"']+)")
_POSIX_PATH = re.compile(r"(/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]+)")
_QUOTED = re.compile(r"\"([^\"]{1,512})\"")
_BAD_TYPE = re.compile(
    r"\b(eval|exec|subprocess|shell|powershell|cmd\.exe|javascript|importlib|ctypes|"
    r"python|bash|cmd\s+/c|rm\s+-rf)\b"
    r"|`|\$\(|<script",
    re.I,
)
_TYPE_FIELD = re.compile(
    r"into (?:the )?([A-Za-z0-9][A-Za-z0-9 _-]{0,39})\s+field"
    r"|into\s+([A-Za-z][A-Za-z0-9_]{2,39})\b",
    re.I,
)
_TARGET_KEYS = ("automation_id", "runtime_id", "control_type", "name", "selected")


@dataclass(frozen=True)
class PlanProposal:
    status: PlannerStatus
    reason_code: str
    plan: Optional[GoalPlan]
    attempts: int = 1

    def as_public(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "reason_code": self.reason_code,
            "attempts": self.attempts,
            "execution_permitted": False,
            "plan": None if self.plan is None else self.plan.as_public(),
        }


def _fail(status: PlannerStatus, code: str) -> PlanProposal:
    return PlanProposal(status=status, reason_code=code, plan=None, attempts=MAX_PLANNING_ATTEMPTS)


def _base_step(capability: str, action: str, parameters: Dict[str, Any], *, verify: str = "") -> Dict[str, Any]:
    return {
        "step_id": "s1",
        "capability_id": capability,
        "action": action,
        "parameters": parameters,
        "dependencies": (),
        "verification_required": bool(verify),
        "verification_type": verify,
        "retry_count": 0,
        "timeout_ms": PLANNER_TIMEOUT_MS,
    }


def _conversation_steps(_goal: GoalSpec) -> List[Dict[str, Any]]:
    return [_base_step("conversation", "RESPOND", {})]


def _memory_steps(goal: GoalSpec) -> List[Dict[str, Any]]:
    query = str(goal.raw_intent or "")[:MAX_PARAM_CHARS]
    return [_base_step("memory_read", "RETRIEVE", {"query": query})]


def _has_computer_session(goal: GoalSpec) -> bool:
    return bool(str(goal.computer_session_id or "").strip())


def _structured_targets(planner_context: Any) -> List[Dict[str, str]]:
    """Typed observation rows only. Memory/experience text is ignored."""
    if not isinstance(planner_context, dict):
        return []
    raw = planner_context.get("structured_targets")
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[Dict[str, str]] = []
    for item in raw[:32]:
        if not isinstance(item, dict):
            continue
        row: Dict[str, str] = {}
        for key in _TARGET_KEYS:
            val = item.get(key)
            if val is None or callable(val):
                continue
            row[key] = str(val)[:128]
        aid = row.get("automation_id", "").strip()
        rid = row.get("runtime_id", "").strip()
        ctype = row.get("control_type", "").strip()
        name = row.get("name", "").strip()
        if (aid or rid) and ctype and name:
            out.append(row)
    return out


def _is_button_target(row: Dict[str, str]) -> bool:
    raw = str(row.get("control_type") or "").strip().lower()
    return raw in ("button", "50000")


def _is_edit_target(row: Dict[str, str]) -> bool:
    raw = str(row.get("control_type") or "").strip().lower()
    return raw in ("edit", "50004") or "edit" in raw


def _match_click_target(text: str, targets: Sequence[Dict[str, str]]) -> Tuple[Optional[Dict[str, str]], str]:
    named = _CLICK_NAME.search(text or "")
    label = ""
    if named:
        label = str(named.group(1) or named.group(2) or "").strip().lower()
    if not label or label in ("something", "it", "that", "this"):
        return None, "PLANNING_UNAVAILABLE"
    hits = [
        t for t in targets
        if str(t.get("name") or "").strip().lower() == label and _is_button_target(t)
    ]
    if len(hits) > 1:
        return None, "AMBIGUOUS_TARGET"
    if len(hits) != 1:
        return None, "PLANNING_UNAVAILABLE"
    hit = hits[0]
    if not str(hit.get("name") or "").strip():
        return None, "PLANNING_UNAVAILABLE"
    return hit, "OK"


def _match_type_target(text: str, targets: Sequence[Dict[str, str]]) -> Tuple[Optional[Dict[str, str]], str]:
    field = _TYPE_FIELD.search(text or "")
    if field:
        label = str(field.group(1) or field.group(2) or "").strip().lower()
        if label not in ("selected", "this", "that", "current"):
            hits = [
                t for t in targets
                if str(t.get("name") or "").strip().lower() == label
                and _is_edit_target(t)
            ]
            if len(hits) > 1:
                return None, "AMBIGUOUS_TARGET"
            if len(hits) != 1:
                return None, "PLANNING_UNAVAILABLE"
            return hits[0], "OK"
    selected = [t for t in targets if str(t.get("selected") or "").lower() in ("1", "true", "yes")]
    if len(selected) > 1:
        return None, "AMBIGUOUS_TARGET"
    if len(selected) == 1:
        if not _is_edit_target(selected[0]):
            return None, "PLANNING_UNAVAILABLE"
        return selected[0], "OK"
    edits = [t for t in targets if _is_edit_target(t)]
    if len(edits) > 1:
        return None, "AMBIGUOUS_TARGET"
    if len(edits) != 1:
        return None, "PLANNING_UNAVAILABLE"
    return edits[0], "OK"


def _type_payload(text: str) -> Optional[str]:
    if _TYPE_TOO_LONG.search(text or ""):
        return None
    quoted = _TYPE_QUOTED.search(text or "")
    if quoted:
        payload = quoted.group(1)
    else:
        word = _TYPE_WORD.search(text or "")
        if not word:
            return None
        payload = word.group(1).strip()
    if not payload or len(payload) > _TYPE_MAX:
        return None
    if _BAD_TYPE.search(payload):
        return None
    if any(tok in payload.lower() for tok in ("eval(", "exec(", "subprocess", "javascript:")):
        return None
    return payload


def _looks_path(path: str) -> bool:
    text = str(path or "").strip()
    if not text or ".." in text or "\x00" in text:
        return False
    return ("\\" in text) or text.startswith("/")


def _extract_path(text: str) -> Optional[str]:
    quoted = _QUOTED.search(text or "")
    if quoted and _looks_path(quoted.group(1)):
        return quoted.group(1)[:MAX_PARAM_CHARS]
    win = _WIN_PATH.search(text or "")
    if win and _looks_path(win.group(1)):
        return win.group(1)[:MAX_PARAM_CHARS]
    posix = _POSIX_PATH.search(text or "")
    if posix and _looks_path(posix.group(1)):
        return posix.group(1)[:MAX_PARAM_CHARS]
    return None


def _extract_http_url(text: str) -> Optional[str]:
    match = _URL.search(text or "")
    if not match:
        return None
    raw = match.group(1).rstrip(".,);]>\"'")
    if "\\" in raw or raw.startswith("//") or "://" not in raw:
        return None
    scheme, rest = raw.split("://", 1)
    scheme = scheme.lower()
    if scheme not in ("http", "https"):
        return None
    host = rest.split("/")[0].split("?")[0].split("#")[0].strip()
    if not host or " " in host:
        return None
    return raw[:MAX_PARAM_CHARS]


def _computer_steps(goal: GoalSpec, planner_context: Any) -> Tuple[Optional[List[Dict[str, Any]]], Optional[PlanProposal]]:
    text = str(goal.raw_intent or "")
    if _COORD.search(text):
        return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    observe = bool(_OBSERVE.search(text))
    click = bool(_CLICK.search(text))
    typing = bool(_TYPE.search(text))
    if observe and (click or typing):
        return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    if click and typing:
        return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    if not _has_computer_session(goal):
        return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    sid = str(goal.computer_session_id)[:64]
    if observe:
        if not is_computer_observe_enabled():
            return None, _fail(PlannerStatus.CAPABILITY_UNAVAILABLE, "CAPABILITY_UNAVAILABLE")
        return [_base_step("computer", "OBSERVE", {"session_id": sid})], None
    if click:
        if not is_computer_click_enabled():
            return None, _fail(PlannerStatus.CAPABILITY_UNAVAILABLE, "CAPABILITY_UNAVAILABLE")
        target, reason = _match_click_target(text, _structured_targets(planner_context))
        if target is None:
            if reason == "AMBIGUOUS_TARGET":
                return None, _fail(PlannerStatus.AMBIGUOUS_TARGET, "AMBIGUOUS_TARGET")
            return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
        params = {
            "session_id": sid,
            "automation_id": target.get("automation_id", ""),
            "runtime_id": target.get("runtime_id", ""),
            "control_type": target.get("control_type", ""),
            "name": target.get("name", ""),
        }
        oh = ""
        if isinstance(planner_context, dict):
            oh = str(planner_context.get("observation_hash") or "")[:64]
        if oh:
            params["precondition_observation_hash"] = oh
        return [_base_step("computer", "CLICK", params, verify="TARGET_STATE_MATCH")], None
    if typing:
        if not is_computer_type_enabled():
            return None, _fail(PlannerStatus.CAPABILITY_UNAVAILABLE, "CAPABILITY_UNAVAILABLE")
        payload = _type_payload(text)
        if payload is None:
            return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
        target, reason = _match_type_target(text, _structured_targets(planner_context))
        if target is None:
            if reason == "AMBIGUOUS_TARGET":
                return None, _fail(PlannerStatus.AMBIGUOUS_TARGET, "AMBIGUOUS_TARGET")
            return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
        params = {
            "session_id": sid,
            "automation_id": target.get("automation_id", ""),
            "runtime_id": target.get("runtime_id", ""),
            "control_type": target.get("control_type", ""),
            "name": target.get("name", ""),
            "text": payload,
        }
        oh = ""
        if isinstance(planner_context, dict):
            oh = str(planner_context.get("observation_hash") or "")[:64]
        if oh:
            params["precondition_observation_hash"] = oh
        return [_base_step("computer", "TYPE", params, verify="TARGET_STATE_MATCH")], None
    return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")


def _browser_steps(goal: GoalSpec) -> Tuple[Optional[List[Dict[str, Any]]], Optional[PlanProposal]]:
    if not is_computer_browser_enabled():
        return None, _fail(PlannerStatus.CAPABILITY_UNAVAILABLE, "CAPABILITY_UNAVAILABLE")
    if not _has_computer_session(goal):
        return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    text = str(goal.raw_intent or "")
    if re.search(r"(javascript:|data:|file:|vbscript:)", text, re.I):
        return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    url = _extract_http_url(text)
    if url is None:
        return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    params = {"session_id": str(goal.computer_session_id)[:64], "url": url}
    return [_base_step("browser", "NAVIGATE", params, verify="URL_MATCH")], None


def _filesystem_steps(goal: GoalSpec) -> Tuple[Optional[List[Dict[str, Any]]], Optional[PlanProposal]]:
    if not is_computer_filesystem_enabled():
        return None, _fail(PlannerStatus.CAPABILITY_UNAVAILABLE, "CAPABILITY_UNAVAILABLE")
    if not _has_computer_session(goal):
        return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    text = str(goal.raw_intent or "")
    if _DELETE.search(text) or _WRITE.search(text):
        return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    path = _extract_path(text)
    if path is None:
        return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    sid_params = {"path": path}
    if _LIST.search(text):
        return [_base_step("filesystem", "LIST_DIRECTORY", sid_params)], None
    if _READ.search(text):
        return [_base_step("filesystem", "READ_FILE", sid_params)], None
    return None, _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")


def plan_goal(goal: Any, planner_context: Any = None) -> PlanProposal:
    """Propose a validated GoalPlan. Does not execute, approve, or authorize.

    planner_context is a V8.8 data-only seam. structured_targets may supply
    typed observation rows. Context cannot authorize, approve, or invent identity.
    """
    if not isinstance(goal, GoalSpec):
        return _fail(PlannerStatus.INVALID_GOAL, "INVALID_GOAL")
    if not is_v8_enabled():
        return _fail(PlannerStatus.V8_DISABLED, "V8_DISABLED")
    if goal.normalized_intent in (IntentClass.UNKNOWN, IntentClass.AMBIGUOUS):
        return _fail(PlannerStatus.UNSUPPORTED_INTENT, "UNSUPPORTED_INTENT")
    cap = goal.capability_class
    if cap is CapabilityClass.NONE:
        return _fail(PlannerStatus.UNSUPPORTED_INTENT, "UNSUPPORTED_INTENT")
    record = lookup(cap)
    if not record.exists:
        return _fail(PlannerStatus.CAPABILITY_UNAVAILABLE, "UNKNOWN_CAPABILITY")
    if not record.available:
        return _fail(PlannerStatus.CAPABILITY_UNAVAILABLE, "CAPABILITY_UNAVAILABLE")
    if goal.normalized_intent in _DEFERRED:
        return _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    extra_fail: Optional[PlanProposal] = None
    if goal.normalized_intent is IntentClass.CONVERSATION:
        drafts = _conversation_steps(goal)
    elif goal.normalized_intent is IntentClass.MEMORY_READ:
        drafts = _memory_steps(goal)
    elif goal.normalized_intent is IntentClass.COMPUTER:
        drafts, extra_fail = _computer_steps(goal, planner_context)
    elif goal.normalized_intent is IntentClass.BROWSER:
        drafts, extra_fail = _browser_steps(goal)
    elif goal.normalized_intent is IntentClass.FILESYSTEM:
        drafts, extra_fail = _filesystem_steps(goal)
    else:
        return _fail(PlannerStatus.UNSUPPORTED_INTENT, "UNSUPPORTED_INTENT")
    if extra_fail is not None:
        return extra_fail
    if not drafts:
        return _fail(PlannerStatus.PLANNING_UNAVAILABLE, "PLANNING_UNAVAILABLE")
    if len(drafts) > MAX_STEPS:
        return _fail(PlannerStatus.PLANNING_UNAVAILABLE, "TOO_MANY_STEPS")
    try:
        plan = build_goal_plan(goal, drafts)
    except PlanValidationError as exc:
        return _fail(PlannerStatus.PLAN_VALIDATION_FAILED, exc.code)
    if len(plan.steps) > MAX_STEPS:
        return _fail(PlannerStatus.PLANNING_UNAVAILABLE, "TOO_MANY_STEPS")
    if plan.execution_permitted or plan.approved:
        return _fail(PlannerStatus.PLAN_VALIDATION_FAILED, "EXECUTION_CLAIM_REJECTED")
    return PlanProposal(
        status=PlannerStatus.SUCCESS,
        reason_code="PLAN_PROPOSED",
        plan=plan,
        attempts=MAX_PLANNING_ATTEMPTS,
    )


def planner_bounds() -> Dict[str, int]:
    return {
        "max_plan_steps": MAX_STEPS,
        "max_dependency_depth": MAX_DEPENDENCY_DEPTH,
        "max_planning_attempts": MAX_PLANNING_ATTEMPTS,
        "max_timeout_ms": MAX_TIMEOUT_MS,
    }
