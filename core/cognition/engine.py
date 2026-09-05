"""
DOOM V4 — Cognitive Core Master Engine
Orchestrates the 9-stage cognitive loop:
UNDERSTAND -> REASON -> DECIDE -> PLAN -> ACT -> OBSERVE -> EVALUATE -> REFLECT -> REPLAN
Integrates with V3.3 TaskEngine, StateMachine, ModelRouter, and GroundTruthVerifier.
"""

import time
from typing import Dict, Any, List, Optional, Callable
from core.cognition.schemas import (
    CognitiveState, CognitiveIntent, CognitiveDecisionType, CognitiveStep,
    CognitiveObservation, CognitiveReflection, EvaluationOutcome, CognitiveTelemetry
)
from core.cognition.understanding import understanding_engine
from core.cognition.reasoning import reasoning_engine
from core.cognition.decision import cognitive_decision_engine
from core.cognition.planner import cognitive_planner
from core.cognition.observation import observation_engine
from core.cognition.reflection import reflection_engine
from core.cognition.replanning import cognitive_replanner
from core.state_machine import state_machine, DoomState
from core.task_engine import task_engine, TaskStatus, StepStatus
from core.verifier import verifier
from core.model_router import model_router, NoCapableProviderError
from tools.base import CanonicalToolResult, RiskLevel


MAX_COGNITIVE_ITERATIONS = 5


class CognitiveEngine:
    """
    Master Cognitive Engine for DOOM V4:
    Executes the goal-oriented cognitive lifecycle with strict ground-truth verification and bounded iterations.
    """

    def __init__(self):
        self._broadcaster: Optional[Callable[[Dict[str, Any]], None]] = None

    def set_broadcaster(self, broadcaster: Callable[[Dict[str, Any]], None]) -> None:
        self._broadcaster = broadcaster

    def _broadcast(self, event_type: str, **payload) -> None:
        if self._broadcaster:
            try:
                self._broadcaster({"type": "cognitive_event", "event": event_type, **payload})
            except Exception:
                pass

    def retrieve_relevant_memory(self, goal: str) -> Dict[str, Any]:
        """Retrieves only relevant memory facts for the given goal to prevent context pollution."""
        from memory import semantic_memory, user_profile
        relevant = {}
        lower = goal.lower()

        # Check for profile facts
        name = user_profile.get_name()
        if "who" in lower or "name" in lower or "me" in lower or "profile" in lower:
            relevant["user_name"] = name
            relevant["user_role"] = user_profile.get_role()

        # Check semantic facts
        for key in ["assistant", "voice", "operating_system", "capabilities"]:
            val = semantic_memory.recall_fact(key)
            if val and (key in lower or "system" in lower or "doom" in lower or "status" in lower):
                relevant[key] = val

        return relevant

    def process(self, user_request: str, context: Optional[Dict[str, Any]] = None) -> CognitiveState:
        """
        Public Master Entry Point for the V4 Cognitive Lifecycle.
        """
        t0 = time.time()
        state = CognitiveState(user_request=user_request)
        self._broadcast("COGNITION_STARTED", request=user_request)

        # ---------------------------------------------------------------------
        # 1. MEMORY & CONTEXT RETRIEVAL
        # ---------------------------------------------------------------------
        state.relevant_memory = self.retrieve_relevant_memory(user_request)

        # ---------------------------------------------------------------------
        # 2. UNDERSTAND
        # ---------------------------------------------------------------------
        t_und = time.time()
        (
            state.intent,
            state.normalized_goal,
            state.entities,
            state.constraints,
            state.required_capabilities,
            state.needs_clarification,
            state.clarification_prompt,
            state.confidence,
            state.task_type
        ) = understanding_engine.understand(user_request, context)
        state.telemetry.understanding_ms = (time.time() - t_und) * 1000.0
        self._broadcast("UNDERSTANDING_COMPLETE", intent=state.intent.value, goal=state.normalized_goal)

        # Early return if clarification is required
        if state.needs_clarification:
            state.decision = CognitiveDecisionType.ASK_CLARIFICATION
            state.final_response = state.clarification_prompt or "Could you clarify what you want me to do, Boss?"
            state.final_response_status = "blocked"
            state.is_terminal = True
            state.termination_reason = "CLARIFICATION_REQUIRED"
            state.telemetry.total_cognitive_ms = (time.time() - t0) * 1000.0
            return state

        # ---------------------------------------------------------------------
        # 3. REASON
        # ---------------------------------------------------------------------
        t_reas = time.time()
        (
            state.reasoning_summary,
            state.assumptions,
            unresolved
        ) = reasoning_engine.reason(
            state.intent,
            state.normalized_goal,
            state.entities,
            state.constraints,
            state.required_capabilities,
            state.relevant_memory
        )
        state.telemetry.reasoning_ms = (time.time() - t_reas) * 1000.0

        # ---------------------------------------------------------------------
        # 4. DECIDE
        # ---------------------------------------------------------------------
        t_dec = time.time()
        (
            state.decision,
            state.decision_basis
        ) = cognitive_decision_engine.decide(
            state.intent,
            state.needs_clarification,
            state.required_capabilities,
            state.entities
        )
        state.telemetry.decision_ms = (time.time() - t_dec) * 1000.0
        self._broadcast("DECISION_MADE", decision=state.decision.value, basis=state.decision_basis)

        # ---------------------------------------------------------------------
        # 5. PLAN
        # ---------------------------------------------------------------------
        t_plan = time.time()
        state.current_plan = cognitive_planner.plan(
            state.intent,
            state.normalized_goal,
            state.entities,
            state.required_capabilities
        )
        state.telemetry.planning_ms = (time.time() - t_plan) * 1000.0
        self._broadcast("PLAN_CREATED", step_count=len(state.current_plan))

        # Check if direct conversational response is needed
        if state.decision == CognitiveDecisionType.ANSWER_DIRECTLY and state.intent == CognitiveIntent.CONVERSATION:
            from memory import user_profile
            state.final_response = f"You are {user_profile.get_name()}, Creator, Boss, and Lead AI Engineer. We are DOOM, your Personal AI Operating System."
            state.final_response_status = "success"
            state.is_terminal = True
            elapsed = (time.time() - t0) * 1000.0
            state.telemetry.total_cognitive_ms = max(elapsed, 0.05)
            return state

        # ---------------------------------------------------------------------
        # 6. COGNITIVE EXECUTION LOOP (ACT -> OBSERVE -> EVALUATE -> REFLECT -> REPLAN)
        # ---------------------------------------------------------------------
        cognitive_cycle = 0
        from core.tool_registry import tool_registry

        while cognitive_cycle < MAX_COGNITIVE_ITERATIONS:
            cognitive_cycle += 1
            state.telemetry.cognitive_cycles = cognitive_cycle

            # Find next incomplete step
            completed_ids = {s.step_id for s in state.completed_steps}
            remaining_steps = [s for s in state.current_plan if s.step_id not in completed_ids]

            if not remaining_steps:
                # All planned steps executed
                break

            current_step = remaining_steps[0]
            state.current_step_id = current_step.step_id
            current_step.status = "running"
            state_machine.transition_to(DoomState.EXECUTING, current_step.objective)

            # Security Gate: verify risk before tool execution
            if current_step.tool_name:
                tool_obj = tool_registry.get_tool(current_step.tool_name)
                if tool_obj and tool_obj.get_effective_risk() in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                    state.decision = CognitiveDecisionType.REQUEST_APPROVAL
                    state.final_response = f"Action '{current_step.tool_name}' requires your explicit authorization, Boss."
                    state.final_response_status = "blocked"
                    state.is_terminal = True
                    state.termination_reason = "USER_APPROVAL_REQUIRED"
                    state_machine.transition_to(DoomState.WAITING_FOR_APPROVAL, f"Authorization required: {current_step.tool_name}")
                    return state

            # ACT: Execute Tool / Action
            t_act = time.time()
            raw_result = None
            if current_step.tool_name == "verifier":
                raw_result = verifier.verify_ground_truth(state.normalized_goal, [o for o in state.observations])
                current_step.status = "succeeded"
                state.completed_steps.append(current_step)
                continue
            elif current_step.tool_name:
                tool_obj = tool_registry.get_tool(current_step.tool_name)
                if tool_obj:
                    try:
                        raw_result = tool_obj.execute(**current_step.tool_args)
                        state.telemetry.tools_executed.append(current_step.tool_name)
                    except Exception as te:
                        raw_result = CanonicalToolResult(tool=current_step.tool_name, success=False, stderr=str(te))
            state.telemetry.execution_ms += (time.time() - t_act) * 1000.0

            # OBSERVE
            t_obs = time.time()
            observation = observation_engine.observe(raw_result, current_step)
            state.observations.append(observation)
            state.telemetry.observation_ms += (time.time() - t_obs) * 1000.0
            self._broadcast("OBSERVATION_RECEIVED", action=observation.action, success=observation.success)

            # EVALUATE
            t_eval = time.time()
            outcome = observation_engine.evaluate(current_step, observation)
            state.evaluation_outcomes.append(outcome)
            state.telemetry.evaluation_ms += (time.time() - t_eval) * 1000.0
            self._broadcast("EVALUATION_COMPLETE", outcome=outcome.value)

            # REFLECT
            t_ref = time.time()
            reflection = reflection_engine.reflect(cognitive_cycle, current_step, observation, outcome)
            state.reflections.append(reflection)
            state.telemetry.reflection_ms += (time.time() - t_ref) * 1000.0
            self._broadcast("REFLECTION_COMPLETE", worked=reflection.worked, next_action=reflection.next_action)

            # Check outcome
            if outcome == EvaluationOutcome.SUCCESS:
                current_step.status = "succeeded"
                current_step.result = observation.stdout or observation.output
                state.completed_steps.append(current_step)
            else:
                # Step failed or blocked -> trigger REPLAN
                current_step.status = "failed" if outcome == EvaluationOutcome.FAILED else "blocked"
                current_step.error = observation.stderr or observation.output

                t_rep = time.time()
                (
                    state.current_plan,
                    replan_record,
                    should_pause
                ) = cognitive_replanner.replan(
                    state.current_plan,
                    state.completed_steps,
                    current_step,
                    reflection,
                    state.plan_version
                )
                state.plan_version += 1
                state.telemetry.replan_count += 1
                state.replan_history.append(replan_record)
                state.telemetry.replanning_ms += (time.time() - t_rep) * 1000.0
                self._broadcast("REPLAN_COMPLETE", plan_version=state.plan_version, strategy=replan_record["strategy"])

                if should_pause:
                    state.decision = CognitiveDecisionType.PAUSE_TASK
                    state.final_response_status = "blocked"
                    state.final_response = "The task is paused and can be resumed when a capable provider or dependency is available."
                    state.is_terminal = True
                    state.termination_reason = "PAUSED_FOR_DEPENDENCY"
                    state.telemetry.total_cognitive_ms = (time.time() - t0) * 1000.0
                    return state

        # ---------------------------------------------------------------------
        # 7. GROUND TRUTH VERIFICATION
        # ---------------------------------------------------------------------
        obs_canonical = []
        for o in state.observations:
            obs_canonical.append(CanonicalToolResult(
                tool=o.tool,
                success=o.success,
                stdout=o.stdout,
                stderr=o.stderr,
                output=o.output,
                exit_code=o.exit_code,
                action=o.action,
                artifact=o.artifacts[0] if o.artifacts else {}
            ))

        state.verification_results = verifier.verify_ground_truth(state.normalized_goal, obs_canonical)

        # ---------------------------------------------------------------------
        # 8. RESPONSE SYNTHESIS & FINAL TRUTH DETERMINATION
        # ---------------------------------------------------------------------
        if state.verification_results.get("verified") and state.verification_results.get("status") == "COMPLETED":
            state.final_response_status = "success"
            # Extract clean stdout if available
            stdout_list = [o.stdout for o in state.observations if o.stdout and o.success]
            if stdout_list:
                state.final_response = f"Done. Output verified:\n\n{stdout_list[-1].strip()}"
            elif state.observations:
                state.final_response = f"Done. {state.observations[-1].output.strip()}"
            else:
                state.final_response = "Goal verified and completed, Boss."
        elif state.completed_steps and len(state.completed_steps) < len(state.current_plan):
            state.final_response_status = "partial_success"
            state.final_response = "Partially completed. Some steps succeeded but full verification could not be completed."
        else:
            state.final_response_status = "failed" if state.observations else "success"
            state.final_response = state.observations[-1].output if state.observations else "Action completed."

        state.is_terminal = True
        state.telemetry.total_cognitive_ms = (time.time() - t0) * 1000.0
        self._broadcast("COGNITION_COMPLETED", status=state.final_response_status)
        return state


cognitive_engine = CognitiveEngine()
