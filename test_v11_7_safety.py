#!/usr/bin/env python
"""V11.7 Safety / Verification / Cost Acceptance Tests."""

import sys
import os
from unittest.mock import patch, MagicMock

# Set environment variables
os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.v11.cognitive_orchestrator import V11CognitiveOrchestrator
from core.cost_guard.types import ResourceRequest, ResourceType, CostDecision, CostDecisionAction, CostReason, CostClass
from orchestration.executor_errors import ExecutionStatus
from core.v11.execution_layer import ExecutionResult
from orchestration.executor import _result
from core.v10.goal_understanding import GoalContinuity
from core.v10.planning_integration import PlanningResult
from core.cognition.schemas import CognitiveDecisionType
from orchestration.goal.types import IntentClass, CapabilityClass, Provenance
from orchestration.experience.types import ExperienceResultStatus


# Mock classes defined at module level for proper scoping
class MockFusedContext:
    def __init__(self):
        self.context = {"test": "context"}
        self.context_hash = "test_hash"


class MockGoalUnderstandingResult:
    def __init__(self):
        class MockGoal:
            def __init__(self):
                self.goal_id = "test_goal_id"
                self.schema_version = "v81.1"
                self.owner_id = "test_owner"
                self.session_id = "test_session"
                self.computer_session_id = ""
                self.raw_intent = "Create a plan to learn Python"
                self.normalized_intent = IntentClass.PLAN
                self.capability_class = CapabilityClass.CONVERSATION
                self.provenance = Provenance.USER_TEXT
                self.requested_unix_ms = 1000
                self.goal_hash = "test_goal_hash"
        self.understood_goal = MockGoal()
        self.continuity = GoalContinuity.NEW_GOAL
        self.confidence = 1.0
        self.timestamp_ms = 1000


class MockReasoningResult:
    def __init__(self):
        self.reasoning_summary = "Test reasoning"
        self.assumptions = []
        self.unresolved_questions = []


class MockDecisionResult:
    def __init__(self):
        self.decision_type = CognitiveDecisionType.CREATE_PLAN
        self.decision_basis = "Test basis"


class MockExperienceResult:
    def __init__(self):
        self.status = ExperienceResultStatus.OK
        self.experience = MagicMock()


class MockExecutionResult:
    def __init__(self, status=ExecutionStatus.SUCCESS, verification_state="VERIFIED"):
        self.execution_id = "test_exec_id"
        self.status = status
        self.goal_id = "test_goal"
        self.plan_hash = "test_plan_hash"
        self.approval_required = False
        self.approval_granted = False
        self.verification_state = verification_state
        self.response_text = "Success"


def test_cost_guard_allows_execution():
    """Test that when cost guard allows, execution proceeds."""
    orchestrator = V11CognitiveOrchestrator()
    # Mock the cost guard decision to allow execution
    mock_decision = CostDecision(
        action=CostDecisionAction.ALLOW,
        cost_class=CostClass.LOCAL_FREE,
        reason=CostReason.LOCAL_RESOURCE_ALLOWED,
        request=ResourceRequest(
            resource_type=ResourceType.OTHER,
            provider="local",
            capability="compute",
            model="",
            endpoint="",
            host="",
            privacy_class="",
            risk_class="",
        )
    )
    with patch.object(orchestrator.cost_guard, 'decision', return_value=mock_decision):
        # Mock the execution layer to return a successful result
        mock_execution_result = MockExecutionResult()
        with patch.object(orchestrator.execution_layer, 'execute_plan', return_value=mock_execution_result):
            # We need a planning result that indicates planning is required and successful
            mock_planning_result = PlanningResult(
                planning_required=True,
                planning_successful=True,
                plan=MagicMock(plan_hash="test_plan_hash"),
                error=None
            )
            with patch.object(orchestrator, 'planning_integration', return_value=mock_planning_result):
                # Mock goal understanding
                mock_goal_result = MockGoalUnderstandingResult()
                with patch.object(orchestrator.goal_understanding, 'understand_goal', return_value=mock_goal_result):
                    # Mock context fusion
                    mock_fused_context = MockFusedContext()
                    with patch('core.v10.context_fusion.fuse_context', return_value=mock_fused_context):
                        # Mock reasoning and decision
                        mock_reasoning_result = MockReasoningResult()
                        mock_decision_result = MockDecisionResult()
                        with patch.object(orchestrator, 'reasoning_decision_integration', return_value=(mock_reasoning_result, mock_decision_result)):
                            # Mock experience integration
                            mock_experience_result = MockExperienceResult()
                            with patch.object(orchestrator.experience_integration, 'integrate_execution_result', return_value=mock_experience_result):
                                # Run the orchestrator
                                result = orchestrator.process_cognitive_cycle(
                                    user_input="Create a plan to learn Python",
                                    owner_id="test_owner",
                                    session_id="test_session"
                                )
                                # Check that execution was not blocked
                                print("Result keys:", list(result["stages"].keys()))
                                if 'error' in result["stages"]:
                                    print("Error stage:", result["stages"]["error"])
                                assert result["stages"]["cost_guard_check"]["skip_execution"] == False
                                # Check that execution layer was called
                                assert result["stages"]["execution"]["execution_result"].status == ExecutionStatus.SUCCESS
                                # Check overall success
                                assert result["success"] == True
    print("? Cost guard allows execution")

def test_cost_guard_blocks_execution():
    """Test that when cost guard blocks, execution is skipped."""
    orchestrator = V11CognitiveOrchestrator()
    # Mock the cost guard decision to block execution
    mock_decision = CostDecision(
        action=CostDecisionAction.BLOCK,
        cost_class=CostClass.PAID,
        reason=CostReason.PAID_PROVIDER_BLOCKED,
        request=ResourceRequest(
            resource_type=ResourceType.OTHER,
            provider="local",
            capability="compute",
            model="",
            endpoint="",
            host="",
            privacy_class="",
            risk_class="",
            correlation_id=""
        )
    )
    with patch.object(orchestrator.cost_guard, 'decision', return_value=mock_decision):
        # We need a planning result that indicates planning is required and successful
        mock_planning_result = PlanningResult(
            planning_required=True,
            planning_successful=True,
            plan=MagicMock(plan_hash="test_plan_hash"),
            error=None
        )
        with patch.object(orchestrator, 'planning_integration', return_value=mock_planning_result):
            # Mock goal understanding
            mock_goal_result = MockGoalUnderstandingResult()
            with patch.object(orchestrator.goal_understanding, 'understand_goal', return_value=mock_goal_result):
                # Mock context fusion
                mock_fused_context = MockFusedContext()
                with patch('core.v10.context_fusion.fuse_context', return_value=mock_fused_context):
                    # Mock reasoning and decision
                    mock_reasoning_result = MockReasoningResult()
                    mock_decision_result = MockDecisionResult()
                    with patch.object(orchestrator, 'reasoning_decision_integration', return_value=(mock_reasoning_result, mock_decision_result)):
                        # Mock execution layer (should not be called when blocked)
                        mock_execute_plan = MagicMock()
                        with patch.object(orchestrator.execution_layer, 'execute_plan', mock_execute_plan):
                            # Mock experience integration
                            mock_experience_result = MockExperienceResult()
                            with patch.object(orchestrator.experience_integration, 'integrate_execution_result', return_value=mock_experience_result):
                                # Run the orchestrator
                                result = orchestrator.process_cognitive_cycle(
                                    user_input="Create a plan to learn Python",
                                    owner_id="test_owner",
                                    session_id="test_session"
                                )
                                # Check that execution was blocked
                                assert result["stages"]["cost_guard_check"]["skip_execution"] == True
                                # Check that execution layer was NOT called
                                mock_execute_plan.assert_not_called()
                                # Check that execution result shows blocked status
                                assert result["stages"]["execution"]["execution_result"].status == ExecutionStatus.BLOCKED
                                # Check overall success is False due to cost guard block
                                assert result["success"] == False
    print("? Cost guard blocks execution")

def test_verification_uses_execution_result():
    """Test that verification uses execution result, not user input."""
    orchestrator = V11CognitiveOrchestrator()
    # Mock the cost guard decision to allow execution
    mock_decision = CostDecision(
        action=CostDecisionAction.ALLOW,
        cost_class=CostClass.LOCAL_FREE,
        reason=CostReason.LOCAL_RESOURCE_ALLOWED,
        request=ResourceRequest(
            resource_type=ResourceType.OTHER,
            provider="local",
            capability="compute",
            model="",
            endpoint="",
            host="",
            privacy_class="",
            risk_class="",
            correlation_id=""
        )
    )
    with patch.object(orchestrator.cost_guard, 'decision', return_value=mock_decision):
        # Mock the execution layer to return a result with FAILED verification
        mock_execution_result = MockExecutionResult(verification_state="FAILED")
        with patch.object(orchestrator.execution_layer, 'execute_plan', return_value=mock_execution_result):
            # We need a planning result that indicates planning is required and successful
            mock_planning_result = PlanningResult(
                planning_required=True,
                planning_successful=True,
                plan=MagicMock(plan_hash="test_plan_hash"),
                error=None
            )
            with patch.object(orchestrator, 'planning_integration', return_value=mock_planning_result):
                # Mock goal understanding
                mock_goal_result = MockGoalUnderstandingResult()
                with patch.object(orchestrator.goal_understanding, 'understand_goal', return_value=mock_goal_result):
                    # Mock context fusion
                    mock_fused_context = MockFusedContext()
                    with patch('core.v10.context_fusion.fuse_context', return_value=mock_fused_context):
                        # Mock reasoning and decision
                        mock_reasoning_result = MockReasoningResult()
                        mock_decision_result = MockDecisionResult()
                        with patch.object(orchestrator, 'reasoning_decision_integration', return_value=(mock_reasoning_result, mock_decision_result)):
                            # Mock experience integration
                            mock_experience_result = MockExperienceResult()
                            with patch.object(orchestrator.experience_integration, 'integrate_execution_result', return_value=mock_experience_result):
                                # Run the orchestrator
                                result = orchestrator.process_cognitive_cycle(
                                    user_input="Create a plan to learn Python",
                                    owner_id="test_owner",
                                    session_id="test_session"
                                )
                                # Check that verification used the execution result's verification_state
                                assert result["stages"]["verification"]["verification_result"]["verified"] == False
                                assert result["stages"]["verification"]["verification_result"]["status"] == "FAILED"
                                # Check overall success is False due to verification failure
                                assert result["success"] == False
    print("? Verification uses execution result")

if __name__ == "__main__":
    test_cost_guard_allows_execution()
    test_cost_guard_blocks_execution()
    test_verification_uses_execution_result()
    print("? All tests passed!")
