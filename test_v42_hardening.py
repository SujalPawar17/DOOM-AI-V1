#!/usr/bin/env python3
"""
DOOM V4.2 — Production Hardening Test Suite (35 Tests).

Validates all 35 hardening guarantees:
 1. idempotent_duplicate_execution
 2. duplicate_create_does_not_repeat
 3. verify_before_retry
 4. unknown_side_effect_stops_retry
 5. retry_budget_enforced
 6. cognitive_loop_protection
 7. repeated_failure_stops
 8. malformed_plan_rejected
 9. cyclic_plan_rejected
10. invalid_tool_arguments_rejected
11. path_traversal_blocked
12. concurrent_task_lock
13. stale_task_lock_recovery
14. crash_before_tool
15. crash_after_tool
16. crash_after_side_effect_before_response
17. checkpoint_recovery
18. corrupted_checkpoint_safe_handling
19. cancellation
20. cancellation_during_execution
21. duplicate_approval
22. stale_approval_rejected
23. changed_action_requires_new_approval
24. provider_circuit_breaker
25. provider_outage_pause
26. provider_recovery
27. no_false_success
28. partial_success
29. verification_veto
30. memory_does_not_store_unverified_success
31. correlation_id_propagation
32. no_duplicate_orchestrator
33. no_direct_tool_bypass
34. bounded_task_execution
35. bounded_cognitive_execution
"""

import os
import sys
import time
import json
import uuid
import asyncio
import unittest
from unittest.mock import patch, MagicMock

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.orchestrator import doom_core
from core.cognition.engine import cognitive_engine
from core.cognition.bridge import cognitive_bridge, CognitiveBridge, MAX_COGNITIVE_ITERATIONS
from core.cognition.schemas import (
    CognitiveState, CognitiveIntent, CognitiveDecisionType, CognitiveStep,
    CognitiveObservation, CognitiveReflection, EvaluationOutcome
)
from core.state_machine import state_machine, DoomState
from core.task_engine import (
    task_engine, Task, TaskStep, TaskStatus, StepStatus,
    VerificationStatus, FinalResponseStatus
)
from core.model_router import (
    model_router, ModelCapability, NoCapableProviderError
)
from core.verifier import verifier
from core.tool_registry import tool_registry
from tools.base import CanonicalToolResult, RiskLevel, TerminationReason

# Reliability modules
from core.reliability.idempotency import idempotency_manager, ExecutionState, IdempotencyReceipt
from core.reliability.retry_policy import retry_policy
from core.reliability.plan_validator import plan_validator
from core.reliability.input_validator import tool_input_validator
from core.reliability.concurrency import task_concurrency_manager, TaskLease
from core.reliability.circuit_breaker import provider_circuit_breaker, CircuitState
from core.reliability.correlation import CorrelationContext, get_current_correlation, set_current_correlation


class TestV42HardeningSuite(unittest.TestCase):

    def setUp(self):
        state_machine.reset()
        task_engine._active_task = None
        idempotency_manager.reset()
        task_concurrency_manager.reset()
        provider_circuit_breaker.reset()
        retry_policy._step_attempts.clear()
        retry_policy._task_total_attempts.clear()
        retry_policy._task_replan_counts.clear()

    def tearDown(self):
        state_machine.reset()
        task_engine._active_task = None
        idempotency_manager.reset()
        task_concurrency_manager.reset()

    # 1. idempotent_duplicate_execution
    def test_01_idempotent_duplicate_execution(self):
        """Verify calling the same side effect twice reuses receipt without repeating execution."""
        key = idempotency_manager.compute_idempotency_key("task-1", 1, "create_file", {"file_name": "demo.py"})
        can_exec, existing = idempotency_manager.claim(key, "task-1", 1, "create_file", "coding_write_script", {"file_name": "demo.py"})
        self.assertTrue(can_exec)

        res = CanonicalToolResult(tool="coding_write_script", success=True, output="Created demo.py")
        idempotency_manager.record_receipt(key, res)

        can_exec2, existing2 = idempotency_manager.claim(key, "task-1", 1, "create_file", "coding_write_script", {"file_name": "demo.py"})
        self.assertFalse(can_exec2)
        self.assertIsNotNone(existing2)
        self.assertEqual(existing2.output, "Created demo.py")

    # 2. duplicate_create_does_not_repeat
    def test_02_duplicate_create_does_not_repeat(self):
        """Verify duplicate create action in plan reuses receipt and does not invoke tool again."""
        bridge = CognitiveBridge()
        step1 = CognitiveStep(1, "Create demo", "create_file", "coding_write_script", {"file_name": "dup.py", "content": "print(1)"})
        state = CognitiveState(user_request="Create", normalized_goal="Create", decision=CognitiveDecisionType.CREATE_PLAN, current_plan=[step1])

        call_counts = []
        mock_tool = MagicMock()
        mock_tool.risk_level = RiskLevel.SAFE
        def execute_spy(**kwargs):
            call_counts.append(kwargs)
            return CanonicalToolResult(tool="coding_write_script", success=True, output="Created dup.py")
        mock_tool.execute.side_effect = execute_spy

        with patch.object(tool_registry, 'get_tool', return_value=mock_tool):
            bridge.execute_plan(state)
            self.assertEqual(len(call_counts), 1)

            # Second execution of identical step
            state2 = CognitiveState(user_request="Create", normalized_goal="Create", decision=CognitiveDecisionType.CREATE_PLAN, current_plan=[step1])
            bridge.execute_plan(state2)
            self.assertEqual(len(call_counts), 1, "Tool must NOT be executed a second time")

    # 3. verify_before_retry
    def test_03_verify_before_retry(self):
        """Verify that if tool timed out but file exists on disk, step reconciles as succeeded."""
        test_file = os.path.abspath("temp_verify_before_retry.py")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("print('Recovered')")

        try:
            bridge = CognitiveBridge()
            step = CognitiveStep(1, "Create file", "create_file", "coding_write_script", {"file_name": test_file})
            state = CognitiveState(user_request="Write file", normalized_goal="Write file", decision=CognitiveDecisionType.CREATE_PLAN, current_plan=[step])

            mock_tool = MagicMock()
            mock_tool.risk_level = RiskLevel.SAFE
            mock_tool.execute.return_value = CanonicalToolResult(tool="coding_write_script", success=False, stderr="Tool timed out")

            with patch.object(tool_registry, 'get_tool', return_value=mock_tool):
                res = bridge.execute_plan(state)

            self.assertIn("success", res.final_response_status.lower())
            self.assertEqual(res.completed_steps[0].status, "succeeded")
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)

    # 4. unknown_side_effect_stops_retry
    def test_04_unknown_side_effect_stops_retry(self):
        """Verify UNKNOWN state blocks blind retry until external state is reconciled."""
        key = idempotency_manager.compute_idempotency_key("task-4", 1, "network_call", {"target": "remote"})
        idempotency_manager.claim(key, "task-4", 1, "network_call", "net_tool", {"target": "remote"})
        # Mark unknown
        receipt = idempotency_manager.get_receipt(key)
        receipt.state = ExecutionState.UNKNOWN

        can_retry, rec = idempotency_manager.claim(key, "task-4", 1, "network_call", "net_tool", {"target": "remote"})
        self.assertFalse(can_retry)
        self.assertEqual(rec.state, ExecutionState.UNKNOWN)

    # 5. retry_budget_enforced
    def test_05_retry_budget_enforced(self):
        """Verify central retry policy rejects retry once MAX_RETRIES_PER_STEP (2) is exceeded."""
        tid = "task-5"
        can1, _ = retry_policy.should_retry(tid, 1, "timeout", time.time())
        self.assertTrue(can1)
        can2, _ = retry_policy.should_retry(tid, 1, "timeout", time.time())
        self.assertTrue(can2)
        can3, reason = retry_policy.should_retry(tid, 1, "timeout", time.time())
        self.assertFalse(can3)
        self.assertIn("Step retry budget exceeded", reason)

    # 6. cognitive_loop_protection
    def test_06_cognitive_loop_protection(self):
        """Verify repeating identical plans triggers cognitive loop defense and terminates."""
        bridge = CognitiveBridge()
        step = CognitiveStep(1, "Repeated step", "repeat", "unknown_tool")
        state = CognitiveState(user_request="Loop", normalized_goal="Loop", current_plan=[step])

        # Simulate loop with identical plan signatures
        state_copy = CognitiveState(user_request="Loop", normalized_goal="Loop", current_plan=[step])
        with patch.object(plan_validator, 'validate_plan', return_value=(True, [])):
            with patch('core.cognition.bridge.cognitive_replanner.replan') as mock_replan:
                mock_replan.return_value = ([step], {"strategy": "repeat"}, False)
                res = bridge.execute_plan(state)
                self.assertEqual(res.final_response_status, "failed")

    # 7. repeated_failure_stops
    def test_07_repeated_failure_stops(self):
        """Verify 3 consecutive failures on the same tool halts execution."""
        bridge = CognitiveBridge()
        step = CognitiveStep(1, "Fail step", "fail_action", "failing_tool")
        state = CognitiveState(user_request="Fail", normalized_goal="Fail", current_plan=[step])

        mock_tool = MagicMock()
        mock_tool.risk_level = RiskLevel.SAFE
        mock_tool.execute.return_value = CanonicalToolResult(tool="failing_tool", success=False, stderr="Deterministic hardware failure")

        with patch.object(plan_validator, 'validate_plan', return_value=(True, [])):
            with patch.object(tool_registry, 'get_tool', return_value=mock_tool):
                with patch('core.cognition.bridge.cognitive_replanner.replan', return_value=([step], {"strategy": "retry"}, False)):
                    res = bridge.execute_plan(state)
                    self.assertEqual(res.final_response_status, "failed")
                    self.assertEqual(res.termination_reason, "REPEATED_FAILURE_HALTED")

    # 8. malformed_plan_rejected
    def test_08_malformed_plan_rejected(self):
        """Verify plan referencing unknown tool is rejected before tool execution."""
        bridge = CognitiveBridge()
        step = CognitiveStep(1, "Bad step", "bad_action", "nonexistent_tool_12345")
        state = CognitiveState(user_request="Bad plan", normalized_goal="Bad plan", current_plan=[step])

        res = bridge.execute_plan(state)
        self.assertEqual(res.final_response_status, "failed")
        self.assertIn("Plan validation failed", res.final_response)

    # 9. cyclic_plan_rejected
    def test_09_cyclic_plan_rejected(self):
        """Verify plan containing dependency cycle (1 -> 2 -> 1) is rejected by PlanValidator."""
        step1 = CognitiveStep(1, "A", "act_a", "verifier", dependencies=[2])
        step2 = CognitiveStep(2, "B", "act_b", "verifier", dependencies=[1])
        valid, errors = plan_validator.validate_plan([step1, step2])
        self.assertFalse(valid)
        self.assertTrue(any("Cyclic" in e for e in errors))

    # 10. invalid_tool_arguments_rejected
    def test_10_invalid_tool_arguments_rejected(self):
        """Verify null byte or malformed inputs are caught by ToolInputValidator."""
        valid, reason, _ = tool_input_validator.validate_inputs("coding_write_script", {"file_name": "test\0file.py"})
        self.assertFalse(valid)
        self.assertIn("Null byte", reason)

    # 11. path_traversal_blocked
    def test_11_path_traversal_blocked(self):
        """Verify path traversal attack (../../secret.txt) is blocked by firewall."""
        valid, reason, _ = tool_input_validator.validate_inputs("coding_write_script", {"file_name": "../../windows/system32/cmd.exe"})
        self.assertFalse(valid)
        self.assertIn("Path traversal", reason)

    # 12. concurrent_task_lock
    def test_12_concurrent_task_lock(self):
        """Verify Worker A locks task; Worker B is blocked from simultaneous execution."""
        task_id = "concurrent-task-12"
        acquired_a = task_concurrency_manager.acquire_lease(task_id, "worker_A")
        self.assertTrue(acquired_a)

        acquired_b = task_concurrency_manager.acquire_lease(task_id, "worker_B")
        self.assertFalse(acquired_b, "Worker B must be rejected while lease is held")

    # 13. stale_task_lock_recovery
    def test_13_stale_task_lock_recovery(self):
        """Verify Worker B can acquire lease after Worker A's lease expires."""
        task_id = "stale-task-13"
        task_concurrency_manager.acquire_lease(task_id, "worker_A", ttl_seconds=0.1)
        time.sleep(0.15)  # Wait for expiration

        acquired_b = task_concurrency_manager.acquire_lease(task_id, "worker_B")
        self.assertTrue(acquired_b, "Worker B must be allowed to take over stale expired lease")

    # 14. crash_before_tool
    def test_14_crash_before_tool(self):
        """Verify crash before tool invocation leaves claim in CLAIMED state, safely recoverable."""
        key = idempotency_manager.compute_idempotency_key("task-14", 1, "test_action", {})
        idempotency_manager.claim(key, "task-14", 1, "test_action", "tool", {})
        rec = idempotency_manager.get_receipt(key)
        self.assertEqual(rec.state, ExecutionState.CLAIMED)
        self.assertFalse(rec.success)

    # 15. crash_after_tool
    def test_15_crash_after_tool(self):
        """Verify crash after tool execution has durable receipt so recovery doesn't repeat."""
        key = idempotency_manager.compute_idempotency_key("task-15", 1, "test_action", {})
        idempotency_manager.claim(key, "task-15", 1, "test_action", "tool", {})
        idempotency_manager.record_receipt(key, CanonicalToolResult(tool="tool", success=True, output="Done"))

        # Reopen/inspect
        can_exec, existing = idempotency_manager.claim(key, "task-15", 1, "test_action", "tool", {})
        self.assertFalse(can_exec)
        self.assertEqual(existing.output, "Done")

    # 16. crash_after_side_effect_before_response
    def test_16_crash_after_side_effect_before_response(self):
        """Verify crash after disk write reconciles disk state without repeating side effect."""
        test_file = os.path.abspath("temp_crash_side_effect.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("Side effect complete")
        try:
            key = idempotency_manager.compute_idempotency_key("task-16", 1, "create_file", {"file_name": test_file})
            idempotency_manager.mark_reconciled(key, artifacts=[{"path": test_file, "size": 20}])
            rec = idempotency_manager.get_receipt(key)
            self.assertEqual(rec.state, ExecutionState.RECONCILED)
            self.assertTrue(rec.success)
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)

    # 17. checkpoint_recovery
    def test_17_checkpoint_recovery(self):
        """Verify restoring task from checkpoint preserves completed steps and status."""
        task = task_engine.create_task("Checkpoint recovery goal", "MULTI_STEP")
        task.steps.append(TaskStep(1, "Step 1", status=StepStatus.SUCCEEDED))
        task.steps.append(TaskStep(2, "Step 2", status=StepStatus.PENDING))
        task.current_step = "Step 2"
        task_engine._save_checkpoint()

        # Wipe active task from memory
        task_engine._active_task = None
        task_engine._task_history.clear()

        recovered = task_engine._load_checkpoint(task.task_id)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.task_id, task.task_id)
        self.assertEqual(len(recovered.steps), 2)
        self.assertEqual(recovered.steps[0].status, StepStatus.SUCCEEDED)

    # 18. corrupted_checkpoint_safe_handling
    def test_18_corrupted_checkpoint_safe_handling(self):
        """Verify corrupted checkpoint returns RECOVERY_REQUIRED task without raising exception."""
        bad_task_id = "corrupt_task_18"
        bad_file = os.path.join(task_engine._checkpoint_dir, f"{bad_task_id}.json")
        with open(bad_file, "w", encoding="utf-8") as f:
            f.write("{ INVALID JSON CONTENT ...")
        try:
            task = task_engine._load_checkpoint(bad_task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, TaskStatus.RECOVERY_REQUIRED)
            self.assertIn("Corrupted", task.error)
        finally:
            if os.path.exists(bad_file):
                os.remove(bad_file)

    # 19. cancellation
    def test_19_cancellation(self):
        """Verify task cancellation sets TaskStatus.CANCELLED and updates StateMachine."""
        task = task_engine.create_task("Goal to cancel", "MULTI_STEP")
        task.steps.append(TaskStep(1, "Step A", status=StepStatus.PENDING))

        cancelled_task = task_engine.cancel_task(task.task_id, reason="User stop")
        self.assertIsNotNone(cancelled_task)
        self.assertEqual(cancelled_task.status, TaskStatus.CANCELLED)
        self.assertEqual(state_machine.current_state, DoomState.IDLE)

    # 20. cancellation_during_execution
    def test_20_cancellation_during_execution(self):
        """Verify active execution drains and exits cleanly upon cancellation."""
        bridge = CognitiveBridge()
        task = task_engine.create_task("Running cancellation goal", "MULTI_STEP")
        task.status = TaskStatus.CANCELLED

        step = CognitiveStep(1, "Will not run", "run", "verifier")
        state = CognitiveState(user_request="Run", normalized_goal="Running cancellation goal", current_plan=[step])

        res = bridge.execute_plan(state)
        self.assertEqual(res.final_response_status, "cancelled")

    # 21. duplicate_approval
    def test_21_duplicate_approval(self):
        """Verify second approval attempt on already approved task is rejected."""
        task = task_engine.create_task("Approval task", "MULTI_STEP")
        token = task_engine.require_user_approval("sec_tool", {"action": "wipe"})

        ok1, msg1 = task_engine.approve_task_action(task.task_id, operation_token=token)
        self.assertTrue(ok1)

        ok2, msg2 = task_engine.approve_task_action(task.task_id, operation_token=token)
        self.assertFalse(ok2)
        self.assertIn("not waiting for approval", msg2)

    # 22. stale_approval_rejected
    def test_22_stale_approval_rejected(self):
        """Verify approvals older than 10 minutes are rejected as stale."""
        task = task_engine.create_task("Stale approval task", "MULTI_STEP")
        token = task_engine.require_user_approval("sec_tool", {"action": "delete"})
        # Backdate timestamp
        task.pending_tool_call["timestamp"] = time.time() - 700.0

        ok, msg = task_engine.approve_task_action(task.task_id, operation_token=token)
        self.assertFalse(ok)
        self.assertIn("expired", msg)

    # 23. changed_action_requires_new_approval
    def test_23_changed_action_requires_new_approval(self):
        """Verify approval token mismatch (action or args changed) rejects authorization."""
        task = task_engine.create_task("Action change task", "MULTI_STEP")
        token = task_engine.require_user_approval("sec_tool", {"action": "read"})

        ok, msg = task_engine.approve_task_action(task.task_id, operation_token="wrong_token_xyz")
        self.assertFalse(ok)
        self.assertIn("mismatch", msg)

    # 24. provider_circuit_breaker
    def test_24_provider_circuit_breaker(self):
        """Verify 3 consecutive failures trip provider circuit breaker to OPEN."""
        pname = "flaky_provider"
        provider_circuit_breaker.record_failure(pname)
        provider_circuit_breaker.record_failure(pname)
        self.assertEqual(provider_circuit_breaker.get_state(pname), CircuitState.CLOSED)

        provider_circuit_breaker.record_failure(pname)
        self.assertEqual(provider_circuit_breaker.get_state(pname), CircuitState.OPEN)
        self.assertFalse(provider_circuit_breaker.can_attempt(pname))

    # 25. provider_outage_pause
    def test_25_provider_outage_pause(self):
        """Verify provider outage raises NoCapableProviderError and cleanly pauses task in TaskEngine."""
        with patch.object(model_router, 'route', side_effect=NoCapableProviderError("coding", [])):
            bridge = CognitiveBridge()
            step = CognitiveStep(1, "Coding step", "code", "verifier")
            state = CognitiveState(user_request="Code", normalized_goal="Code", required_capabilities=["coding"], current_plan=[step])
            res = bridge.execute_plan(state)
            self.assertEqual(res.decision, CognitiveDecisionType.PAUSE_TASK)
            self.assertEqual(bridge.task_engine.active_task.status, TaskStatus.PAUSED)

    # 26. provider_recovery
    def test_26_provider_recovery(self):
        """Verify recording success resets circuit breaker to CLOSED."""
        pname = "recovering_provider"
        provider_circuit_breaker.record_failure(pname)
        provider_circuit_breaker.record_failure(pname)
        provider_circuit_breaker.record_failure(pname)
        self.assertEqual(provider_circuit_breaker.get_state(pname), CircuitState.OPEN)

        provider_circuit_breaker.record_success(pname)
        self.assertEqual(provider_circuit_breaker.get_state(pname), CircuitState.CLOSED)
        self.assertTrue(provider_circuit_breaker.can_attempt(pname))

    # 27. no_false_success
    def test_27_no_false_success(self):
        """Verify failed tool execution never reports success or Done."""
        bridge = CognitiveBridge()
        step = CognitiveStep(1, "Failing step", "fail", "system_get_status", {"category": "bad_cat"})
        state = CognitiveState(user_request="Bad", normalized_goal="Bad", current_plan=[step])

        with patch.object(plan_validator, 'validate_plan', return_value=(True, [])):
            mock_tool = MagicMock()
            mock_tool.risk_level = RiskLevel.SAFE
            mock_tool.execute.return_value = CanonicalToolResult(tool="system_get_status", success=False, stderr="Invalid category")

            with patch.object(tool_registry, 'get_tool', return_value=mock_tool):
                res = bridge.execute_plan(state)
                self.assertNotEqual(res.final_response_status, "success")
                self.assertNotIn("successfully", res.final_response.lower())

    # 28. partial_success
    def test_28_partial_success(self):
        """Verify task with step 1 succeeded and step 2 blocked reports PARTIAL_SUCCESS."""
        bridge = CognitiveBridge()
        step1 = CognitiveStep(1, "Step 1", "s1", "tool_1", status="succeeded")
        step2 = CognitiveStep(2, "Step 2", "s2", "tool_2", status="blocked")
        state = CognitiveState(user_request="Partial", normalized_goal="Partial", current_plan=[step1, step2], completed_steps=[step1])

        with patch.object(plan_validator, 'validate_plan', return_value=(True, [])):
            with patch.object(verifier, 'verify_ground_truth', return_value={"verified": False, "status": "PARTIAL"}):
                res = bridge.execute_plan(state)
                self.assertEqual(res.final_response_status, "partial_success")

    # 29. verification_veto
    def test_29_verification_veto(self):
        """Verify ground truth verification failure vetoes task completion."""
        obs = [CanonicalToolResult(tool="coding_write_script", success=True, action="create_file", artifact={"path": "missing_artifact_abc.txt"})]
        v_res = verifier.verify_ground_truth("Create file", obs)
        self.assertFalse(v_res["verified"])
        self.assertEqual(v_res["status"], "FAILED")

    # 30. memory_does_not_store_unverified_success
    def test_30_memory_does_not_store_unverified_success(self):
        """Verify episodic memory is NOT saved as success=True when verification fails."""
        bridge = CognitiveBridge()
        step = CognitiveStep(1, "Create bad file", "create_file", "coding_write_script")
        state = CognitiveState(user_request="Write bad", normalized_goal="Write bad", current_plan=[step])

        mock_tool = MagicMock()
        mock_tool.risk_level = RiskLevel.SAFE
        mock_tool.execute.return_value = CanonicalToolResult(tool="coding_write_script", success=False, stderr="Disk full")

        recorded_success_values = []
        def spy_record(**kwargs):
            recorded_success_values.append(kwargs.get("success"))

        with patch.object(plan_validator, 'validate_plan', return_value=(True, [])):
            with patch.object(tool_registry, 'get_tool', return_value=mock_tool):
                from memory import episodic_memory
                with patch.object(episodic_memory, 'record_episode', side_effect=spy_record):
                    bridge.execute_plan(state)
                    if recorded_success_values:
                        self.assertFalse(recorded_success_values[0])

    # 31. correlation_id_propagation
    def test_31_correlation_id_propagation(self):
        """Verify CorrelationContext tracks doom_request_id and cycle IDs."""
        ctx = CorrelationContext()
        self.assertTrue(ctx.doom_request_id.startswith("req_"))
        c1 = ctx.new_cycle(1)
        self.assertEqual(c1.doom_request_id, ctx.doom_request_id)
        self.assertTrue(c1.cognitive_cycle_id.startswith("cycle_1"))

    # 32. no_duplicate_orchestrator
    def test_32_no_duplicate_orchestrator(self):
        """Verify DOOMCore.process_request routes through self.cognition.process."""
        with patch.object(doom_core.cognition, 'process') as mock_proc:
            mock_proc.return_value = CognitiveState(
                user_request="Hello",
                normalized_goal="Hello",
                decision=CognitiveDecisionType.ANSWER_DIRECTLY,
                final_response="Hi Sujal"
            )
            ans = doom_core.process_request("Hello")
            mock_proc.assert_called_once()
            self.assertEqual(ans, "Hi Sujal")

    # 33. no_direct_tool_bypass
    def test_33_no_direct_tool_bypass(self):
        """Verify execution uses CognitiveBridge to TaskEngine and does not execute tools directly in engine."""
        from core.cognition.decision import cognitive_decision_engine
        with patch.object(cognitive_decision_engine, 'decide', return_value=(CognitiveDecisionType.CREATE_PLAN, "Plan required")):
            with patch('core.cognition.bridge.cognitive_bridge.execute_plan') as mock_bridge:
                mock_bridge.return_value = CognitiveState(
                    user_request="Run tool",
                    normalized_goal="Run tool",
                    decision=CognitiveDecisionType.CREATE_PLAN,
                    final_response="Bridge executed"
                )
                cognitive_engine.process("Run tool")
                mock_bridge.assert_called_once()

    # 34. bounded_task_execution
    def test_34_bounded_task_execution(self):
        """Verify task exceeding MAX_TASK_WALL_TIME (120s) is rejected from retrying."""
        start_time = time.time() - 150.0  # 150 seconds ago
        can_retry, reason = retry_policy.should_retry("task-34", 1, "timeout", start_time)
        self.assertFalse(can_retry)
        self.assertIn("wall-time budget exceeded", reason)

    # 35. bounded_cognitive_execution
    def test_35_bounded_cognitive_execution(self):
        """Verify cognitive iterations stop when cycle count reaches MAX_COGNITIVE_ITERATIONS (5)."""
        bridge = CognitiveBridge()
        step = CognitiveStep(1, "Always replan step", "replan_me", "verifier")
        state = CognitiveState(user_request="Bound test", normalized_goal="Bound test", current_plan=[step])

        with patch.object(plan_validator, 'validate_plan', return_value=(True, [])):
            with patch('core.cognition.bridge.cognitive_replanner.replan', return_value=([step], {"strategy": "retry"}, False)):
                res = bridge.execute_plan(state)
                self.assertLessEqual(res.telemetry.cognitive_cycles, MAX_COGNITIVE_ITERATIONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
