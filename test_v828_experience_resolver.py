"""V8.28 Phase 3 — Goal Experience resolver + PLAN/DECISION consumer tests."""

from __future__ import annotations

import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
os.environ.setdefault("PROACTIVE_V827_USER_MODEL_ENABLED", "false")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")

from orchestration.experience.resolve import (
    MAX_CONSUMER_EXPERIENCES,
    experience_strings_for_consumer,
    format_experience_for_consumer,
    get_relevant_goal_experiences,
    merge_profile_experience_memory,
    relevance_score,
)
from orchestration.experience.store import (
    create_experience,
    forget_experience,
    list_experiences,
    reset_experience_for_tests,
    use_test_experience_store,
)
from orchestration.experience.types import (
    SCHEMA_VERSION,
    ExperienceResult,
    ExperienceResultStatus,
    ExperienceStatus,
    GoalExperience,
    Outcome,
)
from orchestration.decision.assemble import assemble_decision_input
from orchestration.plan.assemble import assemble_plan_input


class TestV828ExperienceResolver(unittest.TestCase):
    def setUp(self):
        use_test_experience_store(True)
        reset_experience_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "false"

    def tearDown(self):
        reset_experience_for_tests()
        use_test_experience_store(False)
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

    def _create(self, owner="alice", **kwargs):
        base = {
            "title": "Improve Python skills",
            "outcome": Outcome.COMPLETED,
            "step_summary": ("Practice daily drills", "Review basics"),
            "blockers": (),
            "user_note": "",
            "tags": ("python",),
            "source_goal_id": "",
        }
        base.update(kwargs)
        if not base.get("source_goal_id"):
            # Unique source when empty would collide on title-only; leave empty OK
            # for multiple creates without source_goal_id (each gets new id).
            pass
        res = create_experience(owner, base)
        self.assertEqual(res.status, ExperienceResultStatus.OK, msg=str(res.status))
        return res.experience

    def _plan(self, owner="alice"):
        return SimpleNamespace(owner_id=owner)

    # --- Resolver relevance -------------------------------------------------

    def test_01_title_match(self):
        self._create(title="Learn Rust concurrency")
        self._create(
            title="Bake sourdough bread",
            source_goal_id="rg_bread",
            step_summary=("Mix dough",),
        )
        res = get_relevant_goal_experiences(
            "alice", "PLAN", query="Learn Rust concurrency", limit=4
        )
        self.assertEqual(res.status, ExperienceResultStatus.OK)
        self.assertEqual(len(res.experiences), 1)
        self.assertEqual(res.experiences[0].title, "Learn Rust concurrency")

    def test_02_token_overlap(self):
        self._create(
            title="Ship dashboard polish",
            step_summary=("Fix chart labels", "Tighten spacing"),
            source_goal_id="rg_dash",
        )
        res = get_relevant_goal_experiences(
            "alice", "PLAN", query="dashboard chart spacing plan", limit=4
        )
        self.assertEqual(res.status, ExperienceResultStatus.OK)
        self.assertGreaterEqual(len(res.experiences), 1)
        self.assertIn("dashboard", res.experiences[0].title.lower())

    def test_03_irrelevant_excluded(self):
        self._create(title="Organize kitchen pantry", source_goal_id="rg_kit")
        res = get_relevant_goal_experiences(
            "alice", "DECISION", query="choose between llama and mistral", limit=4
        )
        self.assertEqual(res.status, ExperienceResultStatus.OK)
        self.assertEqual(res.experiences, ())

    def test_04_deterministic_ranking(self):
        weak = self._create(
            title="General study habits",
            step_summary=("Read notes about python briefly",),
            source_goal_id="rg_weak",
        )
        strong = self._create(
            title="Master Python asyncio",
            step_summary=("Write async client",),
            source_goal_id="rg_strong",
        )
        res = get_relevant_goal_experiences(
            "alice", "PLAN", query="Master Python asyncio", limit=4
        )
        self.assertGreaterEqual(len(res.experiences), 1)
        self.assertEqual(res.experiences[0].experience_id, strong.experience_id)
        scores = [relevance_score(e, "Master Python asyncio") for e in res.experiences]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertGreater(
            relevance_score(strong, "Master Python asyncio"),
            relevance_score(weak, "Master Python asyncio"),
        )

    def test_05_deterministic_tie_break(self):
        older = self._create(
            title="Refactor auth module",
            source_goal_id="rg_auth_a",
        )
        time.sleep(0.02)
        newer = self._create(
            title="Refactor auth module helpers",
            source_goal_id="rg_auth_b",
            step_summary=("Extract helpers",),
        )
        # Force equal relevance via identical titles for tie-break path.
        from orchestration.experience import store as exp_store

        with exp_store._LOCK:
            rows = exp_store._TEST_ROWS["alice"]
            for row in rows:
                row["title"] = "Refactor auth module"
                if row["experience_id"] == older.experience_id:
                    row["updated_at"] = 1000.0
                if row["experience_id"] == newer.experience_id:
                    row["updated_at"] = 2000.0

        res = get_relevant_goal_experiences(
            "alice", "PLAN", query="Refactor auth module", limit=4
        )
        self.assertGreaterEqual(len(res.experiences), 2)
        self.assertEqual(res.experiences[0].experience_id, newer.experience_id)
        # Same score + same updated_at → experience_id ascending.
        with exp_store._LOCK:
            for row in exp_store._TEST_ROWS["alice"]:
                row["updated_at"] = 1500.0
        res2 = get_relevant_goal_experiences(
            "alice", "PLAN", query="Refactor auth module", limit=4
        )
        ids = [e.experience_id for e in res2.experiences]
        self.assertEqual(ids, sorted(ids))

    def test_06_plan_limit_le_4(self):
        for i in range(6):
            self._create(
                title=f"Python practice session {i}",
                source_goal_id=f"rg_py_{i}",
                step_summary=("Practice python",),
            )
        res = get_relevant_goal_experiences(
            "alice", "PLAN", query="python practice", limit=4
        )
        self.assertLessEqual(len(res.experiences), 4)

    def test_07_decision_limit_le_4(self):
        for i in range(6):
            self._create(
                title=f"Model choice round {i}",
                source_goal_id=f"rg_m_{i}",
                step_summary=("Compare llama mistral",),
            )
        res = get_relevant_goal_experiences(
            "alice", "DECISION", query="llama mistral model choice", limit=4
        )
        self.assertLessEqual(len(res.experiences), 4)

    def test_08_resolver_hard_maximum(self):
        for i in range(8):
            self._create(
                title=f"Docker compose cleanup {i}",
                source_goal_id=f"rg_d_{i}",
                step_summary=("Prune docker images",),
            )
        res = get_relevant_goal_experiences(
            "alice", "PLAN", query="docker compose cleanup", limit=99
        )
        self.assertLessEqual(len(res.experiences), MAX_CONSUMER_EXPERIENCES)
        self.assertEqual(len(res.experiences), MAX_CONSUMER_EXPERIENCES)

    def test_09_malformed_record_skipped(self):
        good = self._create(title="Valid gardening plan", source_goal_id="rg_g")
        bad = GoalExperience(
            schema_version=SCHEMA_VERSION,
            experience_id="ge_bad",
            owner_id="alice",
            source_goal_id="rg_bad",
            title="",
            outcome=Outcome.COMPLETED,
            step_summary=(),
            blockers=(),
            user_note="",
            tags=(),
            status=ExperienceStatus.ACTIVE_RECORD,
            version=1,
            created_at=1.0,
            updated_at=9.0,
            expires_at=None,
        )
        with patch(
            "orchestration.experience.store.list_experiences",
            return_value=ExperienceResult(
                ExperienceResultStatus.OK, experiences=(bad, good)
            ),
        ):
            res = get_relevant_goal_experiences(
                "alice", "PLAN", query="gardening plan", limit=4
            )
        self.assertEqual(len(res.experiences), 1)
        self.assertEqual(res.experiences[0].experience_id, good.experience_id)

    def test_10_forgotten_excluded(self):
        exp = self._create(title="Abandoned wiki rewrite", source_goal_id="rg_wiki")
        forget_experience("alice", exp.experience_id, expected_version=1)
        res = get_relevant_goal_experiences(
            "alice", "PLAN", query="wiki rewrite", limit=4
        )
        self.assertEqual(res.experiences, ())

    def test_11_expired_excluded(self):
        exp = self._create(
            title="Seasonal sprint planning",
            source_goal_id="rg_season",
            expires_at=time.time() - 10,
        )
        res = get_relevant_goal_experiences(
            "alice", "PLAN", query="seasonal sprint planning", limit=4
        )
        self.assertEqual(res.experiences, ())
        self.assertIsNotNone(exp)

    def test_12_owner_isolation(self):
        self._create(owner="alice", title="Alice workshop project", source_goal_id="rg_a")
        self._create(owner="bob", title="Bob workshop project", source_goal_id="rg_b")
        res_a = get_relevant_goal_experiences(
            "alice", "PLAN", query="workshop project", limit=4
        )
        res_b = get_relevant_goal_experiences(
            "bob", "PLAN", query="workshop project", limit=4
        )
        self.assertEqual(len(res_a.experiences), 1)
        self.assertEqual(res_a.experiences[0].owner_id, "alice")
        self.assertEqual(len(res_b.experiences), 1)
        self.assertEqual(res_b.experiences[0].owner_id, "bob")
        texts = experience_strings_for_consumer("alice", "PLAN", "workshop project")
        self.assertTrue(all("bob" not in t.lower() for t in texts))
        self.assertTrue(all("owner_id" not in t.lower() for t in texts))

    def test_13_db_failure_fail_closed(self):
        self._create(title="Database resilience drill", source_goal_id="rg_db")
        with patch(
            "orchestration.experience.store.list_experiences",
            return_value=ExperienceResult(ExperienceResultStatus.UNAVAILABLE),
        ):
            res = get_relevant_goal_experiences(
                "alice", "PLAN", query="database resilience", limit=4
            )
        self.assertEqual(res.status, ExperienceResultStatus.UNAVAILABLE)
        self.assertEqual(res.experiences, ())

    def test_14_store_exception_fail_closed(self):
        with patch(
            "orchestration.experience.store.list_experiences",
            side_effect=RuntimeError("boom"),
        ):
            res = get_relevant_goal_experiences(
                "alice", "DECISION", query="anything", limit=4
            )
        self.assertEqual(res.status, ExperienceResultStatus.UNAVAILABLE)

    # --- PLAN consumer ------------------------------------------------------

    def test_15_plan_receives_relevant_experiences(self):
        self._create(
            title="Improve laptop thermals",
            step_summary=("Clean fans", "Repaste CPU"),
            source_goal_id="rg_therm",
        )
        with patch(
            "orchestration.conversation.personal_memory.search_personal_memories",
            return_value=[],
        ), patch(
            "orchestration.conversation.personal_memory.list_personal_memories",
            return_value=[],
        ):
            pin = assemble_plan_input(
                self._plan(),
                "Plan next steps for improve laptop thermals",
            )
        joined = " ".join(pin.preferences).lower()
        self.assertIn("past:", joined)
        self.assertIn("thermals", joined)

    def test_16_plan_no_result_fallback(self):
        self._create(title="Unrelated pottery class", source_goal_id="rg_pot")
        with patch(
            "orchestration.conversation.personal_memory.search_personal_memories",
            return_value=[],
        ), patch(
            "orchestration.conversation.personal_memory.list_personal_memories",
            return_value=[],
        ):
            pin = assemble_plan_input(
                self._plan(),
                "Plan how to learn quantum computing",
            )
        self.assertTrue(all("pottery" not in p.lower() for p in pin.preferences))
        self.assertEqual(pin.question[:20], "Plan how to learn qu"[:20])

    def test_17_plan_resolver_failure_fallback(self):
        with patch(
            "orchestration.experience.resolve.experience_strings_for_consumer",
            side_effect=RuntimeError("resolver down"),
        ), patch(
            "orchestration.conversation.personal_memory.search_personal_memories",
            return_value=[],
        ), patch(
            "orchestration.conversation.personal_memory.list_personal_memories",
            return_value=[],
        ):
            pin = assemble_plan_input(self._plan(), "Plan a weekend coding sprint")
        self.assertIsInstance(pin.question, str)
        self.assertTrue(pin.question)

    def test_18_plan_flag_off_behavior(self):
        self._create(title="Flag off garden bed", source_goal_id="rg_flag")
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "false"
        with patch(
            "orchestration.conversation.personal_memory.search_personal_memories",
            return_value=[],
        ), patch(
            "orchestration.conversation.personal_memory.list_personal_memories",
            return_value=[],
        ):
            pin = assemble_plan_input(
                self._plan(), "Plan next steps for garden bed"
            )
        self.assertTrue(all("past:" not in p.lower() for p in pin.preferences))
        res = get_relevant_goal_experiences("alice", "PLAN", "garden bed")
        self.assertEqual(res.status, ExperienceResultStatus.UNAVAILABLE)

    # --- DECISION consumer --------------------------------------------------

    def test_19_decision_receives_relevant_experiences(self):
        self._create(
            title="Choose between llama and mistral locally",
            outcome=Outcome.COMPLETED,
            step_summary=("Benchmark tokens",),
            source_goal_id="rg_llm",
        )
        with patch(
            "orchestration.conversation.personal_memory.search_personal_memories",
            return_value=[],
        ), patch(
            "orchestration.conversation.personal_memory.list_personal_memories",
            return_value=[],
        ):
            din = assemble_decision_input(
                self._plan(),
                "Should I choose llama or mistral for local coding?",
            )
        joined = " ".join(din.preferences).lower()
        self.assertIn("past:", joined)
        self.assertTrue("llama" in joined or "mistral" in joined)

    def test_20_decision_no_result_fallback(self):
        self._create(title="Paint the fence", source_goal_id="rg_fence")
        with patch(
            "orchestration.conversation.personal_memory.search_personal_memories",
            return_value=[],
        ), patch(
            "orchestration.conversation.personal_memory.list_personal_memories",
            return_value=[],
        ):
            din = assemble_decision_input(
                self._plan(),
                "Should I pick tea or coffee this morning?",
            )
        self.assertTrue(all("fence" not in p.lower() for p in din.preferences))

    def test_21_decision_resolver_failure_fallback(self):
        with patch(
            "orchestration.experience.resolve.experience_strings_for_consumer",
            side_effect=RuntimeError("nope"),
        ), patch(
            "orchestration.conversation.personal_memory.search_personal_memories",
            return_value=[],
        ), patch(
            "orchestration.conversation.personal_memory.list_personal_memories",
            return_value=[],
        ):
            din = assemble_decision_input(
                self._plan(), "Should I use vim or emacs?"
            )
        self.assertIn("vim", din.question.lower())
        self.assertGreaterEqual(len(din.options), 2)

    def test_22_decision_flag_off_behavior(self):
        self._create(title="Choose editor layout", source_goal_id="rg_ed")
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "false"
        with patch(
            "orchestration.conversation.personal_memory.search_personal_memories",
            return_value=[],
        ), patch(
            "orchestration.conversation.personal_memory.list_personal_memories",
            return_value=[],
        ):
            din = assemble_decision_input(
                self._plan(), "Should I choose editor layout A or B?"
            )
        self.assertTrue(all("past:" not in p.lower() for p in din.preferences))

    def test_23_current_question_authoritative(self):
        self._create(
            title="Migrate search indexes",
            source_goal_id="rg_idx",
        )
        q = "Should I migrate search indexes now or next week?"
        with patch(
            "orchestration.conversation.personal_memory.search_personal_memories",
            return_value=[],
        ), patch(
            "orchestration.conversation.personal_memory.list_personal_memories",
            return_value=[],
        ):
            din = assemble_decision_input(self._plan(), q)
        self.assertTrue(din.question.lower().startswith("should i migrate"))
        self.assertNotEqual(
            " ".join(din.preferences).lower(), din.question.lower()
        )

    def test_24_sensitive_content_rejected_or_omitted(self):
        good = self._create(
            title="Harden laptop backups",
            source_goal_id="rg_bak",
            user_note="Weekly cadence worked",
        )
        sensitive = GoalExperience(
            schema_version=SCHEMA_VERSION,
            experience_id="ge_sens",
            owner_id="alice",
            source_goal_id="rg_sens",
            title="Rotate api_key vault",
            outcome=Outcome.ABANDONED,
            step_summary=("password=hunter2",),
            blockers=("authorization: Bearer tok",),
            user_note="ssn 123-45-6789",
            tags=(),
            status=ExperienceStatus.ACTIVE_RECORD,
            version=1,
            created_at=1.0,
            updated_at=5.0,
            expires_at=None,
        )
        # Title itself is sensitive → entire entry unusable.
        self.assertEqual(format_experience_for_consumer(sensitive), "")
        with patch(
            "orchestration.experience.store.list_experiences",
            return_value=ExperienceResult(
                ExperienceResultStatus.OK, experiences=(sensitive, good)
            ),
        ):
            res = get_relevant_goal_experiences(
                "alice", "PLAN", query="laptop backups", limit=4
            )
        self.assertEqual(len(res.experiences), 1)
        self.assertEqual(res.experiences[0].experience_id, good.experience_id)
        # Partial sensitive fields omitted from format of a safe-title row.
        partial = GoalExperience(
            schema_version=SCHEMA_VERSION,
            experience_id="ge_part",
            owner_id="alice",
            source_goal_id="rg_part",
            title="Tighten laptop backups",
            outcome=Outcome.COMPLETED,
            step_summary=("password reset flow", "Clean clutter"),
            blockers=("csrf token leak",),
            user_note="credit card on file",
            tags=(),
            status=ExperienceStatus.ACTIVE_RECORD,
            version=1,
            created_at=1.0,
            updated_at=5.0,
            expires_at=None,
        )
        formatted = format_experience_for_consumer(partial)
        self.assertIn("Tighten laptop backups", formatted)
        self.assertNotIn("password", formatted.lower())
        self.assertNotIn("csrf", formatted.lower())
        self.assertNotIn("credit card", formatted.lower())
        self.assertIn("Clean clutter", formatted)

    def test_25_no_writes_during_retrieval(self):
        self._create(title="Read only checklist", source_goal_id="rg_ro")
        with patch(
            "orchestration.experience.store.create_experience"
        ) as c, patch(
            "orchestration.experience.store.update_experience"
        ) as u, patch(
            "orchestration.experience.store.transition_experience"
        ) as t, patch(
            "orchestration.experience.store.forget_experience"
        ) as f:
            get_relevant_goal_experiences(
                "alice", "PLAN", query="read only checklist", limit=4
            )
            experience_strings_for_consumer(
                "alice", "DECISION", query="read only checklist", limit=4
            )
        c.assert_not_called()
        u.assert_not_called()
        t.assert_not_called()
        f.assert_not_called()

    def test_26_no_user_model_mutation(self):
        self._create(title="Profile safe study plan", source_goal_id="rg_um")
        with patch(
            "orchestration.user_model.store.create_profile_entry",
            create=True,
        ) as c, patch(
            "orchestration.user_model.store.update_profile_entry",
            create=True,
        ) as u:
            with patch(
                "orchestration.conversation.personal_memory.search_personal_memories",
                return_value=[],
            ), patch(
                "orchestration.conversation.personal_memory.list_personal_memories",
                return_value=[],
            ):
                assemble_plan_input(
                    self._plan(), "Plan next steps for profile safe study plan"
                )
                assemble_decision_input(
                    self._plan(),
                    "Should I continue profile safe study plan A or B?",
                )
            # Patches may not bind if module path unused; ensure no call if bound.
            if isinstance(c, MagicMock):
                c.assert_not_called()
            if isinstance(u, MagicMock):
                u.assert_not_called()

    def test_27_no_goal_registry_mutation(self):
        self._create(title="Registry untouched goal", source_goal_id="rg_reg")
        with patch(
            "orchestration.plan.goal_registry.transition_goal", create=True
        ) as tg, patch(
            "orchestration.plan.goal_registry.create_active_goal", create=True
        ) as cg:
            get_relevant_goal_experiences(
                "alice", "PLAN", "registry untouched goal", limit=4
            )
            with patch(
                "orchestration.conversation.personal_memory.search_personal_memories",
                return_value=[],
            ), patch(
                "orchestration.conversation.personal_memory.list_personal_memories",
                return_value=[],
            ):
                assemble_plan_input(
                    self._plan(), "Plan for registry untouched goal"
                )
        tg.assert_not_called()
        cg.assert_not_called()

    def test_28_no_action_execution_path(self):
        self._create(title="Never execute this goal", source_goal_id="rg_ex")
        with patch("orchestration.executor.execute", create=True) as ex:
            get_relevant_goal_experiences(
                "alice", "DECISION", "never execute this goal", limit=4
            )
            with patch(
                "orchestration.conversation.personal_memory.search_personal_memories",
                return_value=[],
            ), patch(
                "orchestration.conversation.personal_memory.list_personal_memories",
                return_value=[],
            ):
                assemble_decision_input(
                    self._plan(),
                    "Should I never execute this goal now or later?",
                )
        ex.assert_not_called()

    def test_29_merge_precedence_profile_then_experience_then_memory(self):
        merged = merge_profile_experience_memory(
            ["[PREFERENCE] Prefer concise answers"],
            ["[past:COMPLETED] Improve Python skills"],
            ["I like long essays"],
            limit=4,
        )
        self.assertEqual(merged[0], "[PREFERENCE] Prefer concise answers")
        self.assertEqual(merged[1], "[past:COMPLETED] Improve Python skills")
        self.assertEqual(merged[2], "I like long essays")

    def test_30_consumer_strings_omit_owner(self):
        self._create(title="Public facing rewrite", source_goal_id="rg_pub")
        texts = experience_strings_for_consumer(
            "alice", "PLAN", "public facing rewrite", limit=4
        )
        self.assertTrue(texts)
        blob = " ".join(texts).lower()
        self.assertNotIn("owner_id", blob)
        self.assertNotIn("alice", blob)


if __name__ == "__main__":
    unittest.main()
