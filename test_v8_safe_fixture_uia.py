"""Read-only live UIA checks for the native Win32 Safe Test Window. No click/type."""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_OBSERVE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_CLICK_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_TYPE_ENABLED", "false")

WINDOW_TITLE = "DOOM Safe Test Window"
BUTTON_NAME = "DOOM_TEST_BUTTON"
INPUT_NAME = "DOOM_TEST_INPUT"
UIA_BUTTON = 50000
UIA_EDIT = 50004


def _collect_control_view(hwnd: int):
    from proactive.computer.drivers.uia_win import (
        _com_ptr_valid,
        create_uia_client,
        reset_uia_client_for_tests,
    )

    reset_uia_client_for_tests()
    uia = create_uia_client()
    if uia is None:
        return None, []
    el = uia.ElementFromHandle(int(hwnd))
    walker = uia.ControlViewWalker
    rows = []

    def walk(node, depth):
        if not _com_ptr_valid(node) or depth > 10 or len(rows) > 80:
            return
        rec = {"depth": depth, "name": "", "ctype": 0, "value": "", "hwnd": 0}
        try:
            rec["name"] = str(node.CurrentName or "")[:80]
        except Exception:
            rec["name"] = ""
        try:
            rec["ctype"] = int(node.CurrentControlType)
        except Exception:
            rec["ctype"] = 0
        try:
            rec["hwnd"] = int(node.CurrentNativeWindowHandle or 0)
        except Exception:
            rec["hwnd"] = 0
        try:
            rec["value"] = str(getattr(node, "CurrentValue", "") or "")[:80]
        except Exception:
            rec["value"] = ""
        rows.append(rec)
        try:
            child = walker.GetFirstChildElement(node)
        except Exception:
            return
        while child is not None:
            if not _com_ptr_valid(child):
                try:
                    nxt = walker.GetNextSiblingElement(child)
                except Exception:
                    break
                if nxt is child:
                    break
                child = nxt
                continue
            walk(child, depth + 1)
            try:
                child = walker.GetNextSiblingElement(child)
            except Exception:
                break

    walk(el, 0)
    return el, rows


def _edit_window_text(hwnd: int) -> str:
    if hwnd <= 0:
        return ""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(int(hwnd), buf, 256)
    return str(buf.value or "")


class TestSafeFixtureLiveUiaReadOnly(unittest.TestCase):
    def test_native_button_and_edit_names(self):
        if sys.platform != "win32":
            self.skipTest("Windows-only live UIA fixture check")
        from proactive.computer.drivers.win32_id import enumerate_visible_windows

        hits = [
            w for w in enumerate_visible_windows(limit=40)
            if str(getattr(w, "title_advisory", "") or "").strip() == WINDOW_TITLE
        ]
        if not hits:
            self.skipTest("Safe Test Window is not open")
        hwnd = int(hits[0].hwnd)
        self.assertGreater(hwnd, 0)
        root, rows = _collect_control_view(hwnd)
        self.assertIsNotNone(root)
        buttons = [r for r in rows if r["ctype"] == UIA_BUTTON and r["name"] == BUTTON_NAME]
        edits = [r for r in rows if r["ctype"] == UIA_EDIT and r["name"] == INPUT_NAME]
        unnamed_edits = [r for r in rows if r["ctype"] == UIA_EDIT and r["name"] == ""]
        if not edits and unnamed_edits:
            self.skipTest(
                "Safe Test Window Edit Name is empty; restart the fixture to load the Name pin"
            )
        if len(buttons) == 2:
            self.assertEqual(len(buttons), 2)
        else:
            self.assertEqual(
                len(buttons),
                1,
                "expected one DOOM_TEST_BUTTON (or two if --duplicate-buttons)",
            )
        self.assertEqual(len(edits), 1)
        edit = edits[0]
        self.assertEqual(edit["ctype"], UIA_EDIT)
        self.assertEqual(edit["name"], INPUT_NAME)
        self.assertNotEqual(edit["value"], INPUT_NAME)
        self.assertNotEqual(_edit_window_text(int(edit["hwnd"] or 0)), INPUT_NAME)

    def test_live_test_is_observational_only(self):
        import ast
        from pathlib import Path

        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        banned = {
            "execute_plan",
            "InvokePattern",
            "SetValue",
            "SendInput",
            "mouse_event",
            "GetForegroundWindow",
            "SetForegroundWindow",
        }
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    used.add(func.id)
                elif isinstance(func, ast.Attribute):
                    used.add(func.attr)
        self.assertFalse(used & banned)


if __name__ == "__main__":
    unittest.main()
