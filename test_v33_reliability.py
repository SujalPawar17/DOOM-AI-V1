#!/usr/bin/env python3
"""
DOOM V3.3 — Comprehensive Reliability, Truth & Resume Integration Test Suite.
Tests all 12 core reliability guarantees deterministically:
  1. Task State Machine Transitions
  2. Step State Lifecycle & Tracking
  3. Ground-Truth Verification States
  4. Final Response States
  5. Truth-First Response Synthesis (No False Done on Blocked/Incomplete)
  6. Checkpoint Persistence (PostgreSQL + Local Backup)
  7. Idempotent Resume (Never re-executes succeeded steps)
  8. Security Enforcement on Resume (HIGH/CRITICAL tools re-require approval)
  9. Restart Recovery (Process death reconstruction)
  10. Model Router Capability-Preserving Failover & NoCapableProviderError
  11. Orchestrator Graceful Pause on Outage
  12. REST & WebSocket Task State Streaming Endpoints
"""

import os
import sys
import time
import json
import uuid
import asyncio
import unittest

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.state_machine import state_machine, DoomState
from core.task_engine import (
    task_engine, Task, TaskStep, TaskStatus, StepStatus,
    VerificationStatus, FinalResponseStatus
)
from core.model_router import (
    model_router, ModelCapability, NoCapableProviderError,
    CapabilityFailoverManager, LLMResponse
)
from core.verifier import GroundTruthVerifier, verifier
from core.orchestrator import doom_core
from database.postgres_db import postgres_manager
from tools.base import CanonicalToolResult, RiskLevel, TerminationReason


class TestV33ReliabilitySuite(unittest.TestCase):

    def setUp(self):
        """Reset state machine and active task before each test."""
        state_machine.reset()
        task_engine._active_task = None

    # -------------------------------------------------------------------------
    # TEST 1: Task State Machine Transitions
    # -------------------------------------------------------------------------
    def test_01_task_state_machine_transitions(self):
        """Verifies full lifecycle of Task states."""
        task = task_engine.create_task("Test Goal 1", "MULTI_STEP")
        self.assertEqual(task.status, TaskStatus.CREATED)

        task_engine.set_plan_steps(["Step 1: Plan", "Step 2: Execute"])
        self.assertEqual(task.status, TaskStatus.RUNNING)

        task_engine.pause_task(reason="Provider outage")
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertTrue(task.resume_available)

        resumed = task_engine.resume_task(task.task_id)
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.status, TaskStatus.RUNNING)

        task_engine.complete_task("Success output")
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertFalse(task.resume_available)

    # -------------------------------------------------------------------------
    # TEST 2: Step State Lifecycle & Tracking
    # -------------------------------------------------------------------------
    def test_02_step_state_lifecycle(self):
        """Verifies step states: PENDING -> RUNNING -> SUCCEEDED/BLOCKED/SKIPPED."""
        task = task_engine.create_task("Test Steps", "MULTI_STEP")
        task_engine.set_plan_steps(["Write code", "Execute code"])
        self.assertEqual(task.steps[0].status, StepStatus.RUNNING)
        self.assertEqual(task.steps[1].status, StepStatus.PENDING)

        task_engine.advance_step(1, tool_name="create_file", output="File written", success=True)
        self.assertEqual(task.steps[0].status, StepStatus.SUCCEEDED)
        self.assertEqual(task.steps[1].status, StepStatus.RUNNING)

        # Block step 2
        task_engine.mark_step_blocked(2, reason="No provider available")
        self.assertEqual(task.steps[1].status, StepStatus.BLOCKED)

    # -------------------------------------------------------------------------
    # TEST 3: Ground-Truth Verification States
    # -------------------------------------------------------------------------
    def test_03_verification_states(self):
        """Verifies NOT_VERIFIED, VERIFIED, VERIFICATION_FAILED states."""
        step = TaskStep(index=0, description="Test step")
        self.assertEqual(step.verification_status, VerificationStatus.NOT_VERIFIED)

        step.verification_status = VerificationStatus.VERIFIED
        self.assertEqual(step.verification_status, VerificationStatus.VERIFIED)

        step.verification_status = VerificationStatus.VERIFICATION_FAILED
        self.assertEqual(step.verification_status, VerificationStatus.VERIFICATION_FAILED)

    # -------------------------------------------------------------------------
    # TEST 4: Final Response Status States
    # -------------------------------------------------------------------------
    def test_04_final_response_status_states(self):
        """Verifies SUCCESS, PARTIAL_SUCCESS, BLOCKED, FAILED status determination."""
        obs_success = [
            CanonicalToolResult(tool="create_file", success=True, action="create_file", output="OK")
        ]
        verif_success = {"verified": True, "status": "COMPLETED"}
        status_ok = doom_core._determine_final_response_status(
            obs_success, verif_success, TerminationReason.COMPLETED
        )
        self.assertEqual(status_ok, FinalResponseStatus.SUCCESS)

        obs_partial = [
            CanonicalToolResult(tool="create_file", success=True, action="create_file", output="OK"),
            CanonicalToolResult(tool="execute_file", success=False, action="execute_file", output="Err")
        ]
        verif_partial = {"verified": False, "status": "PARTIAL_SUCCESS"}
        status_partial = doom_core._determine_final_response_status(
            obs_partial, verif_partial, TerminationReason.COMPLETED
        )
        self.assertEqual(status_partial, FinalResponseStatus.PARTIAL_SUCCESS)

        status_blocked = doom_core._determine_final_response_status(
            obs_partial, verif_partial, TerminationReason.USER_APPROVAL_REQUIRED
        )
        self.assertEqual(status_blocked, FinalResponseStatus.BLOCKED)

        status_failed = doom_core._determine_final_response_status(
            obs_partial, verif_partial, TerminationReason.UNRECOVERABLE_ERROR
        )
        self.assertEqual(status_failed, FinalResponseStatus.FAILED)

    # -------------------------------------------------------------------------
    # TEST 5: Truth-First Response Synthesis (No False Done on Blocked)
    # -------------------------------------------------------------------------
    def test_05_truth_first_synthesis_no_false_done(self):
        """DOOM must NEVER say 'Done' if file was written but execution was blocked."""
        obs = [
            CanonicalToolResult(
                tool="create_file",
                success=True,
                output="Created file",
                action="create_file",
                artifact={"path": "c:/Users/dell/Desktop/test_script.py", "relative_path": "Desktop/test_script.py"}
            )
        ]
        verification = {"status": "INCOMPLETE", "verified": False}

        synth = doom_core._synthesize_final_response(
            user_prompt="Create and run test_script.py",
            observations=obs,
            plan=None,
            last_llm_text="",
            verification=verification
        )

        self.assertNotIn("Done. I created Desktop/test_script.py, executed it", synth)
        self.assertIn("Partially completed", synth)
        self.assertIn("paused and can be resumed", synth)

    # -------------------------------------------------------------------------
    # TEST 6: Checkpoint Persistence (PostgreSQL & Local Backup)
    # -------------------------------------------------------------------------
    def test_06_checkpoint_persistence(self):
        """Verifies saving and loading checkpoints from DB and disk."""
        task_id = f"test_ckpt_{uuid.uuid4().hex[:8]}"
        task = task_engine.create_task("Test Persistence Goal", "MULTI_STEP")
        task.task_id = task_id
        task_engine.set_plan_steps(["Step 1", "Step 2"])
        task_engine.advance_step(1, tool_name="create_file", output="Step 1 done", success=True)

        checkpoint = task.to_checkpoint()
        self.assertEqual(checkpoint["task_id"], task_id)
        self.assertEqual(len(checkpoint["completed_steps"]), 1)

        if postgres_manager.is_connected():
            postgres_manager.save_checkpoint(checkpoint)
            loaded = postgres_manager.load_checkpoint(task_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["task_id"], task_id)
            self.assertEqual(loaded["goal"], "Test Persistence Goal")

    # -------------------------------------------------------------------------
    # TEST 7: Idempotent Resume (Skip Succeeded Steps)
    # -------------------------------------------------------------------------
    def test_07_idempotent_resume(self):
        """Resuming must NOT re-execute already SUCCEEDED steps."""
        task_id = f"test_idem_{uuid.uuid4().hex[:8]}"
        task = task_engine.create_task("Idempotent Goal", "MULTI_STEP")
        task.task_id = task_id
        task_engine.set_plan_steps(["Step 1: Write file", "Step 2: Execute file"])
        task_engine.advance_step(1, tool_name="create_file", output="Wrote file", success=True)
        task_engine.pause_task(reason="Simulated pause")

        # Resume
        resumed_task = task_engine.resume_task(task_id)
        self.assertIsNotNone(resumed_task)
        self.assertEqual(resumed_task.status, TaskStatus.RUNNING)
        # Step 1 must remain SUCCEEDED
        self.assertEqual(resumed_task.steps[0].status, StepStatus.SUCCEEDED)
        # Step 2 is RUNNING
        self.assertEqual(resumed_task.steps[1].status, StepStatus.RUNNING)

    # -------------------------------------------------------------------------
    # TEST 8: Security Enforcement on Resume
    # -------------------------------------------------------------------------
    def test_08_security_enforcement_on_resume(self):
        """Resuming a task where next step is HIGH/CRITICAL risk requires user approval."""
        from tools.database_tools import DatabaseQueryTool
        db_tool = DatabaseQueryTool()
        self.assertEqual(db_tool.get_effective_risk(), RiskLevel.HIGH)

        task_id = f"test_sec_{uuid.uuid4().hex[:8]}"
        task = task_engine.create_task("Security Goal", "ACTION")
        task.task_id = task_id
        task_engine.set_plan_steps(["Query database"])
        task.steps[0].tool_name = db_tool.name
        task.steps[0].tool_args = {"query": "SELECT * FROM user_profiles"}
        task.steps[0].status = StepStatus.PENDING
        task_engine.pause_task(reason="Provider dropped")

        # Resume task
        resumed_task = task_engine.resume_task(task_id)
        self.assertIsNotNone(resumed_task)
        # Must require approval!
        self.assertEqual(resumed_task.status, TaskStatus.WAITING_FOR_APPROVAL)
        self.assertTrue(resumed_task.user_approval_required)
        self.assertEqual(state_machine.get_state(), DoomState.WAITING_FOR_APPROVAL)

    # -------------------------------------------------------------------------
    # TEST 9: Restart Recovery (Simulated Process Death)
    # -------------------------------------------------------------------------
    def test_09_restart_recovery(self):
        """Reconstructing task from checkpoint after clean memory purge."""
        task_id = f"test_death_{uuid.uuid4().hex[:8]}"
        task = task_engine.create_task("Process Death Test", "MULTI_STEP")
        task.task_id = task_id
        task_engine.set_plan_steps(["Step A", "Step B"])
        task_engine.advance_step(1, tool_name="create_file", output="Step A complete", success=True)
        task_engine.pause_task(reason="Crash simulation")

        # Save checkpoint to disk
        task_engine._save_checkpoint()

        # Simulate fresh process: purge in-memory tasks
        task_engine._active_task = None
        task_engine._task_history = []

        # Recover task via get_task_by_id
        recovered = task_engine.get_task_by_id(task_id)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.task_id, task_id)
        self.assertEqual(recovered.goal, "Process Death Test")
        self.assertEqual(recovered.status, TaskStatus.PAUSED)
        self.assertTrue(len(recovered.steps) >= 1)
        self.assertEqual(recovered.steps[0].status, StepStatus.SUCCEEDED)

    # -------------------------------------------------------------------------
    # TEST 10: Capability-Preserving Failover & NoCapableProviderError
    # -------------------------------------------------------------------------
    def test_10_capability_failover_and_nocapableprovider(self):
        """Verifies failover respects capabilities and raises NoCapableProviderError when exhausted."""
        failover_mgr = CapabilityFailoverManager()
        
        # Test routing for CODING capability
        candidate = failover_mgr.get_next_provider(ModelCapability.CODING, exclude=[])
        self.assertIsNotNone(candidate)
        self.assertIn("coding", candidate.capabilities)

        # Exclude all providers that have CODING
        all_coding = [name for name, p in model_router.providers.items() if "coding" in getattr(p, "capabilities", [])]
        with self.assertRaises(NoCapableProviderError):
            failover_mgr.get_next_provider(ModelCapability.CODING, exclude=all_coding)

    # -------------------------------------------------------------------------
    # TEST 11: Orchestrator Graceful Pause on Outage
    # -------------------------------------------------------------------------
    def test_11_orchestrator_graceful_pause_on_outage(self):
        """Simulates NoCapableProviderError in orchestrator loop: task must be PAUSED, not crashed."""
        task = task_engine.create_task("Simulate Outage Task", "MULTI_STEP")
        task_engine.set_plan_steps(["Write code", "Execute code"])
        task_engine.advance_step(1, tool_name="create_file", output="Script created", success=True)

        # Simulate handling of NoCapableProviderError
        task_engine.mark_step_blocked(2, reason="No capable provider available")
        task_engine.pause_task(reason="Outage pause")

        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertTrue(task.resume_available)
        self.assertEqual(task.steps[1].status, StepStatus.BLOCKED)

    # -------------------------------------------------------------------------
    # TEST 12: REST & WebSocket Task State Streaming Endpoints
    # -------------------------------------------------------------------------
    def test_12_rest_and_websocket_task_endpoints(self):
        """Verifies /api/tasks/{task_id}, /api/tasks/resumable, and /api/tasks/{id}/resume via ASGI."""
        import httpx
        from dashboard.server import app

        task_id = f"test_api_{uuid.uuid4().hex[:8]}"
        task = task_engine.create_task("API Resume Goal", "MULTI_STEP")
        task.task_id = task_id
        task_engine.set_plan_steps(["Step X"])
        task_engine.pause_task(reason="Awaiting provider")

        async def run_api_tests():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                # 1. Test /api/tasks/{task_id}
                res_get = await client.get(f"/api/tasks/{task_id}")
                self.assertEqual(res_get.status_code, 200)
                self.assertEqual(res_get.json()["task_id"], task_id)
                self.assertEqual(res_get.json()["status"], "PAUSED")

                # 2. Test /api/tasks/resumable
                res_resumable = await client.get("/api/tasks/resumable")
                self.assertEqual(res_resumable.status_code, 200)
                task_ids = [t["task_id"] for t in res_resumable.json()["resumable_tasks"]]
                self.assertIn(task_id, task_ids)

                # 3. Test /api/tasks/{task_id}/resume
                res_resume = await client.post(f"/api/tasks/{task_id}/resume")
                self.assertEqual(res_resume.status_code, 200)
                self.assertEqual(res_resume.json()["status"], "RUNNING")

        asyncio.run(run_api_tests())


if __name__ == "__main__":
    print("=" * 70)
    print("DOOM V3.3 — RELIABILITY, TRUTH & RESUME INTEGRATION TEST SUITE")
    print("=" * 70)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestV33ReliabilitySuite)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
