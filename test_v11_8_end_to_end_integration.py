#!/usr/bin/env python
"""
DOOM V11.8 — End-to-End Cognitive Integration Tests

Validates the complete integrated cognitive pipeline:
Input -> Context Fusion -> Memory/User Model -> Goal Understanding ->
Reasoning -> Decision -> Planning (when required) -> Cost Guard ->
Execution -> Authorization -> Verification -> Response -> V9 Delivery Metadata ->
Outcome -> Experience -> Proactive Continuity

Scenarios A-H plus failure/recovery cases. Deterministic, local, HARD $0:
- V8 executor adapters are replaced only through the executor's own
  use_test_execution_hooks (no Ollama, no UIA, no browser, no filesystem writes).
- Experience, authorization and goal-registry stores run in their in-memory test modes.
- The real Cost Guard is used unless a scenario explicitly needs a PAID decision.
- No VoiceEngine / audio output may be constructed (guarded), so no audio device opens.
"""

import os
import sys
import unittest
from dataclasses import replace
from unittest.mock import patch

os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "1"

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.v11.cognitive_orchestrator import V11CognitiveOrchestrator, v11_cognitive_orchestrator
from core.v10.planning_integration import PlanningResult
from core.v10.goal_understanding import GoalContinuity
from core.cost_guard.guard import cost_guard
from core.cost_guard.types import (
    CostClass, CostDecision, CostDecisionAction, CostReason, ResourceRequest, ResourceType,
)
from core.voice.engine import VoiceEngine
from core.voice.outputs import PygameOutput
import orchestration.executor as v8_executor
from orchestration.executor import (
    ExecutionIdentity, use_test_execution_hooks, reset_execution_ledger_for_tests,
)
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.plan_types import GoalPlan, PlanStep
from orchestration.goal.plan_validator import hash_goal_plan
from orchestration.authorization import reset_authorization_store_for_tests, claim_authorization
from orchestration.experience.store import (
    use_test_experience_store, reset_experience_for_tests, list_experiences,
)
from orchestration.experience.types import Outcome
from orchestration.plan import goal_registry
from core.v11 import proactive_behavior
from core.v11.proactive_behavior import (
    ContinuousMonitoringEnhancement, MonitoringEvent, MonitoringEventType, MonitoringPriority,
)

OWNER = "test_owner_v118"
SESSION = "test_sess_v118"
COMPUTER_SESSION = "cs_v118_test"

ALL_CAPABILITIES = (
    "conversation", "system_read", "computer", "filesystem", "memory_read",
    "memory_write", "sequence", "verification", "browser", "world_act",
)


def make_step(step_id, capability, action, params, *, risk="LOW", approval=False,
              verification_required=False, verification_type=""):
    return PlanStep(
        step_id=step_id,
        capability_id=capability,
        action=action,
        parameters=tuple(params),
        dependencies=(),
        verification_required=verification_required,
        verification_type=verification_type,
        risk=risk,
        approval_required=approval,
        retry_count=0,
        timeout_ms=5000,
    )


def make_plan(steps, *, goal_id="ag_v118_goal", plan_risk="LOW", approval=False,
              computer_session_id="", owner_id=OWNER, session_id=SESSION):
    unsigned = GoalPlan(
        plan_id=f"plan_{goal_id}",
        goal_id=goal_id,
        schema_version="v82.1",
        owner_id=owner_id,
        session_id=session_id,
        computer_session_id=computer_session_id,
        steps=tuple(steps),
        plan_risk=plan_risk,
        approval_required=approval,
        provenance="v11_8_test",
        plan_hash="",
    )
    return replace(unsigned, plan_hash=hash_goal_plan(unsigned))


def planning_for(plan):
    return PlanningResult(
        planning_required=True, planning_successful=True, plan=plan, plan_id=plan.plan_id,
    )


class AdapterSpy:
    """Deterministic V8 adapter table; records every adapter invocation."""

    def __init__(self, overrides=None):
        self.calls = []
        self.overrides = dict(overrides or {})

    def _make(self, capability):
        def adapter(step, plan):
            # Record the adapter actually invoked (the verification adapter receives
            # the step being verified, so step.capability_id alone is ambiguous).
            self.calls.append((capability, step.action))
            override = self.overrides.get(capability)
            if callable(override):
                return override(step, plan)
            if override is not None:
                return override
            if capability == "conversation":
                v8_executor._TLS.response_text = "Deterministic local response."
            if capability == "verification":
                return "VERIFIED"
            return ExecutionStatus.SUCCESS.value
        return adapter

    def hooks(self):
        return use_test_execution_hooks(
            adapters={cap: self._make(cap) for cap in ALL_CAPABILITIES},
            emergency_stop_fn=lambda owner, session: False,
            cancelled_fn=lambda: False,
        )


def _no_audio(*_args, **_kwargs):
    raise AssertionError("V11.8 cycle must not construct a VoiceEngine or audio output")


class V118TestBase(unittest.TestCase):

    def setUp(self):
        use_test_experience_store(True)
        reset_experience_for_tests()
        reset_authorization_store_for_tests()
        reset_execution_ledger_for_tests()
        self.orchestrator = V11CognitiveOrchestrator()
        self.spy = AdapterSpy()
        # Hard guarantee: no V9 voice engine or audio output is created during cycles.
        self._audio_guards = [
            patch.object(VoiceEngine, "__init__", _no_audio),
            patch.object(PygameOutput, "__init__", _no_audio),
        ]
        for guard in self._audio_guards:
            guard.start()

    def tearDown(self):
        for guard in self._audio_guards:
            guard.stop()
        reset_experience_for_tests()
        reset_authorization_store_for_tests()
        reset_execution_ledger_for_tests()

    def run_cycle(self, user_input, *, plan=None, planning=None, **kwargs):
        kwargs.setdefault("owner_id", OWNER)
        kwargs.setdefault("session_id", SESSION)
        with self.spy.hooks():
            if planning is None and plan is not None:
                planning = planning_for(plan)
            if planning is not None:
                with patch.object(self.orchestrator, "planning_integration", return_value=planning):
                    return self.orchestrator.process_cognitive_cycle(user_input=user_input, **kwargs)
            return self.orchestrator.process_cognitive_cycle(user_input=user_input, **kwargs)

    def experiences(self, owner=OWNER):
        return list(list_experiences(owner).experiences)

    def assert_no_execution_claim(self, response_text):
        lowered = response_text.lower()
        self.assertNotIn("successfully executed", lowered)
        self.assertNotIn("execution id", lowered)

    def assert_voice_metadata(self, result):
        voice = result["stages"]["voice_output"]
        self.assertTrue(voice["completed"])
        self.assertFalse(voice["audio_played"])
        for key in ("delivery_category", "speaking_mode", "prosody_mode", "length_category", "prosody"):
            self.assertIn(key, voice)
        for key in ("rate", "pitch", "volume", "pause_before", "pause_after", "emphasis"):
            self.assertIn(key, voice["prosody"])


# =============================================================================
# SCENARIO A — Conversational request (real V10 pipeline, no plan)
# =============================================================================
class TestScenarioAConversational(V118TestBase):

    def test_conversational_request_is_truthful_and_successful(self):
        result = self.run_cycle("What is the capital of France?")
        stages = result["stages"]

        self.assertNotIn("error", result, result.get("error"))
        self.assertTrue(result["success"], result.get("failure_reason"))
        self.assertFalse(stages["planning"]["planning_required"])
        self.assertFalse(stages["cost_guard_check"]["checked"])

        verification = stages["verification"]["verification_result"]
        self.assertTrue(verification["verified"])
        self.assertEqual(verification["status"], "NON_PLAN_VERIFIED")

        # No fabricated plan / execution
        self.assertTrue(result["response_text"])
        self.assert_no_execution_claim(result["response_text"])
        exec_result = stages["execution"]["execution_result"]
        self.assertFalse(stages["execution"]["executed"])
        self.assertEqual(exec_result.execution_id, "", "No fabricated execution ID for a non-plan turn")
        self.assertEqual(result["provenance"]["execution"]["execution_id"], "")
        self.assertEqual(exec_result.goal_id, "")
        self.assertEqual(exec_result.plan_hash, "")
        self.assertEqual(exec_result.steps, ())
        self.assertEqual(self.spy.calls, [], "No V8 adapter may run for a conversational turn")

        # No fake experience
        self.assertFalse(stages["experience_integration"]["experience_required"])
        self.assertEqual(result["provenance"]["experience_integration"]["status"], "SKIPPED_NON_PLAN")
        self.assertEqual(self.experiences(), [])

        outcome = stages["outcome_memory_update"]
        self.assertEqual(outcome["cycle_outcome"], "NON_PLAN_RESPONSE")
        self.assertEqual(outcome["goal_state_update"], "NOT_APPLICABLE")
        self.assert_voice_metadata(result)

    def test_delivery_metadata_is_deterministic(self):
        first = self.run_cycle("Hello DOOM")["stages"]["voice_output"]
        second = self.run_cycle("Hello DOOM")["stages"]["voice_output"]
        for key in ("delivery_category", "speaking_mode", "prosody_mode", "length_category", "prosody"):
            self.assertEqual(first[key], second[key])


# =============================================================================
# SCENARIO B — Goal-establishing request (real V10 pipeline)
# =============================================================================
class TestScenarioBGoalEstablishment(V118TestBase):

    def test_goal_is_understood_without_unapproved_execution(self):
        result = self.run_cycle("Create a plan to backup project documents")
        stages = result["stages"]
        self.assertNotIn("error", result, result.get("error"))

        goal = stages["goal_understanding"]
        self.assertTrue(goal["understood_goal_available"])
        self.assertEqual(goal["continuity"], GoalContinuity.NEW_GOAL.value)

        planning = stages["planning"]
        if not planning["planning_required"]:
            # V10 decided no executable plan: nothing may be executed or claimed.
            self.assertTrue(result["success"], result.get("failure_reason"))
            self.assert_no_execution_claim(result["response_text"])
            self.assertEqual(self.spy.calls, [])
            self.assertEqual(self.experiences(), [])
        else:
            # A real plan must pass the Cost Guard before anything runs.
            self.assertTrue(stages["cost_guard_check"]["checked"])
            exec_result = stages["execution"]["execution_result"]
            if exec_result.status != ExecutionStatus.SUCCESS:
                self.assertFalse(result["success"])
                self.assert_no_execution_claim(result["response_text"])

    def test_planning_failure_never_claims_execution(self):
        failed = PlanningResult(planning_required=True, planning_successful=False, plan=None,
                                error="no executable plan")
        result = self.run_cycle("Organize my workspace files", planning=failed)

        self.assertFalse(result["success"])
        self.assertFalse(result["stages"]["cost_guard_check"]["checked"])
        self.assertEqual(result["stages"]["verification"]["verification_result"]["status"], "PLANNING_FAILED")
        self.assertIn("nothing was executed", result["response_text"].lower())
        self.assert_no_execution_claim(result["response_text"])
        self.assertEqual(result["stages"]["outcome_memory_update"]["cycle_outcome"], "PLANNING_FAILED")
        self.assertFalse(result["stages"]["execution"]["executed"])
        self.assertEqual(result["stages"]["execution"]["execution_result"].execution_id, "")
        self.assertEqual(self.spy.calls, [])
        self.assertEqual(self.experiences(), [])


# =============================================================================
# SCENARIO C — Safe local plan execution (real Cost Guard)
# =============================================================================
class TestScenarioCSafeExecution(V118TestBase):

    def test_safe_plan_executes_and_records_completed_experience(self):
        plan = make_plan([
            make_step("s1", "conversation", "RESPOND", [("text", "Summarize the report")]),
            make_step("s2", "system_read", "REPORT", [("text", "status")]),
        ], goal_id="ag_v118_c")

        with patch.object(cost_guard, "decision", wraps=cost_guard.decision) as guard_spy:
            result = self.run_cycle("Summarize the local report", plan=plan)
        stages = result["stages"]

        self.assertTrue(result["success"], result.get("failure_reason") or result.get("error"))
        # Real Cost Guard consulted per capability against existing LOCAL_FREE attestations.
        self.assertTrue(stages["cost_guard_check"]["checked"])
        self.assertFalse(stages["cost_guard_check"]["skip_execution"])
        checked = {(c.args[0].resource_type, c.args[0].provider) for c in guard_spy.call_args_list}
        self.assertEqual(checked, {(ResourceType.LLM, "ollama"), (ResourceType.OTHER, "local_filesystem")})

        exec_result = stages["execution"]["execution_result"]
        self.assertEqual(exec_result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(exec_result.plan_hash, plan.plan_hash)
        self.assertTrue(stages["execution"]["executed"])
        self.assertTrue(exec_result.execution_id, "Real executions keep the executor's own ID")
        self.assertEqual(self.spy.calls, [("conversation", "RESPOND"), ("system_read", "REPORT")])

        verification = stages["verification"]["verification_result"]
        self.assertTrue(verification["verified"])
        self.assertEqual(verification["status"], "EXECUTED_NO_STEP_VERIFICATION_REQUIRED")
        self.assertEqual(result["response_text"], "Deterministic local response.")

        self.assertEqual(stages["outcome_memory_update"]["cycle_outcome"], "EXECUTION_SUCCEEDED")
        experiences = self.experiences()
        self.assertEqual(len(experiences), 1)
        self.assertEqual(experiences[0].outcome, Outcome.COMPLETED)
        self.assert_voice_metadata(result)

    def test_step_verification_reports_verified(self):
        plan = make_plan([
            make_step("s1", "conversation", "RESPOND", [("text", "Confirm")],
                      verification_required=True, verification_type="TARGET_EXISTS"),
        ], goal_id="ag_v118_c2")
        result = self.run_cycle("Confirm the target", plan=plan)

        self.assertTrue(result["success"], result.get("failure_reason"))
        self.assertEqual(result["stages"]["verification"]["verification_result"]["status"], "VERIFIED")
        self.assertIn(("verification", "RESPOND"), self.spy.calls)

    def test_execution_outcome_recorded_on_matching_active_goal(self):
        with patch.dict(os.environ, {"PROACTIVE_V826_GOAL_REGISTRY_ENABLED": "true"}):
            goal_registry.use_test_goal_registry_store(True)
            try:
                snap, status = goal_registry.build_snapshot(
                    owner_id=OWNER, title="Summarize report", plan_title="Summary plan",
                    step_titles=("Respond",), step_states=(goal_registry.StepState.PENDING,),
                )
                self.assertEqual(status, goal_registry.RegistryStatus.OK)
                self.assertEqual(goal_registry.create_active_goal(OWNER, snap).status,
                                 goal_registry.RegistryStatus.OK)

                # Failure records the executor status as the goal blocker.
                self.spy.overrides["conversation"] = ExecutionStatus.STEP_FAILED.value
                failed_plan = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "Try")])],
                                        goal_id=snap.goal_id)
                failed = self.run_cycle("Finish the summary", plan=failed_plan)
                self.assertFalse(failed["success"])
                self.assertEqual(failed["stages"]["outcome_memory_update"]["goal_state_update"],
                                 "BLOCKER_RECORDED")
                after_fail = goal_registry.get_active_goal(OWNER).snapshot
                self.assertEqual(after_fail.lifecycle, goal_registry.GoalLifecycle.ACTIVE)
                self.assertIn("STEP_FAILED", after_fail.blocker_summary)

                # Success clears the blocker; lifecycle is never auto-completed.
                del self.spy.overrides["conversation"]
                ok_plan = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "Retry")])],
                                    goal_id=snap.goal_id)
                ok = self.run_cycle("Finish the summary", plan=ok_plan)
                self.assertTrue(ok["success"], ok.get("failure_reason"))
                self.assertEqual(ok["stages"]["outcome_memory_update"]["goal_state_update"],
                                 "ACTIVITY_RECORDED")
                after_ok = goal_registry.get_active_goal(OWNER).snapshot
                self.assertEqual(after_ok.lifecycle, goal_registry.GoalLifecycle.ACTIVE)
                self.assertEqual(after_ok.blocker_summary, "")
                self.assertGreater(after_ok.version, after_fail.version)

                # A plan for a different goal never touches the active goal.
                other = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "x")])],
                                  goal_id="ag_unrelated")
                unrelated = self.run_cycle("Something else", plan=other)
                self.assertEqual(unrelated["stages"]["outcome_memory_update"]["goal_state_update"],
                                 "NO_MATCHING_ACTIVE_GOAL")
                self.assertEqual(goal_registry.get_active_goal(OWNER).snapshot.version, after_ok.version)
            finally:
                goal_registry.reset_goal_registry_for_tests()
                goal_registry.use_test_goal_registry_store(False)


# =============================================================================
# SCENARIO D — Cost-blocked request (HARD $0)
# =============================================================================
class TestScenarioDCostBlocked(V118TestBase):

    def _assert_blocked(self, result, plan):
        stages = result["stages"]
        self.assertFalse(result["success"])
        self.assertTrue(stages["cost_guard_check"]["skip_execution"])
        exec_result = stages["execution"]["execution_result"]
        self.assertEqual(exec_result.status, ExecutionStatus.BLOCKED)
        self.assertEqual(exec_result.verification_state, "BLOCKED_BY_COST_GUARD")
        self.assertFalse(stages["execution"]["executed"])
        self.assertEqual(exec_result.execution_id, "", "Cost-blocked cycle must not carry an execution ID")
        self.assertFalse(stages["verification"]["verification_result"]["verified"])
        self.assertIn("blocked by cost guard", result["response_text"].lower())
        self.assertIn("nothing was executed", result["response_text"].lower())
        self.assertEqual(self.spy.calls, [], "Executor adapters must never run when cost-blocked")
        self.assertEqual(stages["outcome_memory_update"]["cycle_outcome"], "COST_BLOCKED")
        self.assertEqual(stages["outcome_memory_update"]["goal_state_update"], "RETAINED")
        self.assertEqual(result["provenance"]["experience_integration"]["status"], "SKIPPED_COST_BLOCKED")
        self.assertEqual(self.experiences(), [])
        self.assert_voice_metadata(result)

    def test_unattested_capability_blocked_by_real_cost_guard(self):
        plan = make_plan([make_step("s1", "world_act", "RUN", [("title", "remote_job")], risk="HIGH")],
                         goal_id="ag_v118_d1", plan_risk="HIGH")
        with patch.object(self.orchestrator.execution_layer, "execute_plan") as exec_spy:
            result = self.run_cycle("Run the remote job", plan=plan)
        exec_spy.assert_not_called()
        self._assert_blocked(result, plan)
        self.assertIn("UNKNOWN_PROVIDER_BLOCKED", result["stages"]["cost_guard_check"]["cost_guard_error"])

    def test_one_unattested_step_blocks_whole_plan(self):
        plan = make_plan([
            make_step("s1", "conversation", "RESPOND", [("text", "hi")]),
            make_step("s2", "browser", "NAVIGATE", [("url", "https://example.com")], risk="MEDIUM"),
        ], goal_id="ag_v118_d2", plan_risk="MEDIUM")
        result = self.run_cycle("Open the site", plan=plan)
        self._assert_blocked(result, plan)

    def test_paid_provider_decision_blocks_and_leaks_nothing(self):
        plan = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "Paid query")])],
                         goal_id="ag_v118_d3")
        paid = CostDecision(
            action=CostDecisionAction.BLOCK,
            cost_class=CostClass.PAID,
            reason=CostReason.PAID_PROVIDER_BLOCKED,
            request=ResourceRequest(resource_type=ResourceType.LLM, provider="openai",
                                    endpoint="https://api.openai.com/v1"),
        )
        with patch.object(self.orchestrator.cost_guard, "decision", return_value=paid):
            result = self.run_cycle("Run paid cloud analysis", plan=plan)
        self._assert_blocked(result, plan)
        self.assertNotIn("api.openai.com", result["response_text"])
        self.assertIn("PAID_PROVIDER_BLOCKED", result["stages"]["cost_guard_check"]["cost_guard_error"])


# =============================================================================
# SCENARIO E — Authorization two-phase flow
# =============================================================================
class TestScenarioEAuthorization(V118TestBase):

    def setUp(self):
        super().setUp()
        self.plan = make_plan(
            [make_step("s1", "computer", "CLICK", [("name", "SubmitButton")], risk="MEDIUM", approval=True)],
            goal_id="ag_v118_e", plan_risk="MEDIUM", approval=True, computer_session_id=COMPUTER_SESSION,
        )
        # Treat the test computer session as live for the owner (no real desktop session).
        self._live = patch(
            "orchestration.authorization.verified_computer_session_id",
            side_effect=lambda owner, cs: (cs, "OK") if (owner == OWNER and cs == COMPUTER_SESSION)
            else (None, ExecutionStatus.SESSION_UNAVAILABLE.value),
        )
        self._live.start()

    def tearDown(self):
        self._live.stop()
        super().tearDown()

    def test_two_phase_approval(self):
        # Phase 1: no authorization. computer_session_id is omitted on purpose: the
        # orchestrator must resolve it from the plan exactly as the executor does.
        phase1 = self.run_cycle("Click the submit button", plan=self.plan)
        auth = phase1["stages"]["authorization"]
        exec1 = phase1["stages"]["execution"]["execution_result"]

        self.assertFalse(phase1["success"])
        self.assertEqual(exec1.status, ExecutionStatus.APPROVAL_REQUIRED)
        self.assertTrue(auth["approval_required"])
        self.assertFalse(auth["approval_granted"])
        self.assertEqual(auth["stash_error"], "OK")
        pending_id = auth["pending_id"]
        self.assertTrue(pending_id)
        self.assertIn(pending_id, phase1["response_text"])
        self.assertIn("authorization", phase1["response_text"].lower())
        self.assertEqual(self.spy.calls, [], "Protected action must not run before approval")
        self.assertEqual(phase1["stages"]["outcome_memory_update"]["cycle_outcome"], "AWAITING_AUTHORIZATION")
        self.assertEqual(phase1["provenance"]["experience_integration"]["status"],
                         "SKIPPED_PENDING_AUTHORIZATION")
        self.assertEqual(self.experiences(), [], "Pending approval is not an outcome")

        # A wrong hash never authorizes.
        wrong = self.run_cycle("Click the submit button", plan=self.plan,
                               authorized_plan_hash="0" * 64, computer_session_id=COMPUTER_SESSION)
        self.assertEqual(wrong["stages"]["execution"]["execution_result"].status,
                         ExecutionStatus.APPROVAL_REQUIRED)
        self.assertEqual(self.spy.calls, [])

        # Claim the pending authorization.
        identity = ExecutionIdentity(owner_id=OWNER, session_id=SESSION, computer_session_id=COMPUTER_SESSION)
        claim, code = claim_authorization(pending_id, identity)
        self.assertEqual(code, "OK")
        self.assertEqual(claim.authorized_plan_hash, self.plan.plan_hash)
        _again, again_code = claim_authorization(pending_id, identity)
        self.assertEqual(again_code, ExecutionStatus.AUTHORIZATION_CONSUMED.value)

        # Phase 2: authorized hash + computer session must reach the V8 executor.
        with patch("core.v11.execution_layer.execute_plan", wraps=v8_executor.execute_plan) as exec_spy:
            phase2 = self.run_cycle("Click the submit button", plan=self.plan,
                                    authorized_plan_hash=claim.authorized_plan_hash,
                                    computer_session_id=COMPUTER_SESSION)
        kwargs = exec_spy.call_args.kwargs
        self.assertEqual(kwargs["authorized_plan_hash"], self.plan.plan_hash)
        self.assertEqual(kwargs["identity"].computer_session_id, COMPUTER_SESSION)

        exec2 = phase2["stages"]["execution"]["execution_result"]
        self.assertTrue(phase2["success"], phase2.get("failure_reason"))
        self.assertEqual(exec2.status, ExecutionStatus.SUCCESS)
        self.assertTrue(exec2.approval_granted)
        self.assertEqual(self.spy.calls, [("computer", "CLICK")])
        self.assertIsNone(phase2["stages"]["authorization"]["pending_id"])
        experiences = self.experiences()
        self.assertEqual(len(experiences), 1)
        self.assertEqual(experiences[0].outcome, Outcome.COMPLETED)

    def test_tampered_plan_is_rejected(self):
        tampered = replace(self.plan, steps=(
            make_step("s1", "computer", "CLICK", [("name", "DeleteAllButton")], risk="MEDIUM", approval=True),
        ))
        result = self.run_cycle("Click", plan=tampered, authorized_plan_hash=self.plan.plan_hash,
                                computer_session_id=COMPUTER_SESSION)
        self.assertFalse(result["success"])
        self.assertEqual(result["stages"]["execution"]["execution_result"].status,
                         ExecutionStatus.PLAN_HASH_MISMATCH)
        self.assertEqual(self.spy.calls, [])


# =============================================================================
# SCENARIO F — Execution failure containment
# =============================================================================
class TestScenarioFFailureContainment(V118TestBase):

    def _plan(self, goal_id):
        return make_plan([make_step("s1", "conversation", "RESPOND", [("text", "Fail step")])],
                         goal_id=goal_id)

    def test_step_failure_is_reported_truthfully(self):
        self.spy.overrides["conversation"] = ExecutionStatus.STEP_FAILED.value
        result = self.run_cycle("Run failing operation", plan=self._plan("ag_v118_f1"))
        stages = result["stages"]

        self.assertFalse(result["success"])
        self.assertEqual(stages["execution"]["execution_result"].status, ExecutionStatus.STEP_FAILED)
        self.assertFalse(stages["verification"]["verification_result"]["verified"])
        self.assertIn("unable to execute", result["response_text"].lower())
        self.assert_no_execution_claim(result["response_text"])
        self.assertEqual(stages["outcome_memory_update"]["cycle_outcome"], "EXECUTION_FAILED")
        # Goal registry is disabled by default configuration: reported truthfully, no mutation.
        self.assertEqual(stages["outcome_memory_update"]["goal_state_update"], "ACTIVE_GOAL_UNAVAILABLE")
        experiences = self.experiences()
        self.assertEqual(len(experiences), 1)
        self.assertEqual(experiences[0].outcome, Outcome.ABANDONED)

    def test_step_verification_failure_is_not_success(self):
        self.spy.overrides["verification"] = ExecutionStatus.VERIFICATION_FAILED.value
        plan = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "x")],
                                    verification_required=True, verification_type="TARGET_EXISTS")],
                         goal_id="ag_v118_f2")
        result = self.run_cycle("Do and verify", plan=plan)
        self.assertFalse(result["success"])
        self.assertEqual(result["stages"]["verification"]["verification_result"]["status"],
                         ExecutionStatus.VERIFICATION_FAILED.value)

    def test_adapter_exception_is_contained(self):
        def boom(step, plan):
            raise RuntimeError("adapter crashed")
        self.spy.overrides["conversation"] = boom
        result = self.run_cycle("Run crashing operation", plan=self._plan("ag_v118_f3"))
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "RuntimeError")

    def test_experience_store_failure_is_contained(self):
        with patch.object(self.orchestrator.experience_integration, "integrate_execution_result",
                          side_effect=RuntimeError("store down")):
            result = self.run_cycle("Summarize", plan=self._plan("ag_v118_f4"))
        self.assertNotIn("error", result)
        self.assertFalse(result["success"])
        self.assertEqual(result["provenance"]["experience_integration"]["status"], "ERROR")
        self.assertEqual(result["stages"]["execution"]["execution_result"].status, ExecutionStatus.SUCCESS)
        self.assertEqual(result["response_text"], "Deterministic local response.")

    def test_owner_mismatch_never_executes(self):
        foreign = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "x")])],
                            goal_id="ag_v118_f5", owner_id="someone_else")
        result = self.run_cycle("Run it", plan=foreign)
        self.assertFalse(result["success"])
        self.assertEqual(result["stages"]["execution"]["execution_result"].status,
                         ExecutionStatus.SESSION_UNAVAILABLE)
        self.assertEqual(self.spy.calls, [])


# =============================================================================
# SCENARIO G — Follow-up continuity
# =============================================================================
class TestScenarioGContinuity(V118TestBase):

    def test_follow_up_sees_prior_experience(self):
        plan = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "Backup done")])],
                         goal_id="ag_v118_g_backup")
        turn1 = self.run_cycle("Back up my project documents", plan=plan)
        self.assertTrue(turn1["success"], turn1.get("failure_reason"))
        self.assertEqual(len(self.experiences()), 1)

        turn2 = self.run_cycle("How did the backup go?")
        self.assertNotIn("error", turn2, turn2.get("error"))
        fused = turn2["stages"]["context_fusion"]["fused_context"]
        self.assertEqual(fused.owner_id, OWNER)
        prior_goal_ids = [v for k, v in fused.context.items()
                          if k.startswith("recent_experience_") and k.endswith("_source_goal_id")]
        self.assertIn("ag_v118_g_backup", prior_goal_ids,
                      "Turn 2 context must include the experience recorded in turn 1")
        self.assertTrue(turn2["success"], turn2.get("failure_reason"))
        self.assert_no_execution_claim(turn2["response_text"])

    def test_other_owner_cannot_see_experience(self):
        plan = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "x")])],
                         goal_id="ag_v118_g_private")
        self.assertTrue(self.run_cycle("Do private task", plan=plan)["success"])
        other = self.run_cycle("How did it go?", owner_id="other_owner_v118")
        fused = other["stages"]["context_fusion"]["fused_context"]
        self.assertNotIn("ag_v118_g_private", [str(v) for v in fused.context.values()])


# =============================================================================
# SCENARIO H — Proactive continuity (real monitor handler)
# =============================================================================
class _SyncThread:
    def __init__(self, target=None, daemon=None, **_kwargs):
        self._target = target

    def start(self):
        self._target()

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


class TestScenarioHProactive(V118TestBase):

    def _event(self, event_id="evt_v118_1"):
        return MonitoringEvent(
            event_id=event_id,
            event_type=MonitoringEventType.SYSTEM_HEALTH,
            priority=MonitoringPriority.HIGH,
            owner_id=OWNER,
            session_id=SESSION,
            timestamp=0.0,
            source="continuous_monitor",
            description="Disk space threshold warning",
        )

    def _run_monitor_event(self, monitor, event, planning=None):
        captured = []
        real = v11_cognitive_orchestrator.process_cognitive_cycle

        def capture(**kwargs):
            out = real(**kwargs)
            captured.append((kwargs, out))
            return out

        with self.spy.hooks(), \
                patch.object(proactive_behavior.threading, "Thread", _SyncThread), \
                patch.object(v11_cognitive_orchestrator, "process_cognitive_cycle", side_effect=capture):
            if planning is not None:
                with patch.object(v11_cognitive_orchestrator, "planning_integration", return_value=planning):
                    monitor._handle_monitoring_event(event)
            else:
                monitor._handle_monitoring_event(event)
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_monitor_event_context_reaches_cycle(self):
        monitor = ContinuousMonitoringEnhancement(owner_id=OWNER, session_id=SESSION)
        kwargs, result = self._run_monitor_event(monitor, self._event())

        self.assertTrue(kwargs["context"]["monitoring_trigger"])
        self.assertNotIn("error", result, result.get("error"))
        fused = result["stages"]["context_fusion"]["fused_context"]
        self.assertIs(fused.context["monitoring_trigger"], True)
        self.assertEqual(fused.context["event_id"], "evt_v118_1")
        self.assertEqual(fused.context["event_type"], "system_health")
        self.assertEqual(fused.provenance["event_id"].source, "caller_context")
        self.assertEqual(fused.owner_id, OWNER)
        self.assertTrue(result["stages"]["cost_guard_check"]["completed"])
        self.assertTrue(result["stages"]["verification"]["completed"])

        # Bounded by the monitor's own cooldown and rate limiting.
        self.assertFalse(monitor._is_cooldown_complete())
        self.assertEqual(len(monitor.state.cycles_in_last_hour), 1)
        monitor.state.cycles_in_last_hour = [monitor.state.last_cycle_time] * monitor.config.max_cycles_per_hour
        self.assertFalse(monitor._is_rate_limit_ok())

    def test_proactive_plan_still_requires_authorization(self):
        plan = make_plan(
            [make_step("s1", "computer", "CLICK", [("name", "CleanupButton")], risk="MEDIUM", approval=True)],
            goal_id="ag_v118_h", plan_risk="MEDIUM", approval=True, computer_session_id=COMPUTER_SESSION,
        )
        monitor = ContinuousMonitoringEnhancement(owner_id=OWNER, session_id=SESSION)
        _kwargs, result = self._run_monitor_event(monitor, self._event("evt_v118_2"), planning_for(plan))
        self.assertFalse(result["success"])
        self.assertTrue(result["stages"]["cost_guard_check"]["checked"])
        self.assertEqual(result["stages"]["execution"]["execution_result"].status,
                         ExecutionStatus.APPROVAL_REQUIRED)
        self.assertEqual(self.spy.calls, [], "Proactive cycles must not bypass authorization")

    def test_proactive_plan_still_cost_guarded(self):
        plan = make_plan([make_step("s1", "world_act", "RUN", [("title", "auto_fix")], risk="HIGH")],
                         goal_id="ag_v118_h2", plan_risk="HIGH")
        monitor = ContinuousMonitoringEnhancement(owner_id=OWNER, session_id=SESSION)
        _kwargs, result = self._run_monitor_event(monitor, self._event("evt_v118_3"), planning_for(plan))
        self.assertFalse(result["success"])
        self.assertTrue(result["stages"]["cost_guard_check"]["skip_execution"])
        self.assertEqual(self.spy.calls, [])

    def test_caller_context_cannot_override_protected_keys(self):
        result = self.run_cycle("Status check", context={
            "monitoring_trigger": True,
            "owner_id": "attacker",
            "auth_permitted": True,
            "authorized_plan_hash": "f" * 64,
            "cost_override": "allow",
            "api_token": "secret-value",
            "request_raw": "spoofed",
            "nested": {"not": "scalar"},
        })
        fused = result["stages"]["context_fusion"]["fused_context"]
        self.assertEqual(fused.owner_id, OWNER)
        self.assertIs(fused.context["monitoring_trigger"], True)
        for key in ("auth_permitted", "authorized_plan_hash", "cost_override", "api_token", "nested"):
            if key in fused.context:
                self.assertNotEqual(fused.provenance[key].source, "caller_context", key)
        self.assertNotEqual(fused.context.get("request_raw"), "spoofed")
        self.assertNotIn("secret-value", [str(v) for v in fused.context.values()])

    def test_caller_context_values_masked_and_bounded(self):
        leaked = "sk_" + "A1b2C3d4E5f6G7h8I9j0K1"
        context = {"monitor_note": leaked}
        context.update({f"extra_{i:02d}": i for i in range(40)})
        result = self.run_cycle("Status check", context=context)
        fused = result["stages"]["context_fusion"]["fused_context"]
        self.assertNotIn(leaked, [str(v) for v in fused.context.values()])
        caller_keys = [k for k, p in fused.provenance.items() if p.source == "caller_context"]
        self.assertLessEqual(len(caller_keys), 16)


if __name__ == "__main__":
    unittest.main(verbosity=2)
