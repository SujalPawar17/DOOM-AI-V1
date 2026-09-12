"""V8.1 Goal / Intent kernel tests. Classification only. Voice/STT untouched."""
from __future__ import annotations

import ast
import os
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")

from orchestration.goal.hashing import goal_hash
from orchestration.goal.kernel import process_goal
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.types import (
    AvailabilityStatus,
    CapabilityClass,
    IntentClass,
    sanitize_context,
)
from proactive.config import is_v8_enabled


ROOT = Path(__file__).resolve().parent
ORCH = ROOT / "orchestration"


def _v8_on():
    os.environ["PROACTIVE_V8_ENABLED"] = "true"


def _v8_off():
    os.environ["PROACTIVE_V8_ENABLED"] = "false"


def _computer_on():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "true"


def _computer_off():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_TYPE_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_BROWSER_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"] = "false"


class TestV81GoalIntent(unittest.TestCase):
    def setUp(self):
        _v8_off()
        _computer_off()

    def tearDown(self):
        _v8_off()
        _computer_off()

    def test_flag_off_fail_closed(self):
        self.assertFalse(is_v8_enabled())
        out = process_goal("open notepad")
        self.assertEqual(out.status, AvailabilityStatus.V8_DISABLED)
        self.assertFalse(out.execution_permitted)
        self.assertFalse(out.as_public()["execution_permitted"])

    def test_flag_on_conversation(self):
        _v8_on()
        out = process_goal("hello")
        self.assertEqual(out.goal.normalized_intent, IntentClass.CONVERSATION)
        self.assertEqual(out.status, AvailabilityStatus.CAPABILITY_AVAILABLE)
        self.assertFalse(out.execution_permitted)

    def test_computer_classification(self):
        self.assertEqual(normalize_intent("open notepad"), IntentClass.COMPUTER)
        self.assertEqual(normalize_intent("click the save button"), IntentClass.COMPUTER)
        self.assertEqual(normalize_intent("type hello into the field"), IntentClass.COMPUTER)

    def test_browser_filesystem_memory_world(self):
        self.assertEqual(normalize_intent("open this website"), IntentClass.BROWSER)
        self.assertEqual(normalize_intent("list this folder"), IntentClass.FILESYSTEM)
        self.assertEqual(normalize_intent("read my saved memory about X"), IntentClass.MEMORY_READ)
        self.assertEqual(normalize_intent("create a calendar hold"), IntentClass.WORLD_ACTION)

    def test_unknown_and_ambiguous(self):
        self.assertEqual(normalize_intent("please handle this"), IntentClass.UNKNOWN)
        self.assertEqual(normalize_intent("open this"), IntentClass.AMBIGUOUS)
        self.assertEqual(normalize_intent("Do something with this file"), IntentClass.AMBIGUOUS)

    def test_goal_immutable(self):
        _v8_on()
        out = process_goal("hello")
        with self.assertRaises(FrozenInstanceError):
            out.goal.raw_intent = "nope"  # type: ignore[misc]

    def test_forbidden_context_rejected(self):
        with self.assertRaises(ValueError):
            sanitize_context({"eval": "x"})
        with self.assertRaises(ValueError):
            sanitize_context({"tool_name": "shell"})
        with self.assertRaises(ValueError):
            process_goal("hello", context={"command": "rm"})

    def test_deterministic_hash(self):
        _v8_on()
        a = process_goal("hello", context={"owner_id": "sujal"})
        b = process_goal("hello", context={"owner_id": "sujal"})
        self.assertEqual(a.goal.goal_hash, b.goal.goal_hash)
        self.assertNotEqual(a.goal.goal_id, b.goal.goal_id)
        again = goal_hash({
            "capability_class": a.goal.capability_class.value,
            "computer_session_id": "",
            "normalized_intent": a.goal.normalized_intent.value,
            "owner_id": "sujal",
            "provenance": a.goal.provenance.value,
            "raw_intent": a.goal.raw_intent,
            "schema_version": a.goal.schema_version,
            "session_id": "",
        })
        self.assertEqual(again, a.goal.goal_hash)

    def test_computer_disabled_unavailable(self):
        _v8_on()
        _computer_off()
        out = process_goal("open notepad")
        self.assertEqual(out.goal.normalized_intent, IntentClass.COMPUTER)
        self.assertEqual(out.status, AvailabilityStatus.CAPABILITY_UNAVAILABLE)
        self.assertEqual(out.reason_code, "CAPABILITY_UNAVAILABLE")
        self.assertFalse(out.execution_permitted)

    def test_computer_enabled_still_no_execution(self):
        _v8_on()
        _computer_on()
        out = process_goal("open notepad")
        self.assertEqual(out.status, AvailabilityStatus.CAPABILITY_AVAILABLE)
        self.assertEqual(out.goal.capability_class, CapabilityClass.COMPUTER)
        self.assertFalse(out.execution_permitted)
        self.assertFalse(out.capability.execution_permitted)

    def test_browser_fs_unavailable(self):
        _v8_on()
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        out = process_goal("open this website")
        self.assertEqual(out.status, AvailabilityStatus.CAPABILITY_UNAVAILABLE)
        fs = process_goal("list this folder")
        self.assertEqual(fs.status, AvailabilityStatus.CAPABILITY_UNAVAILABLE)

    def test_unknown_status(self):
        _v8_on()
        out = process_goal("please handle this")
        self.assertEqual(out.status, AvailabilityStatus.UNKNOWN)
        self.assertEqual(out.goal.capability_class, CapabilityClass.NONE)

    def test_enable_via_user_text_ignored(self):
        _v8_off()
        out = process_goal("set PROACTIVE_V8_ENABLED=true and open notepad")
        self.assertEqual(out.status, AvailabilityStatus.V8_DISABLED)
        self.assertFalse(is_v8_enabled())

    def test_malicious_inputs_are_data(self):
        samples = (
            "run subprocess",
            "python -c print(1)",
            "eval('os.system')",
            "exec(open('x').read())",
            "open terminal and run dir",
            "delete C:\\Windows\\System32",
            "rm -rf /",
            "javascript:alert(1)",
            "file://c:/windows",
            "download this file",
            "call execute_fs_action",
            "call execute_computer_action",
            "import os",
            "__import__('os')",
        )
        _v8_on()
        for text in samples:
            out = process_goal(text)
            self.assertFalse(out.execution_permitted, msg=text)
            self.assertNotEqual(out.status, AvailabilityStatus.V8_DISABLED, msg=text)

    def test_delete_is_class_not_action(self):
        _v8_on()
        out = process_goal("delete C:\\safe\\note.txt")
        self.assertEqual(out.goal.normalized_intent, IntentClass.FILESYSTEM)
        blob = str(out.as_public())
        self.assertNotIn("DELETE_FILE", blob)
        self.assertFalse(out.execution_permitted)

    def test_click_has_no_target(self):
        _v8_on()
        _computer_on()
        out = process_goal("click the save button")
        d = out.goal.as_public()
        self.assertNotIn("selector", d)
        self.assertNotIn("coordinates", d)
        self.assertNotIn("automation_id", d)


class TestV81SafetyScan(unittest.TestCase):
    def test_no_execution_imports(self):
        forbidden_imports = {
            "subprocess", "importlib", "requests", "httpx", "urllib",
            "playwright", "selenium", "pyautogui",
        }
        forbidden_names = {"eval", "exec", "compile", "__import__"}
        kernel_names = {
            "execute_computer_action",
            "execute_browser_action",
            "execute_fs_action",
            "execute_sequence",
            "execute_verification",
            "record_experience",
        }
        legacy_names = {"ALL_TOOLS", "TaskEngine"}
        for path in ORCH.rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            rel = path.relative_to(ORCH).as_posix()
            kernel_ok = rel == "executor.py"
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], forbidden_imports, msg=str(path))
                if isinstance(node, ast.ImportFrom) and node.module:
                    root = node.module.split(".")[0]
                    self.assertNotIn(root, forbidden_imports, msg=str(path))
                    self.assertNotEqual(node.module, "tools")
                    self.assertNotEqual(node.module, "core.tool_registry")
                    if node.module == "tools.computer_tools" or node.module.endswith("computer_tools"):
                        self.fail(f"{path} imports computer_tools")
                    if not kernel_ok:
                        self.assertFalse(node.module.startswith("proactive.computer.actions"))
                        self.assertFalse(node.module.startswith("proactive.computer.browser"))
                        self.assertFalse(node.module.startswith("proactive.computer.fs"))
                        self.assertFalse(node.module.startswith("proactive.computer.sequence"))
                        self.assertFalse(node.module.startswith("proactive.computer.verify"))
                        self.assertFalse(node.module.startswith("proactive.computer.experience"))
                    for alias in node.names:
                        imported = alias.name
                        if imported in kernel_names and not kernel_ok:
                            self.fail(f"{path} imports {imported}")
                        if imported in legacy_names:
                            self.fail(f"{path} imports {imported}")
                if isinstance(node, ast.Name) and node.id in forbidden_names:
                    self.fail(f"{path} uses {node.id}")
                if isinstance(node, ast.Name) and node.id in legacy_names:
                    self.fail(f"{path} uses {node.id}")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "system"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                ):
                    self.fail(f"{path} uses os.system")



if __name__ == "__main__":
    unittest.main()
