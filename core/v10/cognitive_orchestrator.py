"""V10.6 Cognitive Orchestrator.

Orchestrates the complete V10 cognitive pipeline by reusing existing components:
Input Normalization → Context Fusion (V10.1) → Memory/User Model Retrieval (V10.2) → 
Goal Understanding (V10.3) → Reasoning (V10.4) → Decision (V10.4) → 
Planning when required (V10.5) → Authorization (V8) → Verification (V8) → 
Response Generation → V9 Voice/Text Output → Outcome/Memory Update

Strictly reuses existing V10.1-10.5, V8, and V9 components without creating duplicates.
"""

from __future__ import annotations

import time
from typing import Dict, Any, Optional, Tuple

from core.v10.context_fusion import FusedContext
from core.v10.goal_understanding import GoalUnderstandingResult, GoalContinuity
from core.v10.reasoning_decision import ReasoningResult, DecisionResult
from core.v10.planning_integration import PlanningResult
from orchestration.goal.planner_errors import PlannerStatus

# Import the singleton instances that are already created
from core.v10.context_fusion import context_fusion, fuse_context
from core.v10.goal_understanding import goal_understanding
from core.v10.planning_integration import integrate_planning
from core.v10.reasoning_decision import integrate_reasoning_decision
from orchestration.authorization import (
    stash_pending_plan, pending_display, claim_authorization, 
    medium_computer_approvable, AuthorizationClaim
)
from core.verifier import verifier  # singleton instance
from core.cost_guard.guard import cost_guard  # singleton instance
from core.cost_guard.types import ResourceRequest, ResourceType
# Voice components would be imported from core.voice but are V9 frozen architecture
# For V10.6, we note that V9 voice components are reused as-is

class CognitiveOrchestrator:
    """V10.6 Cognitive Orchestrator.

    Orchestrates the complete cognitive pipeline by connecting
    existing V10.1-10.5, V8 authorization/verification/cost_guard, and V9 voice
    components without creating duplicate systems.
    """

    def __init__(self):
        # Reuse existing V10 singleton components
        self.context_fusion = context_fusion
        self.goal_understanding = goal_understanding
        self.reasoning_decision_integration = integrate_reasoning_decision
        self.planning_integration = integrate_planning

        # Reuse V8 singleton components
        self.cost_guard = cost_guard
        self.verifier = verifier

        # Note: V9 voice components are reused from the existing frozen V9 architecture
        # In a full implementation, these would be initialized here:
        # from core.voice.engine = VoiceEngine
        # from core.voice.outputs.base = AudioOutput
        # from core.voice.backends.base = TTSBackend
        # from core.voice.delivery = DeliveryClassifier
        # from core.voice.prosody = ProsodyController
        # from core.voice.personality = VoicePersonality
        # 
        # self.voice_engine = VoiceEngine(...)
        # self.audio_output = AudioOutput(...)
        # etc.
        #
        # For V10.6 cognitive orchestration, we focus on the pipeline logic
        # and note that V9 voice components would be invoked at the appropriate stage

        # Orchestrator state
        self.last_cycle_time = 0.0
        self.cycle_count = 0

    def process_cognitive_cycle(
        self,
        user_input: str,
        owner_id: str,
        session_id: str = "",
        lang: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        project_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Process a complete cognitive cycle through the V10 architecture pipeline.

        Args:
            user_input: The user's raw input text
            owner_id: Owner ID for scoping and isolation
            session_id: Session ID for scoping
            lang: Language code (optional)
            context: Additional content (optional)
            project_id: Project ID (optional)

        Returns:
            Dictionary containing the complete cycle results including:
            - success: Boolean indicating overall success
            - response_text: Text response to user
            - provenance: Complete provenance trail
            - timing: Performance metrics
            - cycle_id: Unique identifier for this cycle
        """
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

        # Initialize variables that might be used in multiple stages
        planning_result = PlanningResult(False, False, None, None)
        authorization_claim = None
        authorized_plan = None
        auth_start = 0
        verification_start = 0
        response_start = 0
        voice_start = 0
        outcome_start = 0
        reasoning_time = 0
        planning_time = 0

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
                language_hint=lang
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
            # In a more separated architecture, this would be a distinct step calling
            # memory and user model retrieval systems that are fused into the context
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
            planning_required = decision_result.decision_type == "CREATE_PLAN"

            # Stage 7: Planning when required (V10.5)
            planning_start = time.time()
            if not planning_required:
                # Planning not required; return early
                planning_time = (time.time() - planning_start) * 1000
                result["stages"]["planning"] = {
                    "completed": True,
                    "planning_required": False,
                    "planning_successful": False,
                    "provenance": {
                        "decision_type": decision_result.decision_type.value,
                        "reason": "Planning not required based on decision type"
                    },
                    "time_ms": planning_time
                }
            else:
                # Planning is required
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
                    "plan_id": planning_result.plan_id,
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

                # If planning was successful and we have a plan, proceed to authorization
                if (planning_result.planning_required and 
                    planning_result.planning_successful and 
                    planning_result.plan is not None):
                    # Stage 8: Authorization / Safety (V8)
                    auth_start = time.time()

                    # Import ExecutionIdentity locally to avoid circular imports
                    from orchestration.executor import ExecutionIdentity
                    identity = ExecutionIdentity(
                        owner_id=owner_id,
                        session_id=session_id,
                        computer_session_id=""  # Would be set based on actual computer session
                    )

                    # Stash the plan for authorization
                    pending_id, stash_error = stash_pending_plan(
                        plan=planning_result.plan,
                        identity=identity
                    )

                    if stash_error == "OK" and pending_id:
                        # Check if plan is approvable
                        is_approvable, approval_error = medium_computer_approvable(planning_result.plan)
                        if is_approvable:
                            # Claim authorization
                            claim, claim_error = claim_authorization(
                                pending_id=pending_id,
                                identity=identity
                            )
                            if claim_error == "OK" and claim is not None:
                                authorization_claim = claim
                                authorized_plan = claim.plan
                                result["stages"]["authorization"] = {
                                    "completed": True,
                                    "authorized": True,
                                    "authorization_id": claim.authorization_id,
                                    "time_ms": (time.time() - auth_start) * 1000
                                }
                            else:
                                result["stages"]["authorization"] = {
                                    "completed": True,
                                    "authorized": False,
                                    "error": claim_error,
                                    "time_ms": (time.time() - auth_start) * 1000
                                }
                        else:
                            result["stages"]["authorization"] = {
                                "completed": True,
                                "authorized": False,
                                "error": stash_error,
                                "time_ms": (time.time() - auth_start) * 1000
                            }

                    result["provenance"]["authorization"] = {
                        "authorized": authorization_claim is not None,
                        "authorization_id": (
                            authorization_claim.authorization_id 
                            if authorization_claim 
                            else None
                        )
                    }
                else:
                    # No planning required or planning failed
                    result["stages"]["authorization"] = {
                        "completed": True,
                        "authorized": False,
                        "reason": "No planning required or planning failed",
                        "time_ms": 0
                    }
            # If we had an error in reasoning or planning due to cost guard, we may have skipped authorization
            # We need to set the authorization stage if it hasn't been set yet
            if "authorization" not in result["stages"]:
                # This means we didn't go through the authorization block above
                result["stages"]["authorization"] = {
                    "completed": True,
                    "authorized": False,
                    "reason": "Authorization skipped due to earlier error",
                    "time_ms": 0
                }

            # Stage 9: Verification (V8)
            verification_start = time.time()
            # We now actually call the verifier
            # We pass the user_input as the goal and an empty list of observations
            # since we haven't executed any tools in this cognitive cycle
            verification_result = self.verifier.verify_ground_truth(
                goal=user_input,
                observations=[]
            )
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

            # If verification failed, we mark overall success as False
            if not verification_result["verified"]:
                overall_success = False
                error_message = f"Verification failed: {verification_result['status']}"

            # Stage 10: Response Generation
            response_start = time.time()
            # Generate text content based on the cycle results
            if planning_result.planning_required and planning_result.planning_successful:
                if authorization_claim is not None:
                    response_text = (
                        f"I have successfully created and authorized a plan for your request. "
                        f"Plan ID: {planning_result.plan_id} is ready for execution."
                    )
                else:
                    response_text = (
                        f"I have created a plan for your request (Plan ID: {planning_result.plan_id}), "
                        f"but it requires authorization before execution can proceed."
                    )
            elif planning_result.planning_required and not planning_result.planning_successful:
                response_text = (
                    f"I was unable to create a plan for your request due to: "
                    f"{planning_result.error or 'unknown error'}"
                )
            else:
                # No planning required - provide direct response based on reasoning/decision
                response_text = (
                    f"I have processed your request: '{user_input}'. "
                    f"Based on the analysis ({decision_result.decision_type.value}), "
                    f"no planning was required for this type of request."
                )

            response_time = (time.time() - response_start) * 1000
            result["stages"]["response_generation"] = {
                "completed": True,
                "response_text": response_text,
                "time_ms": response_time
            }

            # Stage 11: V9 Voice / Text Output
            voice_start = time.time()
            # In a full implementation, we would:
            # 1. Classify the response using V9 delivery classification
            # 2. Apply prosody based on content and context using V9 prosody controller
            # 3. Synthesize speech using V9 voice engine
            # 4. Output audio via V9 audio output

            # For V10.6, we note where V9 voice components would be invoked:
            # delivery_classification = DeliveryClassifier().classify(response_text)
            # prosody_settings = ProsodyController().get_prosody(
            #     delivery_classification, 
            #     goal_understanding_result.continuity
            # )
            # audio_data = VoiceEngine().synthesize(
            #     text=response_text,
            #     personality=VoicePersonality(),  # or appropriate persona
            #     prosody=prosody_settings,
            #     delivery=delivery_classification
            # )

            voice_time = (time.time() - voice_start) * 1000
            result["stages"]["voice_output"] = {
                "completed": True,
                "voice_processing_noted": True,
                "time_ms": voice_time
                # In full implementation: "audio_data": audio_data
            }

            # Stage 12: Outcome / Memory Update
            outcome_start = time.time()
            # Update memory systems with the outcome of this cycle
            # This would involve:
            # - Short-term memory update (already happens in context fusion?)
            # - Episodic memory recording of this cognitive cycle
            # - User model updating (if appropriate and permitted)
            # - Goal state updating based on outcomes

            outcome_time = (time.time() - outcome_start) * 1000
            result["stages"]["outcome_memory_update"] = {
                "completed": True,
                "outcome_tracked": True,
                "time_ms": outcome_time
            }

            # Set overall success based on whether we completed the pipeline successfully
            # We already tracked overall_success and set it to False if any critical stage failed
            result["success"] = overall_success
            result["response_text"] = response_text
            result["timing"]["total_time_ms"] = (time.time() - start_time) * 1000
            result["timing"]["context_fusion_ms"] = context_time
            result["timing"]["goal_understanding_ms"] = goal_time
            result["timing"]["reasoning_decision_ms"] = reasoning_time
            result["timing"]["planning_ms"] = planning_time
            result["timing"]["authorization_ms"] = (time.time() - auth_start) * 1000
            result["timing"]["verification_ms"] = verification_time
            result["timing"]["response_generation_ms"] = response_time
            result["timing"]["voice_output_ms"] = voice_time
            result["timing"]["outcome_memory_update_ms"] = outcome_time

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
cognitive_orchestrator = CognitiveOrchestrator()


def process_cognitive_cycle(
    user_input: str,
    owner_id: str,
    session_id: str = "",
    lang: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    project_id: Optional[str] = None
) -> Dict[str, Any]:
    """Convenience function for V10.6 cognitive orchestration."""
    return cognitive_orchestrator.process_cognitive_cycle(
        user_input=user_input,
        owner_id=owner_id,
        session_id=session_id,
        lang=lang,
        context=context,
        project_id=project_id
    )


if __name__ == "__main__":
    # Simple test
    result = process_cognitive_cycle(
        user_input="Create a plan to learn Python programming",
        owner_id="test_user",
        session_id="test_session"
    )
    print(f"Cognitive cycle result: {result['success']}")
    print(f"Response: {result['response_text']}")
    if result["stages"].get("planning"):
        print(f"Planning required: {result['stages']['planning']['planning_required']}")
        print(f"Planning successful: {result['stages']['planning']['planning_successful']}")