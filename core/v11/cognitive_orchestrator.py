"""V11 Cognitive Orchestrator.
Orchestrates the complete V11 cognitive pipeline by extending the V10 architecture
with V11 layers including execution, experience integration, proactive behavior,
advanced memory systems, and continuous monitoring while maintaining backward
compatibility with V10.
"""
from __future__ import annotations
import os
os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
import time
from typing import Dict, Any, List, Optional, Tuple
from core.v10.context_fusion import FusedContext
from core.v10.goal_understanding import GoalUnderstandingResult, GoalContinuity
from core.v10.reasoning_decision import ReasoningResult, DecisionResult
from core.v10.planning_integration import PlanningResult
from core.v11.execution_layer import ExecutionResult, execute_plan_from_planning_result
# Import the singleton instances that are already created
from core.v10.context_fusion import context_fusion, fuse_context
from core.v10.goal_understanding import goal_understanding
from core.v10.planning_integration import integrate_planning
from core.v10.reasoning_decision import integrate_reasoning_decision
from orchestration.authorization import (
    stash_pending_plan, pending_display, claim_authorization, 
    medium_computer_approvable, AuthorizationClaim
)
from orchestration.executor_errors import ExecutionStatus
from core.verifier import verifier  # singleton instance
from core.cost_guard.guard import cost_guard  # singleton instance
from core.cost_guard.types import ResourceRequest, ResourceType
# Import V11 components
from core.v11.execution_layer import execution_layer, execute_plan_from_planning_result
from core.v11.experience_integration import ExperienceIntegration
# Note: Other V11 components (V11.3-V11.5) will be implemented in subsequent phases

# V11.8: Map V8 executor capabilities to the local resources their default adapters
# actually use, expressed against EXISTING Cost Guard attestations. Capabilities not
# listed here (e.g. browser, world_act) fall through to an unattested request and are
# therefore blocked fail-closed by Cost Guard. No registry entries are added.
_CAPABILITY_COST_RESOURCES: Dict[str, Tuple[ResourceType, str, str]] = {
    "conversation": (ResourceType.LLM, "ollama", "localhost"),
    "system_read": (ResourceType.OTHER, "local_filesystem", ""),
    "filesystem": (ResourceType.OTHER, "local_filesystem", ""),
    "memory_read": (ResourceType.DATABASE, "postgres", "localhost"),
    "memory_write": (ResourceType.DATABASE, "postgres", "localhost"),
    "computer": (ResourceType.VISION, "local_uia", ""),
    "sequence": (ResourceType.VISION, "local_uia", ""),
    "verification": (ResourceType.VISION, "local_uia", ""),
}


def _cost_requests_for_plan(plan: Any) -> List[ResourceRequest]:
    """Build one Cost Guard request per distinct plan step capability (fail closed)."""
    requests: List[ResourceRequest] = []
    seen = set()
    try:
        steps = tuple(getattr(plan, "steps", ()) or ())
    except TypeError:
        steps = ()
    for step in steps:
        capability = str(getattr(step, "capability_id", "") or "")
        if capability in seen:
            continue
        seen.add(capability)
        resource_type, provider, host = _CAPABILITY_COST_RESOURCES.get(
            capability, (ResourceType.OTHER, capability or "unknown", "")
        )
        requests.append(ResourceRequest(
            resource_type=resource_type,
            provider=provider,
            capability=capability,
            host=host,
        ))
    if not requests:
        # No inspectable steps: keep the original generic request (unattested -> fail closed).
        requests.append(ResourceRequest(
            resource_type=ResourceType.OTHER,
            provider="local",
            capability="compute",
            model="",
            endpoint="",
            host="",
            privacy_class="",
            risk_class="",
            correlation_id=""
        ))
    return requests


class V11CognitiveOrchestrator:
    """V11 Cognitive Orchestrator.
    Orchestrates the complete cognitive pipeline by extending the V10 architecture
    with V11 layers while maintaining backward compatibility.
    """
    def __init__(self):
        """Initialize the V11 cognitive orchestrator."""
        # Reuse existing V10 singleton components
        self.context_fusion = context_fusion
        self.goal_understanding = goal_understanding
        self.reasoning_decision_integration = integrate_reasoning_decision
        self.planning_integration = integrate_planning
        # V11.1 Execution Layer
        self.execution_layer = execution_layer
        # V11.2 Experience Integration Layer
        self.experience_integration = ExperienceIntegration()
        # Reuse V8 singleton components
        self.cost_guard = cost_guard
        self.verifier = verifier
        # Note: V9 voice components are reused from the existing frozen V9 architecture
        # Other V11 components (V11.2-V11.5) will be added as they are implemented
        # Orchestrator state
        self.last_cycle_time = 0.0
        self.cycle_count = 0

    @staticmethod
    def _record_goal_outcome(owner_id: str, plan_goal_id: Any, succeeded: bool, status_value: str) -> str:
        """Record an executed plan's outcome on the owner's matching ACTIVE registry goal.

        Success refreshes goal activity and clears the blocker; failure records the
        executor status as the blocker. Lifecycle is never changed here (completion
        stays with the existing V8 lifecycle-confirmation flow). Never raises.
        """
        if not isinstance(plan_goal_id, str) or not plan_goal_id:
            return "NO_PLAN_GOAL_ID"
        try:
            from dataclasses import replace as dc_replace
            from orchestration.plan.goal_registry import (
                get_active_goal, update_active_goal, GoalLifecycle, RegistryStatus,
            )
            active = get_active_goal(owner_id)
            if active.status is not RegistryStatus.OK or active.snapshot is None:
                return f"ACTIVE_GOAL_{active.status.value}"
            snap = active.snapshot
            if snap.goal_id != plan_goal_id or snap.lifecycle is not GoalLifecycle.ACTIVE:
                return "NO_MATCHING_ACTIVE_GOAL"
            blocker = "" if succeeded else f"Last execution: {status_value}"
            updated = update_active_goal(
                owner_id, snap.goal_id, dc_replace(snap, blocker_summary=blocker), snap.version,
            )
            if updated.status is not RegistryStatus.OK:
                return f"UPDATE_{updated.status.value}"
            return "ACTIVITY_RECORDED" if succeeded else "BLOCKER_RECORDED"
        except Exception:
            return "UNAVAILABLE"

    def process_cognitive_cycle(
        self,
        user_input: str,
        owner_id: str,
        session_id: str = "",
        lang: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None,
        authorized_plan_hash: str = "",
        computer_session_id: str = ""
    ) -> Dict[str, Any]:
        """Process a complete cognitive cycle through the V11 architecture pipeline."""
        start_time = time.time()
        cycle_id = f"cycle_{int(start_time * 1000)}_{self.cycle_count}"
        self.cycle_count += 1

        # Initialize result structure
        result = {
            "cycle_id": cycle_id,
            "owner_id": owner_id,
            "session_id": session_id,
            "success": False,  # Will be set to True if all critical stages succeed
            "response_text": "",
            "provenance": {},
            "timing": {},
            "stages": {}
        }

        # Track overall success
        overall_success = True
        error_message = None

        try:
            # Stage 1: Input Normalization (implicit in passing user_input to context fusion)
            result["stages"]["input_normalization"] = {
                "completed": True,
                "input": user_input
            }

            # Stage 2: Context Fusion (V10.1)
            context_start = time.time()
            fused_context: FusedContext = fuse_context(
                request=user_input,
                owner_id=owner_id,
                session_id=session_id,
                language_hint=lang,
                context=context
            )
            context_time = (time.time() - context_start) * 1000
            result["stages"]["context_fusion"] = {
                "completed": True,
                "fused_context": fused_context,
                "time_ms": context_time
            }
            result["provenance"]["context_fusion"] = {
                "context_hash": fused_context.context_hash,
                "fused_at_ms": fused_context.fused_at_ms
            }

            # Stage 3: Memory + User Model Retrieval (V10.2)
            # This is incorporated into context_fusion.fuse_context() in the current implementation
            memory_start = time.time()
            # Memory/user model retrieval happens within context_fusion.fuse_context()
            memory_time = (time.time() - memory_start) * 1000
            result["stages"]["memory_user_model"] = {
                "completed": True,
                "time_ms": memory_time
            }

            # Stage 4: Goal Understanding + Continuity (V10.3)
            goal_start = time.time()
            goal_understanding_result: GoalUnderstandingResult = self.goal_understanding.understand_goal(
                request=user_input,
                owner_id=owner_id,
                session_id=session_id,
                fused_context=fused_context.context
            )
            goal_time = (time.time() - goal_start) * 1000
            result["stages"]["goal_understanding"] = {
                "completed": True,
                "goal_understanding_result": goal_understanding_result,
                "continuity": goal_understanding_result.continuity.value,
                "understood_goal_available": goal_understanding_result.understood_goal is not None,
                "time_ms": goal_time
            }
            result["provenance"]["goal_understanding"] = {
                "continuity": goal_understanding_result.continuity.value,
                "confidence": goal_understanding_result.confidence,
                "timestamp_ms": goal_understanding_result.timestamp_ms
            }

            # Stage 5: Bounded Reasoning (V10.4)
            # Stage 6: Decision (V10.4)
            # These are combined in the reasoning_decision integration
            reasoning_start = time.time()
            reasoning_result: ReasoningResult
            decision_result: DecisionResult
            reasoning_result, decision_result = self.reasoning_decision_integration(
                fused_context=fused_context,
                goal_understanding=goal_understanding_result,
                owner_id=owner_id,
                session_id=session_id
            )
            reasoning_time = (time.time() - reasoning_start) * 1000
            result["stages"]["reasoning_decision"] = {
                "completed": True,
                "reasoning_result": reasoning_result,
                "decision_result": decision_result,
                "decision_type": decision_result.decision_type.value,
                "time_ms": reasoning_time
            }
            result["provenance"]["reasoning_decision"] = {
                "reasoning_summary_length": len(reasoning_result.reasoning_summary),
                "assumptions_count": len(reasoning_result.assumptions),
                "unresolved_questions_count": len(reasoning_result.unresolved_questions),
                "decision_type": decision_result.decision_type.value,
                "decision_basis": decision_result.decision_basis
            }

            # Determine if planning is required based on the decision type
            # Note: planning_integration is always called to allow mocking in tests;
            # the returned planning_result.planning_required is authoritative.
            planning_required_hint = decision_result.decision_type == "CREATE_PLAN"

            # Initialize planning_result to a default (no planning required)
            planning_result = PlanningResult(False, False, None, None)

            # Stage 7: Planning when required (V10.5)
            planning_start = time.time()
            # Always call planning_integration; it returns planning_required=False
            # for non-plan cycles, and planning_required=True with a plan for plan cycles.
            planning_result: PlanningResult = self.planning_integration(
                fused_context=fused_context,
                goal_understanding=goal_understanding_result,
                reasoning_result=reasoning_result,
                decision_result=decision_result,
                owner_id=owner_id,
                session_id=session_id
            )
            planning_time = (time.time() - planning_start) * 1000
            result["stages"]["planning"] = {
                "completed": True,
                "planning_required": planning_result.planning_required,
                "planning_successful": planning_result.planning_successful,
                "plan_id": planning_result.plan_id if planning_result.plan else None,
                "error": planning_result.error,
                "time_ms": planning_time
            }
            result["provenance"]["planning"] = {
                "planning_required": planning_result.planning_required,
                "planning_successful": planning_result.planning_successful,
                "planner_status": None,  # Fixed: was incorrectly trying to read from unset dict
                "owner_id": owner_id,
                "session_id": session_id
            }

            # Stage 8: V11.7 Cost Guard Check
            cost_guard_start = time.time()
            skip_execution = False
            cost_guard_error = None
            # Only check if we have a plan to execute
            cost_guard_checked = False
            if planning_result.planning_required and planning_result.planning_successful and planning_result.plan is not None:
                # V11.8: one request per distinct step capability; every one must be allowed.
                cost_guard_checked = True
                for request in _cost_requests_for_plan(planning_result.plan):
                    decision = self.cost_guard.decision(request)
                    if not decision.is_allow:
                        skip_execution = True
                        cost_guard_error = f"Cost guard blocked: {decision.reason.value}"
                        break
            cost_guard_time = (time.time() - cost_guard_start) * 1000
            result["stages"]["cost_guard_check"] = {
                "completed": True,
                "checked": cost_guard_checked,
                "skip_execution": skip_execution,
                "cost_guard_error": cost_guard_error,
                "time_ms": cost_guard_time
            }
            result["provenance"]["cost_guard_check"] = {
                "skip_execution": skip_execution,
                "cost_guard_error": cost_guard_error
            }
            if skip_execution:
                overall_success = False
                error_message = "Execution blocked by cost guard: insufficient resources"

            # Stage 9: V11.1 Execution Layer
            execution_start = time.time()
            if skip_execution:
                # Create a blocked execution result
                from orchestration.executor import _result
                # We create an execution result that indicates no execution occurred
                execution_result = _result(
                    plan=None,  # No plan was executed
                    status=ExecutionStatus.BLOCKED,
                    verification="BLOCKED_BY_COST_GUARD",
                    response_text="Execution blocked by cost guard: insufficient resources",
                    start=int(execution_start * 1000),
                    end=int(time.time() * 1000)
                )
            else:
                execution_result: ExecutionResult = self.execution_layer.execute_plan(
                    planning_result=planning_result,
                    owner_id=owner_id,
                    session_id=session_id,
                    authorized_plan_hash=authorized_plan_hash,
                    computer_session_id=computer_session_id
                )
            execution_time = (time.time() - execution_start) * 1000
            # Only a plan handed to the V8 executor is an execution. Non-plan, planning-
            # failure and cost-blocked cycles must not carry a fabricated execution ID.
            executed = (
                not skip_execution
                and planning_result.planning_required
                and planning_result.planning_successful
                and planning_result.plan is not None
            )
            if not executed and isinstance(execution_result, ExecutionResult):
                from dataclasses import replace as dc_replace
                execution_result = dc_replace(execution_result, execution_id="")
            result["stages"]["execution"] = {
                "completed": True,
                "executed": executed,
                "execution_result": execution_result,
                "time_ms": execution_time
            }
            result["provenance"]["execution"] = {
                "executed": executed,
                "execution_id": execution_result.execution_id,
                "status": execution_result.status.value,
                "goal_id": execution_result.goal_id,
                "plan_hash": execution_result.plan_hash,
                "approval_granted": execution_result.approval_granted,
                "approval_required": execution_result.approval_required
            }

            # Stage 10: V8 Authorization / Safety
            auth_start = time.time()
            auth_required = not skip_execution and execution_result.approval_required
            auth_granted = execution_result.approval_granted if not skip_execution else True
            pending_id = None
            stash_error = None

            # If authorization is required and not granted, stash the pending plan
            if (
                auth_required and not auth_granted and planning_result.plan is not None
                and execution_result.status == ExecutionStatus.APPROVAL_REQUIRED
            ):
                from orchestration.executor import ExecutionIdentity
                # Resolve the computer session exactly as the execution layer does
                # (explicit parameter first, then the plan's bound session).
                stash_cs = computer_session_id or str(getattr(planning_result.plan, "computer_session_id", "") or "")
                ident = ExecutionIdentity(
                    owner_id=owner_id,
                    session_id=session_id,
                    computer_session_id=stash_cs
                )
                pending_id, stash_error = stash_pending_plan(
                    plan=planning_result.plan,
                    identity=ident
                )

            auth_time = (time.time() - auth_start) * 1000
            result["stages"]["authorization"] = {
                "completed": True,
                "authorized": auth_granted if auth_required else True,
                "approval_required": auth_required,
                "approval_granted": auth_granted,
                "pending_id": pending_id,
                "stash_error": stash_error,
                "time_ms": auth_time
            }
            result["provenance"]["authorization"] = {
                "approval_required": auth_required,
                "approval_granted": auth_granted,
                "pending_id": pending_id
            }

            # If authorization was required but not granted, mark overall success as False
            if auth_required and not auth_granted:
                if overall_success:
                    overall_success = False
                    error_message = "Authorization required but not granted"

            # Planning was required but produced no executable plan: nothing was executed.
            planning_failed = planning_result.planning_required and (
                not planning_result.planning_successful or planning_result.plan is None
            )
            if planning_failed and overall_success:
                overall_success = False
                error_message = f"Planning failed: {planning_result.error or 'no plan produced'}"

            # Propagate execution failure to overall success: a planned cycle only
            # succeeds when the executor itself reports SUCCESS.
            if not skip_execution and planning_result.planning_required and planning_result.plan is not None:
                if execution_result.status != ExecutionStatus.SUCCESS:
                    if overall_success:
                        overall_success = False
                        error_message = f"Execution failed: {execution_result.status.value}"

            # Stage 11: V8 Verification
            verification_start = time.time()
            if not planning_result.planning_required:
                # Informational / Conversational request (no tool plan was executed)
                # Verify ground truth of informational response
                verification_info = self.verifier.verify_ground_truth(goal=user_input, observations=[])
                verified = bool(verification_info.get("verified", True))
                v_status = "NON_PLAN_VERIFIED" if verified else "NOT_VERIFIED"
                verification_result = {
                    "verified": verified,
                    "status": v_status,
                    "details": verification_info.get("details", "Direct informational response verified.")
                }
            elif planning_failed:
                # No plan was produced, so nothing was executed or verified.
                verification_result = {
                    "verified": False,
                    "status": "PLANNING_FAILED"
                }
            else:
                # Planned execution - derived strictly from the executor's own facts.
                # V8 executor contract: on SUCCESS, verification_state is "" unless a
                # step's verification failed (then status is NOT_VERIFIED /
                # VERIFICATION_FAILED); verified steps carry "VERIFIED" in their record.
                verification_state = execution_result.verification_state
                if verification_state == "VERIFIED":
                    verified = True
                    v_status = "VERIFIED"
                elif not verification_state and execution_result.status == ExecutionStatus.SUCCESS:
                    verified = True
                    step_records = tuple(getattr(execution_result, "steps", ()) or ())
                    if any(getattr(s, "verification_status", "") == "VERIFIED" for s in step_records):
                        v_status = "VERIFIED"
                    else:
                        v_status = "EXECUTED_NO_STEP_VERIFICATION_REQUIRED"
                else:
                    verified = False
                    v_status = verification_state or execution_result.status.value or "NOT_VERIFIED"
                verification_result = {
                    "verified": verified,
                    "status": v_status
                }

            verification_time = (time.time() - verification_start) * 1000
            result["stages"]["verification"] = {
                "completed": True,
                "verification_result": verification_result,
                "time_ms": verification_time
            }
            result["provenance"]["verification"] = {
                "verified": verification_result["verified"],
                "status": verification_result["status"]
            }

            if not verification_result["verified"]:
                overall_success = False
                error_message = f"Verification failed: {verification_result['status']}"

            # Stage 12: Response Generation
            response_start = time.time()
            if not planning_result.planning_required:
                # Conversational / Informational response (do not fabricate plan execution)
                if execution_result.response_text:
                    response_text = execution_result.response_text
                elif reasoning_result and getattr(reasoning_result, "reasoning_summary", None):
                    response_text = reasoning_result.reasoning_summary
                else:
                    response_text = f"I have processed your request: '{user_input}'."
            elif planning_failed:
                # Planning was required but no plan was produced: never claim execution.
                response_text = (
                    "I could not form an executable plan for your request, so nothing was executed."
                )
            elif skip_execution:
                response_text = (
                    f"Execution was blocked by cost guard: {cost_guard_error or 'insufficient resources or non-free provider'}. "
                    f"Nothing was executed."
                )
            else:
                # Planned execution response
                if execution_result.status == ExecutionStatus.SUCCESS:
                    if execution_result.response_text:
                        response_text = execution_result.response_text
                    else:
                        response_text = (
                            f"I have successfully executed the plan for your request. "
                            f"Execution ID: {execution_result.execution_id}"
                        )
                elif execution_result.status == ExecutionStatus.APPROVAL_REQUIRED:
                    plan_ref = execution_result.plan_hash[:8] if execution_result.plan_hash else 'unknown'
                    if pending_id:
                        pend_info = f" (Pending ID: {pending_id})"
                    else:
                        pend_info = f" The plan could not be held for approval ({stash_error or 'unavailable'})."
                    response_text = (
                        f"I have created a plan for your request, but it requires authorization before execution can proceed. "
                        f"Plan ID: {plan_ref}{pend_info}"
                    )
                elif execution_result.status == ExecutionStatus.BLOCKED:
                    response_text = (
                        "Execution was blocked by a safety policy before the plan could complete."
                    )
                elif execution_result.status == ExecutionStatus.VERIFICATION_FAILED:
                    response_text = (
                        f"I executed the plan for your request, but verification failed: {execution_result.verification_state}"
                    )
                else:
                    response_text = (
                        f"I was unable to execute your request due to: {execution_result.status.value}. "
                        f"If you believe this is in error, please check the system logs."
                    )

            response_time = (time.time() - response_start) * 1000
            result["stages"]["response_generation"] = {
                "completed": True,
                "response_text": response_text,
                "time_ms": response_time
            }

            # Stage 13: V9 Voice / Delivery Metadata
            # Deterministic metadata only, mirroring VoiceEngine._compute_delivery_style
            # with the frozen V9 pure functions. No VoiceEngine, TTS backend or audio
            # output is constructed, so no audio device is ever opened here.
            voice_start = time.time()
            from core.voice.delivery import (
                classify_response_category, get_delivery_profile,
                calculate_response_length_category,
            )
            from core.voice.prosody import ProsodyController
            from core.voice.personality import (
                SpeakingMode, DEFAULT_VOICE_PERSONALITY, TACTICAL_VOICE_PERSONALITY,
            )
            from core.voice.config import VoiceEngineConfig
            awaiting_approval = auth_required and not auth_granted
            delivery_cat = classify_response_category(
                text=response_text,
                is_error=(not overall_success and not awaiting_approval and not skip_execution),
                is_warning=(awaiting_approval or skip_execution),
            )
            delivery_profile = get_delivery_profile(delivery_cat)
            try:
                speaking_mode_enum = SpeakingMode[str(delivery_profile.speaking_mode).upper()]
            except KeyError:
                speaking_mode_enum = SpeakingMode.NORMAL
            voice_profile = getattr(VoiceEngineConfig.from_env(), "voice_profile", "default")
            personality = TACTICAL_VOICE_PERSONALITY if voice_profile == "tactical" else DEFAULT_VOICE_PERSONALITY
            length_category = calculate_response_length_category(response_text)
            speech_style = ProsodyController(personality).for_delivery(
                mode=speaking_mode_enum,
                delivery_profile=delivery_profile,
                length_category=length_category,
            )
            voice_time = (time.time() - voice_start) * 1000
            result["stages"]["voice_output"] = {
                "completed": True,
                "voice_processing_noted": True,
                "audio_played": False,
                "delivery_category": delivery_cat.value,
                "speaking_mode": delivery_profile.speaking_mode,
                "prosody_mode": speaking_mode_enum.value,
                "voice_profile": voice_profile,
                "length_category": length_category,
                "rate_mult": delivery_profile.rate_mult,
                "pitch_shift": delivery_profile.pitch_shift,
                "prosody": {
                    "rate": speech_style.rate,
                    "pitch": speech_style.pitch,
                    "volume": speech_style.volume,
                    "pause_before": speech_style.pause_before,
                    "pause_after": speech_style.pause_after,
                    "emphasis": speech_style.emphasis,
                },
                "time_ms": voice_time
            }
            result["provenance"]["voice_output"] = {
                "delivery_category": delivery_cat.value,
                "speaking_mode": delivery_profile.speaking_mode
            }

            # Stage 14: Outcome / Memory Update
            outcome_start = time.time()
            # Truthful cycle outcome classification (derived from facts above).
            if not planning_result.planning_required:
                cycle_outcome = "NON_PLAN_RESPONSE"
            elif planning_failed:
                cycle_outcome = "PLANNING_FAILED"
            elif skip_execution:
                cycle_outcome = "COST_BLOCKED"
            elif awaiting_approval and execution_result.status == ExecutionStatus.APPROVAL_REQUIRED:
                cycle_outcome = "AWAITING_AUTHORIZATION"
            elif execution_result.status == ExecutionStatus.SUCCESS and verification_result["verified"]:
                cycle_outcome = "EXECUTION_SUCCEEDED"
            else:
                cycle_outcome = "EXECUTION_FAILED"

            # Goal-state update: only an executor outcome is recorded on the owner's
            # matching ACTIVE registry goal. Pending approval, cost blocks and planning
            # failures never mutate goal state.
            goal_state_update = "NOT_APPLICABLE"
            if cycle_outcome in ("EXECUTION_SUCCEEDED", "EXECUTION_FAILED"):
                goal_state_update = self._record_goal_outcome(
                    owner_id,
                    getattr(planning_result.plan, "goal_id", ""),
                    cycle_outcome == "EXECUTION_SUCCEEDED",
                    execution_result.status.value,
                )
            elif planning_result.planning_required and planning_result.plan is not None:
                goal_state_update = "RETAINED"

            outcome_time = (time.time() - outcome_start) * 1000
            result["stages"]["outcome_memory_update"] = {
                "completed": True,
                "outcome_tracked": True,
                "cycle_outcome": cycle_outcome,
                "goal_state_update": goal_state_update,
                "time_ms": outcome_time
            }
            result["provenance"]["outcome_memory_update"] = {
                "cycle_outcome": cycle_outcome,
                "goal_state_update": goal_state_update
            }

            # Stage 15: V11.2 Experience Integration
            experience_start = time.time()
            # Only integrate experience when the V8 executor actually produced an outcome
            # for a real plan. Cost-blocked cycles (nothing executed) and plans awaiting
            # authorization (no outcome yet) must not create experience records.
            if cycle_outcome in ("EXECUTION_SUCCEEDED", "EXECUTION_FAILED"):
                try:
                    experience_result = self.experience_integration.integrate_execution_result(
                        execution_result=execution_result,
                        owner_id=owner_id,
                        session_id=session_id,
                        goal_plan=planning_result.plan
                    )
                except Exception as exp_err:
                    # Contain store failures: the execution outcome above stays truthful.
                    experience_result = None
                    overall_success = False
                    error_message = f"Experience integration failed: {type(exp_err).__name__}"
                experience_time = (time.time() - experience_start) * 1000
                result["stages"]["experience_integration"] = {
                    "completed": True,
                    "experience_required": True,
                    "experience_result": experience_result,
                    "time_ms": experience_time
                }
                result["provenance"]["experience_integration"] = {
                    "status": (
                        "ERROR" if experience_result is None
                        else experience_result.status.name if hasattr(experience_result, 'status') else "UNKNOWN"
                    ),
                    "experience_created": experience_result.experience is not None if hasattr(experience_result, 'experience') else False
                }
                # If experience integration failed for a planned execution, note it
                if hasattr(experience_result, 'status') and experience_result.status.name != "OK":
                    overall_success = False
                    error_message = f"Experience integration failed: {experience_result.status.name}"
            else:
                # No executor outcome: skip experience integration to prevent junk records
                skip_reason = {
                    "NON_PLAN_RESPONSE": "SKIPPED_NON_PLAN",
                    "PLANNING_FAILED": "SKIPPED_PLANNING_FAILED",
                    "COST_BLOCKED": "SKIPPED_COST_BLOCKED",
                    "AWAITING_AUTHORIZATION": "SKIPPED_PENDING_AUTHORIZATION",
                }.get(cycle_outcome, "SKIPPED")
                experience_time = (time.time() - experience_start) * 1000
                result["stages"]["experience_integration"] = {
                    "completed": True,
                    "experience_required": False,
                    "experience_result": None,
                    "time_ms": experience_time
                }
                result["provenance"]["experience_integration"] = {
                    "status": skip_reason,
                    "experience_created": False
                }

            # Set overall success based on whether we completed the pipeline successfully
            # We already tracked overall_success and set it to False if any critical stage failed
            result["success"] = overall_success
            if not overall_success and error_message:
                result["failure_reason"] = error_message
            result["response_text"] = response_text
            result["timing"]["total_time_ms"] = (time.time() - start_time) * 1000
            result["timing"]["context_fusion_ms"] = context_time
            result["timing"]["goal_understanding_ms"] = goal_time
            result["timing"]["reasoning_decision_ms"] = reasoning_time
            result["timing"]["planning_ms"] = planning_time
            result["timing"]["execution_ms"] = execution_time
            result["timing"]["authorization_ms"] = auth_time
            result["timing"]["verification_ms"] = verification_time
            result["timing"]["response_generation_ms"] = response_time
            result["timing"]["voice_output_ms"] = voice_time
            result["timing"]["outcome_memory_update_ms"] = outcome_time
            result["timing"]["experience_integration_ms"] = experience_time

            # Update last cycle time
            self.last_cycle_time = time.time()

        except Exception as e:
            # Handle any unexpected errors gracefully - failure containment
            overall_success = False
            result["success"] = False
            result["error"] = str(e)
            result["error_type"] = type(e).__name__
            result["timing"]["total_time_ms"] = (time.time() - start_time) * 1000
            result["stages"]["error"] = {
                "completed": True,
                "error": str(e),
                "error_type": type(e).__name__,
                "time_ms": (time.time() - start_time) * 1000
            }

        return result


# Global instance for convenience
v11_cognitive_orchestrator = V11CognitiveOrchestrator()


def process_v11_cognitive_cycle(
    user_input: str,
    owner_id: str,
    session_id: str = "",
    lang: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    project_id: Optional[str] = None,
    authorized_plan_hash: str = "",
    computer_session_id: str = ""
) -> Dict[str, Any]:
    """Convenience function for V11 cognitive orchestration."""
    return v11_cognitive_orchestrator.process_cognitive_cycle(
        user_input=user_input,
        owner_id=owner_id,
        session_id=session_id,
        lang=lang,
        context=context,
        project_id=project_id,
        authorized_plan_hash=authorized_plan_hash,
        computer_session_id=computer_session_id
    )


if __name__ == "__main__":
    # Simple test
    result = process_v11_cognitive_cycle(
        user_input="Create a plan to learn Python programming",
        owner_id="test_user",
        session_id="test_session"
    )
    print(f"V11 Cognitive cycle result: {result['success']}")
    print(f"Response: {result['response_text']}")
    if result["stages"].get("planning"):
        print(f"Planning required: {result['stages']['planning']['planning_required']}")
        print(f"Planning successful: {result['stages']['planning']['planning_successful']}")
    if result["stages"].get("execution"):
        exec_result = result["stages"]["execution"]["execution_result"]
        print(f"Execution status: {exec_result.status.value if hasattr(exec_result, 'status') else 'unknown'}")
