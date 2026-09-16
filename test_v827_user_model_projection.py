"""V8.27 Phase 3 — personal memory → User Model projection tests."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

from orchestration.conversation.personal_memory import (
    reset_personal_memory_for_tests,
    save_personal_memory,
    use_test_personal_memory_store,
)
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.goal.types import IntentClass
from orchestration.production import format_v8_execution, prepare_v8_request
from orchestration.user_model.projection import (
    map_memory_to_profile,
    project_after_memory_save,
    project_memory_to_profile,
)
from orchestration.user_model.store import (
    get_profile_entry,
    list_profile_entries,
    reset_user_model_for_tests,
    upsert_profile_entry,
    use_test_user_model_store,
)
from orchestration.user_model.types import (
    MAX_ENTRIES_PER_CATEGORY,
    MAX_PROFILE_ENTRIES,
    Category,
    Confidence,
    ProfileResultStatus,
    Provenance,
)


def _ident(owner="alice", session="sess-p3"):
    return ExecutionIdentity(owner_id=owner, session_id=session, computer_session_id="")


class TestV827UserModelProjection(unittest.TestCase):
    def setUp(self):
        use_test_user_model_store(True)
        reset_user_model_for_tests()
        use_test_personal_memory_store(True)
        reset_personal_memory_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

    def tearDown(self):
        reset_user_model_for_tests()
        use_test_user_model_store(False)
        reset_personal_memory_for_tests()
        use_test_personal_memory_store(False)

    def test_01_preference_projection(self):
        mapped = map_memory_to_profile("prefer_python", "I prefer Python.")
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.category, Category.PREFERENCE)
        self.assertEqual(mapped.key, "prefer_language")
        self.assertEqual(mapped.value, "Python")
        res = project_memory_to_profile("alice", "pm_1", "prefer_python", "I prefer Python.")
        self.assertEqual(res.status, ProfileResultStatus.OK)
        self.assertEqual(res.entry.provenance, Provenance.PROJECTED_MEMORY)

    def test_02_project_projection(self):
        mapped = map_memory_to_profile("building_project", "I am building DOOM.")
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.category, Category.PROJECT)
        self.assertEqual(mapped.key, "current_project")
        res = project_memory_to_profile(
            "alice", "pm_2", "building_project", "I am building DOOM."
        )
        self.assertEqual(res.status, ProfileResultStatus.OK)
        self.assertEqual(res.entry.value, "DOOM")

    def test_03_communication_projection(self):
        mapped = map_memory_to_profile("prefer_concise_answers", "I prefer concise answers.")
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.category, Category.COMMUNICATION)
        self.assertEqual(mapped.key, "style")
        self.assertEqual(mapped.value, "concise")

    def test_04_stable_fact_projection(self):
        mapped = map_memory_to_profile("software_developer", "I am a software developer.")
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.category, Category.STABLE_FACT)
        self.assertEqual(mapped.key, "role")

    def test_05_temporary_fact_projection(self):
        mapped = map_memory_to_profile(
            "preparing_exam", "I am currently preparing for an exam."
        )
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.category, Category.TEMPORARY_FACT)
        self.assertEqual(mapped.confidence, Confidence.MEDIUM)
        res = project_memory_to_profile(
            "alice",
            "pm_t",
            "preparing_exam",
            "I am currently preparing for an exam.",
        )
        self.assertEqual(res.status, ProfileResultStatus.OK)
        self.assertIsNotNone(res.entry.expires_at)

    def test_06_deterministic_mapping(self):
        a = map_memory_to_profile("prefer_python", "I prefer Python.")
        b = map_memory_to_profile("prefer_python", "I prefer Python.")
        self.assertEqual(a, b)

    def test_07_unmapped_memory_noop(self):
        mapped = map_memory_to_profile("python_nice", "Python is nice.")
        self.assertIsNone(mapped)
        res = project_memory_to_profile("alice", "pm_x", "python_nice", "Python is nice.")
        self.assertEqual(res.status, ProfileResultStatus.NOT_FOUND)
        self.assertEqual(list_profile_entries("alice").entries, ())

    def test_08_no_ordinary_chat_projection(self):
        for content in (
            "Today is sunny.",
            "My friend uses TypeScript.",
            "Yesterday I saw Python news.",
        ):
            self.assertIsNone(map_memory_to_profile("misc", content), content)

    def test_09_no_inference(self):
        self.assertIsNone(
            map_memory_to_profile("use_python_every_day", "I use Python every day.")
        )
        self.assertIsNone(
            map_memory_to_profile("work_on_doom", "I work on DOOM.")
        )

    def test_10_source_memory_id_preserved(self):
        res = project_memory_to_profile(
            "alice", "pm_src_abc", "prefer_python", "I prefer Python."
        )
        self.assertEqual(res.entry.source_memory_id, "pm_src_abc")

    def test_11_provenance_projected_memory(self):
        res = project_memory_to_profile(
            "alice", "pm_p", "prefer_python", "I prefer Python."
        )
        self.assertEqual(res.entry.provenance, Provenance.PROJECTED_MEMORY)

    def test_12_confidence_assignment(self):
        pref = map_memory_to_profile("prefer_python", "I prefer Python.")
        self.assertEqual(pref.confidence, Confidence.HIGH)
        temp = map_memory_to_profile(
            "exam", "I am currently preparing for an exam."
        )
        self.assertEqual(temp.confidence, Confidence.MEDIUM)

    def test_13_category_expiry(self):
        proj = project_memory_to_profile(
            "alice", "pm_pr", "building_project", "I am building DOOM."
        )
        self.assertIsNotNone(proj.entry.expires_at)
        pref = project_memory_to_profile(
            "alice", "pm_pf", "prefer_python", "I prefer Python."
        )
        self.assertIsNone(pref.entry.expires_at)

    def test_14_same_key_replacement(self):
        project_memory_to_profile("alice", "pm_1", "prefer_python", "I prefer Python.")
        res = project_memory_to_profile(
            "alice", "pm_2", "prefer_typescript", "I prefer TypeScript."
        )
        self.assertEqual(res.status, ProfileResultStatus.OK)
        self.assertEqual(res.entry.value, "TypeScript")
        self.assertEqual(res.entry.source_memory_id, "pm_2")
        listed = list_profile_entries("alice", categories=(Category.PREFERENCE,))
        self.assertEqual(len(listed.entries), 1)

    def test_15_version_increment(self):
        a = project_memory_to_profile("alice", "pm_1", "prefer_python", "I prefer Python.")
        self.assertEqual(a.entry.version, 1)
        b = project_memory_to_profile(
            "alice", "pm_2", "prefer_typescript", "I prefer TypeScript."
        )
        self.assertEqual(b.entry.version, 2)

    def test_16_version_conflict(self):
        a = project_memory_to_profile("alice", "pm_1", "prefer_python", "I prefer Python.")
        # Force conflict by racing expected version via store after stale expected
        from orchestration.user_model.store import upsert_profile_entry

        bad = upsert_profile_entry(
            "alice",
            {
                "category": Category.PREFERENCE,
                "key": "prefer_language",
                "value": "Rust",
                "provenance": Provenance.PROJECTED_MEMORY,
                "source_memory_id": "pm_x",
            },
            expected_version=a.entry.version + 50,
        )
        self.assertEqual(bad.status, ProfileResultStatus.CONFLICT)

    def test_17_idempotent_repeated_projection(self):
        a = project_memory_to_profile("alice", "pm_1", "prefer_python", "I prefer Python.")
        b = project_memory_to_profile("alice", "pm_1", "prefer_python", "I prefer Python.")
        self.assertEqual(b.status, ProfileResultStatus.OK)
        self.assertEqual(b.entry.version, a.entry.version)
        self.assertEqual(len(list_profile_entries("alice").entries), 1)

    def test_18_owner_isolation(self):
        project_memory_to_profile("alice", "pm_1", "prefer_python", "I prefer Python.")
        self.assertEqual(
            get_profile_entry("bob", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )
        project_memory_to_profile("bob", "pm_b", "prefer_python", "I prefer Python.")
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").entry.owner_id,
            "alice",
        )

    def test_19_sensitive_rejection(self):
        res = project_memory_to_profile(
            "alice", "pm_s", "password_note", "Remember my password is abc123"
        )
        self.assertEqual(res.status, ProfileResultStatus.SENSITIVE_REJECTED)
        self.assertEqual(list_profile_entries("alice").entries, ())

    def test_20_profile_limit_enforcement(self):
        cats = [
            Category.PREFERENCE,
            Category.PROJECT,
            Category.CONSTRAINT,
            Category.COMMUNICATION,
        ]
        n = 0
        for cat in cats:
            for i in range(MAX_ENTRIES_PER_CATEGORY):
                r = upsert_profile_entry(
                    "alice",
                    {"category": cat, "key": f"k_{cat.value}_{i}", "value": f"v{i}"},
                )
                self.assertEqual(r.status, ProfileResultStatus.OK)
                n += 1
        self.assertEqual(n, MAX_PROFILE_ENTRIES)
        res = project_memory_to_profile(
            "alice", "pm_over", "prefer_overflow", "I prefer OverflowLang."
        )
        # prefer_language may update existing prefer slot if any prefer_* exists
        # We filled prefer with k_preference_0..7 not prefer_language — so create rejected
        self.assertEqual(res.status, ProfileResultStatus.REJECTED)

    def test_21_category_limit_enforcement(self):
        for i in range(MAX_ENTRIES_PER_CATEGORY):
            upsert_profile_entry(
                "alice",
                {
                    "category": Category.PREFERENCE,
                    "key": f"prefer_item_{i}",
                    "value": f"v{i}",
                },
            )
        res = project_memory_to_profile(
            "alice", "pm_c", "prefer_newlang", "I prefer NewLang."
        )
        self.assertEqual(res.status, ProfileResultStatus.REJECTED)

    def test_22_malformed_input(self):
        self.assertEqual(
            project_memory_to_profile("", "pm_1", "prefer_python", "I prefer Python.").status,
            ProfileResultStatus.REJECTED,
        )
        self.assertEqual(
            project_memory_to_profile("alice", "", "prefer_python", "I prefer Python.").status,
            ProfileResultStatus.REJECTED,
        )

    def test_23_db_unavailable_via_flag_off(self):
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "false"
        res = project_memory_to_profile(
            "alice", "pm_1", "prefer_python", "I prefer Python."
        )
        self.assertEqual(res.status, ProfileResultStatus.UNAVAILABLE)
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

    def test_24_projection_failure_does_not_fail_memory_save(self):
        with patch(
            "orchestration.user_model.projection.project_after_memory_save",
            side_effect=RuntimeError("boom"),
        ):
            ok, code, msg = save_personal_memory(
                "alice", "Remember that I prefer Python."
            )
            self.assertTrue(ok)
            # Direct save doesn't call project_after — test via executor path
        prep = prepare_v8_request(
            "Remember that I prefer FailLang.", identity=_ident()
        )
        self.assertEqual(prep.intent, IntentClass.MEMORY_SAVE.value)
        with patch(
            "orchestration.user_model.projection.project_after_memory_save",
            side_effect=RuntimeError("boom"),
        ):
            res = execute_plan(
                prep.plan, identity=_ident(), authorized_plan_hash=""
            )
            out = format_v8_execution(res, intent=prep.intent)
        self.assertEqual(
            res.status.value if hasattr(res.status, "value") else res.status,
            "SUCCESS",
        )
        self.assertTrue(len(out.strip()) > 0)

    def test_25_flag_off_no_profile(self):
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "false"
        ok, code, msg = save_personal_memory(
            "alice", "Remember that I prefer Python."
        )
        self.assertTrue(ok)
        project_after_memory_save("alice", "Remember that I prefer Python.")
        # Flag off → store unavailable; force enable check on get
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"
        # Memory saved under test store; profile should be empty because projection ran while OFF
        self.assertEqual(list_profile_entries("alice").entries, ())

    def test_26_flag_on_memory_and_profile(self):
        ok, code, msg = save_personal_memory(
            "alice", "Remember that I prefer Python."
        )
        self.assertTrue(ok)
        res = project_after_memory_save("alice", "Remember that I prefer Python.")
        self.assertEqual(res.status, ProfileResultStatus.OK)
        got = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        self.assertEqual(got.status, ProfileResultStatus.OK)
        self.assertTrue(got.entry.source_memory_id.startswith("pm_"))

    def test_27_phase1_store_compatibility(self):
        res = project_memory_to_profile(
            "alice", "pm_1", "prefer_python", "I prefer Python."
        )
        self.assertEqual(res.status, ProfileResultStatus.OK)
        listed = list_profile_entries("alice")
        self.assertEqual(len(listed.entries), 1)

    def test_28_import_safety(self):
        import subprocess
        import sys

        script = "import orchestration.user_model.projection as p; print('OK')\n"
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_29_end_to_end_memory_save_hook(self):
        prep = prepare_v8_request(
            "Remember that I prefer Python.", identity=_ident()
        )
        self.assertEqual(prep.intent, IntentClass.MEMORY_SAVE.value)
        res = execute_plan(prep.plan, identity=_ident(), authorized_plan_hash="")
        self.assertEqual(
            res.status.value if hasattr(res.status, "value") else res.status,
            "SUCCESS",
        )
        got = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        self.assertEqual(got.status, ProfileResultStatus.OK)
        self.assertEqual(got.entry.value, "Python")

    def test_30_favorite_language_fact_key(self):
        mapped = map_memory_to_profile(
            "favorite_programming_language",
            "My favorite programming language is Python.",
        )
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.category, Category.PREFERENCE)
        self.assertEqual(mapped.key, "prefer_language")
        self.assertEqual(mapped.value, "Python")


if __name__ == "__main__":
    unittest.main()
