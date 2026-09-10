"""V7.2 UIA click/type kernel. No physical desktop required."""
from __future__ import annotations

import ast
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_OBSERVE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_CLICK_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_TYPE_ENABLED", "false")

from core.cost_guard import cost_guard
from proactive.computer.actions import execute_computer_action
from proactive.computer.actions.adapter import MemoryUiaAdapter
from proactive.computer.actions.target import ActionNode
from proactive.computer.actions.types import (
    REDACTED,
    ActionType,
    ApprovalState,
    ComputerActionRequest,
    PATTERN_INVOKE,
    PATTERN_VALUE,
    Status,
    TargetIdentity,
)
from proactive.computer.hash import observation_hash
from proactive.computer.observe import ComputerObservation
from proactive.computer.policy import CAPABILITY_ID, SCHEMA_VERSION, click_allowed, type_allowed


ROOT = Path(__file__).resolve().parent
ACTIONS_DIR = ROOT / "proactive" / "computer" / "actions"
COMPUTER_DIR = ROOT / "proactive" / "computer"


def _obs(**kwargs) -> ComputerObservation:
    o = ComputerObservation(
        owner_id="sujal",
        capability_id=CAPABILITY_ID,
        hwnd=42,
        pid=1001,
        exe_path_norm=r"c:\windows\system32\notepad.exe",
        publisher_norm="",
        window_class="Notepad",
        uia_runtime_id="42.1",
        uia_automation_id="titleBar",
        uia_control_type="50032",
        uia_tree_digest="abc",
        monitor_id=1,
        privacy_class="PRIVATE",
        schema_version=SCHEMA_VERSION,
    )
    for k, v in kwargs.items():
        setattr(o, k, v)
    o.observation_hash = observation_hash(o.as_authoritative())
    return o


def _flags_on():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_TYPE_ENABLED"] = "true"


def _flags_off():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_TYPE_ENABLED"] = "false"


def _button_tree():
    btn = ActionNode(
        automation_id="okButton",
        runtime_id="1.9",
        control_type="50000",
        name="OK",
        patterns=(PATTERN_INVOKE,),
    )
    root = ActionNode(
        automation_id="root",
        runtime_id="1",
        control_type="50032",
        children=[btn],
    )
    btn.parent = root
    return root, btn


def _edit_tree(*, password: bool = False, readonly: bool = False, extra=None):
    edit = ActionNode(
        automation_id="edit1",
        runtime_id="1.4",
        control_type="50004",
        name="Document",
        is_password=password,
        value_readonly=readonly,
        patterns=(PATTERN_VALUE,),
    )
    kids = [edit]
    if extra is not None:
        kids.append(extra)
    root = ActionNode(
        automation_id="root",
        runtime_id="1",
        control_type="50032",
        children=kids,
    )
    edit.parent = root
    return root, edit


def _req(action: ActionType, target: TargetIdentity, obs: ComputerObservation, **kw) -> ComputerActionRequest:
    d = dict(
        action_id=str(uuid.uuid4()),
        action_type=action,
        target=target,
        precondition_observation_hash=obs.observation_hash,
        owner_id="sujal",
        session_id="sess-1",
        approval_state=ApprovalState.APPROVED,
        timeout_ms=800,
        text=kw.pop("text", ""),
        sensitive=kw.pop("sensitive", False),
    )
    d.update(kw)
    return ComputerActionRequest(**d)


class TestV72Contracts(unittest.TestCase):
    def test_click_contract_explicit(self):
        obs = _obs()
        t = TargetIdentity(automation_id="okButton", runtime_id="1.9", control_type="50000")
        req = _req(ActionType.CLICK, t, obs)
        self.assertEqual(req.action_type, ActionType.CLICK)
        self.assertTrue(t.is_deterministic())
        self.assertNotEqual(req.action_type.value, "execute")

    def test_type_contract_explicit(self):
        obs = _obs()
        t = TargetIdentity(automation_id="edit1", runtime_id="1.4", control_type="50004")
        req = _req(ActionType.TYPE, t, obs, text="hello")
        self.assertEqual(req.action_type, ActionType.TYPE)
        self.assertEqual(req.text, "hello")


class TestV72Kernel(unittest.TestCase):
    def setUp(self):
        cost_guard.reset_records_for_tests()
        _flags_on()

    def tearDown(self):
        _flags_off()

    def _run(self, req, adapter, obs, post=None):
        with patch("proactive.computer.actions.kernel._emergency_stop", return_value=False):
            return execute_computer_action(
                req,
                current_observation=obs,
                post_observation=post if post is not None else _obs(uia_tree_digest="after"),
                adapter=adapter,
                capture_after=True,
            )

    def test_valid_target_resolution_and_invoke_click(self):
        obs = _obs()
        root, _btn = _button_tree()
        adapter = MemoryUiaAdapter(root)
        t = TargetIdentity(automation_id="okButton", runtime_id="1.9", control_type="50000")
        out = self._run(_req(ActionType.CLICK, t, obs), adapter, obs)
        self.assertEqual(out.status, Status.SUCCESS)
        self.assertEqual(adapter.invoked, ["invoke"])
        self.assertEqual(out.precondition_status, "ok")
        self.assertTrue(out.after_observation_hash)
        self.assertNotEqual(out.after_observation_hash, "")

    def test_type_value_pattern(self):
        obs = _obs()
        root, _e = _edit_tree()
        adapter = MemoryUiaAdapter(root)
        t = TargetIdentity(automation_id="edit1", runtime_id="1.4", control_type="50004")
        out = self._run(_req(ActionType.TYPE, t, obs, text="hello doom"), adapter, obs)
        self.assertEqual(out.status, Status.SUCCESS)
        self.assertEqual(adapter.last_value, "hello doom")
        self.assertIn("value", out.telemetry.get("pattern", ""))

    def test_target_not_found(self):
        obs = _obs()
        root, _ = _button_tree()
        t = TargetIdentity(automation_id="missing", runtime_id="9.9", control_type="50000")
        out = self._run(_req(ActionType.CLICK, t, obs), MemoryUiaAdapter(root), obs)
        self.assertEqual(out.status, Status.TARGET_NOT_FOUND)

    def test_ambiguous_target(self):
        obs = _obs()
        a = ActionNode(automation_id="dup", runtime_id="1.1", control_type="50000", patterns=(PATTERN_INVOKE,))
        b = ActionNode(automation_id="dup", runtime_id="1.2", control_type="50000", patterns=(PATTERN_INVOKE,))
        root = ActionNode(automation_id="root", runtime_id="1", control_type="50032", children=[a, b])
        t = TargetIdentity(automation_id="dup", runtime_id="", control_type="50000")
        # runtime empty but automation+type still deterministic; two matches
        t = TargetIdentity(automation_id="dup", runtime_id="x", control_type="50000")
        out = self._run(_req(ActionType.CLICK, t, obs), MemoryUiaAdapter(root), obs)
        self.assertEqual(out.status, Status.TARGET_NOT_FOUND)
        t2 = TargetIdentity(automation_id="dup", runtime_id="", control_type="50000")
        self.assertTrue(t2.is_deterministic())
        out2 = self._run(_req(ActionType.CLICK, t2, obs), MemoryUiaAdapter(root), obs)
        self.assertEqual(out2.status, Status.TARGET_AMBIGUOUS)

    def test_weak_identity_fail_closed(self):
        obs = _obs()
        t = TargetIdentity(name="OK", control_type="")
        self.assertFalse(t.is_deterministic())
        root, _ = _button_tree()
        out = self._run(_req(ActionType.CLICK, t, obs), MemoryUiaAdapter(root), obs)
        self.assertEqual(out.status, Status.TARGET_AMBIGUOUS)

    def test_stale_observation_hash(self):
        obs = _obs()
        other = _obs(uia_tree_digest="changed")
        root, _ = _button_tree()
        t = TargetIdentity(automation_id="okButton", runtime_id="1.9", control_type="50000")
        req = _req(ActionType.CLICK, t, other)
        out = self._run(req, MemoryUiaAdapter(root), obs)
        self.assertEqual(out.status, Status.PRECONDITION_FAILED)
        self.assertEqual(out.error_code, "STALE_OBSERVATION_HASH")

    def test_changed_target_identity(self):
        obs = _obs()
        root, _ = _button_tree()
        adapter = MemoryUiaAdapter(root)
        t = TargetIdentity(automation_id="okButton", runtime_id="1.9", control_type="50000")

        class Shift(MemoryUiaAdapter):
            def find(self, ident):
                resolved, st = super().find(ident)
                if resolved is not None:
                    resolved.identity = TargetIdentity(
                        automation_id=resolved.identity.automation_id,
                        runtime_id=resolved.identity.runtime_id,
                        control_type="50099",
                    )
                return resolved, st

        out = self._run(_req(ActionType.CLICK, t, obs), Shift(root), obs)
        self.assertEqual(out.status, Status.PRECONDITION_FAILED)
        self.assertEqual(out.error_code, "TARGET_IDENTITY_CHANGED")

    def test_unsupported_control_no_pattern(self):
        obs = _obs()
        node = ActionNode(automation_id="pane", runtime_id="1.8", control_type="50033", patterns=())
        root = ActionNode(automation_id="root", runtime_id="1", control_type="50032", children=[node])
        t = TargetIdentity(automation_id="pane", runtime_id="1.8", control_type="50033")
        out = self._run(_req(ActionType.CLICK, t, obs), MemoryUiaAdapter(root), obs)
        self.assertEqual(out.status, Status.UNSUPPORTED_ACTION)

    def test_emergency_stop(self):
        obs = _obs()
        root, _ = _button_tree()
        t = TargetIdentity(automation_id="okButton", runtime_id="1.9", control_type="50000")
        with patch("proactive.computer.actions.kernel._emergency_stop", return_value=True):
            out = execute_computer_action(
                _req(ActionType.CLICK, t, obs),
                current_observation=obs,
                adapter=MemoryUiaAdapter(root),
                capture_after=False,
            )
        self.assertEqual(out.status, Status.EMERGENCY_STOP_ACTIVE)

    def test_policy_block_flags(self):
        _flags_off()
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "false"
        obs = _obs()
        t = TargetIdentity(automation_id="okButton", runtime_id="1.9", control_type="50000")
        out = execute_computer_action(
            _req(ActionType.CLICK, t, obs),
            current_observation=obs,
            adapter=MemoryUiaAdapter(_button_tree()[0]),
            capture_after=False,
        )
        self.assertEqual(out.status, Status.POLICY_BLOCKED)
        self.assertFalse(click_allowed())

    def test_cost_guard_block(self):
        obs = _obs()
        t = TargetIdentity(automation_id="okButton", runtime_id="1.9", control_type="50000")
        blocked = MagicMock()
        blocked.is_allow = False
        with patch("proactive.computer.actions.kernel.cost_guard.authorize", return_value=blocked):
            with patch("proactive.computer.actions.kernel._emergency_stop", return_value=False):
                out = execute_computer_action(
                    _req(ActionType.CLICK, t, obs),
                    current_observation=obs,
                    adapter=MemoryUiaAdapter(_button_tree()[0]),
                    capture_after=False,
                )
        self.assertEqual(out.status, Status.POLICY_BLOCKED)
        self.assertEqual(out.error_code, "COST_GUARD_BLOCKED")

    def test_approval_required(self):
        obs = _obs()
        t = TargetIdentity(automation_id="okButton", runtime_id="1.9", control_type="50000")
        req = _req(ActionType.CLICK, t, obs, approval_state=ApprovalState.NONE)
        out = self._run(req, MemoryUiaAdapter(_button_tree()[0]), obs)
        self.assertEqual(out.status, Status.APPROVAL_REQUIRED)

    def test_approval_denied(self):
        obs = _obs()
        t = TargetIdentity(automation_id="okButton", runtime_id="1.9", control_type="50000")
        req = _req(ActionType.CLICK, t, obs, approval_state=ApprovalState.DENIED)
        out = self._run(req, MemoryUiaAdapter(_button_tree()[0]), obs)
        self.assertEqual(out.status, Status.APPROVAL_DENIED)

    def test_password_type_risk_blocked(self):
        obs = _obs()
        t = TargetIdentity(automation_id="edit1", runtime_id="1.4", control_type="50004", is_password=True)
        req = _req(ActionType.TYPE, t, obs, text="s3cret-value")
        out = self._run(req, MemoryUiaAdapter(_edit_tree(password=True)[0]), obs)
        self.assertEqual(out.status, Status.RISK_BLOCKED)
        blob = str(out.as_dict())
        self.assertNotIn("s3cret-value", blob)
        self.assertEqual(out.telemetry.get("payload_preview"), REDACTED)

    def test_telemetry_redacts_sensitive_type(self):
        obs = _obs()
        t = TargetIdentity(automation_id="edit1", runtime_id="1.4", control_type="50004")
        req = _req(ActionType.TYPE, t, obs, text="super-secret-token", sensitive=True)
        out = self._run(req, MemoryUiaAdapter(_edit_tree()[0]), obs)
        self.assertEqual(out.status, Status.SUCCESS)
        self.assertEqual(out.telemetry.get("payload_preview"), REDACTED)
        self.assertNotIn("super-secret-token", str(out.as_dict()))

    def test_disabled_by_default(self):
        _flags_off()
        self.assertFalse(click_allowed())
        self.assertFalse(type_allowed())

    def test_post_action_observation_hash(self):
        obs = _obs()
        after = _obs(uia_tree_digest="post")
        self.assertNotEqual(obs.observation_hash, after.observation_hash)
        root, _ = _button_tree()
        t = TargetIdentity(automation_id="okButton", runtime_id="1.9", control_type="50000")
        out = self._run(_req(ActionType.CLICK, t, obs), MemoryUiaAdapter(root), obs, post=after)
        self.assertEqual(out.before_observation_hash, obs.observation_hash)
        self.assertEqual(out.after_observation_hash, after.observation_hash)


class TestV72SafetyScan(unittest.TestCase):
    def test_no_coordinate_or_injection_stack(self):
        forbidden = (
            "pyautogui", "pynput", "mouse_event", "SetCursorPos",
            "SendInput", "keybd_event", "selenium", "playwright",
            "shell=True", "os.system", "subprocess",
        )
        for p in ACTIONS_DIR.rglob("*.py"):
            src = p.read_text(encoding="utf-8")
            low = src.lower()
            for token in forbidden:
                self.assertNotIn(token.lower(), low)
            self.assertNotIn("http://", src)
            self.assertNotIn("https://", src)
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in ("click", "moveTo", "typewrite"):
                    self.fail("coordinate/mouse API " + node.attr)

    def test_v71_observe_capability_untouched(self):
        self.assertEqual(CAPABILITY_ID, "COMPUTER_OBSERVE_DESKTOP")
        self.assertEqual(SCHEMA_VERSION, "v71.1")


if __name__ == "__main__":
    unittest.main()
