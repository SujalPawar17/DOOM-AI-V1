"""V7.6 structured verification tests. Voice/STT untouched. No visual runtime."""
from __future__ import annotations

import ast
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_VERIFICATION_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_SEQUENCES_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_BROWSER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS", "")

from core.cost_guard import cost_guard
from proactive.computer.actions.types import ApprovalState
from proactive.computer.fs.kernel import execute_fs_action
from proactive.computer.fs.observe import observe_path
from proactive.computer.fs.types import FsActionRequest, FsActionType
from proactive.computer.sequence.kernel import execute_sequence
from proactive.computer.sequence.types import SequenceSpec, SequenceStatus, SequenceStep, freeze_params
from proactive.computer.verify.kernel import execute_verification
from proactive.computer.verify.types import (
    MAX_TEXT_NEEDLE,
    VerificationRequest,
    VerificationSpec,
    VerificationStatus,
    VerificationType,
    freeze_expected,
)
from proactive.computer.verify.visual import visual_evidence


ROOT = Path(__file__).resolve().parent
VERIFY_DIR = ROOT / "proactive" / "computer" / "verify"


def _flags_on(root: Optional[Path] = None):
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_VERIFICATION_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_SEQUENCES_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_BROWSER_ENABLED"] = "true"
    if root is not None:
        os.environ["PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS"] = str(root)


def _flags_off():
    for k in (
        "PROACTIVE_COMPUTER_ENABLED",
        "PROACTIVE_COMPUTER_VERIFICATION_ENABLED",
        "PROACTIVE_COMPUTER_SEQUENCES_ENABLED",
        "PROACTIVE_COMPUTER_FILESYSTEM_ENABLED",
        "PROACTIVE_COMPUTER_BROWSER_ENABLED",
    ):
        os.environ[k] = "false"
        os.environ["PROACTIVE_COMPUTER_SEQUENCE_MAX_STEPS"] = "8"


def _spec(vtype: str, capability: str, **expected) -> VerificationSpec:
    return VerificationSpec(
        verification_id=str(uuid.uuid4()),
        capability=capability,
        verification_type=vtype,
        expected=freeze_expected(expected),
        timeout_ms=expected.pop("timeout_ms", 2000) if False else 2000,
        max_attempts=1,
        owner_id="sujal",
    )


def _req(spec: VerificationSpec, **kw) -> VerificationRequest:
    return VerificationRequest(spec=spec, action_id=kw.get("action_id", "a1"), **{k: v for k, v in kw.items() if k != "action_id"})


def _obs(**kw):
    return SimpleNamespace(
        observation_hash=kw.get("observation_hash", "h1"),
        uia_automation_id=kw.get("automation_id", "btn"),
        uia_runtime_id=kw.get("runtime_id", "r1"),
        uia_control_type=kw.get("control_type", "Button"),
        outcome_code=kw.get("outcome", "OK"),
        hwnd=kw.get("hwnd", 1),
        title_advisory=kw.get("title", "Demo"),
    )


class TestV76Verification(unittest.TestCase):
    def setUp(self):
        cost_guard.reset_records_for_tests()
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name).resolve()
        _flags_on(self.root)

    def tearDown(self):
        _flags_off()
        self.td.cleanup()

    def test_valid_and_immutable_and_invalid_spec(self):
        spec = _spec("TARGET_EXISTS", "filesystem", path=str(self.root))
        self.assertTrue(spec.spec_hash())
        with self.assertRaises(Exception):
            spec.capability = "browser"  # type: ignore[misc]
        bad = VerificationSpec(verification_id="", capability="filesystem", verification_type="TARGET_EXISTS", owner_id="sujal")
        out = execute_verification(_req(bad), cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False)
        self.assertEqual(out.status, VerificationStatus.INVALID_VERIFICATION_SPEC)

    def test_observation_hash_match_and_mismatch(self):
        live = observe_path(self.root)
        ok = execute_verification(
            _req(_spec("OBSERVATION_HASH_MATCH", "filesystem", path=str(self.root), expected_hash=live.observation_hash)),
            cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(ok.status, VerificationStatus.VERIFIED)
        bad = execute_verification(
            _req(_spec("OBSERVATION_HASH_MATCH", "filesystem", path=str(self.root), expected_hash="deadbeef")),
            cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(bad.status, VerificationStatus.NOT_VERIFIED)

    def test_target_exists_absent_identity_state_fs(self):
        d = self.root / "dir1"
        missing = execute_verification(
            _req(_spec("TARGET_EXISTS", "filesystem", path=str(d))),
            cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(missing.status, VerificationStatus.NOT_VERIFIED)
        execute_fs_action(FsActionRequest(
            action_id="c", action_type=FsActionType.CREATE_DIRECTORY, path=str(d),
            owner_id="sujal", approval_state=ApprovalState.APPROVED,
            precondition_observation_hash=observe_path(d).observation_hash, expected_exists=False,
        ))
        exists = execute_verification(
            _req(_spec("TARGET_EXISTS", "filesystem", path=str(d))),
            cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(exists.status, VerificationStatus.VERIFIED)
        ident = execute_verification(
            _req(_spec("TARGET_IDENTITY_MATCH", "filesystem", path=str(d), expected_type="directory")),
            cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(ident.status, VerificationStatus.VERIFIED)
        state = execute_verification(
            _req(_spec("TARGET_STATE_MATCH", "filesystem", path=str(d), expected_type="directory")),
            cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(state.status, VerificationStatus.VERIFIED)
        still = execute_verification(
            _req(_spec("TARGET_ABSENT", "filesystem", path=str(d))),
            cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(still.status, VerificationStatus.NOT_VERIFIED)

    def test_url_origin_browser_and_text(self):
        el = SimpleNamespace(
            element_id="demo", test_id="demo", role="button", name="Safe demo",
            disabled=False, is_password=False, value="",
        )
        obs = SimpleNamespace(
            observation_hash="bh", url="https://example.com/", origin="https://example.com",
            elements=[el], title_advisory="Example",
        )
        browser_fn = lambda sid, owner: {"status": "SUCCESS", "observation": obs}
        url = execute_verification(
            _req(_spec("URL_MATCH", "browser", session_id="s1", expected_url="https://example.com/")),
            browser_fn=browser_fn, cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(url.status, VerificationStatus.VERIFIED)
        origin = execute_verification(
            _req(_spec("ORIGIN_MATCH", "browser", session_id="s1", expected_origin="https://example.com")),
            browser_fn=browser_fn, cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(origin.status, VerificationStatus.VERIFIED)
        present = execute_verification(
            _req(_spec("TEXT_PRESENT", "browser", session_id="s1", text="Safe demo")),
            browser_fn=browser_fn, cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(present.status, VerificationStatus.VERIFIED)
        absent = execute_verification(
            _req(_spec("TEXT_ABSENT", "browser", session_id="s1", text="password123")),
            browser_fn=browser_fn, cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(absent.status, VerificationStatus.VERIFIED)

    def test_filesystem_metadata_and_directory_entry(self):
        f = self.root / "note.txt"
        f.write_bytes(b"abc")
        meta = execute_verification(
            _req(_spec(
                "FILE_METADATA_MATCH", "filesystem", path=str(f),
                expected_type="file", expected_exists="true", expected_size_bytes="3",
            )),
            cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(meta.status, VerificationStatus.VERIFIED)
        entry = execute_verification(
            _req(_spec("DIRECTORY_ENTRY_MATCH", "filesystem", path=str(self.root), expected_name="note.txt")),
            cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(entry.status, VerificationStatus.VERIFIED)

    def test_structured_text_limit_timeout_unavailable(self):
        huge = "x" * (MAX_TEXT_NEEDLE + 1)
        spec = VerificationSpec(
            verification_id="v1", capability="filesystem", verification_type="TEXT_PRESENT",
            expected=freeze_expected({"path": str(self.root), "text": huge}), owner_id="sujal",
        )
        out = execute_verification(_req(spec), cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False)
        self.assertEqual(out.status, VerificationStatus.INVALID_VERIFICATION_SPEC)
        self.assertEqual(out.failure_code, "TEXT_LIMIT")
        times = iter([0.0, 10.0])
        timed = execute_verification(
            _req(_spec("TARGET_EXISTS", "filesystem", path=str(self.root))),
            clock=lambda: next(times), cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(timed.status, VerificationStatus.VERIFICATION_TIMEOUT)
        vis = VerificationSpec(
            verification_id="vv", capability="filesystem", verification_type="TARGET_EXISTS",
            expected=freeze_expected({"path": str(self.root)}), evidence_mode="visual", owner_id="sujal",
        )
        unavail = execute_verification(_req(vis), cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False)
        self.assertEqual(unavail.status, VerificationStatus.VERIFICATION_UNAVAILABLE)
        self.assertEqual(visual_evidence()["cloud"], False)

    def test_emergency_stop_approval_cost_redaction(self):
        stopped = execute_verification(
            _req(_spec("TARGET_EXISTS", "filesystem", path=str(self.root))),
            cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: True,
        )
        self.assertEqual(stopped.status, VerificationStatus.EMERGENCY_STOP_ACTIVE)
        spec = _spec("TARGET_EXISTS", "filesystem", path=str(self.root))
        inv = execute_verification(
            _req(spec, approved_spec_hash="deadbeef"),
            cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(inv.status, VerificationStatus.APPROVAL_INVALIDATED)
        cg = execute_verification(
            _req(spec), cost_ok_fn=lambda _c: False, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(cg.status, VerificationStatus.VERIFICATION_BLOCKED)
        self.assertEqual(cg.failure_code, "COST_GUARD_BLOCKED")
        secret_spec = _spec("TEXT_PRESENT", "filesystem", path=str(self.root), text="token")
        out = execute_verification(
            _req(secret_spec), cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertNotIn("SECRET", str(out.as_dict()).upper() or "")
        self.assertNotIn("password", str(out.telemetry).lower())

    def test_computer_uia_via_public_observe(self):
        obs = _obs()
        ident = execute_verification(
            _req(_spec(
                "TARGET_IDENTITY_MATCH", "computer",
                automation_id="btn", runtime_id="r1", control_type="Button",
            )),
            computer_fn=lambda owner: obs, cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(ident.status, VerificationStatus.VERIFIED)
        mismatch = execute_verification(
            _req(_spec(
                "TARGET_IDENTITY_MATCH", "computer",
                automation_id="other", control_type="Button",
            )),
            computer_fn=lambda owner: obs, cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(mismatch.status, VerificationStatus.NOT_VERIFIED)
        state = execute_verification(
            _req(_spec("TARGET_STATE_MATCH", "computer", expected_outcome="OK")),
            computer_fn=lambda owner: obs, cost_ok_fn=lambda _c: True, emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(state.status, VerificationStatus.VERIFIED)

    def test_sequence_stops_and_continues(self):
        work = self.root / "seq"
        steps_fail = (
            SequenceStep("s1", "filesystem", "CREATE_DIRECTORY", freeze_params({
                "path": str(work),
                "precondition_observation_hash": observe_path(work).observation_hash,
                "expected_exists": "false",
            })),
            SequenceStep("s2", "verify", "TARGET_ABSENT", freeze_params({
                "domain": "filesystem", "path": str(work),
            })),
        )
        spec = SequenceSpec("seq-fail", "sujal", steps_fail)
        fail = execute_sequence(
            spec, ApprovalState.APPROVED, spec.spec_hash(),
            emergency_stop_fn=lambda o, s: False, cost_ok_fn=lambda c: True,
        )
        self.assertEqual(fail.status, SequenceStatus.STEP_FAILED)
        self.assertEqual(fail.completed_step_count, 1)
        work2 = self.root / "seq2"
        steps_ok = (
            SequenceStep("s1", "filesystem", "CREATE_DIRECTORY", freeze_params({
                "path": str(work2),
                "precondition_observation_hash": observe_path(work2).observation_hash,
                "expected_exists": "false",
            })),
            SequenceStep("s2", "verify", "TARGET_EXISTS", freeze_params({
                "domain": "filesystem", "path": str(work2),
            })),
        )
        spec2 = SequenceSpec("seq-ok", "sujal", steps_ok)
        ok = execute_sequence(
            spec2, ApprovalState.APPROVED, spec2.spec_hash(),
            emergency_stop_fn=lambda o, s: False, cost_ok_fn=lambda c: True,
        )
        self.assertEqual(ok.status, SequenceStatus.SUCCESS)
        self.assertEqual(ok.completed_step_count, 2)

    def test_escalation_blocked_in_sequence(self):
        spec = SequenceSpec("esc", "sujal", (
            SequenceStep("s1", "verify", "URL_MATCH", freeze_params({
                "domain": "filesystem", "path": str(self.root), "expected_url": "https://example.com/",
            })),
        ))
        out = execute_sequence(spec, emergency_stop_fn=lambda o, s: False, cost_ok_fn=lambda c: True)
        self.assertEqual(out.status, SequenceStatus.VALIDATION_FAILED)
        self.assertEqual(out.failure_code, "CAPABILITY_ESCALATION")


class TestV76SafetyScan(unittest.TestCase):
    def test_no_visual_agent_or_dynamic_code(self):
        forbidden = (
            "eval(", "exec(", "compile(", "subprocess", "os.system", "shell=True",
            "powershell", "cmd.exe", "javascript:", "pyautogui", "pytesseract",
            "opencv", "requests", "httpx", "urllib", "openai", "gemini", "bedrock",
            "groq", "elevenlabs", "cloud vision", "SetCursorPos",
        )
        for p in VERIFY_DIR.rglob("*.py"):
            src = p.read_text(encoding="utf-8").lower()
            for token in forbidden:
                self.assertNotIn(token.lower(), src, msg=f"{p} {token}")
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Lambda):
                    self.fail("lambda")
                if isinstance(node, ast.Name) and node.id in ("eval", "exec", "compile"):
                    self.fail(node.id)


class TestV76IntegrationSandbox(unittest.TestCase):
    def setUp(self):
        cost_guard.reset_records_for_tests()
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name).resolve()
        _flags_on(self.root)

    def tearDown(self):
        _flags_off()
        self.td.cleanup()

    def test_fs_action_then_verify_sequence(self):
        work = self.root / "work"
        note = work / "note.txt"
        copy = work / "copy.txt"
        moved = work / "moved.txt"

        def st(sid, cap, action, **p):
            return SequenceStep(sid, cap, action, freeze_params(p))

        steps = (
            st("a1", "filesystem", "CREATE_DIRECTORY", path=str(work),
               precondition_observation_hash=observe_path(work).observation_hash, expected_exists="false"),
            st("v1", "verify", "TARGET_EXISTS", domain="filesystem", path=str(work), expected_type="directory"),
            st("a2", "filesystem", "WRITE_FILE", path=str(note), content="harmless-note",
               precondition_observation_hash=observe_path(note).observation_hash, expected_exists="false"),
            st("v2", "verify", "TARGET_EXISTS", domain="filesystem", path=str(note)),
            st("a3", "filesystem", "COPY_FILE", path=str(note), dest_path=str(copy),
               expected_exists="true", expected_type="file"),
            st("v3", "verify", "TARGET_EXISTS", domain="filesystem", path=str(copy)),
            st("a4", "filesystem", "MOVE_FILE", path=str(copy), dest_path=str(moved),
               expected_exists="true", expected_type="file"),
            st("v4", "verify", "TARGET_EXISTS", domain="filesystem", path=str(moved)),
            st("a5", "filesystem", "DELETE_FILE", path=str(moved),
               expected_exists="true", expected_type="file"),
            st("v5", "verify", "TARGET_ABSENT", domain="filesystem", path=str(moved)),
        )
        os.environ["PROACTIVE_COMPUTER_SEQUENCE_MAX_STEPS"] = "16"
        spec = SequenceSpec("v76-int", "sujal", steps, max_steps=16)
        out = execute_sequence(
            spec, ApprovalState.APPROVED, spec.spec_hash(),
            emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(out.status, SequenceStatus.SUCCESS, out.as_dict())
        self.assertEqual(out.completed_step_count, 10)
        self.assertTrue(note.is_file())
        self.assertFalse(moved.exists())
        self.assertNotIn("harmless-note", str(out.as_dict()))
        note.unlink()
        work.rmdir()


if __name__ == "__main__":
    unittest.main()
