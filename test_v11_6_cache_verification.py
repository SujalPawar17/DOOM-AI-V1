#!/usr/bin/env python3
"""Cache verification test for V11.6 Context Fusion caching."""

import sys
import unittest
from unittest.mock import patch, MagicMock

class TestContextFusionCache(unittest.TestCase):
    def setUp(self):
        self.request = "test request"
        self.owner_id = "test_owner"
        self.session_id = "test_session"
        self.language_hint = None
        self.now_ms = 1000000  # fixed timestamp for determinism

        # Remove the module to get a fresh copy
        for mod in list(sys.modules.keys()):
            if mod.startswith('core.v10.context_fusion'):
                del sys.modules[mod]

        # Import the module after removal
        from core.v10.context_fusion import ContextFusion, ContextSource
        self.ContextFusion = ContextFusion
        self.ContextSource = ContextSource

        # Start patches
        self.process_goal_patch = patch('core.v10.context_fusion.process_goal')
        self.mock_process_goal = self.process_goal_patch.start()
        self.get_active_goal_patch = patch('core.v10.context_fusion.get_active_goal')
        self.mock_get_active_goal = self.get_active_goal_patch.start()
        self.list_experiences_patch = patch('core.v10.context_fusion.list_experiences')
        self.mock_list_experiences = self.list_experiences_patch.start()
        self.plan_goal_patch = patch('core.v10.context_fusion.plan_goal')
        self.mock_plan_goal = self.plan_goal_patch.start()

    def tearDown(self):
        # Stop patches
        self.process_goal_patch.stop()
        self.get_active_goal_patch.stop()
        self.list_experiences_patch.stop()
        self.plan_goal_patch.stop()
        # Remove the module to avoid state leakage
        for mod in list(sys.modules.keys()):
            if mod.startswith('core.v10.context_fusion'):
                del sys.modules[mod]

    def test_active_goal_cache(self):
        """Test that ACTIVE_GOAL source uses cache."""
        # Configure mock to return a successful goal
        mock_goal_result = MagicMock()
        mock_goal_result.status.name = "CAPABILITY_AVAILABLE"
        mock_goal = MagicMock()
        mock_goal.goal_id = "test_goal_id"
        mock_goal.normalized_intent.value = "test_intent"
        mock_goal.capability_class.value = "test_capability"
        mock_goal_result.goal = mock_goal
        self.mock_process_goal.return_value = mock_goal_result

        cache = {}
        fusion = self.ContextFusion()
        # First call
        result1 = fusion._fetch_source(
            self.ContextSource.ACTIVE_GOAL,
            self.request,
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )
        # Second call with same parameters, sharing the same cache
        result2 = fusion._fetch_source(
            self.ContextSource.ACTIVE_GOAL,
            self.request,
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )

        # The mock should have been called only once
        self.assertEqual(self.mock_process_goal.call_count, 1)
        # Results should be equal
        self.assertEqual(result1, result2)

    def test_active_goal_cache_different_params(self):
        """Test that different parameters do not incorrectly share cache."""
        print(f'DEBUG: test_active_goal_cache_different_params started')
        # Configure mock to return a successful goal
        mock_goal_result = MagicMock()
        mock_goal_result.status.name = "CAPABILITY_AVAILABLE"
        mock_goal = MagicMock()
        mock_goal.goal_id = "test_goal_id"
        mock_goal.normalized_intent.value = "test_intent"
        mock_goal.capability_class.value = "test_capability"
        mock_goal_result.goal = mock_goal
        self.mock_process_goal.return_value = mock_goal_result

        cache = {}
        fusion = self.ContextFusion()
        # First call with request A
        result1 = fusion._fetch_source(
            self.ContextSource.ACTIVE_GOAL,
            "request A",
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )
        # Second call with request B
        result2 = fusion._fetch_source(
            self.ContextSource.ACTIVE_GOAL,
            "request B",
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )

        # The mock should have been called twice
        self.assertEqual(self.mock_process_goal.call_count, 2)
        # Results should be equal (same mock return) but we called twice
        self.assertEqual(result1, result2)

    def test_goal_state_cache(self):
        """Test that GOAL_STATE source uses cache."""
        from core.v10.context_fusion import ContextFusion, ContextSource
        # Configure mocks
        mock_ag_result = MagicMock()
        mock_ag_result.status.name = "OK"
        mock_ag_result.snapshot = MagicMock()
        mock_ag_result.snapshot.goal_id = "test_goal_id"
        mock_ag_result.snapshot.title = "Test Goal"
        mock_ag_result.snapshot.intent = "test_intent"
        mock_ag_result.snapshot.capability_class = "test_capability"
        mock_ag_result.snapshot.provenance = "test_prov"
        mock_ag_result.snapshot.requested_unix_ms = 500000
        mock_ag_result.snapshot.goal_hash = "test_hash"
        mock_ag_result.snapshot.schema_version = 1
        self.mock_get_active_goal.return_value = mock_ag_result

        mock_exp_result = MagicMock()
        mock_exp_result.status.name = "OK"
        mock_exp_result.experiences = []
        self.mock_list_experiences.return_value = mock_exp_result

        cache = {}
        fusion = self.ContextFusion()
        # First call
        result1 = fusion._fetch_source(
            self.ContextSource.GOAL_STATE,
            self.request,
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )
        # Second call with same parameters, sharing the same cache
        result2 = fusion._fetch_source(
            self.ContextSource.GOAL_STATE,
            self.request,
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )

        # The mock should have been called only once
        self.assertEqual(self.mock_get_active_goal.call_count, 1)
        # Results should be equal
        self.assertEqual(result1, result2)

    def test_outcome_cache(self):
        """Test that OUTCOME source uses cache."""
        # Configure mocks
        mock_exp_result = MagicMock()
        mock_exp_result.status.name = "OK"
        mock_exp_result.experiences = []
        self.mock_list_experiences.return_value = mock_exp_result

        cache = {}
        fusion = self.ContextFusion()
        # First call
        result1 = fusion._fetch_source(
            self.ContextSource.OUTCOME,
            self.request,
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )
        # Second call with same parameters, sharing the same cache
        result2 = fusion._fetch_source(
            self.ContextSource.OUTCOME,
            self.request,
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )

        # The mock should have been called only once
        self.assertEqual(self.mock_list_experiences.call_count, 1)
        # Results should be equal
        self.assertEqual(result1, result2)

    def test_plan_cache(self):
        """Test that PLAN source uses cache."""
        # Configure mocks for get_active_goal
        mock_ag_result = MagicMock()
        mock_ag_result.status.name = "OK"
        mock_ag_result.snapshot = MagicMock()
        mock_ag_result.snapshot.goal_id = "test_goal_id"
        mock_ag_result.snapshot.title = "Test Goal"
        mock_ag_result.snapshot.raw_intent = "make a plan for something"
        mock_ag_result.snapshot.normalized_intent = MagicMock()
        mock_ag_result.snapshot.normalized_intent.value = "test_intent"
        mock_ag_result.snapshot.capability_class = MagicMock()
        mock_ag_result.snapshot.capability_class.value = "test_capability"
        self.mock_get_active_goal.return_value = mock_ag_result

        # Configure mock for plan_goal
        mock_plan_result = MagicMock()
        mock_plan_result.status.name = "SUCCESS"
        mock_plan = MagicMock()
        mock_plan.plan_id = "test_plan_id"
        mock_plan.goal_id = "test_goal_id"
        mock_plan.schema_version = 1
        mock_plan.owner_id = self.owner_id
        mock_plan.session_id = self.session_id
        mock_plan.computer_session_id = ""
        mock_plan.plan_risk = "low"
        mock_plan.approval_required = False
        mock_plan.execution_permitted = True
        mock_plan.approved = False
        mock_plan.steps = []
        mock_plan_result.plan = mock_plan
        self.mock_plan_goal.return_value = mock_plan_result

        cache = {}
        fusion = self.ContextFusion()
        # First call
        result1 = fusion._fetch_source(
            self.ContextSource.PLAN,
            self.request,
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )
        # Second call with same parameters, sharing the same cache
        result2 = fusion._fetch_source(
            self.ContextSource.PLAN,
            self.request,
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )

        # The mock should have been called only once
        self.assertEqual(self.mock_get_active_goal.call_count, 1)
        # The plan_goal mock should have been called only once
        self.assertEqual(self.mock_plan_goal.call_count, 1)
        # Results should be equal
        self.assertEqual(result1, result2)

    def test_cache_isolation_different_owners(self):
        """Test that cache is isolated by owner."""
        from core.v10.context_fusion import ContextFusion, ContextSource
        # Configure mock to return a successful goal
        mock_goal_result = MagicMock()
        mock_goal_result.status.name = "CAPABILITY_AVAILABLE"
        mock_goal = MagicMock()
        mock_goal.goal_id = "test_goal_id"
        mock_goal.normalized_intent.value = "test_intent"
        mock_goal.capability_class.value = "test_capability"
        mock_goal_result.goal = mock_goal
        self.mock_process_goal.return_value = mock_goal_result

        cache = {}
        fusion = self.ContextFusion()
        # First call with owner A
        result1 = fusion._fetch_source(
            self.ContextSource.ACTIVE_GOAL,
            self.request,
            "owner_a",
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )
        # Second call with owner B
        result2 = fusion._fetch_source(
            self.ContextSource.ACTIVE_GOAL,
            self.request,
            "owner_b",
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )

        # The mock should have been called twice
        self.assertEqual(self.mock_process_goal.call_count, 2)
        # Results should be equal (same mock return) but we called twice
        self.assertEqual(result1, result2)

    def test_failed_operation_does_not_cache_invalid(self):
        """Test that a failed underlying operation does not prevent future successful calls from being cached."""
        from core.v10.context_fusion import ContextFusion, ContextSource
        # Configure mock to return a failed goal on first call, success on second
        mock_goal_result_fail = MagicMock()
        mock_goal_result_fail.status.name = "CAPABILITY_UNAVAILABLE"
        mock_goal_result_fail.goal = None
        mock_goal_result_success = MagicMock()
        mock_goal_result_success.status.name = "CAPABILITY_AVAILABLE"
        mock_goal_success = MagicMock()
        mock_goal_success.goal_id = "test_goal_id"
        mock_goal_success.normalized_intent.value = "test_intent"
        mock_goal_success.capability_class.value = "test_capability"
        mock_goal_result_success.goal = mock_goal_success
        self.mock_process_goal.side_effect = [mock_goal_result_fail, mock_goal_result_success]

        cache = {}
        fusion = self.ContextFusion()
        # First call (should fail)
        result1 = fusion._fetch_source(
            self.ContextSource.ACTIVE_GOAL,
            self.request,
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )
        # Second call with same parameters, sharing the same cache (should succeed)
        result2 = fusion._fetch_source(
            self.ContextSource.ACTIVE_GOAL,
            self.request,
            self.owner_id,
            self.session_id,
            self.language_hint,
            self.now_ms,
            cache
        )

        # The mock should have been called only once because the first result (failure) is cached
        self.assertEqual(self.mock_process_goal.call_count, 1)
        # Both results should be the default dict (since the first call failed and we cached that)
        expected_default = {"active_goal_id": "", "active_goal_intent": "", "active_goal_capability": ""}
        self.assertEqual(result1, expected_default)
        self.assertEqual(result2, expected_default)
