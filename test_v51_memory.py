"""
DOOM V5.1 — Memory Foundation Test Suite
35 tests across all V5.1 memory subsystems.
Tests run without requiring PostgreSQL (graceful degradation tested).
"""
import sys
import os
import unittest
import time

# Ensure DOOM root is in path
DOOM_ROOT = os.path.dirname(os.path.abspath(__file__))
if DOOM_ROOT not in sys.path:
    sys.path.insert(0, DOOM_ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Types (5 tests)
# ─────────────────────────────────────────────────────────────────────────────
class TestMemoryTypes(unittest.TestCase):
    def test_memory_type_values(self):
        from memory.types import MemoryType
        self.assertEqual(MemoryType.EXPERIENCE.value, "EXPERIENCE")
        self.assertEqual(MemoryType.SEMANTIC.value, "SEMANTIC")
        self.assertEqual(MemoryType.PREFERENCE.value, "PREFERENCE")
        self.assertEqual(MemoryType.PROJECT.value, "PROJECT")
        self.assertEqual(MemoryType.EPISODIC.value, "EPISODIC")

    def test_memory_status_values(self):
        from memory.types import MemoryStatus
        self.assertIn(MemoryStatus.ACTIVE, list(MemoryStatus))
        self.assertIn(MemoryStatus.SUPERSEDED, list(MemoryStatus))
        self.assertIn(MemoryStatus.ARCHIVED, list(MemoryStatus))
        self.assertIn(MemoryStatus.DELETED, list(MemoryStatus))
        self.assertIn(MemoryStatus.PENDING_VERIFICATION, list(MemoryStatus))

    def test_confidence_levels(self):
        from memory.types import ConfidenceLevel
        levels = [ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM,
                  ConfidenceLevel.LOW, ConfidenceLevel.UNKNOWN]
        self.assertEqual(len(levels), 4)

    def test_memory_source_values(self):
        from memory.types import MemorySource
        self.assertEqual(MemorySource.USER_EXPLICIT.value, "USER_EXPLICIT")
        self.assertEqual(MemorySource.VERIFIED_TASK.value, "VERIFIED_TASK")

    def test_privacy_class_values(self):
        from memory.types import PrivacyClass
        self.assertEqual(PrivacyClass.NORMAL.value, "NORMAL")
        self.assertEqual(PrivacyClass.PRIVATE.value, "PRIVATE")
        self.assertEqual(PrivacyClass.SENSITIVE.value, "SENSITIVE")


# ─────────────────────────────────────────────────────────────────────────────
# Section 2: Schemas (5 tests)
# ─────────────────────────────────────────────────────────────────────────────
class TestMemorySchemas(unittest.TestCase):
    def setUp(self):
        from memory.schemas import MemoryRecord
        from memory.types import MemoryType, MemorySource, ConfidenceLevel
        self.record = MemoryRecord(
            memory_type=MemoryType.EXPERIENCE,
            content="Completed the DOOM V5.1 build successfully.",
            source=MemorySource.VERIFIED_TASK,
            confidence=ConfidenceLevel.HIGH,
            importance=0.9,
        )

    def test_record_has_generated_id(self):
        self.assertTrue(self.record.memory_id.startswith("mem_"))
        self.assertEqual(len(self.record.memory_id), 20)

    def test_record_to_dict(self):
        d = self.record.to_dict()
        self.assertEqual(d["memory_type"], "EXPERIENCE")
        self.assertEqual(d["source"], "VERIFIED_TASK")
        self.assertIn("created_at", d)
        self.assertIn("memory_id", d)

    def test_record_from_dict(self):
        from memory.schemas import MemoryRecord
        d = self.record.to_dict()
        r2 = MemoryRecord.from_dict(d)
        self.assertEqual(r2.memory_id, self.record.memory_id)
        self.assertEqual(r2.content, self.record.content)

    def test_memory_context_has_memories(self):
        from memory.schemas import MemoryContext
        ctx = MemoryContext(query="test", retrieved_memories=[self.record])
        self.assertTrue(ctx.has_memories())

    def test_memory_context_empty(self):
        from memory.schemas import MemoryContext
        ctx = MemoryContext(query="test")
        self.assertFalse(ctx.has_memories())


# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Validators (5 tests)
# ─────────────────────────────────────────────────────────────────────────────
class TestMemoryValidators(unittest.TestCase):
    def setUp(self):
        from memory.validators import MemoryValidator
        self.v = MemoryValidator()

    def test_reject_secret_pattern(self):
        ok, reason = self.v.check_secret("my api_key is abc123")
        self.assertFalse(ok)
        self.assertIn("api_key", reason)

    def test_reject_password(self):
        ok, reason = self.v.check_secret("the user password is letmein123")
        self.assertFalse(ok)

    def test_accept_normal_content(self):
        ok, reason = self.v.check_secret("User completed the DOOM AI project milestone.")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_reject_too_short(self):
        ok, reason = self.v.check_content_length("ab")
        self.assertFalse(ok)

    def test_is_memory_worthy(self):
        from memory.types import MemoryType, MemorySource
        worthy = self.v.is_memory_worthy(
            "User prefers British voice accent in DOOM.", MemoryType.PREFERENCE, MemorySource.USER_EXPLICIT
        )
        self.assertTrue(worthy)
        not_worthy = self.v.is_memory_worthy("done.", MemoryType.SEMANTIC, MemorySource.DERIVED_CONTEXT)
        self.assertFalse(not_worthy)


# ─────────────────────────────────────────────────────────────────────────────
# Section 4: Policy (5 tests)
# ─────────────────────────────────────────────────────────────────────────────
class TestMemoryPolicy(unittest.TestCase):
    def setUp(self):
        from memory.policy import MemoryWritePolicy
        self.policy = MemoryWritePolicy()

    def test_approve_verified_task(self):
        from memory.types import MemoryType, MemorySource
        decision = self.policy.evaluate(
            "Successfully built DOOM V5.1 memory foundation.",
            MemoryType.EXPERIENCE,
            MemorySource.VERIFIED_TASK,
            task_verified=True,
        )
        self.assertTrue(decision.approved)
        from memory.types import ConfidenceLevel, VerificationStatus
        self.assertEqual(decision.confidence, ConfidenceLevel.HIGH)
        self.assertEqual(decision.verification_status, VerificationStatus.VERIFIED)

    def test_reject_secret_content(self):
        from memory.types import MemoryType, MemorySource
        decision = self.policy.evaluate(
            "User's API key is sk-abc1234567890.",
            MemoryType.SEMANTIC,
            MemorySource.USER_CONVERSATION,
        )
        self.assertFalse(decision.approved)

    def test_low_confidence_for_derived(self):
        from memory.types import MemoryType, MemorySource, ConfidenceLevel
        decision = self.policy.evaluate(
            "DOOM might be able to process voice commands.",
            MemoryType.SEMANTIC,
            MemorySource.DERIVED_CONTEXT,
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.confidence, ConfidenceLevel.LOW)

    def test_user_explicit_high_confidence(self):
        from memory.types import MemoryType, MemorySource, ConfidenceLevel
        decision = self.policy.evaluate(
            "Sujal prefers concise British-style responses.",
            MemoryType.PREFERENCE,
            MemorySource.USER_EXPLICIT,
            user_explicit=True,
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.confidence, ConfidenceLevel.HIGH)

    def test_preference_gets_private_class(self):
        from memory.types import MemoryType, MemorySource, PrivacyClass
        decision = self.policy.evaluate(
            "User prefers dark mode UI.",
            MemoryType.PREFERENCE,
            MemorySource.USER_EXPLICIT,
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.privacy_class, PrivacyClass.PRIVATE)


# ─────────────────────────────────────────────────────────────────────────────
# Section 5: Ranking (5 tests)
# ─────────────────────────────────────────────────────────────────────────────
class TestMemoryRanking(unittest.TestCase):
    def setUp(self):
        from memory.ranking import MemoryRanker
        from memory.schemas import MemoryRecord
        from memory.types import MemoryType, MemorySource, ConfidenceLevel
        self.ranker = MemoryRanker()
        self.high_rec = MemoryRecord(
            memory_type=MemoryType.EXPERIENCE,
            content="DOOM completed the system health check successfully.",
            source=MemorySource.VERIFIED_TASK,
            confidence=ConfidenceLevel.HIGH,
            importance=0.9,
        )
        self.low_rec = MemoryRecord(
            memory_type=MemoryType.SEMANTIC,
            content="Some unrelated data.",
            source=MemorySource.DERIVED_CONTEXT,
            confidence=ConfidenceLevel.LOW,
            importance=0.1,
        )

    def test_score_range(self):
        score = self.ranker.score(self.high_rec, "system health check")
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_relevant_scores_higher(self):
        s1 = self.ranker.score(self.high_rec, "system health check doom")
        s2 = self.ranker.score(self.low_rec, "system health check doom")
        self.assertGreater(s1, s2)

    def test_rank_ordering(self):
        scored = self.ranker.rank([self.low_rec, self.high_rec], "system health doom")
        self.assertEqual(scored[0].record.memory_id, self.high_rec.memory_id)

    def test_project_match_boosts_score(self):
        from memory.schemas import MemoryRecord
        from memory.types import MemoryType, MemorySource
        proj_rec = MemoryRecord(
            memory_type=MemoryType.PROJECT,
            content="DOOM project milestone reached.",
            source=MemorySource.USER_CONVERSATION,
            importance=0.5,
            project_id="doom",
        )
        score_with = self.ranker.score(proj_rec, "doom milestone", project_id="doom")
        score_without = self.ranker.score(proj_rec, "doom milestone", project_id=None)
        self.assertGreater(score_with, score_without)

    def test_empty_records(self):
        scored = self.ranker.rank([], "any query")
        self.assertEqual(scored, [])


# ─────────────────────────────────────────────────────────────────────────────
# Section 6: MemoryContext Builder (3 tests)
# ─────────────────────────────────────────────────────────────────────────────
class TestMemoryContextBuilder(unittest.TestCase):
    def test_build_empty(self):
        from memory.context import memory_context_builder
        ctx = memory_context_builder.build(query="test", scored_memories=[])
        self.assertFalse(ctx.has_memories())
        self.assertEqual(ctx.context_summary, "")

    def test_build_with_records(self):
        from memory.context import memory_context_builder
        from memory.schemas import MemoryRecord, ScoredMemory
        from memory.types import MemoryType, MemorySource, ConfidenceLevel
        rec = MemoryRecord(
            memory_type=MemoryType.SEMANTIC,
            content="DOOM is Sujal's Personal AI OS.",
            source=MemorySource.USER_EXPLICIT,
            confidence=ConfidenceLevel.HIGH,
        )
        ctx = memory_context_builder.build("doom", [ScoredMemory(record=rec, score=0.8)])
        self.assertTrue(ctx.has_memories())
        self.assertIn("DOOM is Sujal", ctx.context_summary)

    def test_sensitive_excluded_from_summary(self):
        from memory.context import memory_context_builder
        from memory.schemas import MemoryRecord, ScoredMemory
        from memory.types import MemoryType, MemorySource, PrivacyClass
        rec = MemoryRecord(
            memory_type=MemoryType.PREFERENCE,
            content="Very sensitive personal data.",
            source=MemorySource.USER_EXPLICIT,
            privacy_class=PrivacyClass.SENSITIVE,
        )
        ctx = memory_context_builder.build("test", [ScoredMemory(record=rec, score=0.9)])
        self.assertNotIn("Very sensitive", ctx.context_summary)


# ─────────────────────────────────────────────────────────────────────────────
# Section 7: MemoryManager (5 tests — no PostgreSQL required)
# ─────────────────────────────────────────────────────────────────────────────
class TestMemoryManager(unittest.TestCase):
    def test_manager_initializes(self):
        from memory.manager import memory_manager, MemoryManager
        self.assertIsInstance(memory_manager, MemoryManager)

    def test_store_rejected_secret(self):
        from memory.manager import memory_manager
        from memory.schemas import MemoryRecord
        from memory.types import MemoryType, MemorySource
        record = MemoryRecord(
            memory_type=MemoryType.SEMANTIC,
            content="The api_key is abc123456789",
            source=MemorySource.USER_EXPLICIT,
        )
        result = memory_manager.store(record)
        self.assertIsNone(result, "Secret content must be rejected")

    def test_store_too_short(self):
        from memory.manager import memory_manager
        from memory.schemas import MemoryRecord
        from memory.types import MemoryType, MemorySource
        record = MemoryRecord(
            memory_type=MemoryType.SEMANTIC,
            content="no",
            source=MemorySource.USER_EXPLICIT,
        )
        result = memory_manager.store(record)
        self.assertIsNone(result, "Too-short content must be rejected")

    def test_retrieve_returns_context(self):
        from memory.manager import memory_manager
        ctx = memory_manager.retrieve("DOOM system status")
        from memory.schemas import MemoryContext
        self.assertIsInstance(ctx, MemoryContext)

    def test_telemetry_tracks_ops(self):
        from memory.manager import memory_manager
        initial_retrieve = memory_manager.telemetry.retrieval_count
        memory_manager.retrieve("any query for telemetry tracking")
        self.assertGreater(memory_manager.telemetry.retrieval_count, initial_retrieve)


# ─────────────────────────────────────────────────────────────────────────────
# Section 8: Memory Package Exports (2 tests)
# ─────────────────────────────────────────────────────────────────────────────
class TestMemoryPackageExports(unittest.TestCase):
    def test_legacy_exports_intact(self):
        from memory import user_profile, short_term_memory, episodic_memory, semantic_memory
        self.assertIsNotNone(user_profile)
        self.assertIsNotNone(short_term_memory)
        self.assertIsNotNone(episodic_memory)
        self.assertIsNotNone(semantic_memory)

    def test_v51_exports_available(self):
        from memory import memory_manager, MemoryRecord, MemoryContext, MemoryType
        self.assertIsNotNone(memory_manager)
        self.assertIsNotNone(MemoryRecord)
        self.assertIsNotNone(MemoryContext)
        self.assertIsNotNone(MemoryType)


if __name__ == "__main__":
    print("=" * 60)
    print("DOOM V5.1 — Memory Foundation Test Suite")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestMemoryTypes,
        TestMemorySchemas,
        TestMemoryValidators,
        TestMemoryPolicy,
        TestMemoryRanking,
        TestMemoryContextBuilder,
        TestMemoryManager,
        TestMemoryPackageExports,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    passed = total - failures - errors

    print(f"Results: {passed}/{total} passed | {failures} failures | {errors} errors")
    if result.wasSuccessful():
        print("[PASS] DOOM V5.1 Memory Foundation — All tests passed.")
        sys.exit(0)
    else:
        print("[FAIL] Some tests failed — see output above.")
        sys.exit(1)
