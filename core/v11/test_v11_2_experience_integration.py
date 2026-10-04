"""Test V11.2 Experience Integration."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, Tuple

# Set environment variables to enable V8 goal experience store BEFORE importing any modules
# that might import the config module
os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

# Add the project root to sys.path so we can import core modules
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.v11.experience_integration import ExperienceIntegration
from orchestration.executor import ExecutionResult, ExecutionStatus as V8ExecutionStatus, _result, StepExecutionRecord
from orchestration.goal.plan_types import GoalPlan
from orchestration.experience.types import SOURCE_GOAL_ID_PREFIX


def test_experience_integration_basic():
    """Test basic experience integration from execution result."""
    integration = ExperienceIntegration()
    
    # Create a mock execution result using the V8 executor's _result function
    execution_steps = (
        StepExecutionRecord(
            step_id="step_1",
            capability_id="test.capability",
            action="test_action",
            status="completed",
            attempts=1,
            verification_status="VERIFIED"
        ),
    )
    
    execution_result = _result(
        plan=None,
        status=V8ExecutionStatus.SUCCESS,
        steps=execution_steps,
        completed=("step_1",),
        verification="VERIFIED",
        response_text="Test response",
        start=1000,
        end=2000
    )
    
    # Test integration
    owner_id = "test_user"
    session_id = "test_session"
    goal_plan = GoalPlan(
        plan_id="test_plan",
        goal_id="test_goal",
        schema_version="1.0",
        owner_id=owner_id,
        session_id=session_id,
        computer_session_id="",
        steps=(),
        plan_risk="low",
        approval_required=False,
        provenance="test",
        plan_hash="test_hash_123"
    )
    
    experience_result = integration.integrate_execution_result(
        execution_result=execution_result,
        owner_id=owner_id,
        session_id=session_id,
        goal_plan=goal_plan
    )
    
    # Verify the result
    assert experience_result.status.name == "OK", f"Expected OK status, got {experience_result.status}"
    assert experience_result.experience is not None, "Experience should be created"
    assert experience_result.experience.owner_id == owner_id, f"Owner ID should match: {experience_result.experience.owner_id} != {owner_id}"
    assert len(experience_result.experience.title) > 0, "Experience should have a title"
    assert experience_result.experience.outcome.value == "COMPLETED", "Successful execution should map to COMPLETED outcome"
    assert isinstance(experience_result.experience.step_summary, tuple), "Step summary should be a tuple"
    assert isinstance(experience_result.experience.blockers, tuple), "Blockers should be a tuple"
    assert isinstance(experience_result.experience.tags, tuple), "Tags should be a tuple"
    assert isinstance(experience_result.experience.user_note, str), "User note should be a string"
    actual_source_goal_id = experience_result.experience.source_goal_id
    expected_source_goal_id = SOURCE_GOAL_ID_PREFIX + "test_goal"  # ag_test_goal
    assert actual_source_goal_id == expected_source_goal_id, f"Source goal ID should match: expected '{expected_source_goal_id}', got '{actual_source_goal_id}'"


def test_experience_integration_failed_execution():
    """Test experience integration with failed execution."""
    integration = ExperienceIntegration()
    
    # Create a mock execution result for a failed execution using V8 executor's _result function
    execution_steps = (
        StepExecutionRecord(
            step_id="step_1",
            capability_id="test.capability",
            action="test_action",
            status="failed",
            attempts=1,
            verification_status="FAILED"
        ),
    )
    
    execution_result = _result(
        plan=None,
        status=V8ExecutionStatus.FAILED,
        steps=execution_steps,
        completed=(),
        failed="step_1",
        verification="FAILED",
        response_text="Test failed response",
        start=1000,
        end=1500
    )
    
    # Test integration
    owner_id = "test_user"
    session_id = "test_session"
    
    experience_result = integration.integrate_execution_result(
        execution_result=execution_result,
        owner_id=owner_id,
        session_id=session_id
    )
    
    # Verify the result
    assert experience_result.status.name == "OK", f"Expected OK status, got {experience_result.status}"
    assert experience_result.experience is not None, "Experience should be created"
    assert experience_result.experience.owner_id == owner_id, "Owner ID should match"
    assert experience_result.experience.outcome.value == "ABANDONED", "Failed execution should map to ABANDONED outcome"
    assert len(experience_result.experience.blockers) > 0, "Should have blockers for failed execution"


def test_experience_integration_timeout():
    """Test experience integration with timeout execution."""
    integration = ExperienceIntegration()
    
    # Create a mock execution result for a timeout execution using V8 executor's _result function
    execution_result = _result(
        plan=None,
        status=V8ExecutionStatus.TIMEOUT,
        steps=(),
        completed=(),
        verification="TIMEOUT",
        response_text="Test timeout response",
        start=1000,
        end=5000  # 4 seconds duration
    )
    
    # Test integration
    owner_id = "test_user"
    session_id = "test_session"
    
    experience_result = integration.integrate_execution_result(
        execution_result=execution_result,
        owner_id=owner_id,
        session_id=session_id
    )
    
    # Verify the result
    assert experience_result.status.name == "OK", f"Expected OK status, got {experience_result.status}"
    assert experience_result.experience is not None, "Experience should be created"
    assert experience_result.experience.owner_id == owner_id, "Owner ID should match"
    assert experience_result.experience.outcome.value == "PARTIAL", "Timeout execution should map to PARTIAL outcome"


def test_experience_integration_no_goal_plan():
    """Test experience integration without goal plan."""
    integration = ExperienceIntegration()
    
    # Create a mock execution result using the V8 executor's _result function
    execution_result = _result(
        plan=None,
        status=V8ExecutionStatus.SUCCESS,
        steps=(),
        completed=(),
        verification="VERIFIED",
        response_text="Test response without goal",
        start=1000,
        end=2000
    )
    
    # Test integration without goal plan
    owner_id = "test_user"
    session_id = "test_session"
    
    experience_result = integration.integrate_execution_result(
        execution_result=execution_result,
        owner_id=owner_id,
        session_id=session_id,
        goal_plan=None
    )
    
    # Verify the result
    assert experience_result.status.name == "OK", f"Expected OK status, got {experience_result.status}"
    assert experience_result.experience is not None, "Experience should be created"
    assert experience_result.experience.owner_id == owner_id, "Owner ID should match"
    assert experience_result.experience.source_goal_id == "", "Source goal ID should be empty when no goal plan"


def test_experience_integration_empty_owner_id():
    """Test experience integration with empty owner ID."""
    integration = ExperienceIntegration()
    
    # Create a mock execution result using the V8 executor's _result function
    execution_result = _result(
        plan=None,
        status=V8ExecutionStatus.SUCCESS,
        steps=(),
        completed=(),
        verification="VERIFIED",
        response_text="Test response",
        start=1000,
        end=2000
    )
    
    # Test integration with empty owner ID
    experience_result = integration.integrate_execution_result(
        execution_result=execution_result,
        owner_id="",  # Empty owner ID
        session_id="test_session"
    )
    
    # Should reject due to empty owner ID
    assert experience_result.status.name == "REJECTED", f"Expected REJECTED status for empty owner ID, got {experience_result.status}"
    assert experience_result.experience is None, "No experience should be created for empty owner ID"


if __name__ == "__main__":
    # Run tests
    test_experience_integration_basic()
    print("PASS: test_experience_integration_basic")
    
    test_experience_integration_failed_execution()
    print("PASS: test_experience_integration_failed_execution")
    
    test_experience_integration_timeout()
    print("PASS: test_experience_integration_timeout")
    
    test_experience_integration_no_goal_plan()
    print("PASS: test_experience_integration_no_goal_plan")
    
    test_experience_integration_empty_owner_id()
    print("PASS: test_experience_integration_empty_owner_id")
    
    print("\nAll V11.2 Experience Integration tests passed!")