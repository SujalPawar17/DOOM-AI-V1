"""V10.3 Goal Understanding + Continuity tests."""

from __future__ import annotations

import os
import sys
import unittest

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.v10.goal_understanding import understand_goal, GoalUnderstandingResult, GoalContinuity
from orchestration.goal.types import GoalSpec, IntentClass, CapabilityClass, Provenance


class TestV10_3GoalUnderstandingContinuity(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        pass

    def tearDown(self):
        """Clean up test fixtures."""
        pass

    def test_no_active_goal_new_goal(self):
        """Test that a request classifying to a goal with no active goal results in NEW_GOAL."""
        result = understand_goal(
            request="Help me build a website",
            owner_id="test_user",
            session_id="test_session"
        )
        self.assertIsInstance(result, GoalUnderstandingResult)
        self.assertEqual(result.continuity, GoalContinuity.NEW_GOAL)
        self.assertIsNotNone(result.request_goal)
        self.assertEqual(result.request_goal.normalized_intent, IntentClass.COMPUTER)
        self.assertIsNotNone(result.understood_goal)
        self.assertEqual(result.understood_goal.goal_id, result.request_goal.goal_id)
        self.assertGreater(result.confidence, 0.0)

    def test_no_active_goal_no_goal_classification(self):
        """Test that a request that does not classify to a goal with no active goal results in NO_ACTIVE_GOAL."""
        result = understand_goal(
            request="Hello, how are you?",
            owner_id="test_user",
            session_id="test_session"
        )
        self.assertIsInstance(result, GoalUnderstandingResult)
        self.assertEqual(result.continuity, GoalContinuity.NO_ACTIVE_GOAL)
        self.assertIsNone(result.request_goal)
        self.assertIsNone(result.understood_goal)
        self.assertEqual(result.confidence, 0.0)

    def test_active_goal_completion_signal(self):
        """Test that a completion signal with an active goal results in COMPLETE_GOAL."""
        # We need to have an active goal in the fused context.
        # For simplicity, we'll pass a fused context that contains goal state data.
        # However, note that the understand_goal function does not currently use fused_context for active goal
        # extraction in the test. We will need to mock or pass a fused context.
        # Since we are unit testing, we can pass a fused context with goal state data.
        fused_context = {
            "goal_state": {
                "active_goal_id": "goal_123",
                "active_goal_schema_version": "1",
                "active_goal_owner_id": "test_user",
                "active_goal_session_id": "test_session",
                "active_goal_computer_session_id": "",
                "active_goal_title": "Build a website",
                "active_goal_intent": "COMPUTER",
                "active_goal_capability": "computer",
                "active_goal_provenance": "USER_TEXT",
                "active_goal_requested_unix_ms": "1234567890",
                "active_goal_goal_hash": "abc123",
            }
        }
        result = understand_goal(
            request="I'm done",
            owner_id="test_user",
            session_id="test_session",
            fused_context=fused_context
        )
        self.assertIsInstance(result, GoalUnderstandingResult)
        self.assertEqual(result.continuity, GoalContinuity.COMPLETE_GOAL)
        self.assertIsNotNone(result.active_goal)
        self.assertIsNone(result.understood_goal)  # Completed goal is no longer active
        self.assertGreater(result.confidence, 0.0)

    def test_active_goal_abandonment_signal(self):
        """Test that an abandonment signal with an active goal results in ABANDON_GOAL."""
        fused_context = {
            "goal_state": {
                "active_goal_id": "goal_123",
                "active_goal_schema_version": "1",
                "active_goal_owner_id": "test_user",
                "active_goal_session_id": "test_session",
                "active_goal_computer_session_id": "",
                "active_goal_title": "Build a website",
                "active_goal_intent": "COMPUTER",
                "active_goal_capability": "computer",
                "active_goal_provenance": "USER_TEXT",
                "active_goal_requested_unix_ms": "1234567890",
                "active_goal_goal_hash": "abc123",
            }
        }
        result = understand_goal(
            request="Forget about it",
            owner_id="test_user",
            session_id="test_session",
            fused_context=fused_context
        )
        self.assertIsInstance(result, GoalUnderstandingResult)
        self.assertEqual(result.continuity, GoalContinuity.ABANDON_GOAL)
        self.assertIsNotNone(result.active_goal)
        self.assertIsNone(result.understood_goal)  # Abandoned goal is no longer active
        self.assertGreater(result.confidence, 0.0)

    def test_active_goal_continuation_signal(self):
        """Test that a continuation signal with an active goal results in CONTINUE_GOAL."""
        fused_context = {
            "goal_state": {
                "active_goal_id": "goal_123",
                "active_goal_schema_version": "1",
                "active_goal_owner_id": "test_user",
                "active_goal_session_id": "test_session",
                "active_goal_computer_session_id": "",
                "active_goal_title": "Build a website",
                "active_goal_intent": "COMPUTER",
                "active_goal_capability": "computer",
                "active_goal_provenance": "USER_TEXT",
                "active_goal_requested_unix_ms": "1234567890",
                "active_goal_goal_hash": "abc123",
            }
        }
        result = understand_goal(
            request="Continue with the website",
            owner_id="test_user",
            session_id="test_session",
            fused_context=fused_context
        )
        self.assertIsInstance(result, GoalUnderstandingResult)
        self.assertEqual(result.continuity, GoalContinuity.CONTINUE_GOAL)
        self.assertIsNotNone(result.active_goal)
        self.assertIsNotNone(result.understood_goal)
        self.assertEqual(result.understood_goal.goal_id, result.active_goal.goal_id)
        self.assertGreater(result.confidence, 0.0)

    def test_active_goal_refinement_signal(self):
        """Test that a refinement signal with an active goal results in REFINE_GOAL."""
        fused_context = {
            "goal_state": {
                "active_goal_id": "goal_123",
                "active_goal_schema_version": "1",
                "active_goal_owner_id": "test_user",
                "active_goal_session_id": "test_session",
                "active_goal_computer_session_id": "",
                "active_goal_title": "Build a website",
                "active_goal_intent": "COMPUTER",
                "active_goal_capability": "computer",
                "active_goal_provenance": "USER_TEXT",
                "active_goal_requested_unix_ms": "1234567890",
                "active_goal_goal_hash": "abc123",
            }
        }
        result = understand_goal(
            request="Make the website more interactive",
            owner_id="test_user",
            session_id="test_session",
            fused_context=fused_context
        )
        self.assertIsInstance(result, GoalUnderstandingResult)
        self.assertEqual(result.continuity, GoalContinuity.REFINE_GOAL)
        self.assertIsNotNone(result.active_goal)
        self.assertIsNotNone(result.understood_goal)
        self.assertEqual(result.understood_goal.goal_id, result.active_goal.goal_id)
        self.assertGreater(result.confidence, 0.0)

    def test_active_goal_same_goal_classification(self):
        """Test that a request classifying to the same goal as active goal results in CONTINUE_GOAL."""
        fused_context = {
            "goal_state": {
                "active_goal_id": "goal_123",
                "active_goal_schema_version": "1",
                "active_goal_owner_id": "test_user",
                "active_goal_session_id": "test_session",
                "active_goal_computer_session_id": "",
                "active_goal_title": "Build a website",
                "active_goal_intent": "COMPUTER",
                "active_goal_capability": "computer",
                "active_goal_provenance": "USER_TEXT",
                "active_goal_requested_unix_ms": "1234567890",
                "active_goal_goal_hash": "abc123",
            }
        }
        result = understand_goal(
            request="Help me build the website",
            owner_id="test_user",
            session_id="test_session",
            fused_context=fused_context
        )
        self.assertIsInstance(result, GoalUnderstandingResult)
        self.assertEqual(result.continuity, GoalContinuity.CONTINUE_GOAL)
        self.assertIsNotNone(result.request_goal)
        self.assertIsNotNone(result.active_goal)
        self.assertIsNotNone(result.understood_goal)
        self.assertEqual(result.understood_goal.goal_id, result.active_goal.goal_id)
        self.assertGreater(result.confidence, 0.0)

    def test_active_goal_different_goal_classification(self):
        """Test that a request classifying to a different goal results in NEW_GOAL."""
        fused_context = {
            "goal_state": {
                "active_goal_id": "goal_123",
                "active_goal_schema_version": "1",
                "active_goal_owner_id": "test_user",
                "active_goal_session_id": "test_session",
                "active_goal_computer_session_id": "",
                "active_goal_title": "Build a website",
                "active_goal_intent": "COMPUTER",
                "active_goal_capability": "computer",
                "active_goal_provenance": "USER_TEXT",
                "active_goal_requested_unix_ms": "1234567890",
                "active_goal_goal_hash": "abc123",
            }
        }
        result = understand_goal(
            request="Help me write a novel",
            owner_id="test_user",
            session_id="test_session",
            fused_context=fused_context
        )
        self.assertIsInstance(result, GoalUnderstandingResult)
        self.assertEqual(result.continuity, GoalContinuity.NEW_GOAL)
        self.assertIsNotNone(result.request_goal)
        self.assertIsNotNone(result.active_goal)
        self.assertIsNotNone(result.understood_goal)
        self.assertNotEqual(result.understood_goal.goal_id, result.active_goal.goal_id)
        self.assertGreater(result.confidence, 0.0)

    def test_active_goal_no_request_goal_classification(self):
        """Test that a request that does not classify to a goal with an active goal results in CONTINUE_GOAL."""
        fused_context = {
            "goal_state": {
                "active_goal_id": "goal_123",
                "active_goal_schema_version": "1",
                "active_goal_owner_id": "test_user",
                "active_goal_session_id": "test_session",
                "active_goal_computer_session_id": "",
                "active_goal_title": "Build a website",
                "active_goal_intent": "COMPUTER",
                "active_goal_capability": "computer",
                "active_goal_provenance": "USER_TEXT",
                "active_goal_requested_unix_ms": "1234567890",
                "active_goal_goal_hash": "abc123",
            }
        }
        result = understand_goal(
            request="What's the weather like?",
            owner_id="test_user",
            session_id="test_session",
            fused_context=fused_context
        )
        self.assertIsInstance(result, GoalUnderstandingResult)
        self.assertEqual(result.continuity, GoalContinuity.CONTINUE_GOAL)
        self.assertIsNone(result.request_goal)
        self.assertIsNotNone(result.active_goal)
        self.assertIsNotNone(result.understood_goal)
        self.assertEqual(result.understood_goal.goal_id, result.active_goal.goal_id)
        self.assertGreater(result.confidence, 0.0)

    def test_owner_isolation(self):
        """Test that goals are isolated by owner."""
        # We'll test by setting a goal for owner A and then checking owner B
        fused_context_owner_a = {
            "goal_state": {
                "active_goal_id": "goal_123",
                "active_goal_schema_version": "1",
                "active_goal_owner_id": "owner_a",
                "active_goal_session_id": "test_session",
                "active_goal_computer_session_id": "",
                "active_goal_title": "Build a website",
                "active_goal_intent": "COMPUTER",
                "active_goal_capability": "computer",
                "active_goal_provenance": "USER_TEXT",
                "active_goal_requested_unix_ms": "1234567890",
                "active_goal_goal_hash": "abc123",
            }
        }
        # Owner A's request should see their active goal
        result_a = understand_goal(
            request="What is my goal?",
            owner_id="owner_a",
            session_id="test_session",
            fused_context=fused_context_owner_a
        )
        self.assertIsNotNone(result_a.active_goal)
        self.assertEqual(result_a.active_goal.owner_id, "owner_a")

        # Owner B's request should not see owner A's active goal (because we are not passing owner A's fused context to owner B)
        fused_context_owner_b = {
            "goal_state": {
                "active_goal_id": "goal_456",
                "active_goal_schema_version": "1",
                "active_goal_owner_id": "owner_b",
                "active_goal_session_id": "test_session",
                "active_goal_computer_session_id": "",
                "active_goal_title": "Write a novel",
                "active_goal_intent": "COMPUTER",
                "active_goal_capability": "computer",
                "active_goal_provenance": "USER_TEXT",
                "active_goal_requested_unix_ms": "1234567890",
                "active_goal_goal_hash": "def456",
            }
        }
        result_b = understand_goal(
            request="What is my goal?",
            owner_id="owner_b",
            session_id="test_session",
            fused_context=fused_context_owner_b
        )
        self.assertIsNotNone(result_b.active_goal)
        self.assertEqual(result_b.active_goal.owner_id, "owner_b")
        self.assertNotEqual(result_b.active_goal.goal_id, result_a.active_goal.goal_id)

    def test_invalid_owner_id(self):
        """Test that invalid owner ID raises an error."""
        with self.assertRaises(Exception):
            understand_goal(
                request="Help me",
                owner_id="",
                session_id="test_session"
            )

        with self.assertRaises(Exception):
            understand_goal(
                request="Help me",
                owner_id=None,
                session_id="test_session"
            )


if __name__ == '__main__':
    unittest.main()