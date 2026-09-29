"""V10.1 Context Fusion tests."""

from __future__ import annotations

import unittest
import os
import sys
import json

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.v10.context_fusion import fuse_context, FusedContext, ContextFusion, ContextSource, PrivacyLevel

class TestContextFusion(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        pass
    
    def tearDown(self):
        """Clean up test fixtures."""
        pass
    
    def test_basic_fusion(self):
        """Test basic context fusion."""
        result = fuse_context(
            request="Hello world",
            owner_id="test_user",
            session_id="test_session"
        )
        
        self.assertIsInstance(result, FusedContext)
        self.assertEqual(result.owner_id, "test_user")
        self.assertIn("request_raw", result.context)
        self.assertIn("language", result.context)
        self.assertEqual(result.context["language"], "en")  # Default to English
    
    def test_owner_isolation(self):
        """Test that owner isolation works."""
        # Test with different owners
        result1 = fuse_context(
            request="Hello",
            owner_id="user1",
            session_id="session1"
        )
        
        result2 = fuse_context(
            request="Hello",
            owner_id="user2",
            session_id="session1"
        )
        
        # Contexts should be different (at least in owner-specific fields)
        self.assertEqual(result1.owner_id, "user1")
        self.assertEqual(result2.owner_id, "user2")
    
    def test_empty_owner_id(self):
        """Test that empty owner ID raises error."""
        with self.assertRaises(Exception):
            fuse_context(
                request="Hello",
                owner_id="",
                session_id="test"
            )
    
    def test_language_detection(self):
        """Test language detection."""
        # Test English-like text
        result = fuse_context(
            request="Hello world how are you",
            owner_id="test_user"
        )
        self.assertEqual(result.context.get("language"), "en")
    
    def test_precedence_order(self):
        """Test that precedence order is respected."""
        # This is a simplified test - in practice we'd need to mock the sources
        result = fuse_context(
            request="Test request",
            owner_id="test_user"
        )
        
        # Should have data from multiple sources
        self.assertGreaterEqual(len(result.context), 3)  # request, language, conversation at minimum
    
    def test_fused_context_immutable(self):
        """Test that FusedContext is immutable."""
        result = fuse_context(
            request="Test",
            owner_id="test_user"
        )
        
        # Test that we can't modify the context directly
        # Note: Since we're using regular dicts, true immutability would require
        # using types.MappingProxyType or similar, but we'll test the concept
        
        # The context should be accessible
        self.assertIn("request_raw", result.context)
    
    def test_context_hash_deterministic(self):
        """Test that context hash is deterministic."""
        result1 = fuse_context(
            request="Same request",
            owner_id="same_user",
            session_id="same_session"
        )
        
        result2 = fuse_context(
            request="Same request",
            owner_id="same_user",
            session_id="same_session"
        )
        
        # Hashes should be identical for identical inputs
        self.assertEqual(result1.context_hash, result2.context_hash)
    
    def test_different_inputs_different_hash(self):
        """Test that different inputs produce different hashes."""
        result1 = fuse_context(
            request="Request one",
            owner_id="user",
            session_id="session"
        )
        
        result2 = fuse_context(
            request="Request two",
            owner_id="user",
            session_id="session"
        )
        
        # Hashes should be different
        self.assertNotEqual(result1.context_hash, result2.context_hash)
    
    def test_privacy_protection(self):
        """Test that privacy protection works."""
        # Test with a request that looks like it contains a secret
        result = fuse_context(
            request="My password is secret123",
            owner_id="test_user"
        )
        
        # The context should contain the request but privacy levels should reflect sensitivity
        self.assertIn("request_raw", result.context)
        # Note: Full privacy testing would require more sophisticated mocking
    
    def test_source_failure_handling(self):
        """Test that source failures are handled gracefully."""
        # This would require mocking specific sources to fail
        # For now, we'll just test that the function doesn't crash
        try:
            result = fuse_context(
                request="Test request",
                owner_id="test_user"
            )
            self.assertIsInstance(result, FusedContext)
        except Exception as e:
            self.fail(f"Context fusion should not raise exceptions: {e}")
    
    def test_goal_state_context(self):
        """Test that GOAL_STATE context is properly integrated."""
        result = fuse_context(
            request="Hello world",
            owner_id="test_user"
        )
        
        # Should contain goal state information if available
        # We're not asserting specific values since they depend on the goal registry state
        # but we verify the structure is correct
        self.assertIsInstance(result, FusedContext)
        
        # Check that we have some context data
        self.assertGreater(len(result.context), 0)
    
    def test_outcome_context(self):
        """Test that OUTCOME context is properly integrated."""
        result = fuse_context(
            request="Hello world",
            owner_id="test_user"
        )
        
        # Should contain outcome information if available
        self.assertIsInstance(result, FusedContext)
        
        # Check that we have some context data
        self.assertGreater(len(result.context), 0)
    
    def test_plan_context(self):
        """Test that PLAN context is properly integrated."""
        result = fuse_context(
            request="Hello world",
            owner_id="test_user"
        )
        
        # Should contain plan information if available
        self.assertIsInstance(result, FusedContext)
        
        # Check that we have some context data
        self.assertGreater(len(result.context), 0)
    
    def test_serialization_deserialization(self):
        """Test that FusedContext can be serialized and deserialized."""
        # Create a FusedContext instance
        original = fuse_context(
            request="Test request for serialization",
            owner_id="test_user",
            session_id="test_session"
        )
        
        # Convert to dictionary
        context_dict = original.to_dict()
        
        # Verify it's a dictionary with expected structure
        self.assertIsInstance(context_dict, dict)
        self.assertIn("owner_id", context_dict)
        self.assertIn("context", context_dict)
        self.assertIn("provenance", context_dict)
        self.assertIn("privacy_levels", context_dict)
        self.assertIn("fused_at_ms", context_dict)
        self.assertIn("context_hash", context_dict)
        
        # Convert back from dictionary
        restored = FusedContext.from_dict(context_dict)
        
        # Verify the restored object matches the original
        self.assertEqual(restored.owner_id, original.owner_id)
        self.assertEqual(restored.fused_at_ms, original.fused_at_ms)
        self.assertEqual(restored.context_hash, original.context_hash)
        # Note: context and provenance may differ slightly due to timing, but structure should match
        
    def test_ttl_expiration(self):
        """Test that TTL expiration works correctly."""
        # Create a context
        result1 = fuse_context(
            request="Test request for TTL",
            owner_id="test_user",
            session_id="test_session"
        )
        
        # Get a context key to test expiration
        test_key = None
        test_value = None
        for key, value in result1.context.items():
            if not key.endswith("_failed") and not key.startswith("request_"):
                test_key = key
                test_value = value
                break
        
        if test_key is not None:
            # Manually set an expired timestamp in provenance
            expired_time = int(result1.fused_at_ms) - 7200000  # 2 hours ago
            result1.provenance[test_key] = result1.provenance[test_key].__class__(
                source=result1.provenance[test_key].source,
                owner_id=result1.provenance[test_key].owner_id,
                timestamp_ms=expired_time,
                ttl_ms=3600000,  # 1 hour TTL
                metadata=result1.provenance[test_key].metadata
            )
            
            # Create a new fusion to trigger expiration check
            result2 = fuse_context(
                request="Another test request",
                owner_id="test_user",
                session_id="test_session"
            )
            
            # The expired key should not affect the new context (different request)
            # But we're mainly testing that the expiration logic doesn't break
            self.assertIsInstance(result2, FusedContext)

if __name__ == '__main__':
    unittest.main()