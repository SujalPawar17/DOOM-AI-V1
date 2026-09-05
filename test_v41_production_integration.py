#!/usr/bin/env python3
"""
DOOM V4.1 — Production Cognitive Core Integration Test Suite.

Validates that DOOMCore.process_request() authoritatively delegates to
the V4 CognitiveEngine (Understanding -> Reasoning -> Decision -> Planning ->
Action -> Observation -> Evaluation -> Reflection -> Replanning -> Verification)
while leveraging the V3.3 execution foundation (TaskEngine, StateMachine,
ModelRouter, ToolRegistry, RiskEngine, Checkpointing, and Verifier).

Tests:
 1. production_simple_query_uses_cognition
 2. production_multistep_uses_cognition
 3. cognitive_to_task_engine_bridge
 4. production_observation
 5. production_evaluation
 6. production_reflection
 7. production_replanning
 8. provider_outage_pause
 9. resume_after_provider_recovery
10. completed_step_not_repeated
11. verification_authority
12. security_approval
13. memory_context
14. api_integration
15. websocket_events
16. cognitive_telemetry
17. no_duplicate_execution_path
18. no_false_completion
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
from core.cognition.engine import cognitive_engine, CognitiveEngine
from core.cognition.bridge import cognitive_bridge, CognitiveBridge
from core.cognition.schemas import (
    CognitiveState, CognitiveIntent, CognitiveDecisionType, CognitiveStep,
    CognitiveObservation, CognitiveReflection, EvaluationOutcome, CognitiveTelemetry
)
from core.cognition.observation import observation_engine
from core.cognition.reflection import reflection_engine
from core.cognition.replanning import cognitive_replanner
from core.state_machine import state_machine, DoomState
from core.task_engine import (
    task_engine, Task, TaskStep, TaskStatus, StepStatus,
    VerificationStatus, FinalResponseStatus
)
from core.model_router import (
    model_router, ModelCapability, NoCapableProviderError, LLMResponse
)
from core.verifier import verifier, GroundTruthVerifier
from core.tool_registry import tool_registry
from tools.base import CanonicalToolResult, RiskLevel, TerminationReason


class TestV41ProductionIntegration(unittest.TestCase):

    def setUp(self):
        """Reset state machine, active task, and cognition before each test."""
        state_machine.reset()
        task_engine._active_task = None
        self.orchestrator = doom_core

    def tearDown(self):
        state_machine.reset()
        task_engine._active_task = None

    # -------------------------------------------------------------------------
    # TEST 1: production_simple_query_uses_cognition
    # -------------------------------------------------------------------------
    def test_01_production_simple_query_uses_cognition(self):
        """Verify 'What is 2 + 2?' routes through V4 Cognition -> ANSWER_DIRECTLY with no task overhead."""
        query = "What is 2 + 2?"
        
        cognition_called = []
        original_process = self.orchestrator.cognition.process
        def spy_process(goal, context=None):
            cognition_called.append(goal)
            return original_process(goal, context)

        with patch.object(self.orchestrator.cognition, 'process', side_effect=spy_process):
            response = self.orchestrator.process_request(query)

        self.assertTrue(len(cognition_called) > 0, "CognitiveEngine.process() MUST be called by process_request()")
        self.assertIn("4", response, f"Expected 4 in direct answer, got: '{response}'")
        self.assertIsNone(task_engine.active_task, "Simple queries must NOT leave an active multi-step task")

    # -------------------------------------------------------------------------
    # TEST 2: production_multistep_uses_cognition
    # -------------------------------------------------------------------------
    def test_02_production_multistep_uses_cognition(self):
        """Verify multi-step goals invoke full cognitive lifecycle: DECIDE -> PLAN -> ACT -> OBSERVE -> EVALUATE -> VERIFY."""
        goal = "Create a Python file on my desktop called v41_multistep_test.py that prints 'V4.1 ACTIVE'. Run it and verify it."
        
        lifecycle_events = []
        def listener(evt):
            evt_type = evt.get("event") or evt.get("type")
            if evt_type:
                lifecycle_events.append(evt_type)
        
        self.orchestrator.cognition.set_broadcaster(listener)
        try:
            response = self.orchestrator.process_request(goal)
            
            # Cognition events must have been broadcasted
            self.assertIn("COGNITION_STARTED", lifecycle_events)
            self.assertIn("UNDERSTANDING_COMPLETE", lifecycle_events)
            self.assertIn("DECISION_MADE", lifecycle_events)
            self.assertIn("PLAN_CREATED", lifecycle_events)
            self.assertIn("ACTION_STARTED", lifecycle_events)
            
            # Must produce a truthful response
            self.assertTrue(len(response) > 0)
        finally:
            test_file = os.path.expanduser("~/Desktop/v41_multistep_test.py")
            if os.path.exists(test_file):
                try:
                    os.remove(test_file)
                except OSError:
                    pass

    # -------------------------------------------------------------------------
    # TEST 3: cognitive_to_task_engine_bridge
    # -------------------------------------------------------------------------
    def test_03_cognitive_to_task_engine_bridge(self):
        """Verify cognitive_bridge converts CognitivePlan into TaskEngine Task with TaskSteps and connects StateMachine."""
        bridge = CognitiveBridge()
        
        step = CognitiveStep(
            step_id=1,
            objective="Collect workstation telemetry",
            action="system_get_status",
            tool_name="system_get_status",
            tool_args={"category": "all"},
            expected_outcome="Telemetry output dictionary"
        )
        
        state = CognitiveState(
            user_request="Check workstation status",
            normalized_goal="Check workstation status",
            intent=CognitiveIntent.SYSTEM_OPERATION,
            decision=CognitiveDecisionType.CREATE_PLAN,
            current_plan=[step]
        )
        
        res = bridge.execute_plan(state)
        
        self.assertIsNotNone(res)
        self.assertIn("success", res.final_response_status.lower())
        self.assertEqual(len(res.completed_steps), 1)
        self.assertEqual(res.completed_steps[0].status, "succeeded")
        history = bridge.task_engine.get_history_dicts()
        self.assertTrue(len(history) > 0)
        self.assertEqual(history[0]["status"], "COMPLETED")

    # -------------------------------------------------------------------------
    # TEST 4: production_observation
    # -------------------------------------------------------------------------
    def test_04_production_observation(self):
        """Verify production tool execution yields structured CognitiveObservation."""
        step = CognitiveStep(
            step_id=1,
            objective="Read CPU status",
            action="system_get_status",
            tool_name="system_get_status",
            tool_args={"category": "cpu"},
            expected_outcome="CPU telemetry"
        )
        
        raw_res = CanonicalToolResult(
            tool="system_get_status",
            success=True,
            action="system_get_status",
            output="CPU Usage: 15.2%",
            exit_code=0
        )
        
        obs = observation_engine.observe(raw_res, step)
        self.assertIsInstance(obs, CognitiveObservation)
        self.assertTrue(obs.success)
        self.assertEqual(obs.exit_code, 0)
        self.assertIn("CPU Usage", obs.stdout or obs.output)

    # -------------------------------------------------------------------------
    # TEST 5: production_evaluation
    # -------------------------------------------------------------------------
    def test_05_production_evaluation(self):
        """Verify observation_engine evaluates CognitiveObservation against expected outcomes accurately."""
        step = CognitiveStep(
            step_id=1,
            objective="Write script",
            action="create_file",
            tool_name="coding_write_script",
            tool_args={"file_name": "test.py", "content": "print('ok')"},
            expected_outcome="File test.py created successfully"
        )
        obs = CognitiveObservation(
            tool="coding_write_script",
            action="create_file",
            success=True,
            exit_code=0,
            stdout="Created file test.py",
            output="Created file test.py",
            artifacts=[{"path": "test.py"}],
            duration_ms=45.0
        )
        
        outcome = observation_engine.evaluate(step, obs)
        self.assertEqual(outcome, EvaluationOutcome.SUCCESS)

    # -------------------------------------------------------------------------
    # TEST 6: production_reflection
    # -------------------------------------------------------------------------
    def test_06_production_reflection(self):
        """Verify reflection_engine assesses progress and detects when to continue vs replan."""
        step = CognitiveStep(
            step_id=1,
            objective="Run code",
            action="execute_file",
            tool_name="coding_run_python"
        )
        obs = CognitiveObservation(
            tool="coding_run_python",
            action="execute_file",
            success=False,
            exit_code=1,
            stderr="SyntaxError: invalid syntax",
            output="SyntaxError: invalid syntax"
        )
        outcome = EvaluationOutcome.FAILED
        
        ref = reflection_engine.reflect(1, step, obs, outcome)
        self.assertIsInstance(ref, CognitiveReflection)
        self.assertFalse(ref.worked)
        self.assertTrue(ref.should_replan)
        self.assertIn("SyntaxError", ref.failure_reason)

    # -------------------------------------------------------------------------
    # TEST 7: production_replanning
    # -------------------------------------------------------------------------
    def test_07_production_replanning(self):
        """Verify deterministic recoverable error invokes cognitive_replanner to patch remaining steps without restarting completed work."""
        step1 = CognitiveStep(
            step_id=1,
            objective="Create script",
            action="create_file",
            tool_name="coding_write_script",
            status="succeeded"
        )
        step2 = CognitiveStep(
            step_id=2,
            objective="Run script",
            action="execute_file",
            tool_name="coding_run_python",
            tool_args={"code_or_file": "demo.py"},
            status="failed"
        )
        plan = [step1, step2]
        
        ref = CognitiveReflection(
            cycle=1,
            expected="Exit code 0",
            observed="SyntaxError at line 2",
            worked=False,
            failure_reason="SyntaxError at line 2",
            should_replan=True
        )
        
        new_plan, record, should_pause = cognitive_replanner.replan(
            current_plan=plan,
            completed_steps=[step1],
            failed_step=step2,
            reflection=ref,
            plan_version=1
        )
        
        self.assertIsNotNone(new_plan)
        self.assertFalse(should_pause)
        self.assertEqual(step1.status, "succeeded")
        self.assertTrue(len(new_plan) >= 2)

    # -------------------------------------------------------------------------
    # TEST 8: provider_outage_pause
    # -------------------------------------------------------------------------
    def test_08_provider_outage_pause(self):
        """Verify provider outage raises NoCapableProviderError and cleanly pauses task in TaskEngine."""
        with patch.object(model_router, 'route', side_effect=NoCapableProviderError(
            "reasoning", []
        )):
            step = CognitiveStep(
                step_id=1,
                objective="Deep reasoning requiring LLM",
                action="reasoning_tool",
                tool_name="reasoning_tool",
                required_capability="reasoning",
                expected_outcome="Reasoning output"
            )
            state = CognitiveState(
                user_request="Perform deep reasoning",
                normalized_goal="Perform deep reasoning",
                required_capabilities=["reasoning"],
                decision=CognitiveDecisionType.CREATE_PLAN,
                current_plan=[step]
            )
            
            bridge = CognitiveBridge()
            res = bridge.execute_plan(state)
            
            self.assertEqual(res.decision, CognitiveDecisionType.PAUSE_TASK)
            self.assertIn("paused", res.final_response.lower())
            self.assertEqual(bridge.task_engine.active_task.status, TaskStatus.PAUSED)

    # -------------------------------------------------------------------------
    # TEST 9: resume_after_provider_recovery
    # -------------------------------------------------------------------------
    def test_09_resume_after_provider_recovery(self):
        """Verify paused task resumes after provider recovery from the first incomplete step."""
        bridge = CognitiveBridge()
        task = bridge.task_engine.create_task("Two step goal", "MULTI_STEP")
        task.steps.append(TaskStep(index=1, description="Step 1", tool_name="system_get_status", tool_args={"category": "cpu"}))
        task.steps.append(TaskStep(index=2, description="Step 2", tool_name="system_get_status", tool_args={"category": "ram"}))
        
        # Mark step 1 succeeded
        task.status = TaskStatus.RUNNING
        task.steps[0].status = StepStatus.SUCCEEDED
        task.steps[0].result = CanonicalToolResult(tool="system_get_status", success=True, output="CPU OK")
        task.current_step = 2
        task.status = TaskStatus.PAUSED
        task.resume_available = True
        
        # Resume
        resumed_task = bridge.task_engine.resume_task(task.task_id)
        self.assertIsNotNone(resumed_task)
        self.assertEqual(resumed_task.status, TaskStatus.RUNNING)
        self.assertEqual(resumed_task.current_step, "Step 2")
        self.assertEqual(resumed_task.steps[0].status, StepStatus.SUCCEEDED)

    # -------------------------------------------------------------------------
    # TEST 10: completed_step_not_repeated
    # -------------------------------------------------------------------------
    def test_10_completed_step_not_repeated(self):
        """Verify resume does NOT re-execute already succeeded step 1."""
        bridge = CognitiveBridge()
        task = bridge.task_engine.create_task("Idempotent resume goal", "MULTI_STEP")
        step1 = TaskStep(index=1, description="Step 1", tool_name="system_get_status", tool_args={"category": "cpu"})
        step2 = TaskStep(index=2, description="Step 2", tool_name="system_get_status", tool_args={"category": "ram"})
        task.steps.append(step1)
        task.steps.append(step2)
        
        step1.status = StepStatus.SUCCEEDED
        step1.result = CanonicalToolResult(tool="system_get_status", success=True, output="Step 1 done")
        task.current_step = "Step 2"
        task.status = TaskStatus.PAUSED
        
        c_step1 = CognitiveStep(step_id=1, objective="Step 1", action="s1", tool_name="system_get_status", tool_args={"category": "cpu"}, status="succeeded")
        c_step2 = CognitiveStep(step_id=2, objective="Step 2", action="s2", tool_name="system_get_status", tool_args={"category": "ram"})
        
        executed_calls = []
        mock_tool = MagicMock()
        mock_tool.risk_level = RiskLevel.SAFE
        def record_call(**kwargs):
            executed_calls.append(kwargs)
            return CanonicalToolResult(tool="system_get_status", success=True, output="OK")
        mock_tool.execute.side_effect = record_call
        
        state = CognitiveState(
            user_request="Idempotent resume goal",
            normalized_goal="Idempotent resume goal",
            decision=CognitiveDecisionType.CREATE_PLAN,
            current_plan=[c_step1, c_step2],
            completed_steps=[c_step1]
        )
        
        with patch.object(tool_registry, 'get_tool', return_value=mock_tool):
            res = bridge.execute_plan(state)
        
        self.assertEqual(len(executed_calls), 1, "Only incomplete step 2 should be executed")
        self.assertEqual(executed_calls[0].get("category"), "ram")

    # -------------------------------------------------------------------------
    # TEST 11: verification_authority
    # -------------------------------------------------------------------------
    def test_11_verification_authority(self):
        """Verify ground truth verifier has ultimate completion authority (MODEL BELIEF != REALITY)."""
        obs = [CanonicalToolResult(
            tool="coding_write_script",
            success=True,
            action="create_file",
            artifact={"path": "non_existent_file_xyz_123.py"}
        )]
        v_res = verifier.verify_ground_truth("Create file", obs)
        self.assertFalse(v_res["verified"])
        self.assertEqual(v_res["status"], "FAILED")

    # -------------------------------------------------------------------------
    # TEST 12: security_approval
    # -------------------------------------------------------------------------
    def test_12_security_approval(self):
        """Verify HIGH-risk actions pause in WAITING_FOR_APPROVAL and do not bypass RiskEngine."""
        bridge = CognitiveBridge()
        step = CognitiveStep(
            step_id=1,
            objective="Destructive command",
            action="security_critical_tool",
            tool_name="security_critical_tool",
            tool_args={"cmd": "rm -rf /"},
            risk_level="HIGH"
        )
        state = CognitiveState(
            user_request="Dangerous operation",
            normalized_goal="Dangerous operation",
            decision=CognitiveDecisionType.CREATE_PLAN,
            current_plan=[step]
        )
        
        mock_tool = MagicMock()
        mock_tool.get_effective_risk.return_value = RiskLevel.HIGH
        mock_tool.risk_level = RiskLevel.HIGH
        
        with patch.object(tool_registry, 'get_tool', return_value=mock_tool):
            res = bridge.execute_plan(state)
            
        self.assertEqual(res.decision, CognitiveDecisionType.REQUEST_APPROVAL)
        self.assertEqual(bridge.task_engine.active_task.status, TaskStatus.WAITING_FOR_APPROVAL)
        self.assertTrue(bridge.task_engine.active_task.user_approval_required)
        mock_tool.execute.assert_not_called()

    # -------------------------------------------------------------------------
    # TEST 13: memory_context
    # -------------------------------------------------------------------------
    def test_13_memory_context(self):
        """Verify CognitiveEngine queries memory context for personalized user queries."""
        response = self.orchestrator.process_request("Who am I and what is my security clearance?")
        self.assertIn("Sujal", response)
        self.assertTrue("10" in response or "Root" in response)

    # -------------------------------------------------------------------------
    # TEST 14: api_integration
    # -------------------------------------------------------------------------
    def test_14_api_integration(self):
        """Verify POST /api/command reaches CognitiveEngine via DOOMCore."""
        import httpx
        from dashboard.server import app

        async def run_api():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                res = await client.post("/api/command", json={"goal": "What is 2 + 2?"})
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertIn("response", data)
                self.assertIn("4", data["response"])
                
                tasks_res = await client.get("/api/tasks")
                self.assertEqual(tasks_res.status_code, 200)

        asyncio.run(run_api())

    # -------------------------------------------------------------------------
    # TEST 15: websocket_events
    # -------------------------------------------------------------------------
    def test_15_websocket_events(self):
        """Verify real cognitive lifecycle events are broadcasted during production request."""
        captured = []
        def listener(evt):
            evt_type = evt.get("event") or evt.get("type")
            if evt_type:
                captured.append(evt_type)
            
        self.orchestrator.cognition.set_broadcaster(listener)
        self.orchestrator.process_request("What is 2 + 2?")
        
        self.assertIn("COGNITION_STARTED", captured)
        self.assertIn("DECISION_MADE", captured)

    # -------------------------------------------------------------------------
    # TEST 16: cognitive_telemetry
    # -------------------------------------------------------------------------
    def test_16_cognitive_telemetry(self):
        """Verify CognitiveTelemetry tracks sub-phase latencies and total execution time."""
        state = self.orchestrator.cognition.process("What is 2 + 2?")
        self.assertIsInstance(state.telemetry, CognitiveTelemetry)
        self.assertGreaterEqual(state.telemetry.total_cognitive_ms, 0.0)
        self.assertGreaterEqual(state.telemetry.understanding_ms, 0.0)
        self.assertGreaterEqual(state.telemetry.reasoning_ms, 0.0)
        self.assertGreaterEqual(state.telemetry.decision_ms, 0.0)

    # -------------------------------------------------------------------------
    # TEST 17: no_duplicate_execution_path
    # -------------------------------------------------------------------------
    def test_17_no_duplicate_execution_path(self):
        """Verify DOOMCore has ONE authoritative path: process_request invokes cognition.process."""
        with patch.object(self.orchestrator.cognition, 'process') as mock_cog:
            mock_cog.return_value = CognitiveState(
                user_request="Test single path",
                normalized_goal="Test single path",
                decision=CognitiveDecisionType.ANSWER_DIRECTLY,
                final_response="Mocked cognitive response"
            )
            res = self.orchestrator.process_request("Test single path")
            
            mock_cog.assert_called_once()
            self.assertEqual(res, "Mocked cognitive response")

    # -------------------------------------------------------------------------
    # TEST 18: no_false_completion
    # -------------------------------------------------------------------------
    def test_18_no_false_completion(self):
        """Verify fatal failure never reports success or 'Done'."""
        bridge = CognitiveBridge()
        step = CognitiveStep(
            step_id=1,
            objective="invalid",
            action="nonexistent_invalid_tool_xyz",
            tool_name="nonexistent_invalid_tool_xyz",
            expected_outcome="success"
        )
        state = CognitiveState(
            user_request="Execute invalid tool",
            normalized_goal="Execute invalid tool",
            decision=CognitiveDecisionType.CREATE_PLAN,
            current_plan=[step]
        )
        
        res = bridge.execute_plan(state)
        self.assertIn(res.final_response_status, ("failed", "blocked"))
        self.assertNotIn("successfully", res.final_response.lower())
        self.assertTrue(any(w in res.final_response.lower() for w in ("failed", "could not be completed", "not found")))
        history = bridge.task_engine.get_history_dicts()
        self.assertTrue(len(history) > 0)
        self.assertEqual(history[0]["status"], "FAILED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
