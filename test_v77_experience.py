"""V7.7 hashed experience tests. Voice/STT untouched. No V5 memory writes."""
from __future__ import annotations

import ast
import os
import tempfile
import unittest
import uuid
from pathlib import Path

os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_EXPERIENCE_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_SEQUENCES_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_VERIFICATION_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS", "")

from core.cost_guard import ResourceRequest, ResourceType, cost_guard
from proactive.computer.actions.types import ApprovalState
from proactive.computer.experience.hashing import content_hash, experience_hash
from proactive.computer.experience.kernel import (
    map_outcome,
    record_experience,
    record_from_kernel_result,
    reset_experience_store_for_tests,
)
from proactive.computer.experience.store import list_experiences
from proactive.computer.experience.types import (
    ExperienceDraft,
    ExperienceOutcome,
    ExperienceProvenance,
    ExperienceRecord,
)
from proactive.computer.fs.kernel import execute_fs_action
from proactive.computer.fs.observe import observe_path
from proactive.computer.fs.types import FsActionRequest, FsActionType, ObjectType, Status
from proactive.computer.policy import RISK_FS_DELETE, filesystem_allowed
from proactive.computer.sequence.kernel import execute_sequence
from proactive.computer.sequence.types import SequenceSpec, SequenceStatus, SequenceStep, freeze_params
from proactive.computer.verify.types import VerificationStatus


ROOT = Path(__file__).resolve().parent
EXP_DIR = ROOT / "proactive" / "computer" / "experience"


def _flags_on(root: Path) -> None:
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_EXPERIENCE_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_SEQUENCES_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_VERIFICATION_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS"] = str(root)
    os.environ["PROACTIVE_COMPUTER_SEQUENCE_MAX_STEPS"] = "16"


def _flags_off() -> None:
    for k in (
        "PROACTIVE_COMPUTER_ENABLED",
        "PROACTIVE_COMPUTER_EXPERIENCE_ENABLED",
        "PROACTIVE_COMPUTER_SEQUENCES_ENABLED",
        "PROACTIVE_COMPUTER_FILESYSTEM_ENABLED",
        "PROACTIVE_COMPUTER_VERIFICATION_ENABLED",
    ):
        os.environ[k] = "false"
    os.environ["PROACTIVE_COMPUTER_FS_ALLOWED_ROOTS"] = ""
    os.environ["PROACTIVE_COMPUTER_SEQUENCE_MAX_STEPS"] = "8"


def _draft(**kw) -> ExperienceDraft:
    return ExperienceDraft(
        capability=kw.get("capability", "filesystem"),
        action=kw.get("action", "OBSERVE_PATH"),
        outcome_status=kw.get("outcome_status", "SUCCESS"),
        owner_id="sujal",
        sequence_id=kw.get("sequence_id", ""),
        step_id=kw.get("step_id", "s1"),
        action_id=kw.get("action_id", "s1"),
        sequence_spec_hash=kw.get("sequence_spec_hash", "a" * 64),
        before_observation_hash=kw.get("before", "b" * 64),
        after_observation_hash=kw.get("after", "c" * 64),
        verification_status=kw.get("verification_status", ""),
        verification_type=kw.get("verification_type", ""),
        risk_level=kw.get("risk_level", "LOW"),
        approval_state=kw.get("approval_state", "NONE"),
        duration_ms=kw.get("duration_ms", 5),
        failure_code=kw.get("failure_code", ""),
        provenance=kw.get("provenance", ExperienceProvenance.DIRECT_ACTION),
        sensitive=kw.get("sensitive", False),
        experience_id=kw.get("experience_id", ""),
        timestamp_unix_ms=kw.get("timestamp_unix_ms", 1_700_000_000_000),
        stream_id=kw.get("stream_id", "stream-a"),
    )


class TestV77Experience(unittest.TestCase):
    def setUp(self):
        cost_guard.reset_records_for_tests()
        reset_experience_store_for_tests()
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name).resolve()
        _flags_on(self.root)

    def tearDown(self):
        _flags_off()
        self.td.cleanup()

    def test_valid_immutable_canonical_hashes(self):
        a = record_experience(_draft(experience_id="e1"))
        self.assertIsInstance(a, ExperienceRecord)
        with self.assertRaises(Exception):
            a.outcome = ExperienceOutcome.FAILED  # type: ignore[misc]
        b = record_experience(_draft(experience_id="e1", timestamp_unix_ms=1_700_000_000_000, stream_id="stream-b"))
        self.assertEqual(a.content_hash, b.content_hash)
        self.assertEqual(len(a.experience_hash), 64)
        payload = a.as_dict()
        self.assertEqual(content_hash(payload), a.content_hash)
        inst = {
            "content_hash": a.content_hash,
            "experience_id": a.experience_id,
            "previous_experience_hash": a.previous_experience_hash,
            "schema_version": a.schema_version,
            "timestamp_unix_ms": a.timestamp_unix_ms,
        }
        self.assertEqual(experience_hash(inst), a.experience_hash)

    def test_outcome_and_observation_change_hashes(self):
        ok = record_experience(_draft(experience_id="h1", stream_id="s1"))
        failed = record_experience(_draft(experience_id="h2", outcome_status="EXECUTION_FAILED", failure_code="EXECUTION_FAILED", stream_id="s2"))
        self.assertNotEqual(ok.content_hash, failed.content_hash)
        other = record_experience(_draft(experience_id="h3", after="d" * 64, stream_id="s3"))
        self.assertNotEqual(ok.content_hash, other.content_hash)

    def test_duplicate_and_chain(self):
        first = record_experience(_draft(experience_id="c1", stream_id="chain"))
        second = record_experience(_draft(experience_id="c2", stream_id="chain", step_id="s2", action_id="s2"))
        self.assertEqual(second.previous_experience_hash, first.experience_hash)
        dup = record_experience(_draft(experience_id="c3", stream_id="other"))
        self.assertEqual(dup.duplicate_of, first.experience_id)
        self.assertEqual(dup.content_hash, first.content_hash)
        self.assertNotEqual(dup.experience_hash, first.experience_hash)

    def test_failed_blocked_not_verified_stop_precondition_approval(self):
        self.assertEqual(map_outcome("SUCCESS", "NOT_VERIFIED"), ExperienceOutcome.NOT_VERIFIED)
        failed = record_experience(_draft(outcome_status="PATH_NOT_FOUND", failure_code="PATH_NOT_FOUND"))
        self.assertEqual(failed.outcome, ExperienceOutcome.FAILED)
        blocked = record_experience(_draft(outcome_status="POLICY_BLOCKED", failure_code="POLICY_BLOCKED"))
        self.assertEqual(blocked.outcome, ExperienceOutcome.BLOCKED)
        nv = record_experience(_draft(capability="verify", action="TARGET_EXISTS", outcome_status="NOT_VERIFIED", verification_status="NOT_VERIFIED", provenance=ExperienceProvenance.VERIFICATION_RESULT))
        self.assertEqual(nv.outcome, ExperienceOutcome.NOT_VERIFIED)
        stop = record_experience(_draft(outcome_status="EMERGENCY_STOP_ACTIVE"))
        self.assertEqual(stop.outcome, ExperienceOutcome.EMERGENCY_STOPPED)
        pre = record_experience(_draft(outcome_status="PRECONDITION_FAILED"))
        self.assertEqual(pre.outcome, ExperienceOutcome.PRECONDITION_FAILED)
        need = record_experience(_draft(outcome_status="APPROVAL_REQUIRED", failure_code="APPROVAL_REQUIRED"))
        self.assertEqual(need.outcome, ExperienceOutcome.BLOCKED)
        self.assertEqual(need.provenance, ExperienceProvenance.DIRECT_ACTION)

    def test_sensitive_redaction_and_privacy(self):
        rec = record_experience(_draft(action="TYPE", sensitive=True, capability="computer", risk_level="MEDIUM"))
        blob = str(rec.as_dict()) + str(rec.as_telemetry())
        self.assertTrue(rec.sensitive)
        self.assertNotIn("typed-value", blob)
        self.assertNotIn("clipboard", blob.lower())

    def test_risk_preserved_approval_not_automatic_policy_untouched(self):
        rec = record_experience(_draft(action="DELETE_FILE", risk_level=RISK_FS_DELETE, approval_state="APPROVED", outcome_status="SUCCESS"))
        self.assertEqual(rec.risk_level, "HIGH")
        before_flag = os.environ.get("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED")
        rec2 = record_experience(_draft(action="DELETE_FILE", risk_level=RISK_FS_DELETE, approval_state="APPROVED"))
        self.assertEqual(os.environ.get("PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"), before_flag)
        self.assertTrue(filesystem_allowed())
        need = execute_fs_action(FsActionRequest(
            action_id="x", action_type=FsActionType.DELETE_FILE, path=str(self.root / "nope.txt"),
            owner_id="sujal", approval_state=ApprovalState.NONE,
        ))
        self.assertEqual(need.status, Status.APPROVAL_REQUIRED)
        self.assertEqual(rec2.approval_state, "APPROVED")

    def test_cost_guard_local_and_block(self):
        d = cost_guard.authorize(ResourceRequest(
            resource_type=ResourceType.OTHER, provider="local_experience", capability="computer_experience",
        ))
        self.assertTrue(d.is_allow)
        self.assertEqual(d.cost_class.value, "LOCAL_FREE")
        blocked = type("D", (), {"is_allow": False})()
        from unittest.mock import patch
        with patch("proactive.computer.experience.kernel.cost_guard.authorize", return_value=blocked):
            rec = record_experience(_draft())
        self.assertEqual(rec.failure_code, "COST_GUARD_BLOCKED")

    def test_v72_v73_kernel_adapters(self):
        class Fake:
            status = type("S", (), {"value": "SUCCESS"})()
            action_id = "a1"
            before_observation_hash = "b" * 64
            after_observation_hash = "c" * 64
            error_code = ""
        c = record_from_kernel_result(capability="computer", action="CLICK", result=Fake(), risk_level="MEDIUM", approval_state="APPROVED")
        self.assertEqual(c.capability, "computer")
        self.assertEqual(c.outcome, ExperienceOutcome.SUCCESS)
        b = record_from_kernel_result(capability="browser", action="NAVIGATE", result=Fake(), risk_level="MEDIUM")
        self.assertEqual(b.action, "NAVIGATE")
        v = record_from_kernel_result(
            capability="verify", action="TARGET_EXISTS",
            result=type("R", (), {
                "status": type("S", (), {"value": "VERIFIED"})(),
                "action_id": "v1",
                "before_observation_hash": "",
                "after_observation_hash": "c" * 64,
                "error_code": "",
                "failure_code": "",
            })(),
        )
        self.assertEqual(v.verification_status, "VERIFIED")
        self.assertEqual(v.provenance, ExperienceProvenance.VERIFICATION_RESULT)

    def test_sequence_steps_summary_and_partial_failure(self):
        work = self.root / "expdir"
        note = work / "n.txt"
        steps = (
            SequenceStep("a1", "filesystem", "CREATE_DIRECTORY", freeze_params({
                "path": str(work),
                "precondition_observation_hash": observe_path(work).observation_hash,
                "expected_exists": "false",
            })),
            SequenceStep("v1", "verify", "TARGET_EXISTS", freeze_params({"domain": "filesystem", "path": str(work)})),
            SequenceStep("a2", "filesystem", "WRITE_FILE", freeze_params({
                "path": str(note), "content": "harmless-exp",
                "precondition_observation_hash": observe_path(note).observation_hash,
                "expected_exists": "false",
            })),
            SequenceStep("v2", "verify", "TARGET_EXISTS", freeze_params({"domain": "filesystem", "path": str(note)})),
            SequenceStep("a3", "filesystem", "DELETE_FILE", freeze_params({
                "path": str(self.root / "missing-exp.txt"),
                "expected_exists": "true", "expected_type": "file",
            })),
        )
        spec = SequenceSpec("seq-exp", "sujal", steps, max_steps=16)
        out = execute_sequence(
            spec, ApprovalState.APPROVED, spec.spec_hash(),
            emergency_stop_fn=lambda o, s: False,
        )
        self.assertEqual(out.status, SequenceStatus.PRECONDITION_FAILED)
        recs = list(out.experiences)
        self.assertGreaterEqual(len(recs), 5)
        step_recs = [r for r in recs if r.provenance in (
            ExperienceProvenance.SEQUENCE_STEP, ExperienceProvenance.VERIFICATION_RESULT,
        )]
        self.assertEqual(len(step_recs), 5)
        self.assertFalse(any(r.step_id == "unused" for r in recs))
        summary = [r for r in recs if r.provenance == ExperienceProvenance.SEQUENCE_SUMMARY]
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0].outcome, ExperienceOutcome.PRECONDITION_FAILED)
        blob = str(out.as_dict())
        self.assertNotIn("harmless-exp", blob)
        stored = list_experiences("seq-exp")
        self.assertEqual(len(stored), len(recs))


class TestV77SafetyScan(unittest.TestCase):
    def test_no_exfil_or_dynamic_code(self):
        forbidden = (
            "eval(", "exec(", "compile(", "subprocess", "os.system", "shell=True",
            "powershell", "cmd.exe", "javascript:", "pyautogui", "requests", "httpx",
            "urllib", "openai", "gemini", "bedrock", "groq", "elevenlabs",
            "screenshot", "clipboard", "password", "upload",
        )
        for p in EXP_DIR.rglob("*.py"):
            src = p.read_text(encoding="utf-8").lower()
            for token in forbidden:
                self.assertNotIn(token.lower(), src, msg=f"{p} {token}")
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Lambda):
                    self.fail("lambda")
                if isinstance(node, ast.Name) and node.id in ("eval", "exec", "compile"):
                    self.fail(node.id)


class TestV77IntegrationSandbox(unittest.TestCase):
    def setUp(self):
        cost_guard.reset_records_for_tests()
        reset_experience_store_for_tests()
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name).resolve()
        _flags_on(self.root)

    def tearDown(self):
        _flags_off()
        self.td.cleanup()

    def test_full_verify_sequence_experiences(self):
        work = self.root / "work"
        note = work / "note.txt"
        copy = work / "copy.txt"
        moved = work / "moved.txt"

        def st(sid, cap, action, **p):
            return SequenceStep(sid, cap, action, freeze_params(p))

        steps = (
            st("a1", "filesystem", "CREATE_DIRECTORY", path=str(work),
               precondition_observation_hash=observe_path(work).observation_hash, expected_exists="false"),
            st("v1", "verify", "TARGET_EXISTS", domain="filesystem", path=str(work)),
            st("a2", "filesystem", "WRITE_FILE", path=str(note), content="exp-payload",
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
        spec = SequenceSpec("v77-int", "sujal", steps, max_steps=16)
        out = execute_sequence(spec, ApprovalState.APPROVED, spec.spec_hash(), emergency_stop_fn=lambda o, s: False)
        self.assertEqual(out.status, SequenceStatus.SUCCESS, out.as_dict())
        recs = list(out.experiences)
        self.assertEqual(len([r for r in recs if r.provenance != ExperienceProvenance.SEQUENCE_SUMMARY]), 10)
        self.assertEqual(len([r for r in recs if r.provenance == ExperienceProvenance.SEQUENCE_SUMMARY]), 1)
        verifies = [r for r in recs if r.provenance == ExperienceProvenance.VERIFICATION_RESULT]
        self.assertTrue(all(r.verification_status == VerificationStatus.VERIFIED.value for r in verifies))
        hashes = {r.experience_hash for r in recs}
        self.assertEqual(len(hashes), len(recs))
        blob = str(out.as_dict())
        self.assertNotIn("exp-payload", blob)
        missing = execute_fs_action(FsActionRequest(
            action_id="neg", action_type=FsActionType.DELETE_FILE,
            path=str(self.root / "no-such.txt"),
            owner_id="sujal", approval_state=ApprovalState.APPROVED,
        ))
        self.assertEqual(missing.status, Status.PATH_NOT_FOUND)
        neg = record_from_kernel_result(
            capability="filesystem", action="DELETE_FILE", result=missing,
            risk_level="HIGH", approval_state="APPROVED", provenance=ExperienceProvenance.MANUAL_TEST,
        )
        self.assertEqual(neg.outcome, ExperienceOutcome.FAILED)
        self.assertNotEqual(neg.outcome, ExperienceOutcome.SUCCESS)
        note.unlink(missing_ok=True)
        if work.exists():
            work.rmdir()


if __name__ == "__main__":
    unittest.main()
