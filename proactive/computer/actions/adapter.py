"""UIA semantic patterns only. No mouse, keys, or coordinate fallback."""

from __future__ import annotations

from typing import Optional, Protocol, Tuple

from proactive.computer.actions.target import ActionNode, resolve_target
from proactive.computer.actions.types import (
    PATTERN_INVOKE,
    PATTERN_SELECTION_ITEM,
    PATTERN_TOGGLE,
    PATTERN_VALUE,
    ResolvedTarget,
    Status,
    TargetIdentity,
)

UIA_INVOKE_PATTERN = 10000
UIA_VALUE_PATTERN = 10002
UIA_SELECTION_ITEM_PATTERN = 10010
UIA_TOGGLE_PATTERN = 10015


class UiaActionAdapter(Protocol):
    def find(self, ident: TargetIdentity) -> Tuple[Optional[ResolvedTarget], Status]:
        ...

    def invoke(self, target: ResolvedTarget) -> Status:
        ...

    def select_item(self, target: ResolvedTarget) -> Status:
        ...

    def toggle(self, target: ResolvedTarget) -> Status:
        ...

    def set_value(self, target: ResolvedTarget, text: str) -> Status:
        ...


class MemoryUiaAdapter:
    """Deterministic adapter for tests. Operates on an ActionNode tree."""

    def __init__(self, root: Optional[ActionNode] = None):
        self.root = root
        self.invoked = []
        self.typed = []
        self.last_value = ""

    def find(self, ident: TargetIdentity) -> Tuple[Optional[ResolvedTarget], Status]:
        return resolve_target(self.root, ident)

    def invoke(self, target: ResolvedTarget) -> Status:
        if PATTERN_INVOKE not in (target.patterns or ()):
            return Status.UNSUPPORTED_ACTION
        if not target.is_enabled:
            return Status.CONTROL_NOT_CAPABLE
        self.invoked.append("invoke")
        return Status.SUCCESS

    def select_item(self, target: ResolvedTarget) -> Status:
        if PATTERN_SELECTION_ITEM not in (target.patterns or ()):
            return Status.UNSUPPORTED_ACTION
        if not target.is_enabled:
            return Status.CONTROL_NOT_CAPABLE
        self.invoked.append("selection_item")
        return Status.SUCCESS

    def toggle(self, target: ResolvedTarget) -> Status:
        if PATTERN_TOGGLE not in (target.patterns or ()):
            return Status.UNSUPPORTED_ACTION
        if not target.is_enabled:
            return Status.CONTROL_NOT_CAPABLE
        self.invoked.append("toggle")
        return Status.SUCCESS

    def set_value(self, target: ResolvedTarget, text: str) -> Status:
        if PATTERN_VALUE not in (target.patterns or ()):
            return Status.UNSUPPORTED_ACTION
        if target.is_password or (not target.is_enabled) or target.value_readonly:
            return Status.CONTROL_NOT_CAPABLE
        self.last_value = text
        self.typed.append(PATTERN_VALUE)
        if target.handle is not None and hasattr(target.handle, "name"):
            pass
        return Status.SUCCESS


_IFACES = None


def _pattern_ifaces():
    global _IFACES
    if _IFACES is not None:
        return _IFACES
    from proactive.computer.drivers.uia_win import create_uia_client
    create_uia_client()
    from comtypes.gen.UIAutomationClient import (
        IUIAutomationInvokePattern,
        IUIAutomationSelectionItemPattern,
        IUIAutomationTogglePattern,
        IUIAutomationValuePattern,
    )
    _IFACES = {
        PATTERN_INVOKE: (UIA_INVOKE_PATTERN, IUIAutomationInvokePattern),
        PATTERN_SELECTION_ITEM: (UIA_SELECTION_ITEM_PATTERN, IUIAutomationSelectionItemPattern),
        PATTERN_TOGGLE: (UIA_TOGGLE_PATTERN, IUIAutomationTogglePattern),
        PATTERN_VALUE: (UIA_VALUE_PATTERN, IUIAutomationValuePattern),
    }
    return _IFACES


def _typed_pattern(el, kind: str):
    if el is None:
        return None
    try:
        pattern_id, iface = _pattern_ifaces()[kind]
        raw = el.GetCurrentPattern(int(pattern_id))
        if raw is None:
            return None
        return raw.QueryInterface(iface)
    except Exception:
        return None


def _pattern_names(el) -> Tuple[str, ...]:
    found = []
    for kind in (PATTERN_INVOKE, PATTERN_SELECTION_ITEM, PATTERN_TOGGLE, PATTERN_VALUE):
        if _typed_pattern(el, kind) is not None:
            found.append(kind)
    return tuple(found)


def _com_node(el, parent: Optional[ActionNode], budget: list, t0: float, timeout_ms: int) -> Optional[ActionNode]:
    import time
    if el is None or budget[0] <= 0:
        return None
    if (time.monotonic() - t0) * 1000.0 > float(timeout_ms):
        return None
    node = ActionNode(handle=el, parent=parent)
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
        node.framework_id = str(el.CurrentFrameworkId or "")[:64]
    except Exception:
        node.framework_id = ""
    try:
        node.class_name = str(el.CurrentClassName or "")[:64]
    except Exception:
        node.class_name = ""
    try:
        node.is_password = bool(el.CurrentIsPassword)
    except Exception:
        return None
    try:
        node.is_enabled = bool(el.CurrentIsEnabled)
    except Exception:
        node.is_enabled = True
    node.patterns = _pattern_names(el)
    budget[0] -= 1
    return node


def _fill(uia, el, node: ActionNode, budget: list, t0: float, timeout_ms: int, depth: int, max_depth: int) -> None:
    import time
    if depth >= max_depth:
        return
    if (time.monotonic() - t0) * 1000.0 > float(timeout_ms):
        return
    try:
        walker = uia.ControlViewWalker
        child = walker.GetFirstChildElement(el)
    except Exception:
        return
    while child is not None and budget[0] > 0:
        child_node = _com_node(child, node, budget, t0, timeout_ms)
        if child_node is not None:
            node.children.append(child_node)
            _fill(uia, child, child_node, budget, t0, timeout_ms, depth + 1, max_depth)
        try:
            child = walker.GetNextSiblingElement(child)
        except Exception:
            break


class ComUiaAdapter:
    """Live Windows UI Automation. Semantic patterns only."""

    def __init__(self, hwnd: int, *, timeout_ms: int = 800, max_depth: int = 6, max_nodes: int = 80):
        self.hwnd = int(hwnd or 0)
        self.timeout_ms = int(timeout_ms)
        self.max_depth = int(max_depth)
        self.max_nodes = int(max_nodes)
        self._tree: Optional[ActionNode] = None

    def _load(self) -> Optional[ActionNode]:
        import time
        if self._tree is not None:
            return self._tree
        if self.hwnd <= 0:
            return None
        t0 = time.monotonic()
        from proactive.computer.drivers.uia_win import create_uia_client
        uia = create_uia_client()
        if uia is None:
            return None
        try:
            el = uia.ElementFromHandle(int(self.hwnd))
        except Exception:
            return None
        if el is None:
            return None
        budget = [int(self.max_nodes)]
        root = _com_node(el, None, budget, t0, self.timeout_ms)
        if root is None:
            return None
        _fill(uia, el, root, budget, t0, self.timeout_ms, 0, self.max_depth)
        self._tree = root
        return root

    def find(self, ident: TargetIdentity) -> Tuple[Optional[ResolvedTarget], Status]:
        return resolve_target(self._load(), ident)

    def invoke(self, target: ResolvedTarget) -> Status:
        pat = _typed_pattern(target.handle, PATTERN_INVOKE)
        if pat is None:
            return Status.UNSUPPORTED_ACTION
        try:
            pat.Invoke()
            return Status.SUCCESS
        except Exception:
            return Status.EXECUTION_FAILED

    def select_item(self, target: ResolvedTarget) -> Status:
        pat = _typed_pattern(target.handle, PATTERN_SELECTION_ITEM)
        if pat is None:
            return Status.UNSUPPORTED_ACTION
        try:
            pat.Select()
            return Status.SUCCESS
        except Exception:
            return Status.EXECUTION_FAILED

    def toggle(self, target: ResolvedTarget) -> Status:
        pat = _typed_pattern(target.handle, PATTERN_TOGGLE)
        if pat is None:
            return Status.UNSUPPORTED_ACTION
        try:
            pat.Toggle()
            return Status.SUCCESS
        except Exception:
            return Status.EXECUTION_FAILED

    def set_value(self, target: ResolvedTarget, text: str) -> Status:
        if target.is_password:
            return Status.CONTROL_NOT_CAPABLE
        pat = _typed_pattern(target.handle, PATTERN_VALUE)
        if pat is None:
            return Status.UNSUPPORTED_ACTION
        try:
            if bool(getattr(pat, "CurrentIsReadOnly", False)):
                return Status.CONTROL_NOT_CAPABLE
        except Exception:
            pass
        try:
            pat.SetValue(str(text or ""))
            return Status.SUCCESS
        except Exception:
            return Status.EXECUTION_FAILED
