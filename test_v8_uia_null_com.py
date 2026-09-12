"""NULL COM skip and element-local CurrentIsPassword fail-closed. No click/type."""
from __future__ import annotations

import ast
import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_OBSERVE_ENABLED", "false")

from proactive.computer.drivers.uia_win import (
    REDACTION_FAILED,
    TREE_LIMIT,
    UiaWalkNode,
    _NORMAL_ELEMENT,
    _PASSWORD_PROPERTY_UNAVAILABLE,
    _PASSWORD_TRUE,
    _com_fill_children,
    _com_node_from_element,
    _com_ptr_valid,
    bounded_tree_walk,
    read_foreground_uia_tree,
)

ROOT = Path(__file__).resolve().parent
UIA = ROOT / "proactive" / "computer" / "drivers" / "uia_win.py"


class NullCom:
    """comtypes-style NULL pointer: not Python None, bool() is False."""

    def __bool__(self):
        return False

    def GetRuntimeId(self):
        raise ValueError("NULL COM pointer access")

    @property
    def CurrentName(self):
        raise ValueError("NULL COM pointer access")

    @property
    def CurrentControlType(self):
        raise ValueError("NULL COM pointer access")

    @property
    def CurrentAutomationId(self):
        raise ValueError("NULL COM pointer access")

    @property
    def CurrentHasKeyboardFocus(self):
        raise ValueError("NULL COM pointer access")

    @property
    def CurrentIsPassword(self):
        raise ValueError("NULL COM pointer access")


class FakeEl:
    def __init__(self, name, ctype, *, password=False, pw_exc=False, aid=""):
        self.CurrentName = name
        self.CurrentControlType = ctype
        self.CurrentAutomationId = aid or name
        self.CurrentHasKeyboardFocus = False
        self._password = password
        self._pw_exc = pw_exc
        self._rid = (1, abs(hash(name)) % 100000)

    def __bool__(self):
        return True

    def GetRuntimeId(self):
        return self._rid

    @property
    def CurrentIsPassword(self):
        if self._pw_exc:
            raise RuntimeError("CurrentIsPassword unavailable")
        return self._password


class LinkedWalker:
    def __init__(self, children_of):
        self._children_of = children_of

    def GetFirstChildElement(self, el):
        kids = self._children_of.get(id(el), [])
        return kids[0] if kids else None

    def GetNextSiblingElement(self, el):
        for kids in self._children_of.values():
            for i, k in enumerate(kids):
                if k is el:
                    if i + 1 < len(kids):
                        return kids[i + 1]
                    return None
        return None


class FakeUia:
    def __init__(self, children_of):
        self.ControlViewWalker = LinkedWalker(children_of)


def _walk(root_el, children_of, *, max_depth=6, max_nodes=80):
    uia = FakeUia(children_of)
    root, kind = _com_node_from_element(root_el, 800.0)
    err = _com_fill_children(
        uia, root_el, root, 0, max_depth, max_nodes, [max_nodes - 1], time.monotonic(), 800,
    )
    return root, kind, err


def _names(node):
    out = []
    queue = [node]
    i = 0
    while i < len(queue):
        n = queue[i]
        i += 1
        if n is None:
            continue
        if n.name:
            out.append(n.name)
        queue.extend(list(n.children or []))
    return out


class TestNullComAndPasswordLocalFail(unittest.TestCase):
    def test_a_null_com_child_skipped_tree_continues(self):
        root_el = FakeEl("root", 50032)
        title = FakeEl("TitleBar", 50037)
        null = NullCom()
        button = FakeEl("DOOM_TEST_BUTTON", 50000)
        self.assertIsNotNone(null)
        self.assertFalse(bool(null))
        self.assertFalse(_com_ptr_valid(null))
        root, kind, err = _walk(root_el, {id(root_el): [title, null, button]})
        self.assertIsNone(err)
        self.assertEqual(kind, _NORMAL_ELEMENT)
        self.assertEqual(_names(root), ["root", "TitleBar", "DOOM_TEST_BUTTON"])

    def test_b_password_property_exception_omits_element_only(self):
        root_el = FakeEl("root", 50032)
        bad = FakeEl("mystery", 50000, pw_exc=True)
        button = FakeEl("DOOM_TEST_BUTTON", 50000)
        node, kind = _com_node_from_element(bad, 800.0)
        self.assertIsNone(node)
        self.assertEqual(kind, _PASSWORD_PROPERTY_UNAVAILABLE)
        root, _, err = _walk(root_el, {id(root_el): [bad, button]})
        self.assertIsNone(err)
        names = _names(root)
        self.assertNotIn("mystery", names)
        self.assertIn("DOOM_TEST_BUTTON", names)
        self.assertFalse(any(c.is_password is False and c.name == "mystery" for c in root.children))

    def test_c_password_true_still_protected(self):
        root_el = FakeEl("root", 50032)
        pwd = FakeEl("PasswordBox", 50004, password=True, aid="pwd")
        node, kind = _com_node_from_element(pwd, 800.0)
        self.assertEqual(kind, _PASSWORD_TRUE)
        self.assertTrue(node.is_password)
        root, _, err = _walk(root_el, {id(root_el): [pwd]})
        self.assertIsNone(err)
        self.assertTrue(root.children[0].is_password)
        meta = bounded_tree_walk(root, timeout_ms=800, max_depth=6, max_nodes=80)
        self.assertEqual(meta.outcome, "OK")
        self.assertTrue(meta.sensitive_hit)

    def test_d_password_plus_leaked_secret_still_redaction_failed(self):
        secret = "hunter2-password"
        root = UiaWalkNode(
            runtime_id="1",
            automation_id="pwd",
            control_type="50004",
            is_password=True,
            leaked_secret=secret,
        )
        meta = bounded_tree_walk(root, timeout_ms=800, max_depth=6, max_nodes=80)
        self.assertEqual(meta.outcome, REDACTION_FAILED)
        meta2, tree = read_foreground_uia_tree(
            1, timeout_ms=800, max_depth=6, max_nodes=80, tree_root=root,
        )
        self.assertEqual(meta2.outcome, REDACTION_FAILED)
        self.assertIs(tree, root)

    def test_e_button_after_bad_sibling_discoverable(self):
        root_el = FakeEl("DOOM Safe Test Window", 50032)
        null = NullCom()
        button = FakeEl("DOOM_TEST_BUTTON", 50000)
        root, _, err = _walk(root_el, {id(root_el): [null, button]})
        self.assertIsNone(err)
        self.assertIn("DOOM_TEST_BUTTON", _names(root))
        btn = root.children[0]
        self.assertEqual(btn.name, "DOOM_TEST_BUTTON")
        self.assertEqual(btn.control_type, "50000")

    def test_f_edit_after_bad_sibling_discoverable(self):
        root_el = FakeEl("DOOM Safe Test Window", 50032)
        null = NullCom()
        edit = FakeEl("DOOM_TEST_INPUT", 50004)
        root, _, err = _walk(root_el, {id(root_el): [null, edit]})
        self.assertIsNone(err)
        self.assertIn("DOOM_TEST_INPUT", _names(root))
        field = root.children[0]
        self.assertEqual(field.name, "DOOM_TEST_INPUT")
        self.assertEqual(field.control_type, "50004")

    def test_g_traversal_bounds_still_enforced(self):
        root_el = FakeEl("root", 50032)
        kids = [FakeEl("n%d" % i, 50000) for i in range(12)]
        root, _, err = _walk(root_el, {id(root_el): kids}, max_depth=6, max_nodes=4)
        self.assertEqual(err, TREE_LIMIT)
        self.assertLessEqual(len(root.children), 3)

    def test_fill_children_does_not_abort_tree_on_skip(self):
        src = UIA.read_text(encoding="utf-8")
        fill = src.split("def _com_fill_children")[1].split("def _doom_root")[0]
        self.assertNotIn("return REDACTION_FAILED", fill)
        self.assertIn("_com_ptr_valid", fill)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, ("eval", "exec"))


if __name__ == "__main__":
    unittest.main()
