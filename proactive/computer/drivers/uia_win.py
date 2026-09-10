"""Observe-only UIA metadata. No patterns, no focus, no keys."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

UIA_UNAVAILABLE = "UIA_UNAVAILABLE"
OBSERVATION_TIMEOUT = "OBSERVATION_TIMEOUT"
TREE_LIMIT = "TREE_LIMIT"
REDACTION_FAILED = "REDACTION_FAILED"
OK = "OK"


@dataclass
class UiaWalkNode:
    runtime_id: str = ""
    automation_id: str = ""
    control_type: str = ""
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


def _com_node_from_element(el: Any, remaining_ms: float) -> Optional[UiaWalkNode]:
    if el is None or remaining_ms <= 0:
        return None
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
        node.is_password = bool(el.CurrentIsPassword)
    except Exception:
        return None
    return node


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
    while child is not None:
        if budget[0] <= 0:
            return TREE_LIMIT
        if (time.monotonic() - t0) * 1000.0 > float(timeout_ms):
            return OBSERVATION_TIMEOUT
        remain = float(timeout_ms) - (time.monotonic() - t0) * 1000.0
        child_node = _com_node_from_element(child, remain)
        if child_node is None and remain > 0:
            return REDACTION_FAILED
        if child_node is not None:
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


def create_uia_client() -> Any:
    """Return IUIAutomation or None. Local COM only. No network."""
    global _UIA_CLIENT
    if _UIA_CLIENT is not None:
        return _UIA_CLIENT
    try:
        import comtypes
        import comtypes.client
    except Exception:
        return None
    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    try:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import IUIAutomation
        _UIA_CLIENT = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=IUIAutomation,
        )
        return _UIA_CLIENT
    except Exception:
        return None


def _read_com(hwnd: int, timeout_ms: int, max_depth: int, max_nodes: int) -> UiaMeta:
    t0 = time.monotonic()
    try:
        import comtypes.client
    except Exception:
        return UiaMeta(outcome=UIA_UNAVAILABLE)
    try:
        uia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
        )
        el = uia.ElementFromHandle(int(hwnd))
    except Exception:
        return UiaMeta(outcome=UIA_UNAVAILABLE)
    if el is None:
        return UiaMeta(outcome=UIA_UNAVAILABLE)
    remain = float(timeout_ms) - (time.monotonic() - t0) * 1000.0
    root = _com_node_from_element(el, remain)
    if root is None:
        if remain <= 0:
            return UiaMeta(outcome=OBSERVATION_TIMEOUT)
        return UiaMeta(outcome=REDACTION_FAILED)
    budget = [max(int(max_nodes) - 1, 0)]
    err = _com_fill_children(
        uia, el, root, 0, int(max_depth), int(max_nodes), budget, t0, int(timeout_ms),
    )
    if err == OBSERVATION_TIMEOUT:
        return UiaMeta(outcome=OBSERVATION_TIMEOUT)
    if err == REDACTION_FAILED:
        return UiaMeta(outcome=REDACTION_FAILED, sensitive_hit=True)
    if err == UIA_UNAVAILABLE:
        return UiaMeta(outcome=UIA_UNAVAILABLE)
    meta = bounded_tree_walk(
        root,
        timeout_ms=timeout_ms,
        max_depth=max_depth,
        max_nodes=max_nodes,
        started=t0,
    )
    return meta


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
    return _read_com(int(hwnd), int(timeout_ms), int(max_depth), int(max_nodes))
