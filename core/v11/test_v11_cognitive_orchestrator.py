"""Test suite for V11 Cognitive Orchestrator."""

from unittest.mock import Mock

from core.v11.cognitive_orchestrator import V11CognitiveOrchestrator, process_v11_cognitive_cycle
from core.cognition.schemas import CognitiveDecisionType
from core.v10.planning_integration import PlanningResult


def test_v11_orchestrator_initialization():
    """Test that the V11 cognitive orchestrator initializes correctly."""
    # Act
    orchestrator = V11CognitiveOrchestrator()
    
    # Assert
    assert orchestrator is not None
    assert orchestrator.context_fusion is not None
    assert orchestrator.goal_understanding is not None
    assert orchestrator.reasoning_decision_integration is not None
    assert orchestrator.planning_integration is not None
    assert orchestrator.execution_layer is not None


def test_v11_orchestrator_no_planning_required():
    """Test the V11 orchestrator when planning is not required."""
    # Arrange
    orchestrator = V11CognitiveOrchestrator()
    
    # Mock the V10 components to return a non-CREATE_PLAN decision
    def mock_integrate_reasoning_decision_no_plan(fused_context, goal_understanding, owner_id, session_id):
        mock_reasoning_result = Mock()
        mock_reasoning_result.reasoning_summary = 'Test reasoning summary'
        mock_reasoning_result.assumptions = ['assumption1']
        mock_reasoning_result.unresolved_questions = ['question1']
        
        mock_decision_result = Mock()
        mock_decision_result.decision_type = CognitiveDecisionType.ANSWER_DIRECTLY  # Not CREATE_PLAN
        mock_decision_result.decision_basis = 'Test basis'
        return (mock_reasoning_result, mock_decision_result)

    orchestrator.reasoning_decision_integration = mock_integrate_reasoning_decision_no_plan
    
    # Act
    result = orchestrator.process_cognitive_cycle(
        user_input="Hello",
        owner_id="test_owner",
        session_id="test_session"
    )
    
    # Assert
    assert result["success"] is True
    assert result["stages"]["planning"]["planning_required"] is False
    # Note: No execution stage is added when planning is not required


def test_v11_orchestrator_planning_but_no_execution():
    """Test the V11 orchestrator when planning is required but execution doesn't happen."""
    # Arrange
    orchestrator = V11CognitiveOrchestrator()
    
    # Mock the V10 components to return a CREATE_PLAN decision but planning fails
    def mock_integrate_reasoning_decision_plan(fused_context, goal_understanding, owner_id, session_id):
        mock_reasoning_result = Mock()
        mock_reasoning_result.reasoning_summary = 'Test reasoning summary'
        mock_reasoning_result.assumptions = ['assumption1']
        mock_reasoning_result.unresolved_questions = ['question1']
        
        mock_decision_result = Mock()
        mock_decision_result.decision_type = CognitiveDecisionType.CREATE_PLAN
        mock_decision_result.decision_basis = 'Test basis'
        return (mock_reasoning_result, mock_decision_result)

    orchestrator.reasoning_decision_integration = mock_integrate_reasoning_decision_plan

    # Mock the planning to fail
    def mock_integrate_planning_fail(fused_context, goal_understanding, reasoning_result, decision_result, owner_id, session_id=''):
        return PlanningResult(
            planning_required=True,
            planning_successful=False,  # Planning failed
            plan=None,
            error='Planning failed'
        )

    orchestrator.planning_integration = mock_integrate_planning_fail
    
    # Act
    result = orchestrator.process_cognitive_cycle(
        user_input="Create a plan",
        owner_id="test_owner",
        session_id="test_session"
    )
    
    # Assert
    assert result["stages"]["planning"]["planning_required"] is True
    assert result["stages"]["planning"]["planning_successful"] is False
    # Note: No execution stage is added when planning is not successful


def test_v11_orchestrator_planning_and_execution():
    """Test the V11 orchestrator when planning succeeds and execution occurs."""
    # Arrange
    orchestrator = V11CognitiveOrchestrator()
    
    # Mock the V10 components to return a CREATE_PLAN decision and planning succeeds
    def mock_integrate_reasoning_decision_plan(fused_context, goal_understanding, owner_id, session_id):
        mock_reasoning_result = Mock()
        mock_reasoning_result.reasoning_summary = 'Test reasoning summary'
        mock_reasoning_result.assumptions = ['assumption1']
        mock_reasoning_result.unresolved_questions = ['question1']
        
        mock_decision_result = Mock()
        mock_decision_result.decision_type = CognitiveDecisionType.CREATE_PLAN
        mock_decision_result.decision_basis = 'Test basis'
        return (mock_reasoning_result, mock_decision_result)

    orchestrator.reasoning_decision_integration = mock_integrate_reasoning_decision_plan

    # Mock the planning to succeed
    def mock_integrate_planning_succeed(fused_context, goal_understanding, reasoning_result, decision_result, owner_id, session_id=''):
        # Create a mock plan with required attributes
        mock_plan = Mock()
        mock_plan.plan_id = 'test_plan_123'
        mock_plan.goal_id = 'test_goal_456'
        mock_plan.plan_hash = 'test_hash_789'
        
        return PlanningResult(
            planning_required=True,
            planning_successful=True,  # Planning succeeded
            plan=mock_plan,
            error=None
        )

    orchestrator.planning_integration = mock_integrate_planning_succeed
    
    # Act
    result = orchestrator.process_cognitive_cycle(
        user_input="Create a plan",
        owner_id="test_owner",
        session_id="test_session"
    )
    
    # Assert
    assert result["stages"]["planning"]["planning_required"] is True
    assert result["stages"]["planning"]["planning_successful"] is True
    assert "execution" in result["stages"]
    # Note: Execution success depends on the mocked executor, but the stage should exist


def test_convenience_function():
    """Test the convenience function for V11 cognitive orchestration."""
    # Act
    result = process_v11_cognitive_cycle(
        user_input="Hello",
        owner_id="test_owner",
        session_id="test_session"
    )
    
    # Assert
    assert "cycle_id" in result
    assert result["owner_id"] == "test_owner"
    assert result["session_id"] == "test_session"


if __name__ == "__main__":
    # Run the tests
    test_v11_orchestrator_initialization()
    print("✓ V11 orchestrator initialization test passed")
    
    test_v11_orchestrator_no_planning_required()
    print("✓ V11 orchestrator no planning required test passed")
    
    test_v11_orchestrator_planning_but_no_execution()
    print("✓ V11 orchestrator planning but no execution test passed")
    
    test_v11_orchestrator_planning_and_execution()
    print("✓ V11 orchestrator planning and execution test passed")
    
    test_convenience_function()
    print("✓ V11 convenience function test passed")
    
    print("\nAll tests passed! 🎉")