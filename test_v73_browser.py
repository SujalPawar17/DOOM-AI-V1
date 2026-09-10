"""V7.3 bounded browser control. No physical browser required."""
from __future__ import annotations

import ast
import os
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_BROWSER_ENABLED", "false")

from core.cost_guard import cost_guard
from proactive.computer.actions.types import ApprovalState
from proactive.computer.browser.driver import MemoryBrowserDriver
from proactive.computer.browser.kernel import (
    close_browser_session,
    execute_browser_action,
    open_browser_session,
    reset_browser_sessions_for_tests,
)
from proactive.computer.browser.observe import observation_hash
from proactive.computer.browser.types import (
    REDACTED,
    BrowserActionRequest,
    BrowserActionType,
    BrowserElement,
    BrowserTarget,
    Status,
)
from proactive.computer.browser.url import validate_navigate_url
from proactive.computer.policy import browser_allowed


ROOT = Path(__file__).resolve().parent
BROWSER_DIR = ROOT / "proactive" / "computer" / "browser"


def _flags_on():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_BROWSER_ENABLED"] = "true"


def _flags_off():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_BROWSER_ENABLED"] = "false"


def _req(action: BrowserActionType, session_id: str, **kw) -> BrowserActionRequest:
    d = dict(
        action_id=str(uuid.uuid4()),
        action_type=action,
        session_id=session_id,
        owner_id="sujal",
        approval_state=ApprovalState.APPROVED,
        precondition_observation_hash=kw.pop("precondition_observation_hash", ""),
        target=kw.pop("target", BrowserTarget()),
        url=kw.pop("url", ""),
        text=kw.pop("text", ""),
        sensitive=kw.pop("sensitive", False),
    )
    d.update(kw)
    return BrowserActionRequest(**d)


def _open(driver=None):
    _flags_on()
    return open_browser_session("sujal", driver=driver or MemoryBrowserDriver())


class TestV73Browser(unittest.TestCase):
    def setUp(self):
        cost_guard.reset_records_for_tests()
        reset_browser_sessions_for_tests()
        _flags_on()

    def tearDown(self):
        reset_browser_sessions_for_tests()
        _flags_off()

    def test_session_create_and_close(self):
        opened = _open()
        self.assertTrue(opened["ok"])
        sid = opened["session_id"]
        closed = close_browser_session(sid, "sujal")
        self.assertTrue(closed["ok"])
        req = _req(BrowserActionType.REFRESH, sid, precondition_observation_hash="x")
        out = execute_browser_action(req)
        self.assertEqual(out.status, Status.SESSION_CLOSED)

    def test_navigation_and_invalid_and_unsafe_url(self):
        opened = _open()
        sid = opened["session_id"]
        before = opened["observation"].observation_hash
        out = execute_browser_action(_req(
            BrowserActionType.NAVIGATE, sid,
            url="https://example.com/",
            precondition_observation_hash=before,
        ))
        self.assertEqual(out.status, Status.SUCCESS)
        self.assertTrue(out.after_observation_hash)
        self.assertNotEqual(out.after_observation_hash, before)
        bad = execute_browser_action(_req(
            BrowserActionType.NAVIGATE, sid,
            url="not a url",
            precondition_observation_hash=out.after_observation_hash,
        ))
        self.assertEqual(bad.status, Status.INVALID_URL)
        unsafe = execute_browser_action(_req(
            BrowserActionType.NAVIGATE, sid,
            url="javascript:alert(1)",
            precondition_observation_hash=out.after_observation_hash,
        ))
        self.assertEqual(unsafe.status, Status.NAVIGATION_BLOCKED)
        self.assertEqual(validate_navigate_url("file:///c:/windows/notepad.exe"), Status.NAVIGATION_BLOCKED)
        self.assertEqual(validate_navigate_url("data:text/html,hi"), Status.NAVIGATION_BLOCKED)

    def test_structured_observation_and_hash(self):
        opened = _open()
        obs = opened["observation"]
        self.assertEqual(obs.schema_version, "v73.1")
        h1 = observation_hash(obs.as_authoritative())
        h2 = observation_hash(obs.as_authoritative())
        self.assertEqual(h1, h2)
        self.assertNotIn("title_advisory", obs.as_authoritative())

    def test_target_resolution_click_type_history(self):
        opened = _open()
        sid = opened["session_id"]
        nav = execute_browser_action(_req(
            BrowserActionType.NAVIGATE, sid,
            url="https://example.com/",
            precondition_observation_hash=opened["observation"].observation_hash,
        ))
        note = BrowserTarget(role="textbox", name="Note", element_id="note")
        typed = execute_browser_action(_req(
            BrowserActionType.TYPE, sid, target=note, text="hello",
            precondition_observation_hash=nav.after_observation_hash,
        ))
        self.assertEqual(typed.status, Status.SUCCESS)
        link = BrowserTarget(role="link", name="More information...", element_id="more")
        clicked = execute_browser_action(_req(
            BrowserActionType.CLICK, sid, target=link,
            precondition_observation_hash=typed.after_observation_hash,
        ))
        self.assertEqual(clicked.status, Status.SUCCESS)
        back = execute_browser_action(_req(
            BrowserActionType.BACK, sid,
            precondition_observation_hash=clicked.after_observation_hash,
        ))
        self.assertEqual(back.status, Status.SUCCESS)
        fwd = execute_browser_action(_req(
            BrowserActionType.FORWARD, sid,
            precondition_observation_hash=back.after_observation_hash,
        ))
        self.assertEqual(fwd.status, Status.SUCCESS)
        ref = execute_browser_action(_req(
            BrowserActionType.REFRESH, sid,
            precondition_observation_hash=fwd.after_observation_hash,
        ))
        self.assertEqual(ref.status, Status.SUCCESS)
        self.assertTrue(ref.after_observation_hash)

    def test_target_not_found_and_ambiguous(self):
        opened = _open()
        sid = opened["session_id"]
        nav = execute_browser_action(_req(
            BrowserActionType.NAVIGATE, sid, url="https://example.com/",
            precondition_observation_hash=opened["observation"].observation_hash,
        ))
        missing = execute_browser_action(_req(
            BrowserActionType.CLICK, sid,
            target=BrowserTarget(role="button", name="Nope", element_id="nope"),
            precondition_observation_hash=nav.after_observation_hash,
        ))
        self.assertEqual(missing.status, Status.TARGET_NOT_FOUND)
        drv = MemoryBrowserDriver()
        drv.pages["https://example.com/"] = ("T", [
            BrowserElement(role="button", name="Go", element_id="a", handle="a"),
            BrowserElement(role="button", name="Go", element_id="b", handle="b"),
        ])
        opened2 = _open(drv)
        sid2 = opened2["session_id"]
        nav2 = execute_browser_action(_req(
            BrowserActionType.NAVIGATE, sid2, url="https://example.com/",
            precondition_observation_hash=opened2["observation"].observation_hash,
        ))
        amb = execute_browser_action(_req(
            BrowserActionType.CLICK, sid2,
            target=BrowserTarget(role="button", name="Go"),
            precondition_observation_hash=nav2.after_observation_hash,
        ))
        self.assertEqual(amb.status, Status.TARGET_AMBIGUOUS)

    def test_stale_observation(self):
        opened = _open()
        sid = opened["session_id"]
        out = execute_browser_action(_req(
            BrowserActionType.NAVIGATE, sid, url="https://example.com/",
            precondition_observation_hash="deadbeef",
        ))
        self.assertEqual(out.status, Status.PRECONDITION_FAILED)
        self.assertEqual(out.error_code, "STALE_OBSERVATION_HASH")

    def test_emergency_stop_policy_cost_approval(self):
        opened = _open()
        sid = opened["session_id"]
        with patch("proactive.computer.browser.kernel._emergency_stop", return_value=True):
            out = execute_browser_action(_req(
                BrowserActionType.REFRESH, sid,
                precondition_observation_hash=opened["observation"].observation_hash,
            ))
        self.assertEqual(out.status, Status.EMERGENCY_STOP_ACTIVE)
        req = _req(BrowserActionType.NAVIGATE, sid, url="https://example.com/",
                   precondition_observation_hash=opened["observation"].observation_hash,
                   approval_state=ApprovalState.NONE)
        self.assertEqual(execute_browser_action(req).status, Status.APPROVAL_REQUIRED)
        req2 = _req(BrowserActionType.NAVIGATE, sid, url="https://example.com/",
                    precondition_observation_hash=opened["observation"].observation_hash,
                    approval_state=ApprovalState.DENIED)
        self.assertEqual(execute_browser_action(req2).status, Status.APPROVAL_DENIED)
        blocked = MagicMock()
        blocked.is_allow = False
        with patch("proactive.computer.browser.kernel.cost_guard.authorize", return_value=blocked):
            out = execute_browser_action(_req(
                BrowserActionType.REFRESH, sid,
                precondition_observation_hash=opened["observation"].observation_hash,
            ))
        self.assertEqual(out.status, Status.POLICY_BLOCKED)
        self.assertEqual(out.error_code, "COST_GUARD_BLOCKED")

    def test_sensitive_redaction_and_password_block(self):
        drv = MemoryBrowserDriver()
        drv.pages["https://example.com/"] = ("T", [
            BrowserElement(role="textbox", name="Note", element_id="note", test_id="note", editable=True, handle="note"),
            BrowserElement(role="textbox", name="Secret", element_id="pw", is_password=True, editable=True, handle="pw", input_type="password"),
        ])
        opened = _open(drv)
        sid = opened["session_id"]
        nav = execute_browser_action(_req(
            BrowserActionType.NAVIGATE, sid, url="https://example.com/",
            precondition_observation_hash=opened["observation"].observation_hash,
        ))
        secret = execute_browser_action(_req(
            BrowserActionType.TYPE, sid, text="super-secret-token", sensitive=True,
            target=BrowserTarget(role="textbox", name="Note", element_id="note"),
            precondition_observation_hash=nav.after_observation_hash,
        ))
        self.assertEqual(secret.status, Status.SUCCESS)
        self.assertEqual(secret.telemetry.get("payload_preview"), REDACTED)
        self.assertNotIn("super-secret-token", str(secret.as_dict()))
        pw = execute_browser_action(_req(
            BrowserActionType.TYPE, sid, text="hunter2",
            target=BrowserTarget(role="textbox", name="Secret", element_id="pw", is_password=True),
            precondition_observation_hash=secret.after_observation_hash,
        ))
        self.assertEqual(pw.status, Status.RISK_BLOCKED)
        self.assertNotIn("hunter2", str(pw.as_dict()))

    def test_disabled_by_default_and_download_blocked(self):
        _flags_off()
        self.assertFalse(browser_allowed())
        opened = open_browser_session("sujal", driver=MemoryBrowserDriver())
        self.assertFalse(opened["ok"])
        self.assertEqual(opened["status"], Status.POLICY_BLOCKED.value)
        _flags_on()
        drv = MemoryBrowserDriver()
        drv.pages["https://example.com/"] = ("T", [
            BrowserElement(role="link", name="Get file", element_id="dl", handle="dl", download=True, href="https://example.com/file.bin"),
        ])
        opened = _open(drv)
        sid = opened["session_id"]
        nav = execute_browser_action(_req(
            BrowserActionType.NAVIGATE, sid, url="https://example.com/",
            precondition_observation_hash=opened["observation"].observation_hash,
        ))
        dl = execute_browser_action(_req(
            BrowserActionType.CLICK, sid,
            target=BrowserTarget(role="link", name="Get file", element_id="dl"),
            precondition_observation_hash=nav.after_observation_hash,
        ))
        self.assertEqual(dl.status, Status.UNSUPPORTED_ACTION)
        self.assertEqual(dl.error_code, "DOWNLOAD_BLOCKED")

    def test_click_javascript_and_file_href_blocked(self):
        drv = MemoryBrowserDriver()
        drv.pages["https://example.com/"] = ("T", [
            BrowserElement(
                role="link", name="JS", element_id="js", handle="js",
                href="javascript:alert(1)",
            ),
            BrowserElement(
                role="link", name="Disk", element_id="disk", handle="disk",
                href="file:///C:/Windows/notepad.exe",
            ),
        ])
        opened = _open(drv)
        sid = opened["session_id"]
        nav = execute_browser_action(_req(
            BrowserActionType.NAVIGATE, sid, url="https://example.com/",
            precondition_observation_hash=opened["observation"].observation_hash,
        ))
        js = execute_browser_action(_req(
            BrowserActionType.CLICK, sid,
            target=BrowserTarget(role="link", name="JS", element_id="js"),
            precondition_observation_hash=nav.after_observation_hash,
        ))
        self.assertEqual(js.status, Status.NAVIGATION_BLOCKED)
        self.assertEqual(js.error_code, "HREF_BLOCKED")
        self.assertEqual(drv.url, "https://example.com/")
        disk = execute_browser_action(_req(
            BrowserActionType.CLICK, sid,
            target=BrowserTarget(role="link", name="Disk", element_id="disk"),
            precondition_observation_hash=nav.after_observation_hash,
        ))
        self.assertEqual(disk.status, Status.NAVIGATION_BLOCKED)
        self.assertEqual(drv.url, "https://example.com/")

    def test_session_timeout(self):
        opened = _open()
        sid = opened["session_id"]
        from proactive.computer.browser import kernel as k
        k._SESSIONS[sid].expires_unix_ms = int(time.time() * 1000) - 1
        out = execute_browser_action(_req(
            BrowserActionType.REFRESH, sid,
            precondition_observation_hash=opened["observation"].observation_hash,
        ))
        self.assertEqual(out.status, Status.SESSION_CLOSED)
        self.assertEqual(out.error_code, "SESSION_EXPIRED")


class TestV73SafetyScan(unittest.TestCase):
    def test_no_forbidden_execution_paths(self):
        forbidden = (
            "pyautogui", "selenium", "playwright", "puppeteer",
            "shell=True", "os.system", "subprocess", "eval(", "exec(",
            "openai", "gemini", "bedrock", "elevenlabs", "groq",
            "javascript:", "file:", "data:",
        )
        for p in BROWSER_DIR.rglob("*.py"):
            src = p.read_text(encoding="utf-8")
            low = src.lower()
            for token in forbidden:
                self.assertNotIn(token.lower(), low, msg=f"{p} contains {token}")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in ("moveTo", "typewrite", "screenshot"):
                    self.fail("coordinate API " + node.attr)
                if isinstance(node, ast.Name) and node.id in ("evaluate",):
                    self.fail("evaluate name")


if __name__ == "__main__":
    unittest.main()
