#!/usr/bin/env python
"""Test V10.6 Cognitive Orchestrator."""

import os
os.environ["PROACTIVE_V8_ENABLED"] = "1"
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "1"

from core.v10.cognitive_orchestrator import process_cognitive_cycle

def test_cognitive_orchestrator():
    """Test the cognitive orchestrator with various inputs."""
    
    print("Testing V10.6 Cognitive Orchestrator")
    print("=" * 50)
    
    # Test 1: Planning request
    print("\nTest 1: Planning request")
    result1 = process_cognitive_cycle(
        user_input="Create a plan to learn Python programming",
        owner_id="test_user",
        session_id="test_session_001"
    )
    print(f"Success: {result1['success']}")
    if not result1['success']:
        print(f"Error: {result1.get('error', 'Unknown error')}")
        print(f"Error type: {result1.get('error_type', 'Unknown')}")
    print(f"Response: {result1['response_text'][:100]}...")
    if 'planning' in result1['stages']:
        print(f"Planning required: {result1['stages']['planning']['planning_required']}")
        print(f"Planning successful: {result1['stages']['planning']['planning_successful']}")
    print(f"Total time: {result1['timing']['total_time_ms']:.2f}ms")
    
    # Test 2: Non-planning request
    print("\nTest 2: Non-planning request")
    result2 = process_cognitive_cycle(
        user_input="What is the capital of France?",
        owner_id="test_user",
        session_id="test_session_002"
    )
    print(f"Success: {result2['success']}")
    if not result2['success']:
        print(f"Error: {result2.get('error', 'Unknown error')}")
        print(f"Error type: {result2.get('error_type', 'Unknown')}")
    print(f"Response: {result2['response_text'][:100]}...")
    if 'planning' in result2['stages']:
        print(f"Planning required: {result2['stages']['planning']['planning_required']}")
    print(f"Total time: {result2['timing']['total_time_ms']:.2f}ms")
    
    # Test 3: Empty input
    print("\nTest 3: Empty input")
    result3 = process_cognitive_cycle(
        user_input="",
        owner_id="test_user",
        session_id="test_session_003"
    )
    print(f"Success: {result3['success']}")
    if not result3['success']:
        print(f"Error: {result3.get('error', 'Unknown error')}")
        print(f"Error type: {result3.get('error_type', 'Unknown')}")
    print(f"Response: {result3['response_text']}")
    print(f"Total time: {result3['timing']['total_time_ms']:.2f}ms")
    
    # Test 4: Determine if all stages completed
    print("\nTest 4: Stage completion verification")
    if result1['success']:
        print(f"All stages completed: {all(stage.get('completed', False) for stage in result1['stages'].values() if isinstance(stage, dict))}")
    else:
        print("Test 1 failed, skipping stage completion check")
    
    print("\n" + "=" * 50)
    print("V10.6 Cognitive Orchestrator test completed")

if __name__ == "__main__":
    test_cognitive_orchestrator()