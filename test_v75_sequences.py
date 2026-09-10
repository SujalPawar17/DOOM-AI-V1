"""V7.5 bounded sequence kernel tests. Voice/STT untouched."""
from __future__ import annotations

import ast
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_SEQUENCES_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS", "")

from core.cost_guard import cost_guard
from proactive.computer.actions.types import ApprovalState
from proactive.computer.fs.kernel import execute_fs_action
from proactive.computer.fs.observe import observe_path
from proactive.computer.sequence.kernel import execute_sequence
from proactive.computer.sequence.registry import aggregate_risk
from proactive.computer.sequence.types import (
    SequenceSpec,
    SequenceStatus,
    SequenceStep,
    freeze_params,
)
from proactive.computer.policy import sequences_allowed


ROOT = Path(__file__).resolve().parent
SEQ_DIR = ROOT / "proactive" / "computer" / "sequence"


def _flags_on():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_SEQUENCES_ENABLED"] = "true"


def _flags_off():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_SEQUENCES_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS"] = ""
    os.environ.pop("PROACTIVE_COMPUTER_FS_MAX_READ_BYTES", None)
    os.environ.pop("PROACTIVE_COMPUTER_FS_MAX_WRITE_BYTES", None)
    os.environ.pop("PROACTIVE_COMPUTER_FS_MAX_LIST", None)


def _ok(status="SUCCESS", before="b", after="a", error=""):
    return SimpleNamespace(
        status=SimpleNamespace(value=status),
        before_observation_hash=before,
        after_observation_hash=after,
        error_code=error,
    )


def _step(sid, cap, action, **params):
    return SequenceStep(
        step_id=sid,
        capability=cap,
        action=action,
        parameters=freeze_params(params),
    )


def _spec(*steps, **kw):
    return SequenceSpec(
        sequence_id=kw.pop("sequence_id", str(uuid.uuid4())),
        owner_id=kw.pop("owner_id", "sujal"),
        steps=tuple(steps),
        max_steps=kw.pop("max_steps", 8),
        timeout_ms=kw.pop("timeout_ms", 30000),
        retry_count=kw.pop("retry_count", 0),
        computer_session_id=kw.pop("computer_session_id", ""),
    )


def _run(spec, approval=ApprovalState.NONE, approved="", **kw):
    kw.setdefault("cost_ok_fn", lambda _caps: True)
    kw.setdefault("emergency_stop_fn", lambda _o, _s: False)
    return execute_sequence(spec, approval, approved, **kw)


class TestV75Sequences(unittest.TestCase):
    def setUp(self):
        cost_guard.reset_records_for_tests()
        _flags_on()

    def tearDown(self):
        _flags_off()

    def test_valid_and_successful_multi_step(self):
        fs = MagicMock(side_effect=[_ok(), _ok(before="b2", after="a2")])
        spec = _spec(
            _step("s1", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\sandbox"),
            _step("s2", "filesystem", "READ_FILE", path="C:\\tmp\\sandbox\\a.txt"),
        )
        out = _run(spec, fs_fn=fs, computer_fn=MagicMock(), browser_fn=MagicMock())
        self.assertEqual(out.status, SequenceStatus.SUCCESS)
        self.assertEqual(out.completed_step_count, 2)
        self.assertEqual(fs.call_count, 2)

    def test_empty_sequence(self):
        out = _run(_spec())
        self.assertEqual(out.status, SequenceStatus.VALIDATION_FAILED)
        self.assertEqual(out.failure_code, "EMPTY_SEQUENCE")

    def test_maximum_step_limit(self):
        steps = [_step(f"s{i}", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\x") for i in range(9)]
        out = _run(_spec(*steps, max_steps=8))
        self.assertEqual(out.status, SequenceStatus.SEQUENCE_LIMIT_EXCEEDED)

    def test_timeout_and_deadline(self):
        times = iter([0.0, 10.0])
        spec = _spec(
            _step("s1", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\x"),
            timeout_ms=100,
        )
        fs = MagicMock(return_value=_ok())
        out = _run(spec, clock=lambda: next(times), fs_fn=fs)
        self.assertEqual(out.status, SequenceStatus.SEQUENCE_TIMEOUT)
        fs.assert_not_called()

    def test_unsupported_capability_and_action(self):
        cap = _run(_spec(_step("s1", "shell", "CLICK", path="x")))
        self.assertEqual(cap.status, SequenceStatus.UNSUPPORTED_CAPABILITY)
        act = _run(_spec(_step("s1", "filesystem", "FORMAT_DISK", path="C:\\tmp\\x")))
        self.assertEqual(act.status, SequenceStatus.UNSUPPORTED_ACTION)

    def test_invalid_parameters(self):
        out = _run(_spec(_step("s1", "filesystem", "WRITE_FILE")))
        self.assertEqual(out.status, SequenceStatus.VALIDATION_FAILED)
        self.assertEqual(out.failure_code, "INVALID_PARAMETERS")

    def test_aggregate_risk(self):
        pairs = (("filesystem", "READ_FILE"), ("filesystem", "DELETE_FILE"))
        self.assertEqual(aggregate_risk(pairs), "HIGH")
        spec = _spec(
            _step("s1", "filesystem", "READ_FILE", path="C:\\tmp\\a.txt"),
            _step("s2", "filesystem", "DELETE_FILE", path="C:\\tmp\\a.txt", expected_exists="true"),
        )
        out = _run(spec)
        self.assertEqual(out.status, SequenceStatus.APPROVAL_REQUIRED)
        self.assertEqual(out.aggregate_risk, "HIGH")

    def test_approval_required_denied_invalidated(self):
        spec = _spec(_step(
            "s1", "filesystem", "WRITE_FILE",
            path="C:\\tmp\\a.txt", content="x", expected_exists="false",
        ))
        need = _run(spec, ApprovalState.NONE)
        self.assertEqual(need.status, SequenceStatus.APPROVAL_REQUIRED)
        denied = _run(spec, ApprovalState.DENIED, spec.spec_hash())
        self.assertEqual(denied.status, SequenceStatus.APPROVAL_DENIED)
        bad = _run(spec, ApprovalState.APPROVED, "deadbeef")
        self.assertEqual(bad.status, SequenceStatus.APPROVAL_INVALIDATED)

    def test_sequence_immutability(self):
        spec = _spec(_step("s1", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\x"))
        with self.assertRaises(Exception):
            spec.steps = ()  # type: ignore[misc]
        h1 = spec.spec_hash()
        other = _spec(_step("s1", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\y"), sequence_id=spec.sequence_id)
        self.assertNotEqual(h1, other.spec_hash())

    def test_emergency_stop_before_and_between(self):
        spec = _spec(
            _step("s1", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\x"),
            _step("s2", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\y"),
        )
        fs = MagicMock()
        before = _run(spec, fs_fn=fs, emergency_stop_fn=lambda _o, _s: True)
        self.assertEqual(before.status, SequenceStatus.EMERGENCY_STOP_ACTIVE)
        fs.assert_not_called()
        calls = {"n": 0}

        def stop(_o, _s):
            calls["n"] += 1
            return calls["n"] > 1

        mid = _run(spec, fs_fn=MagicMock(return_value=_ok()), emergency_stop_fn=stop)
        self.assertEqual(mid.status, SequenceStatus.EMERGENCY_STOP_ACTIVE)
        self.assertEqual(mid.completed_step_count, 1)

    def test_precondition_and_step_failures_fail_closed(self):
        fs = MagicMock(side_effect=[
            _ok(),
            _ok(status="PRECONDITION_FAILED", error="STALE_OBSERVATION_HASH"),
            _ok(),
        ])
        spec = _spec(
            _step("s1", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\a"),
            _step("s2", "filesystem", "WRITE_FILE", path="C:\\tmp\\a\\f.txt", content="x"),
            _step("s3", "filesystem", "READ_FILE", path="C:\\tmp\\a\\f.txt"),
        )
        out = _run(spec, ApprovalState.APPROVED, spec.spec_hash(), fs_fn=fs)
        self.assertEqual(out.status, SequenceStatus.PRECONDITION_FAILED)
        self.assertEqual(out.failed_step_id, "s2")
        self.assertEqual(out.completed_step_count, 1)
        self.assertEqual(fs.call_count, 2)

        first = _run(
            _spec(_step("s1", "filesystem", "READ_FILE", path="C:\\tmp\\missing.txt")),
            fs_fn=MagicMock(return_value=_ok(status="PATH_NOT_FOUND", error="PATH_NOT_FOUND")),
        )
        self.assertEqual(first.status, SequenceStatus.STEP_FAILED)
        self.assertEqual(first.completed_step_count, 0)

    def test_bounded_retry_and_mutation_retry_blocked(self):
        os.environ["PROACTIVE_COMPUTER_SEQUENCE_MAX_RETRIES"] = "1"
        try:
            fs = MagicMock(side_effect=[
                _ok(status="PATH_NOT_FOUND", error="PATH_NOT_FOUND"),
                _ok(),
            ])
            spec = _spec(
                _step("s1", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\x"),
                retry_count=1,
            )
            out = _run(spec, fs_fn=fs)
            self.assertEqual(out.status, SequenceStatus.SUCCESS)
            self.assertEqual(fs.call_count, 2)
        finally:
            os.environ["PROACTIVE_COMPUTER_SEQUENCE_MAX_RETRIES"] = "0"
        blocked = _run(_spec(
            _step("s1", "filesystem", "WRITE_FILE", path="C:\\tmp\\a.txt", content="x"),
            retry_count=1,
        ))
        self.assertEqual(blocked.status, SequenceStatus.VALIDATION_FAILED)
        self.assertEqual(blocked.failure_code, "MUTATION_RETRY_BLOCKED")

    def test_feature_flag_disabled(self):
        _flags_off()
        self.assertFalse(sequences_allowed())
        spec = _spec(_step("s1", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\x"))
        out = _run(spec, fs_fn=MagicMock(return_value=_ok()))
        self.assertEqual(out.status, SequenceStatus.POLICY_BLOCKED)
        self.assertEqual(out.failure_code, "SEQUENCES_DISABLED")

    def test_cost_guard_block(self):
        spec = _spec(_step("s1", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\x"))
        out = _run(spec, cost_ok_fn=lambda _c: False, fs_fn=MagicMock())
        self.assertEqual(out.status, SequenceStatus.POLICY_BLOCKED)
        self.assertEqual(out.failure_code, "COST_GUARD_BLOCKED")

    def test_no_capability_escalation_or_implicit_fs(self):
        computer = MagicMock(return_value=_ok())
        browser = MagicMock(return_value=_ok())
        fs = MagicMock(return_value=_ok())
        spec = _spec(_step(
            "s1", "computer", "CLICK",
            automation_id="btn", control_type="Button",
            precondition_observation_hash="abc",
        ))
        out = _run(spec, ApprovalState.APPROVED, spec.spec_hash(),
                   computer_fn=computer, browser_fn=browser, fs_fn=fs)
        self.assertEqual(out.status, SequenceStatus.SUCCESS)
        computer.assert_called_once()
        fs.assert_not_called()
        browser.assert_not_called()
        bspec = _spec(_step(
            "s1", "browser", "NAVIGATE",
            session_id="sess", url="http://127.0.0.1/",
            precondition_observation_hash="h",
        ))
        bout = _run(bspec, ApprovalState.APPROVED, bspec.spec_hash(),
                    computer_fn=computer, browser_fn=browser, fs_fn=fs)
        self.assertEqual(bout.status, SequenceStatus.SUCCESS)
        fs.assert_not_called()

    def test_public_kernel_contracts(self):
        computer = MagicMock(return_value=_ok())
        browser = MagicMock(return_value=_ok())
        fs = MagicMock(return_value=_ok())
        cspec = _spec(_step(
            "c1", "computer", "TYPE",
            automation_id="box", control_type="Edit",
            precondition_observation_hash="h", text="hi",
        ))
        _run(cspec, ApprovalState.APPROVED, cspec.spec_hash(),
             computer_fn=computer, browser_fn=browser, fs_fn=fs)
        req = computer.call_args[0][0]
        self.assertEqual(req.action_type.value, "TYPE")
        self.assertEqual(req.text, "hi")
        bspec = _spec(_step(
            "b1", "browser", "REFRESH", session_id="s1",
            precondition_observation_hash="h",
        ))
        _run(bspec, ApprovalState.APPROVED, bspec.spec_hash(),
             computer_fn=computer, browser_fn=browser, fs_fn=fs)
        breq = browser.call_args[0][0]
        self.assertEqual(breq.action_type.value, "REFRESH")
        fspec = _spec(_step("f1", "filesystem", "LIST_DIRECTORY", path="C:\\tmp\\x"))
        _run(fspec, computer_fn=computer, browser_fn=browser, fs_fn=fs)
        freq = fs.call_args[0][0]
        self.assertEqual(freq.action_type.value, "LIST_DIRECTORY")

    def test_telemetry_redaction(self):
        spec = _spec(_step(
            "s1", "filesystem", "WRITE_FILE",
            path="C:\\tmp\\a.txt", content="supersecret-token",
            expected_exists="false",
        ))
        out = _run(
            spec, ApprovalState.APPROVED, spec.spec_hash(),
            fs_fn=MagicMock(return_value=_ok()),
        )
        blob = str(out.as_dict())
        self.assertNotIn("supersecret-token", blob)
        self.assertEqual(out.telemetry[0]["action"], "WRITE_FILE")

    def test_partial_completion_reporting(self):
        fs = MagicMock(side_effect=[_ok(), _ok(status="EXECUTION_FAILED", error="EXECUTION_FAILED")])
        spec = _spec(
            _step("s1", "filesystem", "OBSERVE_PATH", path="C:\\tmp\\a"),
            _step("s2", "filesystem", "CREATE_DIRECTORY", path="C:\\tmp\\a\\n"),
        )
        out = _run(spec, ApprovalState.APPROVED, spec.spec_hash(), fs_fn=fs)
        self.assertEqual(out.status, SequenceStatus.STEP_FAILED)
        self.assertEqual(out.completed_step_count, 1)
        self.assertEqual(out.total_steps, 2)
        self.assertEqual(out.failed_step_id, "s2")


class TestV75SafetyScan(unittest.TestCase):
    def test_no_dynamic_code_shell_or_js(self):
        forbidden = (
            "eval(", "exec(", "compile(", "subprocess", "os.system", "shell=True",
            "powershell", "cmd.exe", "javascript:", "requests", "httpx", "urllib",
            "webbrowser", "pyautogui", "importlib", "pickle", "marshal",
        )
        for p in SEQ_DIR.rglob("*.py"):
            src = p.read_text(encoding="utf-8")
            low = src.lower()
            for token in forbidden:
                self.assertNotIn(token.lower(), low, msg=f"{p} {token}")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], {
                            "subprocess", "importlib", "pickle", "marshal", "requests",
                            "httpx", "urllib", "webbrowser", "pyautogui",
                        })
                if isinstance(node, ast.Lambda):
                    self.fail("lambda")
                if isinstance(node, ast.Name) and node.id in ("eval", "exec", "compile", "__import__"):
                    self.fail(node.id)
        kernel = (SEQ_DIR / "kernel.py").read_text(encoding="utf-8")
        self.assertNotIn("act_engine", kernel)
        self.assertNotIn("TaskEngine", kernel)
        self.assertNotIn("ActionEngine", kernel)


class TestV75IntegrationSandbox(unittest.TestCase):
    def setUp(self):
        cost_guard.reset_records_for_tests()
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name).resolve()
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_SEQUENCES_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS"] = str(self.root)
        os.environ["PROACTIVE_COMPUTER_FS_DENIED_ROOTS"] = ""
        os.environ.pop("PROACTIVE_COMPUTER_FS_MAX_READ_BYTES", None)
        os.environ.pop("PROACTIVE_COMPUTER_FS_MAX_WRITE_BYTES", None)
        os.environ.pop("PROACTIVE_COMPUTER_FS_MAX_LIST", None)

    def tearDown(self):
        _flags_off()
        self.td.cleanup()

    def test_temp_sandbox_fs_sequence(self):
        work = self.root / "work"
        note = work / "note.txt"
        copy = work / "copy.txt"
        moved = work / "moved.txt"
        steps = [
            _step("s1", "filesystem", "OBSERVE_PATH", path=str(self.root)),
            _step(
                "s2", "filesystem", "CREATE_DIRECTORY", path=str(work),
                precondition_observation_hash=observe_path(work).observation_hash,
                expected_exists="false",
            ),
            _step(
                "s3", "filesystem", "WRITE_FILE", path=str(note), content="hello-v75",
                precondition_observation_hash=observe_path(note).observation_hash,
                expected_exists="false",
            ),
            _step("s4", "filesystem", "READ_FILE", path=str(note)),
            _step(
                "s5", "filesystem", "COPY_FILE", path=str(note), dest_path=str(copy),
                expected_exists="true", expected_type="file",
            ),
            _step(
                "s6", "filesystem", "MOVE_FILE", path=str(copy), dest_path=str(moved),
                expected_exists="true", expected_type="file",
            ),
            _step(
                "s7", "filesystem", "DELETE_FILE", path=str(moved),
                expected_exists="true", expected_type="file",
            ),
        ]
        spec = _spec(*steps)
        out = execute_sequence(
            spec,
            ApprovalState.APPROVED,
            spec.spec_hash(),
            emergency_stop_fn=lambda _o, _s: False,
        )
        self.assertEqual(out.status, SequenceStatus.SUCCESS, out.as_dict())
        self.assertEqual(out.completed_step_count, 7)
        self.assertTrue(work.is_dir())
        self.assertTrue(note.is_file())
        self.assertFalse(copy.exists())
        self.assertFalse(moved.exists())
        blob = str(out.as_dict())
        self.assertNotIn("hello-v75", blob)
        note.unlink()
        work.rmdir()
        self.assertFalse(work.exists())


if __name__ == "__main__":
    unittest.main()
