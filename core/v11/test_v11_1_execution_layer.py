"""Test suite for V11.1 Execution Layer."""
import sys
from pathlib import Path

# Add the project root to sys.path so we can import core modules
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from unittest.mock import Mock, patch

from core.v10.planning_integration import PlanningResult
from core.v11.execution_layer import ExecutionLayer, execute_plan_from_planning_result
from orchestration.executor_errors import ExecutionStatus
from orchestration.goal.plan_types import GoalPlan


def test_execution_layer_no_planning_required():
    """Test that when planning is not required, no execution occurs."""
    # Arrange
    planning_result = PlanningResult(
        planning_required=False,
        planning_successful=False,
        plan=None,
        error=None
    )
    execution_layer = ExecutionLayer()
    
    # Act
    result = execution_layer.execute_plan(
        planning_result=planning_result,
        owner_id="test_owner",
        session_id="test_session"
    )
    
    # Assert
    # When no execution occurs, we should get an ExecutionResult with SUCCESS status
    # (indicating the planning phase completed successfully, even though no execution happened)
    assert result.status == ExecutionStatus.SUCCESS


def test_execution_layer_planning_not_successful():
    """Test that when planning is not successful, no execution occurs."""
    # Arrange
    planning_result = PlanningResult(
        planning_required=True,
        planning_successful=False,
        plan=None,
        error="Planning failed due to invalid input"
    )
    execution_layer = ExecutionLayer()
    
    # Act
    result = execution_layer.execute_plan(
        planning_result=planning_result,
        owner_id="test_owner",
        session_id="test_session"
    )
    
    # Assert
    assert result.status == ExecutionStatus.SUCCESS


def test_execution_layer_successful_execution():
    """Test that when planning succeeds, execution is attempted using V8 executor."""
    # Arrange
    mock_plan = Mock(spec=GoalPlan)
    mock_plan.plan_id = "test_plan_123"
    mock_plan.goal_id = "test_goal_456"
    mock_plan.plan_hash = "test_hash_789"
    
    planning_result = PlanningResult(
        planning_required=True,
        planning_successful=True,
        plan=mock_plan,
        error=None
    )
    
    # Mock execution result from V8 executor
    mock_execution_result = Mock()
    mock_execution_result.status = ExecutionStatus.SUCCESS
    mock_execution_result.execution_id = "exec_123"
    mock_execution_result.goal_id = "test_goal_456"
    mock_execution_result.plan_hash = "test_hash_789"
    
    execution_layer = ExecutionLayer()
    
    # Act
    with patch("core.v11.execution_layer.execute_plan", return_value=mock_execution_result) as mock_execute:
        result = execution_layer.execute_plan(
            planning_result=planning_result,
            owner_id="test_owner",
            session_id="test_session"
        )
        
        # Assert that the V8 executor was called with the correct parameters
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args
        assert call_args[1]["identity"].owner_id == "test_owner"
        assert call_args[1]["identity"].session_id == "test_session"
        assert call_args[1]["plan"] == mock_plan
    
    # Assert
    assert result.status == ExecutionStatus.SUCCESS
    assert result.execution_id == "exec_123"


def test_execution_layer_failed_execution():
    """Test that when execution fails, the failure is propagated correctly."""
    # Arrange
    mock_plan = Mock(spec=GoalPlan)
    mock_plan.plan_id = "test_plan_123"
    
    planning_result = PlanningResult(
        planning_required=True,
        planning_successful=True,
        plan=mock_plan,
        error=None
    )
    
    # Mock execution result from V8 executor indicating failure
    mock_execution_result = Mock()
    mock_execution_result.status = ExecutionStatus.FAILED
    mock_execution_result.execution_id = "exec_456"
    mock_execution_result.goal_id = "test_goal_789"
    mock_execution_result.plan_hash = "test_hash_012"
    
    execution_layer = ExecutionLayer()
    
    # Act
    with patch("core.v11.execution_layer.execute_plan", return_value=mock_execution_result) as mock_execute:
        result = execution_layer.execute_plan(
            planning_result=planning_result,
            owner_id="test_owner",
            session_id="test_session"
        )
        
        # Assert that the V8 executor was called
        mock_execute.assert_called_once()
    
    # Assert
    assert result.status == ExecutionStatus.FAILED
    assert result.execution_id == "exec_456"


def test_execution_layer_planning_successful_no_plan():
    """Test the edge case where planning is successful but no plan is generated."""
    # Arrange
    planning_result = PlanningResult(
        planning_required=True,
        planning_successful=True,
        plan=None,  # No plan despite planning being successful
        error=None
    )
    execution_layer = ExecutionLayer()
    
    # Act
    result = execution_layer.execute_plan(
        planning_result=planning_result,
        owner_id="test_owner",
        session_id="test_session"
    )
    
    # Assert
    assert result.status == ExecutionStatus.INVALID_PLAN
    assert "Planning successful but no plan was generated" in result.response_text


def test_execution_layer_convenience_function():
    """Test the convenience function for the execution layer."""
    # Arrange
    planning_result = PlanningResult(
        planning_required=False,
        planning_successful=False,
        plan=None,
        error=None
    )
    
    # Act
    result = execute_plan_from_planning_result(
        planning_result=planning_result,
        owner_id="test_owner",
        session_id="test_session"
    )
    
    # Assert
    assert result.status == ExecutionStatus.SUCCESS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])