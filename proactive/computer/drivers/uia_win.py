"""Observe-only UIA metadata. No patterns, no focus, no keys."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

UIA_UNAVAILABLE = "UIA_UNAVAILABLE"
UIA_WRAPPER_UNAVAILABLE = "UIA_WRAPPER_UNAVAILABLE"
UIA_CLIENT_UNAVAILABLE = "UIA_CLIENT_UNAVAILABLE"
UIA_ELEMENT_UNAVAILABLE = "UIA_ELEMENT_UNAVAILABLE"
OBSERVATION_TIMEOUT = "OBSERVATION_TIMEOUT"
TREE_LIMIT = "TREE_LIMIT"
REDACTION_FAILED = "REDACTION_FAILED"
OK = "OK"

UIA_FAIL_CLOSED = frozenset({
    UIA_UNAVAILABLE,
    UIA_WRAPPER_UNAVAILABLE,
    UIA_CLIENT_UNAVAILABLE,
    UIA_ELEMENT_UNAVAILABLE,
})


@dataclass
class UiaWalkNode:
    runtime_id: str = ""
    automation_id: str = ""
    control_type: str = ""
    name: str = ""
    focused: bool = False
    is_password: bool = False
    leaked_secret: Optional[str] = None
    children: List["UiaWalkNode"] = field(default_factory=list)


@dataclass
class UiaMeta:
    outcome: str = UIA_UNAVAILABLE
    runtime_id: str = ""
    automation_id: str = ""
    control_type: str = ""
    tree_digest: str = ""
    node_count: int = 0
    sensitive_hit: bool = False
    truncated: bool = False


def _struct_digest(rows: Sequence[dict]) -> str:
    raw = json.dumps(list(rows), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def bounded_tree_walk(
    root: Optional[UiaWalkNode],
    *,
    timeout_ms: int,
    max_depth: int,
    max_nodes: int,
    started: Optional[float] = None,
) -> UiaMeta:
    meta = UiaMeta(outcome=OK)
    if root is None:
        meta.outcome = UIA_UNAVAILABLE
        return meta
    t0 = time.monotonic() if started is None else started
    rows: List[dict] = []
    stack: List[tuple] = [(root, 0)]

    while stack:
        if (time.monotonic() - t0) * 1000.0 > float(timeout_ms):
            meta.outcome = OBSERVATION_TIMEOUT
            meta.tree_digest = ""
            return meta
        node, depth = stack.pop()
        if node is None:
            continue
        if node.is_password and node.leaked_secret is not None:
            meta.outcome = REDACTION_FAILED
            meta.sensitive_hit = True
            meta.tree_digest = ""
            return meta
        if node.is_password:
            meta.sensitive_hit = True
        if depth > int(max_depth):
            meta.truncated = True
            continue
        if len(rows) >= int(max_nodes):
            meta.truncated = True
            meta.outcome = TREE_LIMIT
            break
        rid = str(node.runtime_id or "")[:256]
        aid = str(node.automation_id or "")[:128]
        ctype = str(node.control_type or "")[:64]
        rows.append({"a": aid, "d": int(depth), "r": rid, "t": ctype})
        if depth < int(max_depth):
            kids = list(node.children or [])
            for child in reversed(kids):
                stack.append((child, depth + 1))
        else:
            if node.children:
                meta.truncated = True

    meta.node_count = len(rows)
    meta.tree_digest = _struct_digest(rows)
    if meta.truncated and meta.outcome == OK:
        meta.outcome = TREE_LIMIT
    meta.runtime_id = str(root.runtime_id or "")[:256]
    meta.automation_id = str(root.automation_id or "")[:128]
    meta.control_type = str(root.control_type or "")[:64]
    return meta


# Internal walk outcomes only. Not planner capabilities.
_INVALID_COM_ELEMENT = "INVALID_COM_ELEMENT"
_PASSWORD_PROPERTY_UNAVAILABLE = "PASSWORD_PROPERTY_UNAVAILABLE"
_PASSWORD_TRUE = "PASSWORD_TRUE"
_NORMAL_ELEMENT = "NORMAL_ELEMENT"


def _com_ptr_valid(el: Any) -> bool:
    """True only for a usable COM element. NULL wrappers are not Python None."""
    if el is None:
        return False
    try:
        if not bool(el):
            return False
    except Exception:
        return False
    return True


def _com_node_from_element(el: Any, remaining_ms: float) -> Tuple[Optional[UiaWalkNode], str]:
    if not _com_ptr_valid(el):
        return None, _INVALID_COM_ELEMENT
    if remaining_ms <= 0:
        return None, _INVALID_COM_ELEMENT
    node = UiaWalkNode()
    try:
        rid = el.GetRuntimeId()
        if rid:
            node.runtime_id = ".".join(str(int(x)) for x in list(rid)[:16])
    except Exception:
        node.runtime_id = ""
    try:
        node.automation_id = str(el.CurrentAutomationId or "")[:128]
    except Exception:
        node.automation_id = ""
    try:
        node.control_type = str(int(el.CurrentControlType))
    except Exception:
        node.control_type = ""
    try:
        node.name = str(el.CurrentName or "")[:80]
    except Exception:
        node.name = ""
    try:
        node.focused = bool(el.CurrentHasKeyboardFocus)
    except Exception:
        node.focused = False
    try:
        is_pw = bool(el.CurrentIsPassword)
    except Exception:
        # Fail closed for this element only. Never substitute password=false.
        return None, _PASSWORD_PROPERTY_UNAVAILABLE
    node.is_password = is_pw
    return node, (_PASSWORD_TRUE if is_pw else _NORMAL_ELEMENT)


def _com_fill_children(
    uia: Any,
    el: Any,
    node: UiaWalkNode,
    depth: int,
    max_depth: int,
    max_nodes: int,
    budget: List[int],
    t0: float,
    timeout_ms: int,
) -> Optional[str]:
    if depth >= max_depth:
        return None
    if (time.monotonic() - t0) * 1000.0 > float(timeout_ms):
        return OBSERVATION_TIMEOUT
    try:
        walker = uia.ControlViewWalker
        child = walker.GetFirstChildElement(el)
    except Exception:
        return UIA_UNAVAILABLE
    invalid_skips = 0
    while child is not None:
        if not _com_ptr_valid(child):
            invalid_skips += 1
            if invalid_skips > int(max_nodes):
                break
            try:
                nxt = walker.GetNextSiblingElement(child)
            except Exception:
                break
            if nxt is child:
                break
            child = nxt
            continue
        invalid_skips = 0
        if budget[0] <= 0:
            return TREE_LIMIT
        if (time.monotonic() - t0) * 1000.0 > float(timeout_ms):
            return OBSERVATION_TIMEOUT
        remain = float(timeout_ms) - (time.monotonic() - t0) * 1000.0
        child_node, kind = _com_node_from_element(child, remain)
        if kind in (_INVALID_COM_ELEMENT, _PASSWORD_PROPERTY_UNAVAILABLE) or child_node is None:
            try:
                child = walker.GetNextSiblingElement(child)
            except Exception:
                break
            continue
        node.children.append(child_node)
        budget[0] -= 1
        err = _com_fill_children(
            uia, child, child_node, depth + 1, max_depth, max_nodes, budget, t0, timeout_ms,
        )
        if err:
            return err
        try:
            child = walker.GetNextSiblingElement(child)
        except Exception:
            break
    return None


_UIA_CLIENT = None
_UIA_SETUP_ERROR = ""


def _doom_root() -> Path:
    return Path(__file__).resolve().parents[3]


def comtypes_gen_dir() -> Path:
    """Writable repository-local comtypes wrapper cache. Not site-packages."""
    return _doom_root() / ".doom_runtime" / "comtypes_gen"


def _prepare_comtypes_gen_dir() -> str:
    import comtypes.client
    import comtypes.gen as gen_pkg

    path = comtypes_gen_dir()
    path.mkdir(parents=True, exist_ok=True)
    init_py = path / "__init__.py"
    if not init_py.exists():
        init_py.write_text("# generated comtypes wrappers (runtime cache)\n", encoding="utf-8")
    target = str(path)
    comtypes.client.gen_dir = target
    paths = list(getattr(gen_pkg, "__path__", []) or [])
    if target in paths:
        paths.remove(target)
    paths.insert(0, target)
    gen_pkg.__path__ = paths
    return target


def reset_uia_client_for_tests() -> None:
    global _UIA_CLIENT, _UIA_SETUP_ERROR
    _UIA_CLIENT = None
    _UIA_SETUP_ERROR = ""


def create_uia_client() -> Any:
    """Return IUIAutomation or None. Local COM only. No network."""
    global _UIA_CLIENT, _UIA_SETUP_ERROR
    if _UIA_CLIENT is not None:
        return _UIA_CLIENT
    _UIA_SETUP_ERROR = ""
    try:
        import comtypes
        import comtypes.client
    except Exception:
        _UIA_SETUP_ERROR = UIA_CLIENT_UNAVAILABLE
        return None
    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    try:
        _prepare_comtypes_gen_dir()
    except Exception:
        _UIA_SETUP_ERROR = UIA_WRAPPER_UNAVAILABLE
        return None
    try:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import IUIAutomation
    except Exception:
        _UIA_SETUP_ERROR = UIA_WRAPPER_UNAVAILABLE
        return None
    try:
        _UIA_CLIENT = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=IUIAutomation,
        )
        if _UIA_CLIENT is None:
            _UIA_SETUP_ERROR = UIA_CLIENT_UNAVAILABLE
            return None
        return _UIA_CLIENT
    except Exception:
        _UIA_SETUP_ERROR = UIA_CLIENT_UNAVAILABLE
        return None


def _read_com(hwnd: int, timeout_ms: int, max_depth: int, max_nodes: int):
    t0 = time.monotonic()
    uia = create_uia_client()
    if uia is None:
        code = _UIA_SETUP_ERROR or UIA_UNAVAILABLE
        return UiaMeta(outcome=code), None
    try:
        el = uia.ElementFromHandle(int(hwnd))
    except Exception:
        return UiaMeta(outcome=UIA_ELEMENT_UNAVAILABLE), None
    if not _com_ptr_valid(el):
        return UiaMeta(outcome=UIA_ELEMENT_UNAVAILABLE), None
    remain = float(timeout_ms) - (time.monotonic() - t0) * 1000.0
    root, kind = _com_node_from_element(el, remain)
    if root is None:
        if remain <= 0:
            return UiaMeta(outcome=OBSERVATION_TIMEOUT), None
        if kind == _PASSWORD_PROPERTY_UNAVAILABLE:
            return UiaMeta(outcome=UIA_UNAVAILABLE), None
        return UiaMeta(outcome=UIA_ELEMENT_UNAVAILABLE), None
    budget = [max(int(max_nodes) - 1, 0)]
    err = _com_fill_children(
        uia, el, root, 0, int(max_depth), int(max_nodes), budget, t0, int(timeout_ms),
    )
    if err == OBSERVATION_TIMEOUT:
        return UiaMeta(outcome=OBSERVATION_TIMEOUT), None
    if err == REDACTION_FAILED:
        return UiaMeta(outcome=REDACTION_FAILED, sensitive_hit=True), None
    if err == UIA_UNAVAILABLE:
        return UiaMeta(outcome=UIA_UNAVAILABLE), None
    meta = bounded_tree_walk(
        root,
        timeout_ms=timeout_ms,
        max_depth=max_depth,
        max_nodes=max_nodes,
        started=t0,
    )
    return meta, root


def read_foreground_uia_meta(
    hwnd: int,
    *,
    timeout_ms: int,
    max_depth: int,
    max_nodes: int,
    tree_root: Optional[UiaWalkNode] = None,
) -> UiaMeta:
    if int(hwnd or 0) == 0:
        return UiaMeta(outcome=UIA_UNAVAILABLE)
    if tree_root is not None:
        return bounded_tree_walk(
            tree_root,
            timeout_ms=timeout_ms,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
    meta, _root = _read_com(int(hwnd), int(timeout_ms), int(max_depth), int(max_nodes))
    return meta


def read_foreground_uia_tree(
    hwnd: int,
    *,
    timeout_ms: int,
    max_depth: int,
    max_nodes: int,
    tree_root: Optional[UiaWalkNode] = None,
):
    """Observe-only tree plus digest metadata. No mutation."""
    if int(hwnd or 0) == 0:
        return UiaMeta(outcome=UIA_UNAVAILABLE), None
    if tree_root is not None:
        meta = bounded_tree_walk(
            tree_root,
            timeout_ms=timeout_ms,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        return meta, tree_root
    return _read_com(int(hwnd), int(timeout_ms), int(max_depth), int(max_nodes))
