"""V11.2 Experience Integration Layer.

Integrates V11.1 ExecutionResults with the V8 Goal Experience system.
Converts execution outcomes into structured experiences for learning.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple, Dict, Any, List

from core.v11.execution_layer import ExecutionResult
from orchestration.experience.store import create_experience
from orchestration.experience.types import (
    ExperienceResult,
    ExperienceResultStatus,
    GoalExperience,
    Outcome,
    ExperienceStatus,
    SCHEMA_VERSION as EXPERIENCE_SCHEMA_VERSION,
    SOURCE_GOAL_ID_PREFIX,
    REGISTRY_GOAL_ID_PREFIX
)
from orchestration.executor import ExecutionStatus as V8ExecutionStatus
from orchestration.goal.plan_types import GoalPlan
from orchestration.goal.plan_registry import PLAN_SCHEMA_VERSION


class ExperienceIntegration:
    """V11.2 Experience Integration Layer.
    
    Converts V11.1 ExecutionResults into V8 Goal Experiences for learning.
    Integrates with existing V8 experience system while maintaining
    strict owner-scoped state and security boundaries.
    """

    def __init__(self):
        """Initialize the experience integration layer."""
        pass

    def integrate_execution_result(
        self,
        execution_result: ExecutionResult,
        owner_id: str,
        session_id: str = "",
        goal_plan: Optional[GoalPlan] = None
    ) -> ExperienceResult:
        """Integrate an execution result into the V8 experience system.
        
        Args:
            execution_result: The result from V11.1 execution layer
            owner_id: Owner ID for scoping and isolation
            session_id: Session ID for scoping
            goal_plan: Optional goal plan that was executed
            
        Returns:
            ExperienceResult from creating the experience
        """
        # Validate inputs
        if not owner_id or not isinstance(owner_id, str):
            return ExperienceResult(ExperienceResultStatus.REJECTED)
        
        # Convert V11 execution status to V8 experience outcome
        outcome = self._map_execution_status_to_outcome(execution_result.status)
        
        # Generate experience title from goal or execution info
        title = self._generate_experience_title(execution_result, goal_plan)
        
        # Generate step summary from execution steps
        step_summary = self._generate_step_summary(execution_result)
        
        # Generate blockers from failed steps or verification issues
        blockers = self._generate_blockers(execution_result)
        
        # Generate user note with execution details
        user_note = self._generate_user_note(execution_result, session_id)
        
        # Generate tags for categorization
        tags = self._generate_tags(execution_result, goal_plan)
        
        # Generate source goal ID from goal plan if available
        source_goal_id = self._generate_source_goal_id(goal_plan)
        
        # Prepare experience fields
        fields: Dict[str, Any] = {
            "owner_id": owner_id,
            "title": title,
            "outcome": outcome.value,  # Convert enum to string for validation
            "step_summary": step_summary,
            "blockers": blockers,
            "user_note": user_note,
            "tags": tags,
            "source_goal_id": source_goal_id,
            "schema_version": EXPERIENCE_SCHEMA_VERSION,  # Use experience schema version
        }
        
        # Create the experience using V8 experience store
        experience_result = create_experience(owner_id, fields)
        
        return experience_result

    def _map_execution_status_to_outcome(self, status: V8ExecutionStatus) -> Outcome:
        """Map V8 execution status to V8 experience outcome.
        
        Args:
            status: The execution status from V11.1 execution
            
        Returns:
            The corresponding V8 experience outcome
        """
        # Map execution outcomes to experience outcomes
        if status == V8ExecutionStatus.SUCCESS:
            return Outcome.COMPLETED
        elif status in (
            V8ExecutionStatus.FAILED,
            V8ExecutionStatus.VERIFICATION_FAILED,
            V8ExecutionStatus.PRECONDITION_FAILED,
            V8ExecutionStatus.STEP_FAILED,
            V8ExecutionStatus.ACTION_UNAVAILABLE,
            V8ExecutionStatus.CAPABILITY_UNAVAILABLE,
            V8ExecutionStatus.AUTHORIZATION_INVALID,
            V8ExecutionStatus.AUTHORIZATION_EXPIRED,
            V8ExecutionStatus.AUTHORIZATION_REVOKED,
            V8ExecutionStatus.AUTHORIZATION_CONSUMED,
            V8ExecutionStatus.RISK_NOT_APPROVABLE,
        ):
            return Outcome.ABANDONED  # Treat failures as abandoned for experience
        elif status in (
            V8ExecutionStatus.TIMEOUT,
            V8ExecutionStatus.LOCAL_MODEL_TIMEOUT,
            V8ExecutionStatus.SESSION_UNAVAILABLE,
        ):
            return Outcome.PARTIAL  # Timeouts and session issues as partial
        elif status in (
            V8ExecutionStatus.V8_DISABLED,
            V8ExecutionStatus.IDENTITY_REQUIRED,
            V8ExecutionStatus.PLAN_HASH_MISMATCH,
            V8ExecutionStatus.INVALID_PLAN,
            V8ExecutionStatus.APPROVAL_REQUIRED,
        ):
            # These are pre-execution failures, treat as abandoned
            return Outcome.ABANDONED
        else:
            # Default to abandoned for any other status
            return Outcome.ABANDONED

    def _generate_experience_title(
        self, 
        execution_result: ExecutionResult, 
        goal_plan: Optional[GoalPlan]
    ) -> str:
        """Generate a descriptive title for the experience.
        
        Args:
            execution_result: The execution result
            goal_plan: Optional goal plan that was executed
            
        Returns:
            A descriptive title for the experience
        """
        # Try to get title from goal plan first
        if goal_plan and hasattr(goal_plan, 'goal_id') and goal_plan.goal_id:
            # Use goal ID as basis for title if it's descriptive
            goal_id = goal_plan.goal_id
            if goal_id and not goal_id.startswith(('ag_', 'rg_')):  # Not auto-generated
                return f"Goal execution: {goal_id}"
        
        # Fall back to execution ID based title
        if execution_result.execution_id:
            return f"Execution {execution_result.execution_id[:8]}"
        
        # Last resort
        return f"Execution {execution_result.status.value}"

    def _generate_step_summary(self, execution_result: ExecutionResult) -> Tuple[str, ...]:
        """Generate step summary from execution steps.
        
        Args:
            execution_result: The execution result
            
        Returns:
            Tuple of step summary strings
        """
        if not execution_result.steps:
            return ()
        
        step_summaries = []
        for step in execution_result.steps:
            # Create a concise summary of each step
            summary_parts = []
            if step.capability_id:
                summary_parts.append(step.capability_id)
            if step.action:
                summary_parts.append(step.action)
            if step.status and step.status != "UNKNOWN":
                summary_parts.append(f"status:{step.status}")
            
            if summary_parts:
                step_summaries.append(" ".join(summary_parts))
            else:
                step_summaries.append(f"step_{step.step_id}")
        
        # Limit to max steps as defined in experience types
        from orchestration.experience.types import MAX_STEPS
        return tuple(step_summaries[:MAX_STEPS])

    def _generate_blockers(self, execution_result: ExecutionResult) -> Tuple[str, ...]:
        """Generate blockers from execution result.
        
        Args:
            execution_result: The execution result
            
        Returns:
            Tuple of blocker strings
        """
        blockers = []
        
        # Add verification issues as blockers
        if execution_result.verification_state:
            if execution_result.verification_state != "VERIFIED":
                blockers.append(f"verification:{execution_result.verification_state}")
        
        # Add failed step as blocker if applicable
        if execution_result.failed_step_id:
            blockers.append(f"failed_step:{execution_result.failed_step_id}")
        
        # Add specific error conditions
        if execution_result.status == V8ExecutionStatus.TIMEOUT:
            blockers.append("timeout")
        elif execution_result.status == V8ExecutionStatus.VERIFICATION_FAILED:
            blockers.append("verification_failed")
        elif execution_result.status == V8ExecutionStatus.AUTHORIZATION_INVALID:
            blockers.append("authorization_invalid")
        elif execution_result.status == V8ExecutionStatus.SESSION_UNAVAILABLE:
            blockers.append("session_unavailable")
        
        # Limit to max blockers as defined in experience types
        from orchestration.experience.types import MAX_BLOCKERS
        return tuple(blockers[:MAX_BLOCKERS])

    def _generate_user_note(self, execution_result: ExecutionResult, session_id: str) -> str:
        """Generate user note with execution details.
        
        Args:
            execution_result: The execution result
            session_id: Session ID
            
        Returns:
            User note string (sanitized to avoid secrets)
        """
        note_parts = []
        
        # Add session info if available
        if session_id:
            note_parts.append(f"session:{session_id[:16]}")  # Limit session ID length
        
        # Add timing info
        if execution_result.started_unix_ms and execution_result.ended_unix_ms:
            duration_ms = execution_result.ended_unix_ms - execution_result.started_unix_ms
            note_parts.append(f"duration_ms:{duration_ms}")
        
        # Add response text length (but not the content to avoid secrets)
        if execution_result.response_text:
            note_parts.append(f"response_length:{len(execution_result.response_text)}")
        
        # Add completion stats
        if execution_result.completed_step_ids:
            note_parts.append(f"completed_steps:{len(execution_result.completed_step_ids)}")
        
        if execution_result.failed_step_id:
            note_parts.append("has_failed_step:true")
        
        # Join and limit length to avoid excessive notes
        note = " | ".join(note_parts)
        # Limit to reasonable length (experience types limit user_note to 200 chars)
        return note[:200] if note else ""

    def _generate_tags(self, execution_result: ExecutionResult, goal_plan: Optional[GoalPlan]) -> Tuple[str, ...]:
        """Generate tags for categorization.
        
        Args:
            execution_result: The execution result
            goal_plan: Optional goal plan that was executed
            
        Returns:
            Tuple of tag strings
        """
        tags = ["v11_execution"]  # Mark as V11 execution experience
        
        # Add status-based tag
        tags.append(f"status_{execution_result.status.value.lower()}")
        
        # Add goal plan info if available
        if goal_plan:
            if hasattr(goal_plan, 'goal_id') and goal_plan.goal_id:
                # Only add if it looks like a meaningful goal ID (not auto-generated)
                goal_id = goal_plan.goal_id
                if goal_id and not goal_id.startswith(('ag_', 'rg_', 'ge_')):
                    tags.append(f"goal_{goal_id[:16]}")  # Limit length
            
            if hasattr(goal_plan, 'plan_id') and goal_plan.plan_id:
                tags.append(f"plan_{goal_plan.plan_id[:16]}")  # Limit length
        
        # Add capability tags from steps
        if execution_result.steps:
            capabilities = set()
            for step in execution_result.steps:
                if step.capability_id:
                    # Normalize capability ID for tag
                    cap_tag = step.capability_id.lower().replace('-', '_').replace('.', '_')
                    # Only keep alphanumeric and underscore
                    cap_tag = ''.join(c for c in cap_tag if c.isalnum() or c == '_')
                    if cap_tag:
                        capabilities.add(cap_tag)
            
            # Add capability tags (limit to reasonable number)
            for cap in list(capabilities)[:3]:  # Limit to 3 capability tags
                tags.append(f"cap_{cap}")
        
        # Limit to max tags as defined in experience types
        from orchestration.experience.types import MAX_TAGS
        return tuple(tags[:MAX_TAGS])

    def _generate_source_goal_id(self, goal_plan: Optional[GoalPlan]) -> str:
        """Generate source goal ID from goal plan.
        
        Args:
            goal_plan: Optional goal plan that was executed
            
        Returns:
            Source goal ID string formatted for V8 experience store
        """
        if goal_plan and hasattr(goal_plan, 'goal_id') and goal_plan.goal_id:
            goal_id = goal_plan.goal_id
            # Only return if it looks like a valid goal ID
            if goal_id and isinstance(goal_id, str):
                # Format for V8 experience store: must start with ag_ or rg_
                # If it doesn't already have a valid prefix, add ag_ prefix for legacy goals
                if not (goal_id.startswith(REGISTRY_GOAL_ID_PREFIX) or goal_id.startswith(SOURCE_GOAL_ID_PREFIX)):
                    goal_id = SOURCE_GOAL_ID_PREFIX + goal_id
                # Limit to max length
                return goal_id[:64]
        return ""  # Empty string if no valid goal ID