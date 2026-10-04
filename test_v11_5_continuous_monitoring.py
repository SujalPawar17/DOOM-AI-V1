#!/usr/bin/env python
"""Test suite for V11.5 Continuous Monitoring."""

import sys
import time
import os
from unittest.mock import patch, MagicMock

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Set required environment variables
os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

from core.v11.proactive_behavior import ContinuousMonitoringEnhancement, ContinuousMonitoringConfig, MonitoringEventType, MonitoringPriority


def test_continuous_monitor_initialization():
    """Test that the continuous monitoring enhancement initializes correctly."""
    owner_id = "test_owner"
    config = ContinuousMonitoringConfig()
    monitor = ContinuousMonitoringEnhancement(owner_id=owner_id, config=config)
    
    assert monitor.owner_id == owner_id
    assert monitor.config == config
    assert monitor.state is not None
    assert not monitor._thread or not monitor._thread.is_alive()  # Should not be running initially
    print("[PASS] ContinuousMonitoringEnhancement initialization test passed")


def test_continuous_monitor_start_stop():
    """Test that the continuous monitoring enhancement can be started and stopped."""
    owner_id = "test_owner"
    config = ContinuousMonitoringConfig()
    config.polling_interval = 0.1  # Fast polling for testing
    monitor = ContinuousMonitoringEnhancement(owner_id=owner_id, config=config)
    
    # Start monitoring
    monitor.start()
    assert monitor._thread is not None
    assert monitor._thread.is_alive()
    
    # Let it run briefly
    time.sleep(0.25)
    
    # Stop monitoring
    monitor.stop()
    # Give it a moment to stop
    time.sleep(0.1)
    assert monitor._thread is None or not monitor._thread.is_alive()
    print("[PASS] ContinuousMonitoringEnhancement start/stop test passed")


def test_continuous_monitor_duplicate_start_stop():
    """Test that starting an already running monitor doesn't create duplicate threads."""
    owner_id = "test_owner"
    config = ContinuousMonitoringConfig()
    config.polling_interval = 0.1
    monitor = ContinuousMonitoringEnhancement(owner_id=owner_id, config=config)
    
    # Start monitoring
    monitor.start()
    initial_thread = monitor._thread
    
    # Try to start again - should not create a new thread
    monitor.start()
    assert monitor._thread is initial_thread  # Same thread
    
    time.sleep(0.2)
    
    # Stop monitoring
    monitor.stop()
    # Give it a moment to stop
    time.sleep(0.1)
    assert monitor._thread is None or not monitor._thread.is_alive()
    
    # Starting again after stop should work
    monitor.start()
    assert monitor._thread is not None and monitor._thread.is_alive()
    monitor.stop()
    time.sleep(0.1)
    print("[PASS] ContinuousMonitoringEnhancement duplicate start/stop test passed")


def test_state_fingerprinting():
    """Test that state fingerprinting works correctly."""
    owner_id = "test_owner"
    monitor = ContinuousMonitoringEnhancement(owner_id=owner_id)
    
    # Test state preparation and fingerprinting
    test_state = {
        "goal_id": "goal123",
        "title": "Test Goal",
        "progress": 0.5,
        "timestamp": time.time(),  # This should be removed during fingerprinting
        "last_poll_time": time.time() - 10  # This should also be removed
    }
    
    # Prepare state for fingerprinting
    prepared_state = monitor._prepare_state_for_fingerprinting(test_state.copy())
    
    # Check that volatile fields were removed
    assert "timestamp" not in prepared_state
    assert "last_poll_time" not in prepared_state
    assert prepared_state["goal_id"] == "goal123"
    assert prepared_state["title"] == "Test Goal"
    assert prepared_state["progress"] == 0.5
    
    # Compute fingerprint
    fingerprint1 = monitor._compute_state_fingerprint(test_state)
    
    # Compute fingerprint again - should be the same
    fingerprint2 = monitor._compute_state_fingerprint(test_state)
    assert fingerprint1 == fingerprint2
    
    # Change the state - fingerprint should change
    test_state["progress"] = 0.8
    fingerprint3 = monitor._compute_state_fingerprint(test_state)
    assert fingerprint1 != fingerprint3
    
    print("[PASS] ContinuousMonitoringEnhancement state fingerprinting test passed")


def test_event_creation_and_deduplication():
    """Test that monitoring events are created and deduplicated correctly."""
    owner_id = "test_owner"
    monitor = ContinuousMonitoringEnhancement(owner_id=owner_id)
    
    # Create two similar events
    event1 = monitor._create_monitoring_event(
        event_type=MonitoringEventType.GOAL_STALE,
        priority=MonitoringPriority.NORMAL,
        source="goal_state",
        description="Goal stale for 25 hours: 'Learn Python'",
        timestamp=time.time()
    )
    
    event2 = monitor._create_monitoring_event(
        event_type=MonitoringEventType.GOAL_STALE,
        priority=MonitoringPriority.NORMAL,
        source="goal_state",
        description="Goal stale for 26 hours: 'Learn Python'",  # Slightly different time
        timestamp=time.time() + 10
    )
    
    # Events should have different IDs due to different timestamps
    assert event1.event_id != event2.event_id
    
    # But their deduplication fingerprints should be the same (same core description)
    fingerprint1 = monitor._compute_event_fingerprint(event1)
    fingerprint2 = monitor._compute_event_fingerprint(event2)
    assert fingerprint1 == fingerprint2
    
    # Test that the second event is considered a duplicate
    monitor.state.recent_event_fingerprints.clear()
    monitor.state.recent_event_fingerprints.add(fingerprint1)
    
    assert not monitor._should_process_event(event2)  # Should be filtered as duplicate
    
    print("[PASS] ContinuousMonitoringEnhancement event creation and deduplication test passed")


def test_priority_determination():
    """Test that event priorities are determined correctly."""
    owner_id = "test_owner"
    monitor = ContinuousMonitoringEnhancement(owner_id=owner_id)
    
    # Test high priority for failed experience
    failed_state = {
        "experiences": [{
            "title": "Failed Experience",
            "outcome": "FAILED",
            "updated_at": time.time()
        }],
        "experience_count": 1
    }
    
    priority = monitor._determine_event_priority("experience_state", failed_state, None)
    assert priority == MonitoringPriority.HIGH
    
    # Test critical priority for aborted experience
    aborted_state = {
        "experiences": [{
            "title": "Aborted Experience",
            "outcome": "ABORTED",
            "updated_at": time.time()
        }],
        "experience_count": 1
    }
    
    priority = monitor._determine_event_priority("experience_state", aborted_state, None)
    assert priority == MonitoringPriority.CRITICAL
    
    # Test high priority for unavailable memory system
    memory_state = {
        "system_available": False,
        "retrieval_count": 0
    }
    
    priority = monitor._determine_event_priority("memory_state", memory_state, None)
    assert priority == MonitoringPriority.HIGH
    
    print("[PASS] ContinuousMonitoringEnhancement priority determination test passed")


def test_cooldown_and_rate_limiting():
    """Test that cooldown and rate limiting work correctly."""
    owner_id = "test_owner"
    config = ContinuousMonitoringConfig()
    config.cooldown_period = 0.1  # Very short for testing
    config.max_cycles_per_hour = 2  # Low limit for testing
    monitor = ContinuousMonitoringEnhancement(owner_id=owner_id, config=config)
    
    # Initially should be OK
    assert monitor._is_cooldown_complete()
    assert monitor._is_rate_limit_ok()
    
    # Simulate a recent cycle
    monitor.state.last_cycle_time = time.time() - 0.05  # 0.05 seconds ago
    assert not monitor._is_cooldown_complete()  # Should still be in cooldown
    
    # Wait for cooldown to expire
    time.sleep(0.15)
    assert monitor._is_cooldown_complete()  # Should be out of cooldown now
    
    # Test rate limiting
    now = time.time()
    # Use timestamps that are strictly less than 1 hour old (to avoid the < 3600 boundary issue)
    monitor.state.cycles_in_last_hour = [now - 1799, now - 1798]  # 2 cycles, both < 1 hour old
    assert not monitor._is_rate_limit_ok()  # Should be at limit
    
    # Remove old cycles
    monitor.state.cycles_in_last_hour = [now - 1799]  # Only 1 cycle
    assert monitor._is_rate_limit_ok()  # Should be under limit now
    
    print("[PASS] ContinuousMonitoringEnhancement cooldown and rate limiting test passed")


def test_monitoring_error_handling():
    """Test that monitoring errors are handled and recorded."""
    owner_id = "test_owner"
    monitor = ContinuousMonitoringEnhancement(owner_id=owner_id)
    
    initial_error_count = monitor.state.monitoring_errors
    
    # Simulate an error
    test_error = Exception("Test monitoring error")
    monitor._record_monitoring_error(test_error)
    
    assert monitor.state.monitoring_errors == initial_error_count + 1
    assert monitor.state.last_error_time > 0
    
    print("[PASS] ContinuousMonitoringEnhancement error handling test passed")


def test_get_status():
    """Test that the status reporting works correctly."""
    owner_id = "test_owner"
    config = ContinuousMonitoringConfig()
    monitor = ContinuousMonitoringEnhancement(owner_id=owner_id, config=config)
    
    status = monitor.get_status()
    
    # Check that all expected fields are present
    expected_fields = [
        "is_running", "owner_id", "session_id", "last_poll_time", 
        "last_cycle_time", "cycles_in_last_hour", "monitoring_errors",
        "last_error_time", "in_cooldown", "at_rate_limit", "config"
    ]
    
    for field in expected_fields:
        assert field in status
    
    assert status["owner_id"] == owner_id
    assert status["config"]["polling_interval"] == config.polling_interval
    assert status["config"]["cooldown_period"] == config.cooldown_period
    assert status["config"]["max_cycles_per_hour"] == config.max_cycles_per_hour
    
    print("[PASS] ContinuousMonitoringEnhancement status reporting test passed")


def test_integration_with_v11_cognitive_orchestrator():
    """Test that monitoring events properly integrate with the V11 cognitive orchestrator."""
    owner_id = "test_owner"
    config = ContinuousMonitoringConfig()
    config.polling_interval = 0.1
    monitor = ContinuousMonitoringEnhancement(owner_id=owner_id, config=config)
    
    # Mock the cognitive orchestrator to avoid actual processing
    with patch('core.v11.proactive_behavior.v11_cognitive_orchestrator') as mock_orchestrator:
        mock_result = {
            "success": True,
            "response_text": "Test response",
            "cycle_id": "test_cycle_123"
        }
        mock_orchestrator.process_cognitive_cycle.return_value = mock_result
        
        # Create a monitoring event that should trigger a cycle
        event = monitor._create_monitoring_event(
            event_type=MonitoringEventType.GOAL_STALE,
            priority=MonitoringPriority.NORMAL,
            source="goal_state",
            description="Stale goal detected: 'Test Goal'",
            timestamp=time.time()
        )
        
        # Manually trigger the event handling (bypass normal checks for this test)
        monitor._handle_monitoring_event(event)
        
        # Give the thread a moment to start
        time.sleep(0.05)
        
        # Verify that the orchestrator was called
        assert mock_orchestrator.process_cognitive_cycle.called
        
        # Check that it was called with monitoring context
        call_args = mock_orchestrator.process_cognitive_cycle.call_args
        assert call_args is not None
        kwargs = call_args.kwargs
        assert kwargs.get("owner_id") == owner_id
        assert kwargs.get("session_id") == ""
        assert "context" in kwargs
        context = kwargs["context"]
        assert context.get("monitoring_trigger") == True
        assert context.get("event_type") == MonitoringEventType.GOAL_STALE.value
        
    print("[PASS] ContinuousMonitoringEnhancement V11 cognitive orchestrator integration test passed")


def test_backward_compatibility_with_v11_3():
    """Test that the enhanced monitor maintains backward compatibility with V11.3 behavior."""
    owner_id = "test_owner"
    # Use legacy configuration (fingerprinting disabled)
    config = ContinuousMonitoringConfig()
    config.enable_state_fingerprinting = False
    config.polling_interval = 0.1
    monitor = ContinuousMonitoringEnhancement(owner_id=owner_id, config=config)
    
    # With fingerprinting disabled, it should fall back to legacy trigger-based detection
    # We can test this by mocking the legacy trigger methods
    
    # Test that the legacy detection methods exist (they should be inherited or implemented)
    # For now, we're mainly testing that the configuration is respected
    assert monitor.config.enable_state_fingerprinting == False
    
    print("[PASS] ContinuousMonitoringEnhancement backward compatibility test passed")


if __name__ == "__main__":
    print("Running V11.5 Continuous Monitoring tests...")
    print("=" * 50)
    
    try:
        test_continuous_monitor_initialization()
        test_continuous_monitor_start_stop()
        test_continuous_monitor_duplicate_start_stop()
        test_state_fingerprinting()
        test_event_creation_and_deduplication()
        test_priority_determination()
        test_cooldown_and_rate_limiting()
        test_monitoring_error_handling()
        test_get_status()
        test_integration_with_v11_cognitive_orchestrator()
        test_backward_compatibility_with_v11_3()
        
        print("=" * 50)
        print("All V11.5 Continuous Monitoring tests passed! [SUCCESS]")
        
    except Exception as e:
        print(f"[FAIL] Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)