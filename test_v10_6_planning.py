#!/usr/bin/env python
import os
os.environ["PROACTIVE_V8_ENABLED"] = "1"
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "1"

from core.v10.cognitive_orchestrator import process_cognitive_cycle

def test_planning_request():
    # This request should trigger planning because it involves multiple steps
    user_input = "Create a Python file on my desktop called system_info.py, run it, and verify the result"
    result = process_cognitive_cycle(
        user_input=user_input,
        owner_id="test_user",
        session_id="planning_test"
    )
    
    print("=== V10.6 Cognitive Orchestrator Planning Test ===")
    print(f"Request: {user_input}")
    print(f"Success: {result['success']}")
    if not result['success']:
        print(f"Error: {result.get('error', 'Unknown error')}")
        print(f"Error type: {result.get('error_type', 'Unknown')}")
        return
    
    print(f"Response: {result['response_text']}")
    print(f"Planning required: {result['stages']['planning']['planning_required']}")
    print(f"Planning successful: {result['stages']['planning']['planning_successful']}")
    if result['stages']['planning']['planning_successful']:
        print(f"Plan ID: {result['stages']['planning'].get('plan_id', 'None')}")
    else:
        print(f"Planning error: {result['stages']['planning'].get('error', 'None')}")
    
    # Also show the decision type from reasoning/decision stage
    print(f"Decision type: {result['stages']['reasoning_decision']['decision_type']}")
    
    # Check if we went through authorization and verification when planning was successful
    if result['stages']['planning']['planning_required'] and result['stages']['planning']['planning_successful']:
        print("Authorization stage:", result['stages'].get('authorization', 'Not present'))
        print("Verification stage:", result['stages'].get('verification', 'Not present'))
    
    print(f"Total time: {result['timing']['total_time_ms']:.2f}ms")
    print("==================================================")

if __name__ == "__main__":
    test_planning_request()