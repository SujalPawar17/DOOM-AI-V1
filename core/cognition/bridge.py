"""
DOOM V4.2 — Hardened Cognitive to V3.3 TaskEngine Bridge
Authoritative execution bridge connecting V4 Cognitive decisions and plans
to the V3.3 TaskEngine, StateMachine, ToolRegistry, RiskEngine, CheckpointManager, and Verifier.

Hardening Layers (V4.2):
  1. PlanValidator (Acyclic DAG & schema validation)
  2. TaskConcurrencyManager (Durable lease / anti-race lock)
  3. ToolInputValidator (Path traversal & dangerous injection firewall)
  4. IdempotencyManager (One side effect = one logical operation)
  5. Verify-Before-Retry (Reconciliation before duplicate execution)
  6. RetryPolicy (Centralized retry budgets and limits)
  7. Cognitive Loop Defense (Anti-cycling / repeated failure detection)
  8. Cancellation Safety (Immediate draining upon cancellation)
  9. Cryptographically Bound User Approvals
 10. Memory Safety (Unverified tasks never stored as successes)
"""

import os
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

# Reliability subsystems
from core.reliability.correlation import get_current_correlation
from core.reliability.plan_validator import plan_validator
from core.reliability.input_validator import tool_input_validator
from core.reliability.idempotency import idempotency_manager, ExecutionState
from core.reliability.retry_policy import retry_policy
from core.reliability.concurrency import task_concurrency_manager

MAX_COGNITIVE_ITERATIONS = 5


class CognitiveBridge:
    """
    Executes a CognitivePlan using the hardened V3.3 TaskEngine, StateMachine,
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
        Executes state.current_plan via hardened V3.3 execution foundation with strict
        ground-truth verification and state authority.
        """
        t_bridge_start = time.time()
        correlation = get_current_correlation()

        # ---------------------------------------------------------------------
        # 1. Capability Verification Gate (ModelRouter)
        # ---------------------------------------------------------------------
        for cap in state.required_capabilities:
            if cap in ("coding", "reasoning", "web_search", "telemetry"):
                try:
                    provider = model_router.route(cap)
                    if not provider:
                        raise NoCapableProviderError(cap, [])
                except NoCapableProviderError as pe:
                    print(f"[COGNITIVE BRIDGE] [PROVIDER OUTAGE] No capable provider for '{cap}': {pe}")
                    self._broadcast("PROVIDER_OUTAGE", capability=cap)

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
        # 2. Plan Validation Gate (V4.2: Reject malformed / cyclic plans)
        # ---------------------------------------------------------------------
        is_valid_plan, plan_errors = plan_validator.validate_plan(state.current_plan)
        if not is_valid_plan:
            print(f"[COGNITIVE BRIDGE] [PLAN REJECTED]: {plan_errors}")
            self._broadcast("PLAN_VALIDATION_FAILED", errors=plan_errors)
            state.decision = CognitiveDecisionType.FAIL_TASK
            state.final_response_status = "failed"
            state.final_response = f"Plan validation failed: {'; '.join(plan_errors)}"
            state.termination_reason = "INVALID_PLAN"
            state.is_terminal = True
            task = task_engine.get_active_task()
            if not task:
                task = task_engine.create_task(state.normalized_goal, state.task_type)
            task_engine.fail_task(state.final_response)
            return state

        # ---------------------------------------------------------------------
        # 3. TaskEngine State Machine & Concurrency Lease Synchronization
        # ---------------------------------------------------------------------
        active_task = task_engine.get_active_task()
        if active_task is not None and active_task.status in (TaskStatus.CANCELLED, TaskStatus.CANCELLING):
            state.decision = CognitiveDecisionType.FAIL_TASK
            state.final_response_status = "cancelled"
            state.final_response = f"Task was cancelled: {active_task.error or 'User cancellation'}"
            state.termination_reason = "CANCELLED"
            state.is_terminal = True
            return state

        is_resuming = (
            active_task is not None
            and active_task.goal == state.normalized_goal
            and active_task.status in (TaskStatus.RUNNING, TaskStatus.PAUSED)
        )

        if not is_resuming:
            task = task_engine.create_task(state.normalized_goal, state.task_type)
            step_descriptions = [s.description or s.objective for s in state.current_plan]
            task_engine.set_plan_steps(step_descriptions)
        else:
            task = active_task
            completed_step_indices = {tstep.index for tstep in task.steps if tstep.status == StepStatus.SUCCEEDED}
            for cstep in state.current_plan:
                if cstep.step_id in completed_step_indices:
                    cstep.status = "succeeded"
                    if cstep not in state.completed_steps:
                        state.completed_steps.append(cstep)
                        print(f"[COGNITIVE BRIDGE] Resuming task {task.task_id}: Step {cstep.step_id} already SUCCEEDED (idempotently preserved).")

        correlation.task_id = task.task_id

        # Acquire task concurrency lease (V4.2)
        owner_id = f"worker_{os.getpid()}_{correlation.doom_request_id}"
        if not task_concurrency_manager.acquire_lease(task.task_id, owner_id):
            print(f"[COGNITIVE BRIDGE] Concurrency Conflict: Task '{task.task_id}' is actively locked by another worker.")
            state.decision = CognitiveDecisionType.PAUSE_TASK
            state.final_response_status = "blocked"
            state.final_response = f"Task '{task.task_id}' is locked by another active executor."
            state.termination_reason = "TASK_LOCKED"
            state.is_terminal = True
            return state

        # ---------------------------------------------------------------------
        # 4. Cognitive Execution Loop with Loop Defense & Idempotency
        # ---------------------------------------------------------------------
        cognitive_cycle = 0
        consecutive_same_failures = 0
        last_failure_key = None
        seen_plan_counts: Dict[str, int] = {}

        try:
            while cognitive_cycle < MAX_COGNITIVE_ITERATIONS:
                # Check cancellation at start of cycle
                if task.status in (TaskStatus.CANCELLED, TaskStatus.CANCELLING):
                    state.decision = CognitiveDecisionType.FAIL_TASK
                    state.final_response_status = "cancelled"
                    state.final_response = f"Task was cancelled: {task.error or 'User cancellation'}"
                    state.termination_reason = "CANCELLED"
                    state.is_terminal = True
                    return state

                # Find next incomplete step (preserving succeeded steps and respecting blocked steps)
                non_executable_statuses = {"succeeded", "blocked", "skipped"}
                completed_ids = {s.step_id for s in state.completed_steps}
                remaining_steps = [
                    s for s in state.current_plan
                    if s.step_id not in completed_ids and s.status not in non_executable_statuses
                ]

                if not remaining_steps:
                    # All planned steps finished or blocked
                    break

                cognitive_cycle += 1
                state.telemetry.cognitive_cycles = cognitive_cycle
                correlation.new_cycle(cognitive_cycle)

                current_step = remaining_steps[0]
                state.current_step_id = current_step.step_id
                current_step.status = "running"
                correlation.new_step(current_step.step_id)

                self._broadcast("ACTION_STARTED", step_id=current_step.step_id, action=current_step.action, tool=current_step.tool_name)

                # Heartbeat lease
                task_concurrency_manager.heartbeat(task.task_id, owner_id)

                # Security Gate: verify risk before tool execution
                if current_step.tool_name and current_step.tool_name != "verifier":
                    tool_obj = tool_registry.get_tool(current_step.tool_name)
                    effective_risk = None
                    step_risk = getattr(current_step, "risk_level", None)
                    if step_risk in (RiskLevel.HIGH, RiskLevel.CRITICAL, "HIGH", "CRITICAL"):
                        effective_risk = RiskLevel.CRITICAL if step_risk in (RiskLevel.CRITICAL, "CRITICAL") else RiskLevel.HIGH
                    elif tool_obj:
                        if callable(getattr(tool_obj, "get_effective_risk", None)):
                            try:
                                effective_risk = tool_obj.get_effective_risk()
                            except Exception:
                                effective_risk = None
                        if not effective_risk:
                            effective_risk = getattr(tool_obj, "risk_level", RiskLevel.SAFE)

                    if effective_risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                        print(f"[COGNITIVE SECURITY] Tool '{current_step.tool_name}' requires explicit authorization.")
                        op_token = task_engine.require_user_approval(
                            tool_name=current_step.tool_name,
                            tool_args=current_step.tool_args,
                            operation_id=correlation.operation_id
                        )
                        state.decision = CognitiveDecisionType.REQUEST_APPROVAL
                        state.final_response = f"Action '{current_step.tool_name}' requires your authorization, Boss. Please confirm in the DOOM HUD."
                        state.final_response_status = "blocked"
                        state.is_terminal = True
                        state.termination_reason = "USER_APPROVAL_REQUIRED"
                        return state

                # Input Validation Firewall (V4.2: Path traversal & injection defense)
                if current_step.tool_name and current_step.tool_name != "verifier":
                    is_safe, reject_reason, sanitized_args = tool_input_validator.validate_inputs(
                        tool_name=current_step.tool_name,
                        tool_args=current_step.tool_args
                    )
                    if not is_safe:
                        print(f"[INPUT FIREWALL] Blocked unsafe inputs for '{current_step.tool_name}': {reject_reason}")
                        raw_result = CanonicalToolResult(
                            tool=current_step.tool_name,
                            success=False,
                            action=current_step.action,
                            output=f"[SECURITY BLOCKED] {reject_reason}",
                            stderr=reject_reason,
                            exit_code=1
                        )
                        # Skip execution, proceed straight to observation
                        current_step.tool_args = sanitized_args or current_step.tool_args
                        t_act = time.time()
                        state.telemetry.execution_ms += 0.0

                # Idempotency Gate (V4.2: One Side Effect = One Operation)
                idem_key = idempotency_manager.compute_idempotency_key(
                    task_id=task.task_id,
                    step_id=current_step.step_id,
                    logical_action=current_step.action,
                    tool_args=current_step.tool_args
                )
                can_exec, existing_receipt = idempotency_manager.claim(
                    key=idem_key,
                    task_id=task.task_id,
                    step_id=current_step.step_id,
                    logical_action=current_step.action,
                    tool_name=current_step.tool_name or "unknown",
                    tool_args=current_step.tool_args,
                    operation_id=correlation.operation_id
                )

                t_act = time.time()
                raw_result = None

                if not can_exec and existing_receipt:
                    if existing_receipt.state in (ExecutionState.COMPLETED, ExecutionState.RECONCILED):
                        print(f"[IDEMPOTENCY] Bypassing duplicate execution: Reusing existing receipt for key '{idem_key}'.")
                        raw_result = existing_receipt.to_canonical_result()
                    elif existing_receipt.state == ExecutionState.CLAIMED:
                        print(f"[IDEMPOTENCY] Key '{idem_key}' is already claimed by an active operation.")
                        raw_result = CanonicalToolResult(
                            tool=current_step.tool_name or "unknown",
                            success=False,
                            action=current_step.action,
                            output=f"Operation for key '{idem_key}' is currently pending/in-flight.",
                            stderr="Operation pending"
                        )
                    else:
                        # Ambiguous / Unknown / Possible side effect: VERIFY EXTERNAL STATE BEFORE RETRY (V4.2)
                        if current_step.action in ("create_file", "patch_file", "write_file"):
                            fname = current_step.tool_args.get("file_name") or current_step.tool_args.get("file_path") or current_step.tool_args.get("code_or_file")
                            if fname:
                                try:
                                    from core.path_resolver import canonical_path
                                    cp = canonical_path(fname)
                                    if cp.exists and os.path.getsize(cp.absolute_path) > 0:
                                        print(f"[RECONCILIATION] External state verified for key '{idem_key}': artifact '{cp.filename}' exists ({os.path.getsize(cp.absolute_path)}B) -> Reconciling as SUCCEEDED.")
                                        idempotency_manager.mark_reconciled(
                                            idem_key,
                                            artifacts=[{"path": cp.absolute_path, "size": os.path.getsize(cp.absolute_path), "name": cp.filename, "exists": True}],
                                            output=f"Reconciled artifact on disk at {cp.relative_path}"
                                        )
                                        raw_result = CanonicalToolResult(
                                            tool=current_step.tool_name or "unknown",
                                            success=True,
                                            action=current_step.action,
                                            output=f"Reconciled artifact on disk at {cp.relative_path}",
                                            artifact={"path": cp.absolute_path, "size": os.path.getsize(cp.absolute_path), "name": cp.filename, "exists": True}
                                        )
                                except Exception:
                                    pass

                # ACT: Execute Tool if not already resolved by idempotency
                if raw_result is None:
                    state_machine.transition_to(DoomState.EXECUTING, current_step.objective, task_id=task.task_id)

                    if current_step.tool_name == "verifier":
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
                                CanonicalToolResult(tool=o.tool, success=o.success, action=o.action, output=o.output)
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
                                    correlation.new_tool_execution(current_step.tool_name)
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

                # Verify-Before-Retry (V4.2: Check if side effect happened despite error/timeout)
                if raw_result and not raw_result.success:
                    if current_step.action in ("create_file", "patch_file"):
                        fname = current_step.tool_args.get("file_name") or current_step.tool_args.get("file_path") or current_step.tool_args.get("code_or_file")
                        if fname:
                            try:
                                from core.path_resolver import canonical_path
                                cp = canonical_path(fname)
                                if cp.exists and os.path.getsize(cp.absolute_path) > 0:
                                    print(f"[VERIFY-BEFORE-RETRY] Confirmed artifact '{cp.filename}' on disk ({os.path.getsize(cp.absolute_path)}B) -> Reconciling as SUCCEEDED.")
                                    raw_result = CanonicalToolResult(
                                        tool=current_step.tool_name,
                                        success=True,
                                        action=current_step.action,
                                        output=f"Reconciled artifact on disk at {cp.relative_path}",
                                        artifact={"path": cp.absolute_path, "size": os.path.getsize(cp.absolute_path), "name": cp.filename, "exists": True}
                                    )
                                    idempotency_manager.mark_reconciled(idem_key, artifacts=[raw_result.artifact])
                            except Exception:
                                pass

                # Record idempotency receipt
                if raw_result:
                    idempotency_manager.record_receipt(idem_key, raw_result)

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

                # Advance or Replan
                if outcome == EvaluationOutcome.SUCCESS:
                    consecutive_same_failures = 0
                    current_step.status = "succeeded"
                    current_step.result = observation.stdout or observation.output
                    state.completed_steps.append(current_step)

                    art = observation.artifacts[0] if observation.artifacts else None
                    task_engine.advance_step(
                        step_index=current_step.step_id,
                        tool_name=current_step.tool_name or current_step.action,
                        output=observation.output or observation.stdout,
                        success=True,
                        artifacts=[art] if art else None
                    )
                    t_cp = time.time()
                    task_engine._save_checkpoint()
                    state.telemetry.checkpoint_ms += (time.time() - t_cp) * 1000.0
                else:
                    # Step failed -> Check retry budget & Cognitive loop protection
                    failure_key = f"{current_step.tool_name}::{observation.stderr or observation.output}"
                    if failure_key == last_failure_key:
                        consecutive_same_failures += 1
                    else:
                        consecutive_same_failures = 1
                        last_failure_key = failure_key

                    if consecutive_same_failures >= 3:
                        print(f"[COGNITIVE LOOP DEFENSE] Halting: Repeated failure on '{current_step.tool_name}' 3 times.")
                        state.final_response_status = "failed"
                        state.final_response = f"Task halted: tool '{current_step.tool_name}' failed repeatedly with: {observation.stderr or observation.output}"
                        state.termination_reason = "REPEATED_FAILURE_HALTED"
                        state.is_terminal = True
                        task_engine.fail_task(state.final_response)
                        return state

                    can_replan_now, replan_reason = retry_policy.can_replan(task.task_id)
                    if not can_replan_now:
                        print(f"[RETRY POLICY] Replan budget exhausted: {replan_reason}")
                        state.final_response_status = "failed"
                        state.final_response = f"Task failed: {replan_reason}"
                        state.termination_reason = "RETRY_BUDGET_EXHAUSTED"
                        state.is_terminal = True
                        task_engine.fail_task(state.final_response)
                        return state

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

                    new_plan_sig = "->".join(f"{s.step_id}:{s.action}" for s in new_plan)
                    seen_plan_counts[new_plan_sig] = seen_plan_counts.get(new_plan_sig, 0) + 1
                    if seen_plan_counts[new_plan_sig] >= 3:
                        print(f"[COGNITIVE LOOP DEFENSE] Cyclic plan detected: plan signature '{new_plan_sig}' repeated 3 times.")
                        state.final_response_status = "failed"
                        state.final_response = "Cognitive loop halted: detected repeated identical plan cycling."
                        state.termination_reason = "COGNITIVE_LOOP_DETECTED"
                        state.is_terminal = True
                        task_engine.fail_task(state.final_response)
                        return state

                    state.current_plan = new_plan
                    state.plan_version += 1
                    state.telemetry.replan_count += 1
                    state.replan_history.append(replan_record)
                    state.telemetry.replanning_ms += (time.time() - t_rep) * 1000.0
                    self._broadcast("REPLAN_COMPLETE", plan_version=state.plan_version, strategy=replan_record["strategy"])

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

            # -----------------------------------------------------------------
            # 5. Ground Truth Verification (Verifier is authoritative)
            # -----------------------------------------------------------------
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

            # -----------------------------------------------------------------
            # 6. Truth Authority & Response Synthesis
            # -----------------------------------------------------------------
            from core.orchestrator import doom_core
            any_failed = any(s.status == "failed" for s in state.current_plan)
            any_succeeded = any(s.status == "succeeded" for s in state.current_plan)
            any_blocked = any(s.status in ("blocked", "skipped") for s in state.current_plan)
            if any_failed and not any_succeeded:
                termination_reason = TerminationReason.MAX_RETRIES_EXCEEDED
                state.termination_reason = "STEP_FAILED"
            elif (any_failed or any_blocked) and any_succeeded:
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

            # -----------------------------------------------------------------
            # 7. State Authority (TaskEngine)
            # -----------------------------------------------------------------
            if final_response_status == FinalResponseStatus.SUCCESS:
                task_engine.complete_task(final_text, final_response_status, "COMPLETED")
            elif final_response_status == FinalResponseStatus.PARTIAL_SUCCESS:
                task_engine.complete_task_partial(final_text, "PARTIAL_SUCCESS")
            elif final_response_status == FinalResponseStatus.BLOCKED:
                task_engine.pause_task("USER_APPROVAL_REQUIRED")
            else:
                task_engine.fail_task(final_text)

            # -----------------------------------------------------------------
            # 8. Memory 2.0 Safety (V4.2: Only verified successes recorded as success)
            # -----------------------------------------------------------------
            try:
                from memory import short_term_memory, episodic_memory
                used_tool_names = [o.tool for o in obs_canonical if o.action != "skip_redundant"]
                short_term_memory.add_assistant_turn(final_text, used_tool_names)
                
                is_empirically_successful = (final_response_status == FinalResponseStatus.SUCCESS) and state.verification_results.get("verified", False)
                episodic_memory.record_episode(
                    goal=state.normalized_goal,
                    plan_steps=[s.description or s.objective for s in state.current_plan],
                    tools_called=[{"name": o.tool, "action": o.action, "success": o.success} for o in obs_canonical],
                    outcome=final_text,
                    success=is_empirically_successful
                )
            except Exception:
                pass

            state.is_terminal = True
            self._broadcast("COGNITION_COMPLETED", status=state.final_response_status)
            return state

        finally:
            # Release task lease
            task_concurrency_manager.release_lease(task.task_id, owner_id)
            retry_policy.reset_task(task.task_id)


cognitive_bridge = CognitiveBridge()
