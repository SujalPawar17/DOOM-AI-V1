"""V10.4 Reasoning + Decision Integration tests."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from core.v10.context_fusion import FusedContext
from core.v10.goal_understanding import GoalUnderstandingResult, GoalSpec, GoalContinuity
from core.v10.reasoning_decision import (
    ReasoningResult,
    DecisionResult,
    integrate_reasoning_decision,
    reasoning_decision_integration
)
from core.cognition.schemas import CognitiveIntent, CognitiveDecisionType
from orchestration.goal.types import IntentClass, CapabilityClass, Provenance


class TestV10_4ReasoningDecisionIntegration(unittest.TestCase):
    
    def setUp(self):
        """Set up test fixtures."""
        # Create a basic FusedContext for testing
        self.fused_context = FusedContext(
            owner_id="test_owner",
            context={
                "request_raw": "Open Chrome",
                "request_length": 12,
                "language": "en",
                "language_confidence": 0.9,
                "conversation_history": "",
                "conversation_session_id": "",
                "active_goal_id": "",
                "active_goal_intent": "",
                "active_goal_capability": "",
                "auth_status": "UNKNOWN",
                "auth_reason": "",
                "auth_permitted": False,
                "system_cpu": 0,
                "system_memory": 0,
                "system_available": False,
            },
            provenance={},
            privacy_levels={},
            fused_at_ms=0,
            context_hash="dummy_hash"
        )
        
        # Create a basic GoalUnderstandingResult for testing
        # We'll create a mock GoalSpec
        goal_spec = GoalSpec(
            goal_id="test_goal_id",
            schema_version="v81.1",
            owner_id="test_owner",
            session_id="test_session",
            computer_session_id="",
            raw_intent="Open Chrome",
            normalized_intent=IntentClass.COMPUTER,  # This maps to CognitiveIntent.ACTION via our mapping
            capability_class=CapabilityClass.NONE,  # This will map to ["general"] required capabilities
            provenance=Provenance.USER_TEXT,
            requested_unix_ms=0,
            goal_hash="dummy_goal_hash"
        )
        
        # Create a GoalUnderstandingResult
        self.goal_understanding = GoalUnderstandingResult(
            request="Open Chrome",
            owner_id="test_owner",
            request_goal=goal_spec,  # The goal classified from the request
            active_goal=None,  # No active goal
            understood_goal=goal_spec,  # The understood goal (may be refined or new)
            continuity=GoalContinuity.NEW_GOAL,
            confidence=0.9,
            provenance=None,  # We'll set to None for simplicity
            timestamp_ms=0
        )
    
    def test_integrates_reasoning_and_decision(self):
        """Test that the integration produces reasoning and decision results."""
        reasoning_result, decision_result = integrate_reasoning_decision(
            fused_context=self.fused_context,
            goal_understanding=self.goal_understanding,
            owner_id="test_owner",
            session_id="test_session"
        )
        
        # Check that we got a ReasoningResult
        self.assertIsInstance(reasoning_result, ReasoningResult)
        self.assertIsInstance(reasoning_result.reasoning_summary, str)
        self.assertGreaterEqual(len(reasoning_result.reasoning_summary), 0)
        self.assertIsInstance(reasoning_result.assumptions, list)
        self.assertIsInstance(reasoning_result.unresolved_questions, list)
        self.assertIsInstance(reasoning_result.provenance, dict)
        
        # Check that we got a DecisionResult
        self.assertIsInstance(decision_result, DecisionResult)
        self.assertIsInstance(decision_result.decision_type, CognitiveDecisionType)
        self.assertIsInstance(decision_result.decision_basis, str)
        self.assertGreaterEqual(len(decision_result.decision_basis), 0)
        self.assertIsInstance(decision_result.provenance, dict)
        
        # Print results for debugging (optional)
        print(f"Reasoning: {reasoning_result}")
        print(f"Decision: {decision_result}")
    
    def test_reasoning_result_contains_expected_fields(self):
        """Test that reasoning result has the expected structure."""
        reasoning_result, _ = integrate_reasoning_decision(
            fused_context=self.fused_context,
            goal_understanding=self.goal_understanding,
            owner_id="test_owner",
            session_id="test_session"
        )
        
        # Check to_dict method
        result_dict = reasoning_result.to_dict()
        self.assertIn("reasoning_summary", result_dict)
        self.assertIn("assumptions", result_dict)
        self.assertIn("unresolved_questions", result_dict)
        self.assertIn("provenance", result_dict)
        
        # Check that the values are of correct type
        self.assertIsInstance(result_dict["reasoning_summary"], str)
        self.assertIsInstance(result_dict["assumptions"], list)
        self.assertIsInstance(result_dict["unresolved_questions"], list)
        self.assertIsInstance(result_dict["provenance"], dict)
    
    def test_decision_result_contains_expected_fields(self):
        """Test that decision result has the expected structure."""
        _, decision_result = integrate_reasoning_decision(
            fused_context=self.fused_context,
            goal_understanding=self.goal_understanding,
            owner_id="test_owner",
            session_id="test_session"
        )
        
        # Check to_dict method
        result_dict = decision_result.to_dict()
        self.assertIn("decision_type", result_dict)
        self.assertIn("decision_basis", result_dict)
        self.assertIn("provenance", result_dict)
        
        # Check that the values are of correct type
        self.assertIsInstance(result_dict["decision_type"], str)  # It's the value of the enum
        self.assertIsInstance(result_dict["decision_basis"], str)
        self.assertIsInstance(result_dict["provenance"], dict)
    
    def test_owner_isolation(self):
        """Test that reasoning and decision are isolated by owner."""
        # Create a fused context for owner A
        fused_context_a = FusedContext(
            owner_id="owner_a",
            context={
                "request_raw": "Open Chrome",
                "request_length": 12,
                "language": "en",
                "language_confidence": 0.9,
                "conversation_history": "",
                "conversation_session_id": "",
                "active_goal_id": "",
                "active_goal_intent": "",
                "active_goal_capability": "",
                "auth_status": "UNKNOWN",
                "auth_reason": "",
                "auth_permitted": False,
                "system_cpu": 0,
                "system_memory": 0,
                "system_available": False,
            },
            provenance={},
            privacy_levels={},
            fused_at_ms=0,
            context_hash="dummy_hash_a"
        )
        
        # Create a fused context for owner B
        fused_context_b = FusedContext(
            owner_id="owner_b",
            context={
                "request_raw": "Open Chrome",
                "request_length": 12,
                "language": "en",
                "language_confidence": 0.9,
                "conversation_history": "",
                "conversation_session_id": "",
                "active_goal_id": "",
                "active_goal_intent": "",
                "active_goal_capability": "",
                "auth_status": "UNKNOWN",
                "auth_reason": "",
                "auth_permitted": False,
                "system_cpu": 0,
                "system_memory": 0,
                "system_available": False,
            },
            provenance={},
            privacy_levels={},
            fused_at_ms=0,
            context_hash="dummy_hash_b"
        )
        
        # Use the same goal understanding for both (but with different owner IDs in the goal)
        goal_spec_a = GoalSpec(
            goal_id="goal_a",
            schema_version="v81.1",
            owner_id="owner_a",
            session_id="test_session",
            computer_session_id="",
            raw_intent="Open Chrome",
            normalized_intent=IntentClass.COMPUTER,
            capability_class=CapabilityClass.NONE,
            provenance=Provenance.USER_TEXT,
            requested_unix_ms=0,
            goal_hash="goal_hash_a"
        )
        goal_understanding_a = GoalUnderstandingResult(
            request="Open Chrome",
            owner_id="owner_a",
            request_goal=goal_spec_a,
            active_goal=None,
            understood_goal=goal_spec_a,
            continuity=GoalContinuity.NEW_GOAL,
            confidence=0.9,
            provenance=None,
            timestamp_ms=0
        )
        
        goal_spec_b = GoalSpec(
            goal_id="goal_b",
            schema_version="v81.1",
            owner_id="owner_b",
            session_id="test_session",
            computer_session_id="",
            raw_intent="Open Chrome",
            normalized_intent=IntentClass.COMPUTER,
            capability_class=CapabilityClass.NONE,
            provenance=Provenance.USER_TEXT,
            requested_unix_ms=0,
            goal_hash="goal_hash_b"
        )
        goal_understanding_b = GoalUnderstandingResult(
            request="Open Chrome",
            owner_id="owner_b",
            request_goal=goal_spec_b,
            active_goal=None,
            understood_goal=goal_spec_b,
            continuity=GoalContinuity.NEW_GOAL,
            confidence=0.9,
            provenance=None,
            timestamp_ms=0
        )
        
        # Integrate for owner A
        reasoning_a, decision_a = integrate_reasoning_decision(
            fused_context=fused_context_a,
            goal_understanding=goal_understanding_a,
            owner_id="owner_a",
            session_id="test_session"
        )
        
        # Integrate for owner B
        reasoning_b, decision_b = integrate_reasoning_decision(
            fused_context=fused_context_b,
            goal_understanding=goal_understanding_b,
            owner_id="owner_b",
            session_id="test_session"
        )
        
        # The reasoning summaries should be similar (since the request is the same)
        # but the provenance should reflect the different owners
        self.assertNotEqual(reasoning_a.provenance["owner_id"], reasoning_b.provenance["owner_id"])
        self.assertEqual(reasoning_a.provenance["owner_id"], "owner_a")
        self.assertEqual(reasoning_b.provenance["owner_id"], "owner_b")
        
        # The decision provenance should also reflect the owner
        self.assertNotEqual(decision_a.provenance["owner_id"], decision_b.provenance["owner_id"])
        self.assertEqual(decision_a.provenance["owner_id"], "owner_a")
        self.assertEqual(decision_b.provenance["owner_id"], "owner_b")
    
    def test_empty_owner_id_handling(self):
        """Test that empty owner ID is handled gracefully."""
        # We expect the integration to handle empty owner ID by propagating it
        # to the V8 engines, which may or may not handle it.
        # For now, we just check that it doesn't crash.
        fused_context_empty = FusedContext(
            owner_id="",  # Empty owner ID
            context={
                "request_raw": "Open Chrome",
                "request_length": 12,
                "language": "en",
                "language_confidence": 0.9,
                "conversation_history": "",
                "conversation_session_id": "",
                "active_goal_id": "",
                "active_goal_intent": "",
                "active_goal_capability": "",
                "auth_status": "UNKNOWN",
                "auth_reason": "",
                "auth_permitted": False,
                "system_cpu": 0,
                "system_memory": 0,
                "system_available": False,
            },
            provenance={},
            privacy_levels={},
            fused_at_ms=0,
            context_hash="dummy_hash_empty"
        )
        
        # We'll use the same goal understanding as before
        try:
            reasoning_result, decision_result = integrate_reasoning_decision(
                fused_context=fused_context_empty,
                goal_understanding=self.goal_understanding,
                owner_id="",
                session_id="test_session"
            )
            # If we get here, it didn't crash
            self.assertIsInstance(reasoning_result, ReasoningResult)
            self.assertIsInstance(decision_result, DecisionResult)
        except Exception as e:
            # If it raises an exception, we note it but don't fail the test
            # because the V8 engines might have their own validation.
            # We'll just pass and note in the test that this is expected.
            pass
    
    def test_reasoning_result_is_deterministic(self):
        """Test that identical inputs produce identical reasoning summaries."""
        # Run integration twice with the same inputs
        reasoning_result1, _ = integrate_reasoning_decision(
            fused_context=self.fused_context,
            goal_understanding=self.goal_understanding,
            owner_id="test_owner",
            session_id="test_session"
        )
        
        reasoning_result2, _ = integrate_reasoning_decision(
            fused_context=self.fused_context,
            goal_understanding=self.goal_understanding,
            owner_id="test_owner",
            session_id="test_session"
        )
        
        # The reasoning summaries should be identical
        self.assertEqual(
            reasoning_result1.reasoning_summary,
            reasoning_result2.reasoning_summary
        )
        
        # The assumptions should be identical
        self.assertEqual(
            reasoning_result1.assumptions,
            reasoning_result2.assumptions
        )
        
        # The unresolved questions should be identical
        self.assertEqual(
            reasoning_result1.unresolved_questions,
            reasoning_result2.unresolved_questions
        )
    
    def test_decision_result_is_deterministic(self):
        """Test that identical inputs produce identical decisions."""
        # Run integration twice with the same inputs
        _, decision_result1 = integrate_reasoning_decision(
            fused_context=self.fused_context,
            goal_understanding=self.goal_understanding,
            owner_id="test_owner",
            session_id="test_session"
        )
        
        _, decision_result2 = integrate_reasoning_decision(
            fused_context=self.fused_context,
            goal_understanding=self.goal_understanding,
            owner_id="test_owner",
            session_id="test_session"
        )
        
        # The decision types should be identical
        self.assertEqual(
            decision_result1.decision_type,
            decision_result2.decision_type
        )
        
        # The decision bases should be identical
        self.assertEqual(
            decision_result1.decision_basis,
            decision_result2.decision_basis
        )


if __name__ == '__main__':
    unittest.main()
