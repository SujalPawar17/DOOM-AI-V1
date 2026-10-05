"""V13.3 Safe Real Computer Interaction — gated real UI backend for the V12.5 controller.

The V12.5 ComputerInteractionController already enforces: Cost Guard (local_uia),
claimed single-use plan-bound approval, browser refusal, password/sensitive refusal,
unique visible/enabled targets, stale-observation detection, bounded timeouts,
post-action verification, TYPE rollback, emergency stop and an owner-scoped audit.

This module only adds a *real* UIBackend, with extra gates:
  1. explicit enablement: DOOM_V13_REAL_COMPUTER=1 (otherwise construction is refused)
  2. exact window allowlist: by default ONLY the DOOM Safe Test Window (title + class)
  3. UIA semantic patterns only (Invoke / Value): no mouse, no keyboard, no coordinates,
     no focus stealing, no shell
  4. depends only on committed V8 code (create_uia_client, adapter tree walk/patterns);
     it does not use the uncommitted proactive/computer working-tree helpers

Anything outside the allowlist remains unreachable: real computer interaction stays
gated to explicitly allowlisted windows.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from core.v12.computer_interaction import UIBackend, UIElement, UIRole, UISnapshot

UIA_BUTTON, UIA_CHECKBOX, UIA_EDIT, UIA_TEXT = 50000, 50002, 50004, 50020
_ROLE = {UIA_BUTTON: UIRole.BUTTON, UIA_CHECKBOX: UIRole.CHECKBOX, UIA_EDIT: UIRole.INPUT, UIA_TEXT: UIRole.LABEL}


class RealComputerGated(RuntimeError):
    pass


@dataclass(frozen=True)
class AllowedWindow:
    title: str
    class_name: str
    # Win32 control-id -> stable label name (status labels whose Name is their text)
    label_aliases: Tuple[Tuple[str, str], ...] = ()


SAFE_TEST_WINDOW = AllowedWindow("DOOM Safe Test Window", "DoomSafeTestWindow", (("2002", "Status"),))


def real_computer_enabled() -> bool:
    return os.getenv("DOOM_V13_REAL_COMPUTER", "").strip() == "1"


class RealUIABackend(UIBackend):

    def __init__(self, allowed: AllowedWindow = SAFE_TEST_WINDOW):
        if sys.platform != "win32":
            raise RealComputerGated("real computer interaction requires Windows UI Automation")
        if not real_computer_enabled():
            raise RealComputerGated("real computer interaction is gated (set DOOM_V13_REAL_COMPUTER=1)")
        self.allowed = allowed
        self._handles: Dict[str, Any] = {}

    def _hwnd(self) -> int:
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowW.restype = ctypes.c_void_p
        hwnd = user32.FindWindowW(self.allowed.class_name, self.allowed.title)
        if not hwnd:
            raise RealComputerGated("allowlisted window is not open")
        return int(hwnd)

    def observe(self) -> UISnapshot:
        from proactive.computer.actions.adapter import ComUiaAdapter, _typed_pattern
        from proactive.computer.actions.types import PATTERN_VALUE
        root = ComUiaAdapter(self._hwnd(), timeout_ms=1500)._load()
        if root is None:
            raise RealComputerGated("UI Automation is unavailable for the allowlisted window")
        aliases = dict(self.allowed.label_aliases)
        elements, handles, stack = [], {}, [root]
        while stack:
            node = stack.pop()
            stack.extend(reversed(getattr(node, "children", []) or []))
            try:
                role = _ROLE.get(int(node.control_type or 0))
            except ValueError:
                role = None
            if role is None or node is root:
                continue
            # Only application controls with a stable automation id; window chrome
            # (title-bar Minimize/Maximize/Close, identified only by runtime id) is excluded.
            element_id = node.automation_id
            if not element_id:
                continue
            name, value = node.name, ""
            if role is UIRole.LABEL and node.automation_id in aliases:
                name, value = aliases[node.automation_id], node.name
            elif role is UIRole.INPUT and not node.is_password:
                pat = _typed_pattern(node.handle, PATTERN_VALUE)
                try:
                    value = str(getattr(pat, "CurrentValue", "") or "") if pat is not None else ""
                except Exception:
                    value = ""
            elements.append(UIElement(element_id, role, name, value, enabled=bool(node.is_enabled),
                                      visible=True, is_password=bool(node.is_password)))
            handles[element_id] = node.handle
        self._handles = handles
        return UISnapshot(self.allowed.title, "app", tuple(elements))

    def _pattern(self, element_id: str, kind: str):
        from proactive.computer.actions.adapter import _typed_pattern
        handle = self._handles.get(element_id)
        if handle is None:
            raise RuntimeError("element not in the latest observation")
        pat = _typed_pattern(handle, kind)
        if pat is None:
            raise RuntimeError(f"element does not support {kind}")
        return pat

    def click(self, element_id: str) -> None:
        from proactive.computer.actions.types import PATTERN_INVOKE
        self._pattern(element_id, PATTERN_INVOKE).Invoke()

    def set_text(self, element_id: str, text: str) -> None:
        from proactive.computer.actions.types import PATTERN_VALUE
        self._pattern(element_id, PATTERN_VALUE).SetValue(str(text))


class SafeTestWindowProcess:
    """Launch / close the repository's harmless Safe Test Window (evaluation fixture)."""

    def __init__(self, root: str):
        self.root = root
        self.proc: Optional[subprocess.Popen] = None

    def __enter__(self) -> "SafeTestWindowProcess":
        self.proc = subprocess.Popen([sys.executable, os.path.join(self.root, "tools", "doom_safe_test_window.py")],
                                     cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowW.restype = ctypes.c_void_p
        deadline = time.time() + 15
        while time.time() < deadline:
            if user32.FindWindowW(SAFE_TEST_WINDOW.class_name, SAFE_TEST_WINDOW.title):
                time.sleep(0.5)  # let the accessible-name pinning finish
                return self
            time.sleep(0.1)
        self.__exit__(None, None, None)
        raise RuntimeError("Safe Test Window did not appear")

    def __exit__(self, *exc) -> bool:
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        hwnd = user32.FindWindowW(SAFE_TEST_WINDOW.class_name, SAFE_TEST_WINDOW.title)
        if hwnd:
            user32.PostMessageW(hwnd, 0x0010, None, None)  # WM_CLOSE
        if self.proc is not None:
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        return False
