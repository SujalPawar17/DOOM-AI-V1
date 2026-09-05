"""
DOOM V4.1 — Cognitive to V3.3 TaskEngine Bridge
Authoritative execution bridge connecting V4 Cognitive decisions and plans
to the V3.3 TaskEngine, StateMachine, ToolRegistry, RiskEngine, CheckpointManager, and Verifier.

Architecture:
  CognitiveDecision / CognitivePlan
              ↓
      CognitiveBridge
              ↓
  TaskEngine (Authoritative State)
              ↓
  ToolRegistry & Security Gates
              ↓
  Observation & Evaluation
              ↓
  Reflection & Adaptive Replanning
              ↓
  GroundTruthVerifier (Authoritative Truth)
              ↓
  Truth-First Synthesizer & Memory 2.0
"""

import time
from typing import Dict, Any, List, Optional, Callable
from core.cognition.schemas import (
    CognitiveState, CognitiveDecisionType, CognitiveIntent, CognitiveStep,
    EvaluationOutcome
)
from core.cognition.observation import observation_engine
from core.cognition.reflection import reflection_engine
from core.cognition.replanning import cognitive_replanner
from core.state_machine import state_machine, DoomState
from core.task_engine import task_engine, TaskStatus, StepStatus
from core.verifier import verifier
from core.model_router import model_router, NoCapableProviderError
from core.decision_engine import decision_engine
from core.tool_registry import tool_registry
from tools.base import CanonicalToolResult, RiskLevel, TerminationReason, FinalResponseStatus

MAX_COGNITIVE_ITERATIONS = 5


class CognitiveBridge:
    """
    Executes a CognitivePlan using the existing V3.3 TaskEngine, StateMachine,
    ModelRouter, ToolRegistry, RiskEngine, and Checkpointing infrastructure.
    """

    def __init__(self):
        self._broadcaster: Optional[Callable[[Dict[str, Any]], None]] = None
        self.task_engine = task_engine

    def set_broadcaster(self, broadcaster: Callable[[Dict[str, Any]], None]) -> None:
        self._broadcaster = broadcaster

    def _broadcast(self, event_type: str, **payload) -> None:
        if self._broadcaster:
            try:
                self._broadcaster({"type": "cognitive_event", "event": event_type, **payload})
            except Exception:
                pass

    def execute_plan(
        self,
        state: CognitiveState,
        context: Optional[Dict[str, Any]] = None
    ) -> CognitiveState:
        """
        Executes state.current_plan via V3.3 execution foundation with strict
        ground-truth verification and state authority.
        """
        t_bridge_start = time.time()

        # ---------------------------------------------------------------------
        # 1. Capability Verification Gate (ModelRouter)
        # ---------------------------------------------------------------------
        # Verify provider capabilities for required capabilities (e.g. coding)
        for cap in state.required_capabilities:
            if cap in ("coding", "reasoning", "web_search", "telemetry"):
                try:
                    # Check if router can route this capability without error
                    provider = model_router.route(cap)
                    if not provider:
                        raise NoCapableProviderError(cap)
                except NoCapableProviderError as pe:
                    print(f"[COGNITIVE BRIDGE] [PROVIDER OUTAGE] No capable provider for '{cap}': {pe}")
                    self._broadcast("PROVIDER_OUTAGE", capability=cap)

                    # Initialize task and pause
                    task = task_engine.get_active_task()
                    if not task:
                        task = task_engine.create_task(state.normalized_goal, state.task_type)
                        task_engine.set_plan_steps([s.description or s.objective for s in state.current_plan])

                    task_engine.pause_task(f"NO_CAPABLE_MODEL_AVAILABLE: {cap}")
                    task_engine._save_checkpoint()

                    state.decision = CognitiveDecisionType.PAUSE_TASK
                    state.final_response_status = "blocked"
                    state.final_response = (
                        f"Boss, the task is paused because no capable reasoning provider is currently available for {cap}. "
                        f"The completed work has been saved and the task can resume from the current step when a provider is available."
                    )
                    state.termination_reason = "PAUSED_FOR_DEPENDENCY"
                    state.is_terminal = True
                    return state

        # ---------------------------------------------------------------------
        # 2. TaskEngine State Machine Synchronization
        # ---------------------------------------------------------------------
        active_task = task_engine.get_active_task()
        is_resuming = (
            active_task is not None
            and active_task.goal == state.normalized_goal
            and active_task.status in (TaskStatus.RUNNING, TaskStatus.PAUSED)
            and any(s.status == StepStatus.SUCCEEDED for s in active_task.steps)
        )

        if not is_resuming:
            task = task_engine.create_task(state.normalized_goal, state.task_type)
            step_descriptions = [s.description or s.objective for s in state.current_plan]
            task_engine.set_plan_steps(step_descriptions)
        else:
            task = active_task
            # Resuming or existing task: Sync already completed steps
            completed_step_indices = {tstep.index for tstep in task.steps if tstep.status == StepStatus.SUCCEEDED}
            for cstep in state.current_plan:
                if cstep.step_id in completed_step_indices:
                    cstep.status = "succeeded"
                    if cstep not in state.completed_steps:
                        state.completed_steps.append(cstep)
                        print(f"[COGNITIVE BRIDGE] Resuming task {task.task_id}: Step {cstep.step_id} already SUCCEEDED (will NOT re-execute).")

        # ---------------------------------------------------------------------
        # 3. Cognitive Execution Loop (ACT -> OBSERVE -> EVALUATE -> REFLECT -> REPLAN)
        # ---------------------------------------------------------------------
        cognitive_cycle = 0

        while cognitive_cycle < MAX_COGNITIVE_ITERATIONS:
            cognitive_cycle += 1
            state.telemetry.cognitive_cycles = cognitive_cycle

            # Find next incomplete step (preserving succeeded steps)
            completed_ids = {s.step_id for s in state.completed_steps}
            remaining_steps = [s for s in state.current_plan if s.step_id not in completed_ids]

            if not remaining_steps:
                # All planned steps finished
                break

            current_step = remaining_steps[0]
            state.current_step_id = current_step.step_id
            current_step.status = "running"

            self._broadcast("ACTION_STARTED", step_id=current_step.step_id, action=current_step.action, tool=current_step.tool_name)

            # Security Gate: verify risk before tool execution
            if current_step.tool_name and current_step.tool_name != "verifier":
                tool_obj = tool_registry.get_tool(current_step.tool_name)
                effective_risk = None
                if tool_obj:
                    if callable(getattr(tool_obj, "get_effective_risk", None)):
                        try:
                            effective_risk = tool_obj.get_effective_risk()
                        except Exception:
                            effective_risk = None
                    if not effective_risk:
                        effective_risk = getattr(tool_obj, "risk_level", RiskLevel.SAFE)
                elif getattr(current_step, "risk_level", None) == "HIGH":
                    effective_risk = RiskLevel.HIGH

                if effective_risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                    print(f"[COGNITIVE SECURITY] Tool '{current_step.tool_name}' requires explicit authorization.")
                    task_engine.require_user_approval(current_step.tool_name, current_step.tool_args)
                    state_machine.transition_to(
                        DoomState.WAITING_FOR_APPROVAL,
                        f"Authorization required: {current_step.tool_name}",
                        task_id=task.task_id
                    )
                    state.decision = CognitiveDecisionType.REQUEST_APPROVAL
                    state.final_response = f"Action '{current_step.tool_name}' requires your authorization, Boss. Please confirm in the DOOM HUD."
                    state.final_response_status = "blocked"
                    state.is_terminal = True
                    state.termination_reason = "USER_APPROVAL_REQUIRED"
                    return state

            # ACT: Execute Tool / Action
            t_act = time.time()
            state_machine.transition_to(DoomState.EXECUTING, current_step.objective, task_id=task.task_id)

            raw_result = None
            if current_step.tool_name == "verifier":
                # Verifier tool step
                obs_canonical = [
                    CanonicalToolResult(
                        tool=o.tool,
                        success=o.success,
                        stdout=o.stdout,
                        stderr=o.stderr,
                        output=o.output,
                        exit_code=o.exit_code,
                        action=o.action,
                        artifact=o.artifacts[0] if o.artifacts else {}
                    )
                    for o in state.observations
                ]
                raw_result = verifier.verify_ground_truth(state.normalized_goal, obs_canonical)
                current_step.status = "succeeded"
                state.completed_steps.append(current_step)
                state.telemetry.execution_ms += (time.time() - t_act) * 1000.0
                continue
            elif current_step.tool_name:
                tool_obj = tool_registry.get_tool(current_step.tool_name)
                if tool_obj:
                    # Pre-Execution Decision Gate: Check for redundant tool calls
                    obs_canonical_current = [
                        CanonicalToolResult(
                            tool=o.tool,
                            success=o.success,
                            action=o.action,
                            output=o.output
                        )
                        for o in state.observations
                    ]
                    should_run, skip_reason = decision_engine.should_execute(
                        tool_name=current_step.tool_name,
                        tool_args=current_step.tool_args,
                        executed_observations=obs_canonical_current,
                        already_called_signatures=[]
                    )
                    if not should_run:
                        print(f"[COGNITIVE BRIDGE] Skipping redundant tool: {current_step.tool_name} ({skip_reason})")
                        raw_result = CanonicalToolResult(
                            tool=current_step.tool_name,
                            success=True,
                            action="skip_redundant",
                            output=f"[SKIPPED REDUNDANT] {skip_reason}",
                            metadata={"skip_reason": skip_reason}
                        )
                    else:
                        try:
                            raw_result = tool_obj.execute(**current_step.tool_args)
                            state.telemetry.tools_executed.append(current_step.tool_name)
                            task_engine.record_tool_call(current_step.tool_name)
                        except Exception as te:
                            raw_result = CanonicalToolResult(tool=current_step.tool_name, success=False, stderr=str(te))
                else:
                    raw_result = CanonicalToolResult(
                        tool=current_step.tool_name,
                        success=False,
                        action=current_step.action,
                        output=f"Tool '{current_step.tool_name}' not found.",
                        stderr=f"Tool '{current_step.tool_name}' not found.",
                        exit_code=1
                    )
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

            # Check outcome and advance/replan
            if outcome == EvaluationOutcome.SUCCESS:
                current_step.status = "succeeded"
                current_step.result = observation.stdout or observation.output
                state.completed_steps.append(current_step)

                # Advance step in TaskEngine with artifact tracking
                art = observation.artifacts[0] if observation.artifacts else None
                task_engine.advance_step(
                    step_index=current_step.step_id,
                    tool_name=current_step.tool_name or current_step.action,
                    output=observation.output or observation.stdout,
                    success=True,
                    artifacts=[art] if art else None
                )
                # Save checkpoint after each tool execution
                t_cp = time.time()
                task_engine._save_checkpoint()
                state.telemetry.checkpoint_ms += (time.time() - t_cp) * 1000.0
            else:
                # Step failed or blocked -> trigger REPLAN
                current_step.status = "failed" if outcome == EvaluationOutcome.FAILED else "blocked"
                current_step.error = observation.stderr or observation.output

                self._broadcast("REPLAN_STARTED", failed_step_id=current_step.step_id, reason=current_step.error)
                t_rep = time.time()
                new_plan, replan_record, should_pause = cognitive_replanner.replan(
                    current_plan=state.current_plan,
                    completed_steps=state.completed_steps,
                    failed_step=current_step,
                    reflection=reflection,
                    plan_version=state.plan_version
                )
                state.current_plan = new_plan
                state.plan_version += 1
                state.telemetry.replan_count += 1
                state.replan_history.append(replan_record)
                state.telemetry.replanning_ms += (time.time() - t_rep) * 1000.0
                self._broadcast("REPLAN_COMPLETE", plan_version=state.plan_version, strategy=replan_record["strategy"])

                # Update step descriptions in task_engine for newly inserted steps
                new_descriptions = [s.description or s.objective for s in new_plan]
                task_engine.set_plan_steps(new_descriptions)

                if should_pause:
                    task_engine.pause_task("REPLAN_PAUSED")
                    task_engine._save_checkpoint()
                    state.decision = CognitiveDecisionType.PAUSE_TASK
                    state.final_response_status = "blocked"
                    state.final_response = "The task is paused and can be resumed when a capable provider or dependency is available."
                    state.termination_reason = "PAUSED_FOR_DEPENDENCY"
                    state.is_terminal = True
                    return state

        # ---------------------------------------------------------------------
        # 4. Ground Truth Verification (Verifier is authoritative)
        # ---------------------------------------------------------------------
        self._broadcast("VERIFICATION_STARTED")
        state_machine.transition_to(DoomState.VERIFYING, "Verifying results...", task_id=task.task_id)

        obs_canonical = [
            CanonicalToolResult(
                tool=o.tool,
                success=o.success,
                stdout=o.stdout,
                stderr=o.stderr,
                output=o.output,
                exit_code=o.exit_code,
                action=o.action,
                artifact=o.artifacts[0] if o.artifacts else {}
            )
            for o in state.observations
        ]

        t_ver = time.time()
        state.verification_results = verifier.verify_ground_truth(state.normalized_goal, obs_canonical)
        state.telemetry.verification_ms = (time.time() - t_ver) * 1000.0
        self._broadcast(
            "VERIFICATION_COMPLETE",
            verified=state.verification_results.get("verified", False),
            status=state.verification_results.get("status", "FAILED")
        )

        # ---------------------------------------------------------------------
        # 5. Truth Authority & Response Synthesis
        # ---------------------------------------------------------------------
        from core.orchestrator import doom_core
        any_failed = any(s.status == "failed" for s in state.current_plan)
        any_succeeded = any(s.status == "succeeded" for s in state.current_plan)
        if any_failed and not any_succeeded:
            termination_reason = TerminationReason.MAX_RETRIES_EXCEEDED
            state.termination_reason = "STEP_FAILED"
        elif any_failed and any_succeeded:
            termination_reason = TerminationReason.PARTIAL_COMPLETION
            state.termination_reason = "PARTIAL_COMPLETION"
        else:
            termination_reason = TerminationReason.COMPLETED
            state.termination_reason = "COMPLETED"

        final_response_status = doom_core._determine_final_response_status(
            obs_canonical, state.verification_results, termination_reason
        )
        state.final_response_status = final_response_status.value

        final_text = doom_core._synthesize_final_response(
            user_prompt=state.normalized_goal,
            observations=obs_canonical,
            plan=None,
            last_llm_text="",
            verification=state.verification_results
        )
        state.final_response = final_text

        # ---------------------------------------------------------------------
        # 6. State Authority (TaskEngine)
        # ---------------------------------------------------------------------
        if final_response_status == FinalResponseStatus.SUCCESS:
            task_engine.complete_task(final_text, final_response_status, "COMPLETED")
        elif final_response_status == FinalResponseStatus.PARTIAL_SUCCESS:
            task_engine.complete_task_partial(final_text, "PARTIAL_SUCCESS")
        elif final_response_status == FinalResponseStatus.BLOCKED:
            task_engine.pause_task("USER_APPROVAL_REQUIRED")
        else:
            task_engine.fail_task(final_text)

        # ---------------------------------------------------------------------
        # 7. Memory 2.0 Integration
        # ---------------------------------------------------------------------
        try:
            from memory import short_term_memory, episodic_memory
            used_tool_names = [o.tool for o in obs_canonical if o.action != "skip_redundant"]
            short_term_memory.add_assistant_turn(final_text, used_tool_names)
            episodic_memory.record_episode(
                goal=state.normalized_goal,
                plan_steps=[s.description or s.objective for s in state.current_plan],
                tools_called=[{"name": o.tool, "action": o.action, "success": o.success} for o in obs_canonical],
                outcome=final_text,
                success=state.verification_results.get("verified", True)
            )
        except Exception:
            pass

        state.is_terminal = True
        self._broadcast("COGNITION_COMPLETED", status=state.final_response_status)
        return state


cognitive_bridge = CognitiveBridge()
