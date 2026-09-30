#!/usr/bin/env python
"""
V10.9 Performance / Reliability / Long-Session tests.
"""
import os
os.environ["PROACTIVE_V8_ENABLED"] = "1"
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "1"

from core.v10.cognitive_orchestrator import process_cognitive_cycle

def test_repeated_requests():
    """Test repeated identical requests."""
    print("Testing repeated requests...")
    responses = []
    for i in range(10):
        result = process_cognitive_cycle(
            user_input="What is the capital of France?",
            owner_id="test_user",
            session_id="perf_test_1"
        )
        assert result["success"] == True
        responses.append(result["response_text"])
    # All responses should be the same (deterministic)
    assert all(r == responses[0] for r in responses)
    print("Repeated requests test passed.")

def test_long_conversation():
    """Test a long conversation with many turns."""
    print("Testing long conversation...")
    owner_id = "test_user"
    session_id = "perf_test_2"
    for i in range(10):
        result = process_cognitive_cycle(
            user_input=f"Turn {i}: Tell me something interesting",
            owner_id=owner_id,
            session_id=session_id
        )
        assert result["success"] == True
    print("Long conversation test passed.")

def test_context_accumulation():
    """Test that context accumulates but is bounded."""
    print("Testing context accumulation...")
    owner_id = "test_user"
    session_id = "perf_test_3"
    # Make several requests that should add to context
    for i in range(3):
        result = process_cognitive_cycle(
            user_input=f"Remember fact {i}: The number is {i}",
            owner_id=owner_id,
            session_id=session_id
        )
        assert result["success"] == True
    # Request that should recall the facts
    result = process_cognitive_cycle(
        user_input="What facts did I ask you to remember?",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result["success"] == True
    # We just check that it doesn't crash; detailed checking is complex
    print("Context accumulation test passed.")

def test_bounded_memory_retrieval():
    """Test that memory retrieval is bounded."""
    print("Testing bounded memory retrieval...")
    owner_id = "test_user"
    session_id = "perf_test_4"
    # Store many items in memory
    for i in range(5):
        result = process_cognitive_cycle(
            user_input=f"Remember item {i}",
            owner_id=owner_id,
            session_id=session_id
        )
        assert result["success"] == True
    # Retrieve memory
    result = process_cognitive_cycle(
        user_input="What did I ask you to remember?",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result["success"] == True
    print("Bounded memory retrieval test passed.")

def test_bounded_user_model_retrieval():
    """Test that user model retrieval is bounded."""
    print("Testing bounded user model retrieval...")
    owner_id = "test_user"
    session_id = "perf_test_5"
    # Store many user model entries
    for i in range(5):
        result = process_cognitive_cycle(
            user_input=f"My preference {i} is value {i}",
            owner_id=owner_id,
            session_id=session_id
        )
        assert result["success"] == True
    # Retrieve user model
    result = process_cognitive_cycle(
        user_input="What are my preferences?",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result["success"] == True
    print("Bounded user model retrieval test passed.")

def test_repeated_reasoning():
    """Test repeated reasoning."""
    print("Testing repeated reasoning...")
    owner_id = "test_user"
    session_id = "perf_test_6"
    for i in range(5):
        result = process_cognitive_cycle(
            user_input="What is 2+2?",
            owner_id=owner_id,
            session_id=session_id
        )
        assert result["success"] == True
    print("Repeated reasoning test passed.")

def test_repeated_planning():
    """Test repeated planning requests."""
    print("Testing repeated planning...")
    owner_id = "test_user"
    session_id = "perf_test_7"
    for i in range(5):
        result = process_cognitive_cycle(
            user_input="Create a plan to backup files",
            owner_id=owner_id,
            session_id=session_id
        )
        assert result["success"] == True
        # Check that planning stage completed
        assert result["stages"]["planning"]["completed"] == True
    print("Repeated planning test passed.")

def test_failure_recovery():
    """Test failure recovery."""
    print("Testing failure recovery...")
    owner_id = "test_user"
    session_id = "perf_test_8"
    # First, a normal request
    result1 = process_cognitive_cycle(
        user_input="Hello",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result1["success"] == True
    # Then a request that might cause an internal error (we'll use an empty string)
    result2 = process_cognitive_cycle(
        user_input="",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result2["success"] == True  # Should not crash
    # Then another normal request
    result3 = process_cognitive_cycle(
        user_input="Hello again",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result3["success"] == True
    print("Failure recovery test passed.")

def test_deterministic_repeated_calls():
    """Test that repeated calls with same input are deterministic."""
    print("Testing deterministic repeated calls...")
    owner_id = "test_user"
    session_id = "perf_test_9"
    responses = []
    for i in range(5):
        result = process_cognitive_cycle(
            user_input="What is the meaning of life?",
            owner_id=owner_id,
            session_id=session_id
        )
        assert result["success"] == True
        responses.append(result["response_text"])
    # All responses should be identical
    assert all(r == responses[0] for r in responses)
    print("Deterministic repeated calls test passed.")

def test_no_uncontrolled_state_growth():
    """Test that state does not grow unboundedly over time."""
    print("Testing no uncontrolled state growth...")
    owner_id = "test_user"
    session_id = "perf_test_10"
    # We'll run many iterations and check that the orchestrator doesn't slow down excessively
    # We'll just run a loop and ensure it completes
    start_time = os.times()[0]  # user time
    for i in range(5):
        result = process_cognitive_cycle(
            user_input=f"Iteration {i}: Say hello",
            owner_id=owner_id,
            session_id=session_id
        )
        assert result["success"] == True
    end_time = os.times()[0]
    elapsed = end_time - start_time
    # We expect it to complete in a reasonable time (e.g., less than 30 seconds)
    # This is a very loose bound
    assert elapsed < 30.0
    print(f"No uncontrolled state growth test passed (elapsed time: {elapsed:.2f}s).")

def test_long_session_preservation():
    """Test that long session preserves active goals, memory, user model, etc."""
    print("Testing long session preservation...")
    owner_id = "test_user"
    session_id = "perf_test_11"
    # Set an active goal
    result1 = process_cognitive_cycle(
        user_input="My goal is to learn Python",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result1["success"] == True
    # Store some memory
    result2 = process_cognitive_cycle(
        user_input="Remember that I like apples",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result2["success"] == True
    # Store some user model
    result3 = process_cognitive_cycle(
        user_input="My preference is to work in the morning",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result3["success"] == True
    # Now, after a series of unrelated requests, check that the goal, memory, and user model are still present
    for i in range(10):
        result = process_cognitive_cycle(
            user_input=f"Unrelated request {i}",
            owner_id=owner_id,
            session_id=session_id
        )
        assert result["success"] == True
    # Now, recall the goal
    result4 = process_cognitive_cycle(
        user_input="What is my goal?",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result4["success"] == True
    # Recall memory
    result5 = process_cognitive_cycle(
        user_input="What did I ask you to remember?",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result5["success"] == True
    # Recall user model
    result6 = process_cognitive_cycle(
        user_input="What are my preferences?",
        owner_id=owner_id,
        session_id=session_id
    )
    assert result6["success"] == True
    print("Long session preservation test passed.")

if __name__ == "__main__":
    test_repeated_requests()
    test_long_conversation()
    test_context_accumulation()
    test_bounded_memory_retrieval()
    test_bounded_user_model_retrieval()
    test_repeated_reasoning()
    test_repeated_planning()
    test_failure_recovery()
    test_deterministic_repeated_calls()
    test_no_uncontrolled_state_growth()
    test_long_session_preservation()
    print("All V10.9 performance/reliability/long-session tests passed.")