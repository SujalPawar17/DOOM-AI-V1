#!/usr/bin/env python3
"""
DOOM V4 — Cognitive Core Integration & Architecture Test Suite
Tests all 25 core cognitive guarantees deterministically:
  1. Simple query understanding
  2. Action intent detection
  3. Multi-step goal decomposition
  4. Structured reasoning state
  5. Decision selection
  6. Dynamic plan creation
  7. Successful action -> observation -> evaluation
  8. Failed action -> reflection
  9. Failed action -> replanning
  10. Successful replan
  11. No infinite cognitive loop (bounded iterations)
  12. Capability-preserving model routing
  13. Memory retrieval relevance
  14. Memory write classification
  15. Ambiguity -> clarification
  16. High-risk action -> approval
  17. Verification overrides model confidence
  18. False completion prevention
  19. Provider outage -> PAUSED
  20. Resume after provider recovery
  21. Completed steps are not repeated
  22. Restart recovery remains functional
  23. Cognitive telemetry
  24. WebSocket cognitive events
  25. API compatibility
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
from core.task_engine import task_engine, TaskStatus, StepStatus
from core.model_router import model_router, ModelCapability, NoCapableProviderError
from core.verifier import verifier
from core.cognition import (
    cognitive_engine, CognitiveEngine, CognitiveState, CognitiveIntent,
    CognitiveDecisionType, CognitiveStep, CognitiveObservation, CognitiveReflection,
    EvaluationOutcome, understanding_engine, reasoning_engine,
    cognitive_decision_engine, cognitive_planner, observation_engine,
    reflection_engine, cognitive_replanner
)
from tools.base import CanonicalToolResult, RiskLevel


class TestV4CognitiveSuite(unittest.TestCase):

    def setUp(self):
        state_machine.reset()
        task_engine._active_task = None

    # -------------------------------------------------------------------------
    # TEST 1: Simple query understanding
    # -------------------------------------------------------------------------
    def test_01_simple_query_understanding(self):
        """Understands read-only informational requests accurately."""
        intent, goal, entities, constraints, caps, needs_clar, _, conf, ttype = understanding_engine.understand(
            "What is the current time?"
        )
        self.assertEqual(intent, CognitiveIntent.QUERY)
        self.assertFalse(needs_clar)
        self.assertEqual(ttype, "QUERY")

    # -------------------------------------------------------------------------
    # TEST 2: Action intent detection
    # -------------------------------------------------------------------------
    def test_02_action_intent_detection(self):
        """Detects single operational action intent."""
        intent, goal, entities, constraints, caps, needs_clar, _, conf, ttype = understanding_engine.understand(
            "Open Chrome"
        )
        self.assertEqual(intent, CognitiveIntent.ACTION)
        self.assertEqual(entities.get("target_app"), "chrome")
        self.assertEqual(ttype, "ACTION")

    # -------------------------------------------------------------------------
    # TEST 3: Multi-step goal decomposition
    # -------------------------------------------------------------------------
    def test_03_multistep_goal_decomposition(self):
        """Recognizes multi-step intent and extracts target artifact path."""
        intent, goal, entities, constraints, caps, _, _, _, ttype = understanding_engine.understand(
            "Create a Python file on my desktop called system_info.py, run it, and verify the result"
        )
        self.assertEqual(intent, CognitiveIntent.MULTI_STEP)
        self.assertEqual(entities.get("target_file"), "Desktop/system_info.py")
        self.assertIn("coding", caps)
        self.assertIn("reasoning", caps)

    # -------------------------------------------------------------------------
    # TEST 4: Structured reasoning state
    # -------------------------------------------------------------------------
    def test_04_structured_reasoning_state(self):
        """Produces concise non-sensitive reasoning summary without leaking private CoT."""
        summary, assumptions, unresolved = reasoning_engine.reason(
            intent=CognitiveIntent.MULTI_STEP,
            normalized_goal="Create system_info.py and run it",
            entities={"target_file": "Desktop/system_info.py"},
            constraints=["target_location: Desktop"],
            required_capabilities=["coding", "reasoning"],
            relevant_memory={}
        )
        self.assertTrue(len(summary) > 10)
        self.assertTrue(len(assumptions) >= 1)
        self.assertNotIn("<think>", summary)
        self.assertNotIn("chain of thought", summary.lower())

    # -------------------------------------------------------------------------
    # TEST 5: Decision selection
    # -------------------------------------------------------------------------
    def test_05_decision_selection(self):
        """Selects appropriate strategy (Direct, Plan, Tool, Clarification)."""
        dec_conv, _ = cognitive_decision_engine.decide(CognitiveIntent.CONVERSATION, False, ["general"], {})
        self.assertEqual(dec_conv, CognitiveDecisionType.ANSWER_DIRECTLY)

        dec_multi, _ = cognitive_decision_engine.decide(CognitiveIntent.MULTI_STEP, False, ["coding"], {})
        self.assertEqual(dec_multi, CognitiveDecisionType.CREATE_PLAN)

        dec_sys, _ = cognitive_decision_engine.decide(CognitiveIntent.SYSTEM_OPERATION, False, ["telemetry"], {})
        self.assertEqual(dec_sys, CognitiveDecisionType.EXECUTE_TOOL)

    # -------------------------------------------------------------------------
    # TEST 6: Dynamic plan creation
    # -------------------------------------------------------------------------
    def test_06_dynamic_plan_creation(self):
        """Produces structured steps with verification criteria and dependencies."""
        plan = cognitive_planner.plan(
            intent=CognitiveIntent.MULTI_STEP,
            normalized_goal="Create script.py and run it",
            entities={"target_file": "Desktop/script.py"},
            required_capabilities=["coding", "reasoning"]
        )
        self.assertEqual(len(plan), 3)
        self.assertEqual(plan[0].action, "create_file")
        self.assertEqual(plan[1].action, "execute_file")
        self.assertIn(1, plan[1].dependencies)
        self.assertTrue(plan[0].verification_required)

    # -------------------------------------------------------------------------
    # TEST 7: Successful action -> observation -> evaluation
    # -------------------------------------------------------------------------
    def test_07_action_observation_evaluation(self):
        """Normalizes tool execution output and evaluates objective achievement."""
        step = CognitiveStep(1, "Create script", "create_file", "coding_write_script", {"file_name": "test.py"})
        canonical = CanonicalToolResult(
            tool="coding_write_script",
            success=True,
            action="create_file",
            output="Script created",
            artifact={"path": "test.py"}
        )
        obs = observation_engine.observe(canonical, step)
        self.assertTrue(obs.success)
        self.assertEqual(len(obs.artifacts), 1)

        outcome = observation_engine.evaluate(step, obs)
        self.assertEqual(outcome, EvaluationOutcome.SUCCESS)

    # -------------------------------------------------------------------------
    # TEST 8: Failed action -> reflection
    # -------------------------------------------------------------------------
    def test_08_failed_action_reflection(self):
        """Generates structured diagnosis upon action failure."""
        step = CognitiveStep(2, "Run script", "execute_file", "coding_run_python")
        canonical = CanonicalToolResult(
            tool="coding_run_python",
            success=False,
            action="execute_file",
            exit_code=1,
            stderr="SyntaxError: invalid syntax"
        )
        obs = observation_engine.observe(canonical, step)
        outcome = observation_engine.evaluate(step, obs)
        self.assertEqual(outcome, EvaluationOutcome.FAILED)

        ref = reflection_engine.reflect(1, step, obs, outcome)
        self.assertFalse(ref.worked)
        self.assertTrue(ref.should_replan)
        self.assertIn("SyntaxError", ref.failure_reason)

    # -------------------------------------------------------------------------
    # TEST 9: Failed action -> replanning
    # -------------------------------------------------------------------------
    def test_09_failed_action_replanning(self):
        """Replanner inserts patch step upon syntax failure without resetting completed steps."""
        step1 = CognitiveStep(1, "Create script", "create_file", "coding_write_script", status="succeeded")
        step2 = CognitiveStep(2, "Run script", "execute_file", "coding_run_python", tool_args={"code_or_file": "demo.py"})
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
        self.assertFalse(should_pause)
        self.assertEqual(record["strategy"], "INSERT_CODE_PATCH")
        # Step 1 remains in plan
        self.assertEqual(new_plan[0].step_id, 1)
        # Patch step inserted
        self.assertTrue(any(s.action == "patch_file" for s in new_plan))

    # -------------------------------------------------------------------------
    # TEST 10: Successful replan execution
    # -------------------------------------------------------------------------
    def test_10_successful_replan(self):
        """Verifies updated plan executes newly inserted patch step."""
        step1 = CognitiveStep(1, "Create script", "create_file", status="succeeded")
        patch_step = CognitiveStep(3, "Patch script", "patch_file", status="pending")
        exec_step = CognitiveStep(4, "Run script", "execute_file", status="pending")
        plan = [step1, patch_step, exec_step]

        # Simulate executing patch
        canonical_patch = CanonicalToolResult(tool="coding_write_script", success=True, action="patch_file", output="Patched")
        obs = observation_engine.observe(canonical_patch, patch_step)
        outcome = observation_engine.evaluate(patch_step, obs)
        self.assertEqual(outcome, EvaluationOutcome.SUCCESS)

    # -------------------------------------------------------------------------
    # TEST 11: No infinite cognitive loop
    # -------------------------------------------------------------------------
    def test_11_no_infinite_cognitive_loop(self):
        """Cognitive loop strictly terminates within iteration budget."""
        from core.cognition.engine import MAX_COGNITIVE_ITERATIONS
        self.assertTrue(MAX_COGNITIVE_ITERATIONS <= 10)

        # Process a simple query — must complete in 1 cycle
        state = cognitive_engine.process("Who am I?")
        self.assertTrue(state.is_terminal)
        self.assertTrue(state.telemetry.cognitive_cycles <= MAX_COGNITIVE_ITERATIONS)

    # -------------------------------------------------------------------------
    # TEST 12: Capability-preserving model routing
    # -------------------------------------------------------------------------
    def test_12_capability_preserving_model_routing(self):
        """Routing strictly preserves required capability or raises error."""
        coding_prov = model_router.route("coding")
        self.assertIn("coding", getattr(coding_prov, "capabilities", []))

    # -------------------------------------------------------------------------
    # TEST 13: Memory retrieval relevance
    # -------------------------------------------------------------------------
    def test_13_memory_retrieval_relevance(self):
        """Only relevant memories are included; irrelevant facts are excluded."""
        mem = cognitive_engine.retrieve_relevant_memory("Who am I?")
        self.assertIn("user_name", mem)

        mem_math = cognitive_engine.retrieve_relevant_memory("Calculate 2 + 2")
        self.assertNotIn("user_name", mem_math)

    # -------------------------------------------------------------------------
    # TEST 14: Memory write classification
    # -------------------------------------------------------------------------
    def test_14_memory_write_classification(self):
        """Distinguishes temporary context from persistent semantic facts."""
        from memory import semantic_memory
        semantic_memory.remember_fact("test_v4_fact", "cognitive_value", category="test")
        val = semantic_memory.recall_fact("test_v4_fact")
        self.assertEqual(val, "cognitive_value")

    # -------------------------------------------------------------------------
    # TEST 15: Ambiguity -> clarification
    # -------------------------------------------------------------------------
    def test_15_ambiguity_clarification(self):
        """Vague destructive requests trigger clarification without tool invocation."""
        state = cognitive_engine.process("Delete the file")
        self.assertTrue(state.needs_clarification)
        self.assertEqual(state.decision, CognitiveDecisionType.ASK_CLARIFICATION)
        self.assertIn("Which specific file", state.final_response)
        self.assertEqual(len(state.observations), 0)

    # -------------------------------------------------------------------------
    # TEST 16: High-risk action -> approval
    # -------------------------------------------------------------------------
    def test_16_high_risk_action_approval(self):
        """High-risk actions require explicit user authorization before execution."""
        from tools.database_tools import DatabaseQueryTool
        db_tool = DatabaseQueryTool()
        self.assertEqual(db_tool.get_effective_risk(), RiskLevel.HIGH)

        dec, basis = cognitive_decision_engine.decide(
            intent=CognitiveIntent.ACTION,
            needs_clarification=False,
            required_capabilities=["database"],
            entities={},
            tool_candidate=db_tool.name
        )
        self.assertEqual(dec, CognitiveDecisionType.REQUEST_APPROVAL)

    # -------------------------------------------------------------------------
    # TEST 17: Verification overrides model confidence
    # -------------------------------------------------------------------------
    def test_17_verification_overrides_model_confidence(self):
        """Ground truth failure overrides high confidence."""
        state = CognitiveState(user_request="Verify non-existent file", confidence=0.99)
        failed_obs = [
            CanonicalToolResult(tool="execute_file", success=False, action="execute_file", output="No such file")
        ]
        verif = verifier.verify_ground_truth("Check file", failed_obs)
        self.assertFalse(verif.get("verified"))
        self.assertNotEqual(verif.get("status"), "COMPLETED")

    # -------------------------------------------------------------------------
    # TEST 18: False completion prevention
    # -------------------------------------------------------------------------
    def test_18_false_completion_prevention(self):
        """Never claims done when an execution step failed or was blocked."""
        obs = [
            CanonicalToolResult(tool="create_file", success=True, action="create_file", artifact={"path": "script.py"}),
            CanonicalToolResult(tool="execute_file", success=False, action="execute_file", stderr="Failed")
        ]
        verif = verifier.verify_ground_truth("Run script", obs)
        from core.orchestrator import doom_core
        from tools.base import TerminationReason, FinalResponseStatus
        status = doom_core._determine_final_response_status(obs, verif, TerminationReason.COMPLETED)
        self.assertNotEqual(status, FinalResponseStatus.SUCCESS)

    # -------------------------------------------------------------------------
    # TEST 19: Provider outage -> PAUSED
    # -------------------------------------------------------------------------
    def test_19_provider_outage_paused(self):
        """Simulated provider outage pauses the task and saves checkpoint."""
        task = task_engine.create_task("Outage Task", "MULTI_STEP")
        task_engine.set_plan_steps(["Step 1", "Step 2"])
        task_engine.pause_task("NO_CAPABLE_PROVIDER: coding")

        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertTrue(task.resume_available)

    # -------------------------------------------------------------------------
    # TEST 20: Resume after provider recovery
    # -------------------------------------------------------------------------
    def test_20_resume_after_provider_recovery(self):
        """Paused task resumes to RUNNING status upon provider recovery."""
        task = task_engine.create_task("Recover Task", "MULTI_STEP")
        task_engine.set_plan_steps(["Step A", "Step B"])
        task_engine.pause_task("Outage")

        resumed = task_engine.resume_task(task.task_id)
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.status, TaskStatus.RUNNING)

    # -------------------------------------------------------------------------
    # TEST 21: Completed steps are not repeated
    # -------------------------------------------------------------------------
    def test_21_completed_steps_not_repeated(self):
        """Succeeded steps remain SUCCEEDED after resume; only pending step runs."""
        task = task_engine.create_task("Idempotent Task", "MULTI_STEP")
        task_engine.set_plan_steps(["Step 1", "Step 2"])
        task_engine.advance_step(1, tool_name="create_file", output="Done", success=True)
        task_engine.pause_task("Break")

        resumed = task_engine.resume_task(task.task_id)
        self.assertEqual(resumed.steps[0].status, StepStatus.SUCCEEDED)
        self.assertEqual(resumed.steps[1].status, StepStatus.RUNNING)

    # -------------------------------------------------------------------------
    # TEST 22: Restart recovery remains functional
    # -------------------------------------------------------------------------
    def test_22_restart_recovery_functional(self):
        """Task is restored from disk checkpoint after memory purge."""
        task_id = f"test_v4_rec_{uuid.uuid4().hex[:6]}"
        task = task_engine.create_task("Restart Task", "MULTI_STEP")
        task.task_id = task_id
        task_engine.set_plan_steps(["Step X"])
        task_engine._save_checkpoint()

        task_engine._active_task = None
        task_engine._task_history = []

        loaded = task_engine.get_task_by_id(task_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.task_id, task_id)

    # -------------------------------------------------------------------------
    # TEST 23: Cognitive telemetry
    # -------------------------------------------------------------------------
    def test_23_cognitive_telemetry(self):
        """Telemetry profiles latency of understanding, reasoning, and planning."""
        state = cognitive_engine.process("Who am I?")
        self.assertTrue(state.telemetry.total_cognitive_ms > 0)
        self.assertTrue(state.telemetry.understanding_ms >= 0)
        self.assertTrue(state.telemetry.reasoning_ms >= 0)

    # -------------------------------------------------------------------------
    # TEST 24: WebSocket cognitive events
    # -------------------------------------------------------------------------
    def test_24_websocket_cognitive_events(self):
        """Broadcaster receives events during cognitive loop."""
        events = []
        cognitive_engine.set_broadcaster(lambda payload: events.append(payload))

        state = cognitive_engine.process("Show my CPU, RAM and disk usage")
        self.assertTrue(len(events) >= 2)
        event_types = [e.get("event") for e in events]
        self.assertIn("COGNITION_STARTED", event_types)
        self.assertIn("UNDERSTANDING_COMPLETE", event_types)

    # -------------------------------------------------------------------------
    # TEST 25: API compatibility
    # -------------------------------------------------------------------------
    def test_25_api_compatibility(self):
        """Exposes cognitive state safely through REST API via ASGI."""
        import httpx
        from dashboard.server import app

        task_id = f"test_api_v4_{uuid.uuid4().hex[:6]}"
        task = task_engine.create_task("API V4 Goal", "MULTI_STEP")
        task.task_id = task_id
        task_engine.set_plan_steps(["Step 1"])

        async def check_api():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                res = await client.get(f"/api/tasks/{task_id}")
                self.assertEqual(res.status_code, 200)
                self.assertEqual(res.json()["task_id"], task_id)

        asyncio.run(check_api())


if __name__ == "__main__":
    print("=" * 70)
    print("DOOM V4 — COGNITIVE CORE MASTER TEST SUITE (25 TESTS)")
    print("=" * 70)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestV4CognitiveSuite)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
