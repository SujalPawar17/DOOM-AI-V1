"""Focused tests for writable comtypes UIAutomation wrapper generation. No click/type."""
from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path

os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_OBSERVE_ENABLED", "false")

from proactive.computer.drivers.uia_win import (
    UIA_FAIL_CLOSED,
    UIA_UNAVAILABLE,
    comtypes_gen_dir,
    create_uia_client,
    read_foreground_uia_meta,
    reset_uia_client_for_tests,
)

ROOT = Path(__file__).resolve().parent
UIA = ROOT / "proactive" / "computer" / "drivers" / "uia_win.py"


class TestComtypesWritableGenDir(unittest.TestCase):
    def test_gen_dir_is_repo_runtime_not_site_packages(self):
        path = comtypes_gen_dir()
        self.assertEqual(path, ROOT / ".doom_runtime" / "comtypes_gen")
        self.assertNotIn("site-packages", str(path).replace("\\", "/").lower())
        self.assertTrue(str(path).startswith(str(ROOT)))

    def test_create_uia_client_uses_writable_gen_dir(self):
        reset_uia_client_for_tests()
        client = create_uia_client()
        import comtypes.client
        gen = Path(str(comtypes.client.gen_dir or ""))
        self.assertEqual(gen.resolve(), comtypes_gen_dir().resolve())
        self.assertTrue(gen.is_dir())
        self.assertNotIn("site-packages", str(gen).replace("\\", "/").lower())
        self.assertIsNotNone(client)
        friendly = gen / "UIAutomationClient.py"
        wrappers = list(gen.glob("_944DE083*.py"))
        self.assertTrue(friendly.exists() or wrappers, "expected generated UIA wrappers in DOOM runtime dir")

    def test_typed_client_has_element_from_handle(self):
        client = create_uia_client()
        self.assertIsNotNone(client)
        self.assertTrue(callable(getattr(client, "ElementFromHandle", None)))

    def test_zero_hwnd_fail_closed(self):
        meta = read_foreground_uia_meta(0, timeout_ms=50, max_depth=2, max_nodes=8)
        self.assertEqual(meta.outcome, UIA_UNAVAILABLE)
        self.assertIn(UIA_UNAVAILABLE, UIA_FAIL_CLOSED)

    def test_no_foreground_fallback_or_unsafe_ops(self):
        src = UIA.read_text(encoding="utf-8")
        self.assertNotIn("GetForegroundWindow", src)
        self.assertNotIn("pyautogui", src)
        self.assertNotIn("subprocess", src)
        self.assertNotIn("os.system", src)
        self.assertNotIn("eval(", src)
        self.assertNotIn("exec(", src)
        self.assertIn("comtypes.client.gen_dir", src)
        create = src.split("def create_uia_client")[1].split("def _read_com")[0]
        self.assertIn("_prepare_comtypes_gen_dir()", create)
        self.assertIn("GetModule", create)
        self.assertIn("IUIAutomation", create)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("eval", "exec"))

    def test_hwnd_not_from_planner_or_browser(self):
        src = UIA.read_text(encoding="utf-8")
        self.assertNotIn("body.get(\"hwnd\")", src)
        self.assertNotIn("request.json", src)
        planner = (ROOT / "orchestration" / "goal" / "planner.py").read_text(encoding="utf-8")
        click = planner.split("def _computer_steps")[1].split("def _browser_steps")[0]
        self.assertNotIn("hwnd", click.lower())

    def test_auth_and_cost_guard_untouched_in_driver(self):
        src = UIA.read_text(encoding="utf-8")
        self.assertNotIn("claim_authorization", src)
        self.assertNotIn("execute_plan", src)
        self.assertNotIn("cost_guard", src)
        self.assertNotIn("DOOM_ASK_UNLOCK", src)


class TestBoundWindowObserveReadOnly(unittest.TestCase):
    def test_element_from_handle_bound_safe_window_if_present(self):
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "true"
        from dotenv import load_dotenv
        load_dotenv()
        from proactive.config import OWNER_ID
        from proactive.store import proactive_store
        from proactive.computer.session import validate_bound_identity

        row = proactive_store.get_observing_computer_session(OWNER_ID)
        if not row or int(row.get("bound_hwnd") or 0) <= 0:
            self.skipTest("no bound OBSERVING computer session")
        live, code = validate_bound_identity(row)
        self.assertEqual(code, "OK")
        self.assertIsNotNone(live)
        title = str(getattr(live, "title_advisory", "") or "")
        if "DOOM Safe Test Window" not in title:
            self.skipTest("bound window is not the Safe Test Window")
        client = create_uia_client()
        self.assertIsNotNone(client)
        el = client.ElementFromHandle(int(live.hwnd))
        self.assertIsNotNone(el)
        self.assertIn("DOOM Safe Test Window", str(el.CurrentName or ""))


if __name__ == "__main__":
    unittest.main()
