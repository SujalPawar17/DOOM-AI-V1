"""V7.4 bounded filesystem kernel tests. Uses a temporary sandbox root only."""
from __future__ import annotations

import ast
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS", "")

from core.cost_guard import cost_guard
from proactive.computer.actions.types import ApprovalState
from proactive.computer.fs.kernel import execute_fs_action
from proactive.computer.fs.observe import observe_path
from proactive.computer.fs.paths import canonicalize, scope_status
from proactive.computer.fs.types import FsActionRequest, FsActionType, ObjectType, Status
from proactive.computer.policy import filesystem_allowed


ROOT = Path(__file__).resolve().parent
FS_DIR = ROOT / "proactive" / "computer" / "fs"


def _flags_on(root: Path, denied: str = ""):
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS"] = str(root)
    os.environ["PROACTIVE_COMPUTER_FS_DENIED_ROOTS"] = denied


def _flags_off():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS"] = ""
    os.environ["PROACTIVE_COMPUTER_FS_DENIED_ROOTS"] = ""
    os.environ.pop("PROACTIVE_COMPUTER_FS_MAX_READ_BYTES", None)
    os.environ.pop("PROACTIVE_COMPUTER_FS_MAX_WRITE_BYTES", None)
    os.environ.pop("PROACTIVE_COMPUTER_FS_MAX_LIST", None)


def _req(action: FsActionType, path: str, **kw) -> FsActionRequest:
    return FsActionRequest(
        action_id=str(uuid.uuid4()),
        action_type=action,
        path=path,
        owner_id="sujal",
        dest_path=kw.pop("dest_path", ""),
        content=kw.pop("content", b""),
        precondition_observation_hash=kw.pop("precondition_observation_hash", ""),
        expected_exists=kw.pop("expected_exists", None),
        expected_type=kw.pop("expected_type", None),
        approval_state=kw.pop("approval_state", ApprovalState.NONE),
    )


class TestV74Filesystem(unittest.TestCase):
    def setUp(self):
        cost_guard.reset_records_for_tests()
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name).resolve()
        _flags_on(self.root)

    def tearDown(self):
        _flags_off()
        self.td.cleanup()

    def test_canonicalization_and_traversal_and_roots(self):
        inside = (self.root / "a").resolve()
        inside.mkdir()
        p, st = canonicalize(str(inside / ".." / "a"))
        self.assertEqual(st, Status.SUCCESS)
        self.assertEqual(p, inside)
        escaped = self.root.parent / "nope"
        p2, st2 = canonicalize(str(escaped))
        self.assertEqual(st2, Status.SUCCESS)
        self.assertEqual(scope_status(p2), Status.PATH_NOT_ALLOWED)
        denied = self.root / "secret"
        denied.mkdir()
        os.environ["PROACTIVE_COMPUTER_FS_DENIED_ROOTS"] = str(denied)
        self.assertEqual(scope_status(denied.resolve()), Status.PATH_DENIED)
        _, bad = canonicalize("")
        self.assertEqual(bad, Status.PATH_INVALID)
        _, rel = canonicalize("relative\\path")
        self.assertEqual(rel, Status.PATH_INVALID)
        _, device = canonicalize(r"\\?\C:\Windows")
        self.assertEqual(device, Status.PATH_INVALID)
        _, unc = canonicalize(r"\\localhost\c$\Windows")
        self.assertEqual(unc, Status.PATH_INVALID)

    def test_allowed_root_prefix_not_confused(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        evil = self.root / "workspace_evil"
        evil.mkdir()
        (evil / "secret.txt").write_text("nope", encoding="utf-8")
        _flags_on(workspace)
        escaped = execute_fs_action(_req(FsActionType.READ_FILE, str(evil / "secret.txt")))
        self.assertEqual(escaped.status, Status.PATH_NOT_ALLOWED)

    def test_observe_list_read_write_copy_move_delete(self):
        d = self.root / "work"
        d.mkdir()
        obs = execute_fs_action(_req(FsActionType.OBSERVE_PATH, str(d)))
        self.assertEqual(obs.status, Status.SUCCESS)
        created = execute_fs_action(_req(
            FsActionType.WRITE_FILE, str(d / "note.txt"),
            content=b"hello",
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash=observe_path(d / "note.txt").observation_hash,
            expected_exists=False,
        ))
        self.assertEqual(created.status, Status.SUCCESS)
        read = execute_fs_action(_req(FsActionType.READ_FILE, str(d / "note.txt")))
        self.assertEqual(read.status, Status.SUCCESS)
        self.assertEqual(read.content, b"hello")
        self.assertNotIn("hello", str(read.as_dict()["telemetry"]))
        listed = execute_fs_action(_req(FsActionType.LIST_DIRECTORY, str(d)))
        self.assertEqual(listed.status, Status.SUCCESS)
        self.assertTrue(any(c["name"] == "note.txt" for c in listed.listing))
        copied = execute_fs_action(_req(
            FsActionType.COPY_FILE, str(d / "note.txt"),
            dest_path=str(d / "copy.txt"),
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash=observe_path(d / "note.txt").observation_hash,
            expected_exists=True,
            expected_type=ObjectType.FILE,
        ))
        self.assertEqual(copied.status, Status.SUCCESS)
        moved = execute_fs_action(_req(
            FsActionType.MOVE_FILE, str(d / "copy.txt"),
            dest_path=str(d / "moved.txt"),
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash=observe_path(d / "copy.txt").observation_hash,
            expected_exists=True,
            expected_type=ObjectType.FILE,
        ))
        self.assertEqual(moved.status, Status.SUCCESS)
        self.assertFalse((d / "copy.txt").exists())
        self.assertTrue((d / "moved.txt").exists())
        deleted = execute_fs_action(_req(
            FsActionType.DELETE_FILE, str(d / "moved.txt"),
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash=observe_path(d / "moved.txt").observation_hash,
            expected_exists=True,
            expected_type=ObjectType.FILE,
        ))
        self.assertEqual(deleted.status, Status.SUCCESS)
        self.assertFalse((d / "moved.txt").exists())
        self.assertTrue(deleted.after_observation_hash)

    def test_create_directory_and_dir_delete_blocked(self):
        target = self.root / "newdir"
        out = execute_fs_action(_req(
            FsActionType.CREATE_DIRECTORY, str(target),
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash=observe_path(target).observation_hash,
            expected_exists=False,
        ))
        self.assertEqual(out.status, Status.SUCCESS)
        blocked = execute_fs_action(_req(
            FsActionType.DELETE_FILE, str(target),
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash=observe_path(target).observation_hash,
            expected_exists=True,
            expected_type=ObjectType.DIRECTORY,
        ))
        self.assertEqual(blocked.status, Status.DIRECTORY_DELETE_BLOCKED)

    def test_oversized_read_and_missing_and_invalid_dest(self):
        os.environ["PROACTIVE_COMPUTER_FS_MAX_READ_BYTES"] = "8"
        f = self.root / "big.bin"
        f.write_bytes(b"0123456789")
        out = execute_fs_action(_req(FsActionType.READ_FILE, str(f)))
        self.assertEqual(out.status, Status.FILE_TOO_LARGE)
        missing = execute_fs_action(_req(
            FsActionType.COPY_FILE, str(self.root / "nope.txt"),
            dest_path=str(self.root / "x.txt"),
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash=observe_path(self.root / "nope.txt").observation_hash,
        ))
        self.assertEqual(missing.status, Status.PATH_NOT_FOUND)
        src = self.root / "ok.txt"
        src.write_text("x", encoding="utf-8")
        dest = self.root.parent / "outside.txt"
        bad = execute_fs_action(_req(
            FsActionType.COPY_FILE, str(src),
            dest_path=str(dest),
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash=observe_path(src).observation_hash,
            expected_exists=True,
            expected_type=ObjectType.FILE,
        ))
        self.assertEqual(bad.status, Status.PATH_NOT_ALLOWED)

    def test_atomic_write_failure_leaves_original(self):
        f = self.root / "keep.txt"
        f.write_bytes(b"original")
        with patch("proactive.computer.fs.kernel.os.replace", side_effect=OSError("fail")):
            out = execute_fs_action(_req(
                FsActionType.WRITE_FILE, str(f),
                content=b"new",
                approval_state=ApprovalState.APPROVED,
                precondition_observation_hash=observe_path(f).observation_hash,
                expected_exists=True,
                expected_type=ObjectType.FILE,
            ))
        self.assertEqual(out.status, Status.EXECUTION_FAILED)
        self.assertEqual(f.read_bytes(), b"original")

    def test_stale_hash_identity_policy_approval_stop(self):
        f = self.root / "x.txt"
        f.write_bytes(b"a")
        stale = execute_fs_action(_req(
            FsActionType.DELETE_FILE, str(f),
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash="deadbeef",
            expected_exists=True,
            expected_type=ObjectType.FILE,
        ))
        self.assertEqual(stale.status, Status.PRECONDITION_FAILED)
        f.unlink()
        f.mkdir()
        mismatch = execute_fs_action(_req(
            FsActionType.DELETE_FILE, str(f),
            approval_state=ApprovalState.APPROVED,
            precondition_observation_hash=observe_path(f).observation_hash,
            expected_exists=True,
            expected_type=ObjectType.FILE,
        ))
        self.assertEqual(mismatch.status, Status.PRECONDITION_FAILED)
        self.assertEqual(mismatch.error_code, "IDENTITY_MISMATCH")
        need = execute_fs_action(_req(
            FsActionType.WRITE_FILE, str(self.root / "n.txt"),
            content=b"z",
            approval_state=ApprovalState.NONE,
            precondition_observation_hash=observe_path(self.root / "n.txt").observation_hash,
        ))
        self.assertEqual(need.status, Status.APPROVAL_REQUIRED)
        denied = execute_fs_action(_req(
            FsActionType.WRITE_FILE, str(self.root / "n.txt"),
            content=b"z",
            approval_state=ApprovalState.DENIED,
            precondition_observation_hash=observe_path(self.root / "n.txt").observation_hash,
        ))
        self.assertEqual(denied.status, Status.APPROVAL_DENIED)
        with patch("proactive.computer.fs.kernel._emergency_stop", return_value=True):
            stopped = execute_fs_action(_req(
                FsActionType.WRITE_FILE, str(self.root / "n.txt"),
                content=b"z",
                approval_state=ApprovalState.APPROVED,
                precondition_observation_hash=observe_path(self.root / "n.txt").observation_hash,
            ))
        self.assertEqual(stopped.status, Status.EMERGENCY_STOP_ACTIVE)
        blocked = MagicMock()
        blocked.is_allow = False
        with patch("proactive.computer.fs.kernel.cost_guard.authorize", return_value=blocked):
            cg = execute_fs_action(_req(FsActionType.OBSERVE_PATH, str(self.root)))
        self.assertEqual(cg.status, Status.POLICY_BLOCKED)
        self.assertEqual(cg.error_code, "COST_GUARD_BLOCKED")

    def test_sensitive_and_default_off_and_bounded_list(self):
        envf = self.root / ".env"
        envf.write_text("SECRET=1", encoding="utf-8")
        out = execute_fs_action(_req(FsActionType.READ_FILE, str(envf)))
        self.assertEqual(out.status, Status.SENSITIVE_FILE_BLOCKED)
        deleted = execute_fs_action(_req(
            FsActionType.DELETE_FILE, str(envf),
            approval_state=ApprovalState.APPROVED,
        ))
        self.assertEqual(deleted.status, Status.SENSITIVE_FILE_BLOCKED)
        self.assertTrue(envf.exists())
        _flags_off()
        self.assertFalse(filesystem_allowed())
        blocked = execute_fs_action(_req(FsActionType.OBSERVE_PATH, str(self.root)))
        self.assertEqual(blocked.status, Status.POLICY_BLOCKED)
        _flags_on(self.root)
        os.environ["PROACTIVE_COMPUTER_FS_MAX_LIST"] = "2"
        for i in range(5):
            (self.root / f"f{i}.txt").write_text("x", encoding="utf-8")
        listed = execute_fs_action(_req(FsActionType.LIST_DIRECTORY, str(self.root)))
        self.assertEqual(listed.status, Status.SUCCESS)
        self.assertTrue(listed.truncated)
        self.assertLessEqual(len(listed.listing), 2)

    def test_symlink_escape(self):
        from proactive.computer.fs.paths import escape_if_unproven
        traversal = execute_fs_action(_req(FsActionType.READ_FILE, str(self.root / ".." / "x.txt")))
        self.assertEqual(traversal.status, Status.PATH_NOT_ALLOWED)
        windows = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
        st = escape_if_unproven(str(windows), windows)
        self.assertEqual(st, Status.PATH_ESCAPE_BLOCKED)


class TestV74SafetyScan(unittest.TestCase):
    def test_no_shell_http_or_browser(self):
        forbidden = (
            "subprocess", "os.system", "shell=True", "cmd.exe", "powershell",
            "pwsh", "eval(", "exec(", "requests", "httpx", "urllib",
            "playwright", "selenium", "download",
        )
        for p in FS_DIR.rglob("*.py"):
            src = p.read_text(encoding="utf-8")
            low = src.lower()
            for token in forbidden:
                self.assertNotIn(token.lower(), low, msg=f"{p} {token}")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in ("Popen", "system"):
                    self.fail(node.id)


if __name__ == "__main__":
    unittest.main()
