"""Deterministic UIA target resolution. No coordinates, OCR, or screenshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from proactive.computer.actions.types import (
    AncestryNode,
    ResolvedTarget,
    Status,
    TargetIdentity,
)


@dataclass
class ActionNode:
    """In-memory UIA node for tests and COM adapters."""

    automation_id: str = ""
    runtime_id: str = ""
    control_type: str = ""
    name: str = ""
    framework_id: str = ""
    class_name: str = ""
    is_password: bool = False
    is_enabled: bool = True
    value_readonly: bool = False
    patterns: Tuple[str, ...] = ()
    children: List["ActionNode"] = field(default_factory=list)
    handle: object = None
    parent: Optional["ActionNode"] = None


def _eq(a: str, b: str) -> bool:
    return str(a or "") == str(b or "")


def _specified(value: str) -> bool:
    return bool(str(value or "").strip())


def _ancestry_ok(node: ActionNode, expected: Sequence[AncestryNode]) -> bool:
    if not expected:
        return True
    cur = node.parent
    for spec in expected:
        if cur is None:
            return False
        if _specified(spec.automation_id) and not _eq(spec.automation_id, cur.automation_id):
            return False
        if _specified(spec.control_type) and not _eq(spec.control_type, cur.control_type):
            return False
        if _specified(spec.runtime_id) and not _eq(spec.runtime_id, cur.runtime_id):
            return False
        cur = cur.parent
    return True


def _matches(node: ActionNode, ident: TargetIdentity) -> bool:
    if _specified(ident.automation_id) and not _eq(ident.automation_id, node.automation_id):
        return False
    if _specified(ident.runtime_id) and not _eq(ident.runtime_id, node.runtime_id):
        return False
    if _specified(ident.control_type) and not _eq(ident.control_type, node.control_type):
        return False
    if _specified(ident.framework_id) and not _eq(ident.framework_id, node.framework_id):
        return False
    if _specified(ident.class_name) and not _eq(ident.class_name, node.class_name):
        return False
    if _specified(ident.name) and not _eq(ident.name, node.name):
        return False
    return _ancestry_ok(node, ident.ancestry or ())


def flatten(root: Optional[ActionNode]) -> List[ActionNode]:
    out: List[ActionNode] = []
    if root is None:
        return out
    stack = [root]
    while stack:
        n = stack.pop()
        out.append(n)
        for child in reversed(list(n.children or [])):
            child.parent = n
            stack.append(child)
    return out


def resolve_target(
    root: Optional[ActionNode],
    ident: TargetIdentity,
) -> Tuple[Optional[ResolvedTarget], Status]:
    if ident is None or not ident.is_deterministic():
        return None, Status.TARGET_AMBIGUOUS
    nodes = [n for n in flatten(root) if _matches(n, ident)]
    if not nodes:
        return None, Status.TARGET_NOT_FOUND
    if len(nodes) > 1:
        return None, Status.TARGET_AMBIGUOUS
    n = nodes[0]
    live = TargetIdentity(
        automation_id=n.automation_id,
        runtime_id=n.runtime_id,
        control_type=n.control_type,
        name=n.name,
        framework_id=n.framework_id,
        class_name=n.class_name,
        ancestry=ident.ancestry,
        exe_path_norm=ident.exe_path_norm,
        window_class=ident.window_class,
        is_password=bool(n.is_password),
    )
    return ResolvedTarget(
        handle=n.handle if n.handle is not None else n,
        identity=live,
        patterns=tuple(n.patterns or ()),
        is_password=bool(n.is_password),
        is_enabled=bool(n.is_enabled),
        value_readonly=bool(n.value_readonly),
    ), Status.SUCCESS
