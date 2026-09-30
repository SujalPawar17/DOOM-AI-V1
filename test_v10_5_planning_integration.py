"""Test suite for V10.5 Planning Integration."""

import pytest
from unittest.mock import Mock, patch

from core.v10.context_fusion import FusedContext
from core.v10.goal_understanding import GoalUnderstandingResult, GoalContinuity
from core.v10.reasoning_decision import ReasoningResult, DecisionResult
from core.v10.planning_integration import integrate_planning, PlanningResult
from orchestration.goal.planner_errors import PlannerStatus


def test_planning_not_required_when_decision_is_not_create_plan():
    """Test that planning is not required when decision type is not CREATE_PLAN."""
    # Arrange
    fused_context = FusedContext(
        owner_id="test_owner",
        context={"request_raw": "Hello"},
        provenance={},
        privacy_levels={},
        fused_at_ms=1000,
        context_hash="abc"
    )
    goal_understanding = GoalUnderstandingResult(
        request="Hello",
        owner_id="test_owner",
        continuity=GoalContinuity.NO_ACTIVE_GOAL
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="test",
        assumptions=[],
        unresolved_questions=[]
    )
    decision_result = DecisionResult(
        decision_type="ANSWER_DIRECTLY",  # Not CREATE_PLAN
        decision_basis="test"
    )
    
    # Act
    result = integrate_planning(
        fused_context=fused_context,
        goal_understanding=goal_understanding,
        reasoning_result=reasoning_result,
        decision_result=decision_result,
        owner_id="test_owner"
    )
    
    # Assert
    assert result.planning_required is False
    assert result.planning_successful is False
    assert result.plan is None
    assert result.error is None
    assert result.provenance["decision_type"] == "ANSWER_DIRECTLY"


def test_planning_required_and_successful():
    """Test that planning is required and successful when decision is CREATE_PLAN and planner succeeds."""
    # Arrange
    fused_context = FusedContext(
        owner_id="test_owner",
        context={"request_raw": "Create a plan to backup files"},
        provenance={},
        privacy_levels={},
        fused_at_ms=1000,
        context_hash="def"
    )
    # Create a mock GoalSpec that the planner will accept
    mock_goal_spec = Mock()
    mock_goal_spec.to_dict.return_value = {"goal_id": "test_goal"}
    
    goal_understanding = GoalUnderstandingResult(
        request="Create a plan to backup files",
        owner_id="test_owner",
        understood_goal=mock_goal_spec,
        continuity=GoalContinuity.NEW_GOAL
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="test reasoning",
        assumptions=["assump1"],
        unresolved_questions=["question1"]
    )
    decision_result = DecisionResult(
        decision_type="CREATE_PLAN",
        decision_basis="User wants to create a plan"
    )
    
    # Mock the planner to return a successful plan proposal
    mock_plan = Mock()
    mock_plan.plan_id = "plan_123"
    mock_plan.to_dict.return_value = {"plan_id": "plan_123", "steps": []}
    
    mock_proposal = Mock()
    mock_proposal.status = PlannerStatus.SUCCESS
    mock_proposal.plan = mock_plan
    mock_proposal.reason_code = "PLAN_PROPOSED"
    
    with patch("core.v10.planning_integration.plan_goal", return_value=mock_proposal):
        # Act
        result = integrate_planning(
            fused_context=fused_context,
            goal_understanding=goal_understanding,
            reasoning_result=reasoning_result,
            decision_result=decision_result,
            owner_id="test_owner"
        )
    
    # Assert
    assert result.planning_required is True
    assert result.planning_successful is True
    assert result.plan is not None
    assert result.error is None
    assert result.plan_id == "plan_123"
    assert result.provenance["planner_status"] == "SUCCESS"
    assert result.provenance["owner_id"] == "test_owner"


def test_planning_required_but_planner_fails():
    """Test that planning is required but fails when the planner returns failure."""
    # Arrange
    fused_context = FusedContext(
        owner_id="test_owner",
        context={"request_raw": "Invalid request"},
        provenance={},
        privacy_levels={},
        fused_at_ms=1000,
        context_hash="ghi"
    )
    mock_goal_spec = Mock()
    mock_goal_spec.to_dict.return_value = {"goal_id": "test_goal"}
    
    goal_understanding = GoalUnderstandingResult(
        request="Invalid request",
        owner_id="test_owner",
        understood_goal=mock_goal_spec,
        continuity=GoalContinuity.NEW_GOAL
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="test",
        assumptions=[],
        unresolved_questions=[]
    )
    decision_result = DecisionResult(
        decision_type="CREATE_PLAN",
        decision_basis="test"
    )
    
    # Mock the planner to return a failed proposal
    mock_proposal = Mock()
    mock_proposal.status = PlannerStatus.UNSUPPORTED_INTENT
    mock_proposal.plan = None
    mock_proposal.reason_code = "UNSUPPORTED_INTENT"
    
    with patch("core.v10.planning_integration.plan_goal", return_value=mock_proposal):
        # Act
        result = integrate_planning(
            fused_context=fused_context,
            goal_understanding=goal_understanding,
            reasoning_result=reasoning_result,
            decision_result=decision_result,
            owner_id="test_owner"
        )
    
    # Assert
    assert result.planning_required is True
    assert result.planning_successful is False
    assert result.plan is None
    assert result.error is not None
    assert "UNSUPPORTED_INTENT" in result.error
    assert result.provenance["planner_status"] == "UNSUPPORTED_INTENT"


def test_planning_required_with_no_understood_goal_fallback():
    """Test planning when understood_goal is None (fallback to creating a GoalSpec)."""
    # Arrange
    fused_context = FusedContext(
        owner_id="test_owner",
        context={"request_raw": "Do something"},
        provenance={},
        privacy_levels={},
        fused_at_ms=1000,
        context_hash="jkl"
    )
    goal_understanding = GoalUnderstandingResult(
        request="Do something",
        owner_id="test_owner",
        understood_goal=None,  # No understood goal
        continuity=GoalContinuity.NO_ACTIVE_GOAL
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="test",
        assumptions=[],
        unresolved_questions=[]
    )
    decision_result = DecisionResult(
        decision_type="CREATE_PLAN",
        decision_basis="test"
    )
    
    # Mock the planner to return a successful plan for the fallback goal
    mock_plan = Mock()
    mock_plan.plan_id = "fallback_plan"
    mock_plan.to_dict.return_value = {"plan_id": "fallback_plan", "steps": []}
    
    mock_proposal = Mock()
    mock_proposal.status.name = "SUCCESS"
    mock_proposal.plan = mock_plan
    mock_proposal.reason_code = "PLAN_PROPOSED"
    
    with patch("core.v10.planning_integration.plan_goal", return_value=mock_proposal):
        # Act
        result = integrate_planning(
            fused_context=fused_context,
            goal_understanding=goal_understanding,
            reasoning_result=reasoning_result,
            decision_result=decision_result,
            owner_id="test_owner"
        )
    
    # Assert
    assert result.planning_required is True
    assert result.planning_successful is True
    assert result.plan_id == "fallback_plan"


def test_planning_integration_passes_owner_and_session_id():
    """Test that owner_id and session_id are included in the provenance."""
    # Arrange
    fused_context = FusedContext(
        owner_id="test_owner",
        context={"request_raw": "test"},
        provenance={},
        privacy_levels={},
        fused_at_ms=1000,
        context_hash="mno"
    )
    mock_goal_spec = Mock()
    mock_goal_spec.to_dict.return_value = {"goal_id": "test_goal"}
    
    goal_understanding = GoalUnderstandingResult(
        request="test",
        owner_id="test_owner",
        understood_goal=mock_goal_spec
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="test",
        assumptions=[],
        unresolved_questions=[]
    )
    decision_result = DecisionResult(
        decision_type="CREATE_PLAN",
        decision_basis="test"
    )
    
    mock_plan = Mock()
    mock_plan.plan_id = "plan_456"
    mock_plan.to_dict.return_value = {"plan_id": "plan_456"}
    
    mock_proposal = Mock()
    mock_proposal.status.name = "SUCCESS"
    mock_proposal.plan = mock_plan
    mock_proposal.reason_code = "PLAN_PROPOSED"
    
    with patch("core.v10.planning_integration.plan_goal", return_value=mock_proposal):
        # Act
        result = integrate_planning(
            fused_context=fused_context,
            goal_understanding=goal_understanding,
            reasoning_result=reasoning_result,
            decision_result=decision_result,
            owner_id="test_owner",
            session_id="test_session"
        )
    
    # Assert
    assert result.provenance["owner_id"] == "test_owner"
    assert result.provenance["session_id"] == "test_session"


def test_planning_integration_is_deterministic():
    """Test that identical inputs produce identical outputs."""
    # Arrange
    fused_context = FusedContext(
        owner_id="det_owner",
        context={"request_raw": "Same request"},
        provenance={},
        privacy_levels={},
        fused_at_ms=1000,
        context_hash="same"
    )
    mock_goal_spec = Mock()
    mock_goal_spec.to_dict.return_value = {"goal_id": "same_goal"}
    
    goal_understanding = GoalUnderstandingResult(
        request="Same request",
        owner_id="det_owner",
        understood_goal=mock_goal_spec,
        continuity=GoalContinuity.NEW_GOAL
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="Same reasoning",
        assumptions=["same_assump"],
        unresolved_questions=["same_question"]
    )
    decision_result = DecisionResult(
        decision_type="CREATE_PLAN",
        decision_basis="Same basis"
    )
    
    mock_plan = Mock()
    mock_plan.plan_id = "deterministic_plan"
    mock_plan.to_dict.return_value = {"plan_id": "deterministic_plan"}
    
    mock_proposal = Mock()
    mock_proposal.status.name = "SUCCESS"
    mock_proposal.plan = mock_plan
    mock_proposal.reason_code = "PLAN_PROPOSED"
    
    with patch("core.v10.planning_integration.plan_goal", return_value=mock_proposal):
        # Act
        result1 = integrate_planning(
            fused_context=fused_context,
            goal_understanding=goal_understanding,
            reasoning_result=reasoning_result,
            decision_result=decision_result,
            owner_id="det_owner"
        )
        result2 = integrate_planning(
            fused_context=fused_context,
            goal_understanding=goal_understanding,
            reasoning_result=reasoning_result,
            decision_result=decision_result,
            owner_id="det_owner"
        )
    
    # Assert
    assert result1.planning_required == result2.planning_required
    assert result1.planning_successful == result2.planning_successful
    assert result1.plan_id == result2.plan_id
    assert result1.error == result2.error
    # Provenance should be identical except possibly timestamps? We don't have timestamps in our provenance.
    assert result1.provenance == result2.provenance


if __name__ == "__main__":
    pytest.main([__file__, "-v"])