"""Fixture-only accessibility Name pin tests. No V8 execution, no mouse/keyboard."""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_OBSERVE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_CLICK_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_TYPE_ENABLED", "false")

ROOT = Path(__file__).resolve().parent
PROBE_TITLE = "DOOM Safe Test Probe AccName"
INPUT_NAME = "DOOM_TEST_INPUT"
UIA_EDIT = 50004
UIA_VALUE_PATTERN = 10002
WM_CLOSE = 0x0010


def _fixture_src() -> str:
    return (ROOT / "tools" / "doom_safe_test_window.py").read_text(encoding="utf-8")


class TestSafeFixtureAccNameSource(unittest.TestCase):
    def test_pins_name_via_sethwndpropstr_not_edit_value(self):
        src = _fixture_src()
        self.assertIn("SetHwndPropStr", src)
        self.assertIn("ClearHwndProps", src)
        self.assertIn("NAME_PROPERTY_GUID", src)
        self.assertIn("0xC3A6921B", src)
        self.assertIn("0x608D3DF8", src)
        self.assertIn("pin_edit_accessible_name", src)
        self.assertIn('child(WS_EX_CLIENTEDGE, "EDIT", "", edit_style', src)
        self.assertNotIn("SendInput", src)
        self.assertNotIn("pyautogui", src)
        self.assertNotIn("ES_PASSWORD", src)


@unittest.skipUnless(sys.platform == "win32", "Windows-only fixture UIA lifecycle")
class TestSafeFixtureAccNameLifecycle(unittest.TestCase):
    def test_name_survives_uia_setvalue(self):
        from tools.doom_safe_test_window import INPUT_NAME as FIX_NAME
        from tools.doom_safe_test_window import run_window
        from proactive.computer.actions.adapter import ComUiaAdapter
        from proactive.computer.actions.types import (
            PATTERN_VALUE,
            ResolvedTarget,
            Status,
            TargetIdentity,
        )
        from proactive.computer.drivers.uia_win import (
            _com_ptr_valid,
            create_uia_client,
            reset_uia_client_for_tests,
        )
        from proactive.computer.drivers.win32_id import enumerate_visible_windows

        self.assertEqual(FIX_NAME, INPUT_NAME)

        def _post_close(target_hwnd: int) -> None:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.PostMessageW.restype = wintypes.BOOL
            if int(target_hwnd or 0) > 0:
                user32.PostMessageW(int(target_hwnd), WM_CLOSE, 0, 0)

        from proactive.computer.drivers.win32_id import enumerate_visible_windows as _enum0
        for w in _enum0(limit=60):
            if str(getattr(w, "title_advisory", "") or "").strip() == PROBE_TITLE:
                _post_close(int(w.hwnd))
                time.sleep(0.2)

        ready = threading.Event()
        err = []

        def _run():
            try:
                run_window(title=PROBE_TITLE, ready_event=ready)
            except Exception as exc:
                err.append(exc)
                ready.set()

        th = threading.Thread(target=_run, daemon=True)
        th.start()
        self.assertTrue(ready.wait(15), "probe window did not become ready")
        if err:
            raise err[0]

        hwnd = 0
        for _ in range(40):
            hits = [
                w for w in enumerate_visible_windows(limit=60)
                if str(getattr(w, "title_advisory", "") or "").strip() == PROBE_TITLE
            ]
            if hits:
                hwnd = int(hits[0].hwnd)
                break
            time.sleep(0.05)
        self.assertGreater(hwnd, 0, "probe HWND not found")

        def _close():
            _post_close(hwnd)
            th.join(timeout=8)

        try:
            reset_uia_client_for_tests()
            uia = create_uia_client()
            self.assertIsNotNone(uia)
            root = uia.ElementFromHandle(int(hwnd))
            walker = uia.ControlViewWalker
            edits = []

            def walk(node, depth):
                if not _com_ptr_valid(node) or depth > 10 or len(edits) > 4:
                    return
                try:
                    ctype = int(node.CurrentControlType)
                except Exception:
                    ctype = 0
                try:
                    name = str(node.CurrentName or "")
                except Exception:
                    name = ""
                if ctype == UIA_EDIT:
                    edits.append(node)
                    self.assertEqual(name, INPUT_NAME)
                try:
                    child = walker.GetFirstChildElement(node)
                except Exception:
                    return
                while child is not None:
                    if _com_ptr_valid(child):
                        walk(child, depth + 1)
                    try:
                        child = walker.GetNextSiblingElement(child)
                    except Exception:
                        break

            walk(root, 0)
            named = []
            for el in edits:
                try:
                    n = str(el.CurrentName or "")
                except Exception:
                    n = ""
                if n == INPUT_NAME:
                    named.append(el)
            self.assertEqual(len(named), 1, "expected one named Edit, not STATIC")
            edit = named[0]
            self.assertEqual(int(edit.CurrentControlType), UIA_EDIT)
            self.assertFalse(bool(edit.CurrentIsPassword))
            raw = edit.GetCurrentPattern(int(UIA_VALUE_PATTERN))
            self.assertIsNotNone(raw)
            from comtypes.gen.UIAutomationClient import IUIAutomationValuePattern

            pat = raw.QueryInterface(IUIAutomationValuePattern)
            self.assertFalse(bool(pat.CurrentIsReadOnly))
            self.assertEqual(str(pat.CurrentValue or ""), "")

            adapter = ComUiaAdapter(int(hwnd))
            status = adapter.set_value(
                ResolvedTarget(
                    handle=edit,
                    identity=TargetIdentity(name=INPUT_NAME, control_type="Edit"),
                    patterns=(PATTERN_VALUE,),
                    is_password=False,
                    value_readonly=False,
                ),
                "hello DOOM",
            )
            self.assertEqual(status, Status.SUCCESS)
            self.assertEqual(str(edit.CurrentName or ""), INPUT_NAME)
            self.assertEqual(int(edit.CurrentControlType), UIA_EDIT)
            self.assertEqual(str(pat.CurrentValue or ""), "hello DOOM")
            self.assertNotEqual(str(pat.CurrentValue or ""), INPUT_NAME)
        finally:
            _close()


if __name__ == "__main__":
    unittest.main()
