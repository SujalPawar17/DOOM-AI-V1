"""Expanded test suite for V10.5 Planning Integration covering all continuity states."""

import pytest
from unittest.mock import Mock, patch

from core.v10.context_fusion import FusedContext
from core.v10.goal_understanding import GoalUnderstandingResult, GoalContinuity
from core.v10.reasoning_decision import ReasoningResult, DecisionResult
from core.v10.planning_integration import integrate_planning, PlanningResult
from orchestration.goal.planner_errors import PlannerStatus


def create_fused_context_with_active_goal(owner_id="test_owner", session_id="test_session"):
    """Create a fused context with an active goal for testing continuity states."""
    return FusedContext(
        owner_id=owner_id,
        context={
            "request_raw": "test request",
            "goal_state": {
                "active_goal_id": "active_goal_123",
                "active_goal_schema_version": "1",
                "active_goal_owner_id": owner_id,
                "active_goal_session_id": session_id,
                "active_goal_computer_session_id": "",
                "active_goal_title": "Build a website",
                "active_goal_intent": "COMPUTER",
                "active_goal_capability": "computer",
                "active_goal_provenance": "USER_TEXT",
                "active_goal_requested_unix_ms": "1234567890",
                "active_goal_goal_hash": "abc123",
            }
        },
        provenance={},
        privacy_levels={},
        fused_at_ms=1000,
        context_hash="active_context"
    )


def create_fused_context_no_active_goal(owner_id="test_owner", session_id="test_session"):
    """Create a fused context with no active goal."""
    return FusedContext(
        owner_id=owner_id,
        context={"request_raw": "test request"},
        provenance={},
        privacy_levels={},
        fused_at_ms=1000,
        context_hash="no_active_context"
    )


def test_planning_not_required_when_decision_is_not_create_plan():
    """Test that planning is not required when decision type is not CREATE_PLAN."""
    # Arrange
    fused_context = create_fused_context_no_active_goal()
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


def test_planning_required_and_successful_new_goal():
    """Test planning success with NEW_GOAL continuity."""
    # Arrange
    fused_context = create_fused_context_no_active_goal()
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


def test_planning_required_and_successful_continue_goal():
    """Test planning success with CONTINUE_GOAL continuity."""
    # Arrange
    fused_context = create_fused_context_with_active_goal()
    # For CONTINUE_GOAL, understood_goal should be the active goal
    mock_active_goal = Mock()
    mock_active_goal.to_dict.return_value = {"goal_id": "active_goal_123"}
    
    goal_understanding = GoalUnderstandingResult(
        request="Continue with the website",
        owner_id="test_owner",
        understood_goal=mock_active_goal,
        continuity=GoalContinuity.CONTINUE_GOAL
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="test reasoning",
        assumptions=[],
        unresolved_questions=[]
    )
    decision_result = DecisionResult(
        decision_type="CREATE_PLAN",
        decision_basis="User wants to continue with current goal"
    )
    
    # Mock the planner to return a successful plan proposal
    mock_plan = Mock()
    mock_plan.plan_id = "plan_continue"
    mock_plan.to_dict.return_value = {"plan_id": "plan_continue", "steps": []}
    
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
    assert result.plan_id == "plan_continue"
    assert result.provenance["planner_status"] == "SUCCESS"


def test_planning_required_and_successful_refine_goal():
    """Test planning success with REFINE_GOAL continuity."""
    # Arrange
    fused_context = create_fused_context_with_active_goal()
    # For REFINE_GOAL, understood_goal should be the active goal (to be refined)
    mock_active_goal = Mock()
    mock_active_goal.to_dict.return_value = {"goal_id": "active_goal_123"}
    
    goal_understanding = GoalUnderstandingResult(
        request="Make the website more interactive",
        owner_id="test_owner",
        understood_goal=mock_active_goal,
        continuity=GoalContinuity.REFINE_GOAL
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="test reasoning",
        assumptions=[],
        unresolved_questions=[]
    )
    decision_result = DecisionResult(
        decision_type="CREATE_PLAN",
        decision_basis="User wants to refine current goal"
    )
    
    # Mock the planner to return a successful plan proposal
    mock_plan = Mock()
    mock_plan.plan_id = "plan_refine"
    mock_plan.to_dict.return_value = {"plan_id": "plan_refine", "steps": []}
    
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
    assert result.plan_id == "plan_refine"
    assert result.provenance["planner_status"] == "SUCCESS"


def test_planning_required_and_successful_change_goal():
    """Test planning success with CHANGE_GOAL continuity."""
    # Arrange
    fused_context = create_fused_context_with_active_goal()
    # For CHANGE_GOAL, understood_goal should be a new goal (different from active)
    mock_new_goal = Mock()
    mock_new_goal.to_dict.return_value = {"goal_id": "new_goal_456"}
    
    goal_understanding = GoalUnderstandingResult(
        request="Instead of website, help me plan a novel",
        owner_id="test_owner",
        understood_goal=mock_new_goal,
        continuity=GoalContinuity.CHANGE_GOAL
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="test reasoning",
        assumptions=[],
        unresolved_questions=[]
    )
    decision_result = DecisionResult(
        decision_type="CREATE_PLAN",
        decision_basis="User wants to change to a new goal"
    )
    
    # Mock the planner to return a successful plan proposal
    mock_plan = Mock()
    mock_plan.plan_id = "plan_change"
    mock_plan.to_dict.return_value = {"plan_id": "plan_change", "steps": []}
    
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
    assert result.plan_id == "plan_change"
    assert result.provenance["planner_status"] == "SUCCESS"


def test_planning_required_and_successful_complete_goal():
    """Test planning with COMPLETE_GOAL continuity (understood_goal=None)."""
    # Arrange
    fused_context = create_fused_context_with_active_goal()
    # For COMPLETE_GOAL, understood_goal is None (goal completed)
    goal_understanding = GoalUnderstandingResult(
        request="I'm done with the website",
        owner_id="test_owner",
        understood_goal=None,  # Completed goal
        continuity=GoalContinuity.COMPLETE_GOAL
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="test reasoning",
        assumptions=[],
        unresolved_questions=[]
    )
    decision_result = DecisionResult(
        decision_type="CREATE_PLAN",
        decision_basis="User completed goal and wants to plan next steps"
    )
    
    # Mock the planner to return a successful plan for the fallback goal
    mock_plan = Mock()
    mock_plan.plan_id = "fallback_plan_complete"
    mock_plan.to_dict.return_value = {"plan_id": "fallback_plan_complete", "steps": []}
    
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
    assert result.plan_id == "fallback_plan_complete"
    assert result.error is None
    assert result.provenance["planner_status"] == "SUCCESS"


def test_planning_required_and_successful_abandon_goal():
    """Test planning with ABANDON_GOAL continuity (understood_goal=None)."""
    # Arrange
    fused_context = create_fused_context_with_active_goal()
    # For ABANDON_GOAL, understood_goal is None (goal abandoned)
    goal_understanding = GoalUnderstandingResult(
        request="Forget about the website, let's do something else",
        owner_id="test_owner",
        understood_goal=None,  # Abandoned goal
        continuity=GoalContinuity.ABANDON_GOAL
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="test reasoning",
        assumptions=[],
        unresolved_questions=[]
    )
    decision_result = DecisionResult(
        decision_type="CREATE_PLAN",
        decision_basis="User abandoned goal and wants to plan something new"
    )
    
    # Mock the planner to return a successful plan for the fallback goal
    mock_plan = Mock()
    mock_plan.plan_id = "fallback_plan_abandon"
    mock_plan.to_dict.return_value = {"plan_id": "fallback_plan_abandon", "steps": []}
    
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
    assert result.plan_id == "fallback_plan_abandon"
    assert result.error is None
    assert result.provenance["planner_status"] == "SUCCESS"


def test_planning_required_with_no_understood_goal_fallback_no_active_goal():
    """Test planning when understood_goal is None and no active goal (fallback to creating a GoalSpec)."""
    # Arrange
    fused_context = create_fused_context_no_active_goal()
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
    assert result.plan_id == "fallback_plan"
    assert result.error is None
    assert result.provenance["planner_status"] == "SUCCESS"


def test_planning_required_but_planner_fails_with_fallback_goal():
    """Test that planning fails when understood_goal is None and planner rejects fallback goal."""
    # Arrange
    fused_context = create_fused_context_no_active_goal()
    goal_understanding = GoalUnderstandingResult(
        request="Invalid request",
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
    
    # Mock the planner to return a failed proposal for the fallback goal
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


def test_planning_with_real_v8_planner_new_goal():
    """Test planning with REAL V8 planner (not mocked) for NEW_GOAL continuity."""
    # Arrange
    fused_context = create_fused_context_no_active_goal()
    # Create a real GoalSpec for testing
    from orchestration.goal.types import GoalSpec, IntentClass, CapabilityClass, Provenance
    import time
    
    goal_spec = GoalSpec(
        goal_id="test_real_goal",
        schema_version="1",
        owner_id="test_owner",
        session_id="test_session",
        computer_session_id="",
        raw_intent="Help me learn Python programming",
        normalized_intent=IntentClass.COMPUTER,
        capability_class=CapabilityClass.COMPUTER,
        provenance=Provenance.USER_TEXT,
        requested_unix_ms=int(time.time() * 1000),
        goal_hash="abc123def456"
    )
    
    goal_understanding = GoalUnderstandingResult(
        request="Help me learn Python programming",
        owner_id="test_owner",
        understood_goal=goal_spec,
        continuity=GoalContinuity.NEW_GOAL
    )
    reasoning_result = ReasoningResult(
        reasoning_summary="User wants to learn Python",
        assumptions=["User is a beginner"],
        unresolved_questions=["What resources are best?"])
    decision_result = DecisionResult(
        decision_type="CREATE_PLAN",
        decision_basis="User requested learning plan for Python"
    )
    
    # Act - Use real V8 planner (no mocking)
    result = integrate_planning(
        fused_context=fused_context,
        goal_understanding=goal_understanding,
        reasoning_result=reasoning_result,
        decision_result=decision_result,
        owner_id="test_owner"
    )
    
    # Assert
    assert result.planning_required is True
    # Note: With real planner, success depends on V8 configuration and capabilities
    # But we should at least get a deterministic result
    assert result.plan is not None or result.error is not None
    assert result.provenance["planner_status"] in [status.value for status in PlannerStatus]
    assert result.provenance["owner_id"] == "test_owner"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])