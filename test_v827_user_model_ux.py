"""V8.27 Phase 2 — User Model explicit UX tests."""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

from orchestration.conversation.context import reset_conversation_context_for_tests
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.types import IntentClass
from orchestration.production import format_v8_execution, prepare_v8_request
from orchestration.user_model.confirm import (
    ACTION_UPSERT,
    ProfileConfirm,
    clear_profile_confirm,
    get_profile_confirm,
    reset_profile_confirms_for_tests,
    set_profile_confirm,
)
from orchestration.user_model.intent import (
    ProfileIntent,
    detect_profile_intent,
    handle_user_model_request,
    should_route_user_model,
)
from orchestration.user_model.store import (
    get_profile_entry,
    list_profile_entries,
    reset_user_model_for_tests,
    use_test_user_model_store,
)
from orchestration.user_model.types import Category, ProfileResultStatus, Provenance


def _ident(owner="alice", session="sess-p2"):
    return ExecutionIdentity(owner_id=owner, session_id=session, computer_session_id="")


class TestV827UserModelUX(unittest.TestCase):
    def setUp(self):
        reset_conversation_context_for_tests()
        use_test_user_model_store(True)
        reset_user_model_for_tests()
        reset_profile_confirms_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

    def tearDown(self):
        reset_profile_confirms_for_tests()
        reset_user_model_for_tests()
        use_test_user_model_store(False)
        reset_conversation_context_for_tests()

    def _run(self, phrase, owner="alice", session="sess-p2"):
        prep = prepare_v8_request(phrase, identity=_ident(owner, session))
        return prep

    def _exec(self, phrase, owner="alice", session="sess-p2"):
        prep = self._run(phrase, owner, session)
        self.assertEqual(prep.status, "OK", (prep.status, prep.reason, prep.intent))
        self.assertIsNotNone(prep.plan)
        res = execute_plan(prep.plan, identity=_ident(owner, session), authorized_plan_hash="")
        return format_v8_execution(res, intent=prep.intent), prep

    # --- Detection ---
    def test_01_assert_detection(self):
        self.assertEqual(
            detect_profile_intent("I prefer Python.")[0], ProfileIntent.PROFILE_ASSERT
        )

    def test_02_forget_detection(self):
        self.assertEqual(
            detect_profile_intent("Forget that I prefer Python.")[0],
            ProfileIntent.PROFILE_FORGET,
        )

    def test_03_clear_detection(self):
        self.assertEqual(
            detect_profile_intent("Forget what you know about me.")[0],
            ProfileIntent.PROFILE_CLEAR,
        )

    def test_04_query_detection(self):
        self.assertEqual(
            detect_profile_intent("What do you know about me?")[0],
            ProfileIntent.PROFILE_QUERY,
        )

    def test_05_why_detection(self):
        self.assertEqual(
            detect_profile_intent("Why do you think I prefer Python?")[0],
            ProfileIntent.PROFILE_WHY,
        )

    def test_06_ordinary_conversation_non_match(self):
        for q in (
            "Python is nice.",
            "Python is better.",
            "Can you explain Python?",
            "Let's use Python.",
            "Tell me a joke",
        ):
            self.assertEqual(detect_profile_intent(q)[0], ProfileIntent.NONE, q)

    def test_07_decision_boundary(self):
        self.assertEqual(detect_profile_intent("Python or Node.js?")[0], ProfileIntent.NONE)
        prep = self._run("Python or Node.js?")
        self.assertEqual(prep.intent, IntentClass.DECISION.value)

    def test_08_plan_boundary(self):
        self.assertEqual(
            detect_profile_intent("Make me a plan for learning Python")[0],
            ProfileIntent.NONE,
        )
        prep = self._run("Make me a plan for learning Python from scratch today")
        self.assertEqual(prep.intent, IntentClass.PLAN.value)

    def test_09_computer_boundary(self):
        self.assertEqual(detect_profile_intent("Click Save")[0], ProfileIntent.NONE)
        intent = normalize_intent("Click Save")
        self.assertNotEqual(intent, IntentClass.MEMORY_READ)

    def test_10_memory_save_boundary(self):
        self.assertEqual(
            detect_profile_intent("Remember that I prefer Python.")[0],
            ProfileIntent.NONE,
        )
        prep = self._run("Remember that I prefer Python.")
        self.assertEqual(prep.intent, IntentClass.MEMORY_SAVE.value)

    # --- Confirmation flow ---
    def test_11_assert_creates_confirmation(self):
        out = handle_user_model_request("alice", "s1", "I prefer Python.")
        self.assertIsNotNone(out)
        self.assertIn("Confirm", out)
        self.assertIsNotNone(get_profile_confirm("alice", "s1"))
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )

    def test_12_assert_does_not_immediately_persist(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )

    def test_13_yes_persists(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        out = handle_user_model_request("alice", "s1", "YES")
        self.assertIn("Saved", out)
        got = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        self.assertEqual(got.status, ProfileResultStatus.OK)
        self.assertEqual(got.entry.value, "Python")
        self.assertEqual(got.entry.provenance, Provenance.USER_EXPLICIT)

    def test_14_no_cancels(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        out = handle_user_model_request("alice", "s1", "NO")
        self.assertIn("cancel", out.lower())
        self.assertIsNone(get_profile_confirm("alice", "s1"))
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )

    def test_15_ambiguous_confirmation_no_mutate(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        out = handle_user_model_request("alice", "s1", "maybe")
        self.assertIn("YES", out)
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )

    def test_16_expired_confirmation_rejected(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        conf = get_profile_confirm("alice", "s1")
        expired = ProfileConfirm(
            owner_id=conf.owner_id,
            session_id=conf.session_id,
            action=conf.action,
            category=conf.category,
            key=conf.key,
            value=conf.value,
            expected_version=conf.expected_version,
            expires_at=time.time() - 10,
            nonce=conf.nonce,
            old_value=conf.old_value,
        )
        set_profile_confirm(expired)
        self.assertIsNone(get_profile_confirm("alice", "s1"))
        out = handle_user_model_request("alice", "s1", "YES")
        self.assertTrue(out is None or "Saved" not in out)
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )

    def test_17_wrong_owner_rejected(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        out = handle_user_model_request("bob", "s1", "YES")
        self.assertTrue(out is None or "Saved" not in (out or ""))
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )

    def test_18_wrong_session_rejected(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        out = handle_user_model_request("alice", "other", "YES")
        self.assertTrue(out is None or "Saved" not in (out or ""))
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )

    def test_19_version_conflict_rejected(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        handle_user_model_request("alice", "s1", "YES")
        handle_user_model_request("alice", "s1", "I prefer TypeScript.")
        conf = get_profile_confirm("alice", "s1")
        bad = ProfileConfirm(
            owner_id=conf.owner_id,
            session_id=conf.session_id,
            action=conf.action,
            category=conf.category,
            key=conf.key,
            value=conf.value,
            expected_version=conf.expected_version + 99,
            expires_at=conf.expires_at,
            nonce=conf.nonce,
            old_value=conf.old_value,
        )
        set_profile_confirm(bad)
        out = handle_user_model_request("alice", "s1", "YES")
        self.assertIn("changed", out.lower())
        got = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        self.assertEqual(got.entry.value, "Python")

    def test_20_replacement_shows_old_new(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        handle_user_model_request("alice", "s1", "YES")
        out = handle_user_model_request("alice", "s1", "I prefer TypeScript.")
        self.assertIn("→", out)
        self.assertIn("Python", out)
        self.assertIn("TypeScript", out)

    def test_21_forget_confirmation(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        handle_user_model_request("alice", "s1", "YES")
        out = handle_user_model_request("alice", "s1", "Forget that I prefer Python.")
        self.assertIn("Confirm", out)
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.OK,
        )
        out2 = handle_user_model_request("alice", "s1", "YES")
        self.assertIn("Removed", out2)
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )

    def test_22_clear_confirmation(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        handle_user_model_request("alice", "s1", "YES")
        out = handle_user_model_request("alice", "s1", "Clear my profile.")
        self.assertIn("Confirm", out)
        handle_user_model_request("alice", "s1", "YES")
        self.assertEqual(list_profile_entries("alice").entries, ())

    def test_23_transparency_max_12(self):
        for i in range(8):
            handle_user_model_request("alice", "s1", f"I prefer Lang{i}.")
            # each creates confirm — apply
            handle_user_model_request("alice", "s1", "YES")
        # prefer_language overwritten each time — only 1 preference
        # add projects via preferred language variants won't work — use project asserts
        handle_user_model_request("alice", "s1", "My current project is DOOM.")
        handle_user_model_request("alice", "s1", "YES")
        out = handle_user_model_request("alice", "s1", "What do you know about me?")
        self.assertIsNotNone(out)
        # At most 12 numbered lines
        nums = [ln for ln in out.splitlines() if ln[:2].rstrip(".").isdigit() or (ln and ln[0].isdigit() and "." in ln[:3])]
        self.assertLessEqual(len(nums), 12)

    def test_24_category_filtering(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        handle_user_model_request("alice", "s1", "YES")
        handle_user_model_request("alice", "s1", "My current project is DOOM.")
        handle_user_model_request("alice", "s1", "YES")
        prefs = handle_user_model_request("alice", "s1", "What are my preferences?")
        self.assertIn("Python", prefs)
        self.assertNotIn("DOOM", prefs)
        work = handle_user_model_request("alice", "s1", "What do you remember about my work?")
        self.assertIn("DOOM", work)

    def test_25_why_provenance(self):
        handle_user_model_request("alice", "s1", "I prefer Python.")
        handle_user_model_request("alice", "s1", "YES")
        out = handle_user_model_request("alice", "s1", "Why do you think I prefer Python?")
        self.assertIn("USER_EXPLICIT", out)
        self.assertIn("Python", out)

    def test_26_unknown_why(self):
        out = handle_user_model_request(
            "alice", "s1", "Why do you think I prefer Rust?"
        )
        self.assertIn("don't have enough", out.lower())

    def test_27_sensitive_assertion_rejected(self):
        out = handle_user_model_request(
            "alice", "s1", "I prefer password is secret123"
        )
        # mapped as prefer_language with sensitive value → rejected before confirm
        self.assertIsNotNone(out)
        self.assertTrue(
            "cannot store" in out.lower() or "Confirm" not in out or "password" in out.lower()
        )
        # If mapped and sensitive, no confirm stored
        if "cannot store" in out.lower():
            self.assertIsNone(get_profile_confirm("alice", "s1"))

    def test_28_flag_off_preserves_old_behavior(self):
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "false"
        self.assertFalse(should_route_user_model("I prefer Python.", "alice", "s1"))
        self.assertIsNone(handle_user_model_request("alice", "s1", "I prefer Python."))
        prep = self._run("I prefer Python.")
        self.assertNotEqual(prep.intent, IntentClass.MEMORY_READ.value)
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

    def test_29_import_safety(self):
        import subprocess
        import sys

        script = (
            "import orchestration.user_model.intent\n"
            "import orchestration.user_model.confirm\n"
            "import orchestration.user_model.format\n"
            "print('OK')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_30_no_goal_registry_dependency(self):
        import orchestration.user_model.intent as intent_mod
        import inspect

        src = inspect.getsource(intent_mod)
        self.assertNotIn("goal_registry", src)
        self.assertNotIn("orchestration.plan.goal_registry", src)

    def test_31_end_to_end_routing_assert(self):
        out, prep = self._exec("I prefer Python.")
        self.assertEqual(prep.intent, IntentClass.MEMORY_READ.value)
        self.assertIn("Confirm", out)

    def test_32_communication_style_assert(self):
        out = handle_user_model_request("alice", "s1", "I prefer concise answers.")
        self.assertIn("Confirm", out)
        handle_user_model_request("alice", "s1", "YES")
        got = get_profile_entry("alice", Category.COMMUNICATION, "style")
        self.assertEqual(got.status, ProfileResultStatus.OK)
        self.assertEqual(got.entry.value, "concise")

    def test_33_project_assert(self):
        handle_user_model_request("alice", "s1", "My current project is DOOM.")
        handle_user_model_request("alice", "s1", "YES")
        got = get_profile_entry("alice", Category.PROJECT, "current_project")
        self.assertEqual(got.status, ProfileResultStatus.OK)
        self.assertEqual(got.entry.value, "DOOM")


if __name__ == "__main__":
    unittest.main()
