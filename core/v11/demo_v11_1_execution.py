"""Demonstration of V11.1 Execution Layer.

This script demonstrates how the V11.1 execution layer works by:
1. Creating a planning result (simulating output from V10.5 planning integration)
2. Passing it to the V11.1 execution layer
3. Showing how it executes the plan using the V8 executor
4. Displaying the results
"""

from unittest.mock import Mock
from core.v11.execution_layer import ExecutionLayer, execute_plan_from_planning_result
from core.v10.planning_integration import PlanningResult
from orchestration.goal.plan_types import GoalPlan
from orchestration.executor_errors import ExecutionStatus


def demo_execution_layer():
    """Demonstrate the V11.1 execution layer functionality."""
    print("=== V11.1 Execution Layer Demo ===\n")
    
    # Create an execution layer instance
    execution_layer = ExecutionLayer()
    
    # Demo 1: Planning not required
    print("Demo 1: Planning not required")
    planning_result_no_plan = PlanningResult(
        planning_required=False,
        planning_successful=False,
        plan=None,
        error=None
    )
    
    result = execution_layer.execute_plan(
        planning_result=planning_result_no_plan,
        owner_id="demo_user",
        session_id="demo_session"
    )
    
    print(f"  Planning required: {planning_result_no_plan.planning_required}")
    print(f"  Planning successful: {planning_result_no_plan.planning_successful}")
    print(f"  Execution status: {result.status.value}")
    print(f"  Execution successful: {result.status == ExecutionStatus.SUCCESS}")
    print()
    
    # Demo 2: Planning required but not successful
    print("Demo 2: Planning required but not successful")
    planning_result_fail = PlanningResult(
        planning_required=True,
        planning_successful=False,
        plan=None,
        error="Planning failed due to invalid goal specification"
    )
    
    result = execution_layer.execute_plan(
        planning_result=planning_result_fail,
        owner_id="demo_user",
        session_id="demo_session"
    )
    
    print(f"  Planning required: {planning_result_fail.planning_required}")
    print(f"  Planning successful: {planning_result_fail.planning_successful}")
    print(f"  Execution status: {result.status.value}")
    print(f"  Execution successful: {result.status == ExecutionStatus.SUCCESS}")
    print()
    
    # Demo 3: Planning successful but no plan generated (edge case)
    print("Demo 3: Planning successful but no plan generated")
    planning_result_no_plan_gen = PlanningResult(
        planning_required=True,
        planning_successful=True,
        plan=None,  # No plan despite success
        error=None
    )
    
    result = execution_layer.execute_plan(
        planning_result=planning_result_no_plan_gen,
        owner_id="demo_user",
        session_id="demo_session"
    )
    
    print(f"  Planning required: {planning_result_no_plan_gen.planning_required}")
    print(f"  Planning successful: {planning_result_no_plan_gen.planning_successful}")
    print(f"  Plan generated: {planning_result_no_plan_gen.plan is not None}")
    print(f"  Execution status: {result.status.value}")
    print(f"  Execution successful: {result.status == ExecutionStatus.SUCCESS}")
    if result.status != ExecutionStatus.SUCCESS:
        print(f"  Error message: {result.response_text}")
    print()
    
    # Demo 4: Planning successful with plan (would execute if we had a real plan)
    print("Demo 4: Planning successful with plan (mocked execution)")
    # Create a mock plan
    mock_plan = Mock(spec=GoalPlan)
    mock_plan.plan_id = "demo_plan_001"
    mock_plan.goal_id = "demo_goal_001"
    mock_plan.plan_hash = "demo_hash_001"
    
    planning_result_with_plan = PlanningResult(
        planning_required=True,
        planning_successful=True,
        plan=mock_plan,
        error=None
    )
    
    # Note: In a real scenario, this would call the V8 executor
    # For this demo, we'll show what would happen
    print(f"  Planning required: {planning_result_with_plan.planning_required}")
    print(f"  Planning successful: {planning_result_with_plan.planning_successful}")
    print(f"  Plan ID: {planning_result_with_plan.plan.plan_id}")
    print(f"  Goal ID: {planning_result_with_plan.plan.goal_id}")
    print(f"  Plan hash: {planning_result_with_plan.plan.plan_hash}")
    print("  In a real implementation, this would:")
    print("  1. Create an ExecutionIdentity")
    print("  2. Call the V8 executor's execute_plan function")
    print("  3. Return the ExecutionResult from the V8 executor")
    print()
    
    # Demo 5: Using the convenience function
    print("Demo 5: Using the convenience function")
    result = execute_plan_from_planning_result(
        planning_result=planning_result_no_plan,
        owner_id="demo_user",
        session_id="demo_session"
    )
    
    print(f"  Convenience function result status: {result.status.value}")
    print(f"  Matches direct call: {result.status == execution_layer.execute_plan(planning_result_no_plan, 'demo_user', 'demo_session').status}")
    print()


if __name__ == "__main__":
    demo_execution_layer()
    print("Demo completed successfully!")