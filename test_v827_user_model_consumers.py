"""V8.27 Phase 4 — User Model consumer integration tests."""

from __future__ import annotations

import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

from orchestration.conversation.context import (
    _load_personal_section,
    assemble_respond_context,
    clear_conversation_thread,
    record_conversation_turn,
)
from orchestration.conversation.personal_memory import (
    reset_personal_memory_for_tests,
    save_personal_memory,
    use_test_personal_memory_store,
)
from orchestration.conversation.respond import _try_direct_memory_answer
from orchestration.decision.assemble import assemble_decision_input
from orchestration.goal.plan_types import GoalPlan, PlanStep
from orchestration.plan.assemble import assemble_plan_input
from orchestration.situation.assemble import _memory_items, build_situation_model
from orchestration.user_model.resolve import (
    format_profile_for_consumer,
    get_relevant_user_profile,
    profile_strings_for_consumer,
    try_direct_profile_fact_answer,
)
from orchestration.user_model.store import (
    forget_profile_entry,
    list_profile_entries,
    reset_user_model_for_tests,
    upsert_profile_entry,
    use_test_user_model_store,
)
from orchestration.user_model.types import (
    Category,
    Confidence,
    ProfileResultStatus,
    ProfileStatus,
    Provenance,
)


def _owner_plan(owner="alice", session="sess-p4"):
    return SimpleNamespace(owner_id=owner, session_id=session)


def _goal_plan(owner="alice", session="sess-p4"):
    return GoalPlan(
        plan_id="p1",
        goal_id="g1",
        schema_version="v82.1",
        owner_id=owner,
        session_id=session,
        computer_session_id="",
        steps=(
            PlanStep(
                step_id="s1",
                capability_id="conversation",
                action="RESPOND",
                parameters=(("text", "hi"),),
                dependencies=(),
                verification_required=False,
                verification_type="",
                risk="LOW",
                approval_required=False,
                retry_count=0,
                timeout_ms=55000,
            ),
        ),
        plan_risk="LOW",
        approval_required=False,
        provenance="TEST",
        plan_hash="h" * 64,
        execution_permitted=False,
        approved=False,
    )


def _pref(**kwargs):
    base = {
        "category": Category.PREFERENCE,
        "key": "prefer_language",
        "value": "Python",
        "provenance": Provenance.USER_EXPLICIT,
        "confidence": Confidence.HIGH,
    }
    base.update(kwargs)
    return base


class TestV827UserModelConsumers(unittest.TestCase):
    def setUp(self):
        use_test_user_model_store(True)
        reset_user_model_for_tests()
        use_test_personal_memory_store(True)
        reset_personal_memory_for_tests()
        clear_conversation_thread("alice", "sess-p4")
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

    def tearDown(self):
        clear_conversation_thread("alice", "sess-p4")
        reset_user_model_for_tests()
        use_test_user_model_store(False)
        reset_personal_memory_for_tests()
        use_test_personal_memory_store(False)

    # --- Decision ---

    def test_01_decision_receives_profile_preferences(self):
        upsert_profile_entry("alice", _pref())
        inp = assemble_decision_input(
            _owner_plan(), "Python or Node.js?"
        )
        joined = " ".join(inp.preferences)
        self.assertIn("[preference]", joined)
        self.assertIn("Python", joined)
        self.assertTrue(inp.source_flags.memory)

    def test_02_decision_max_4(self):
        for i in range(6):
            upsert_profile_entry(
                "alice",
                _pref(key=f"prefer_item_{i}", value=f"Val{i}"),
            )
        strings = profile_strings_for_consumer(
            "alice", "DECISION", query="my preferences", limit=4
        )
        self.assertLessEqual(len(strings), 4)
        inp = assemble_decision_input(_owner_plan(), "A or B considering my preferences?")
        self.assertLessEqual(len(inp.preferences), 4)

    def test_03_decision_fallback_to_personal_memory(self):
        save_personal_memory("alice", "Remember that I prefer Rust.")
        # No profile entries → memory path
        inp = assemble_decision_input(
            _owner_plan(), "Rust or Go considering my preferences?"
        )
        self.assertTrue(any("Rust" in p for p in inp.preferences))

    def test_04_decision_db_unavailable_fallback(self):
        save_personal_memory("alice", "Remember that I prefer Go.")
        with patch(
            "orchestration.user_model.resolve.get_relevant_user_profile",
            return_value=type("R", (), {"status": ProfileResultStatus.UNAVAILABLE, "entries": ()})(),
        ):
            # Force profile_strings empty via patching profile_strings
            with patch(
                "orchestration.user_model.resolve.profile_strings_for_consumer",
                return_value=(),
            ):
                inp = assemble_decision_input(
                    _owner_plan(), "Go or Zig considering my preferences?"
                )
        self.assertTrue(any("Go" in p for p in inp.preferences))

    def test_05_decision_malformed_profile_skipped(self):
        upsert_profile_entry("alice", _pref())
        # Inject a junk row into the test store that fails usability checks.
        from orchestration.user_model import store as um_store

        with um_store._LOCK:
            um_store._TEST_ROWS.setdefault("alice", []).append(
                {
                    "entry_id": "up_bad",
                    "owner_id": "alice",
                    "schema_version": 1,
                    "category": "preference",
                    "key": "broken",
                    "value": "",
                    "confidence": "HIGH",
                    "provenance": "USER_EXPLICIT",
                    "source_memory_id": "",
                    "version": 1,
                    "status": "ACTIVE",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "confirmed_at": time.time(),
                    "expires_at": None,
                }
            )
        res = get_relevant_user_profile("alice", "DECISION", limit=4)
        self.assertEqual(res.status, ProfileResultStatus.OK)
        self.assertTrue(all(e.value for e in res.entries))

    def test_06_decision_low_confidence_excluded(self):
        upsert_profile_entry(
            "alice",
            _pref(key="prefer_low", value="Haskell", confidence=Confidence.LOW),
        )
        upsert_profile_entry("alice", _pref(value="Python"))
        strings = profile_strings_for_consumer("alice", "DECISION", limit=4)
        joined = " ".join(strings)
        self.assertIn("Python", joined)
        self.assertNotIn("Haskell", joined)

    def test_07_decision_current_instruction_precedence(self):
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.COMMUNICATION,
                key="style",
                value="concise",
            ),
        )
        upsert_profile_entry("alice", _pref(value="Python"))
        q = "Give me a detailed explanation: Python or Node.js?"
        inp = assemble_decision_input(_owner_plan(), q)
        # Question preserved; communication not in DECISION bias.
        self.assertIn("detailed", inp.question.lower())
        joined = " ".join(inp.preferences).lower()
        self.assertNotIn("[communication]", joined)
        self.assertIn("python", joined)

    # --- Plan ---

    def test_08_plan_receives_profile_context(self):
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.PROJECT,
                key="current_project",
                value="DOOM",
            ),
        )
        inp = assemble_plan_input(
            _owner_plan(), "Plan next steps for my project."
        )
        joined = " ".join(inp.preferences)
        self.assertIn("[project]", joined)
        self.assertIn("DOOM", joined)

    def test_09_plan_max_4(self):
        for i in range(5):
            upsert_profile_entry(
                "alice",
                _pref(key=f"prefer_p_{i}", value=f"P{i}"),
            )
        inp = assemble_plan_input(_owner_plan(), "Plan using my preferences.")
        self.assertLessEqual(len(inp.preferences), 4)

    def test_10_plan_fallback(self):
        save_personal_memory("alice", "Remember that I prefer TypeScript.")
        inp = assemble_plan_input(
            _owner_plan(), "Plan with my language preferences."
        )
        self.assertTrue(any("TypeScript" in p for p in inp.preferences))

    def test_11_plan_db_unavailable(self):
        save_personal_memory("alice", "Remember that I prefer Kotlin.")
        with patch(
            "orchestration.user_model.resolve.profile_strings_for_consumer",
            side_effect=RuntimeError("db down"),
        ):
            inp = assemble_plan_input(
                _owner_plan(), "Plan with my preferences."
            )
        self.assertTrue(any("Kotlin" in p for p in inp.preferences))

    def test_12_plan_low_confidence_excluded(self):
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.PROJECT,
                key="current_project",
                value="SecretProj",
                confidence=Confidence.LOW,
            ),
        )
        strings = profile_strings_for_consumer("alice", "PLAN", limit=4)
        self.assertFalse(any("SecretProj" in s for s in strings))

    def test_13_plan_does_not_mutate_goal_registry(self):
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.PROJECT,
                key="current_project",
                value="DOOM",
            ),
        )
        with patch(
            "orchestration.plan.goal_registry.upsert_active_goal",
            create=True,
        ) as upsert_goal:
            assemble_plan_input(_owner_plan(), "Plan for my project DOOM.")
            upsert_goal.assert_not_called()

    # --- Situation ---

    def test_14_situation_receives_max_2(self):
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.CONSTRAINT,
                key="cost",
                value="hard zero cost",
            ),
        )
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.TEMPORARY_FACT,
                key="exam_prep",
                value="Preparing for exam",
            ),
        )
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.CONSTRAINT,
                key="offline",
                value="prefer offline tools",
            ),
        )
        strings = profile_strings_for_consumer(
            "alice", "SITUATION", query="about me", limit=4
        )
        self.assertLessEqual(len(strings), 2)

    def test_15_situation_only_constraint_temp(self):
        upsert_profile_entry("alice", _pref(value="Python"))
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.CONSTRAINT,
                key="cost",
                value="hard zero cost",
            ),
        )
        strings = profile_strings_for_consumer("alice", "SITUATION", limit=2)
        joined = " ".join(strings).lower()
        self.assertIn("constraint", joined)
        self.assertNotIn("[preference]", joined)

    def test_16_situation_fallback(self):
        save_personal_memory("alice", "Remember that I prefer dark theme.")
        q = (
            "Considering what you know about me and my current computer, "
            "what should I focus on?"
        )
        items = _memory_items("alice", q)
        self.assertTrue(any("dark theme" in m.lower() for m in items))

    # --- Conversation ---

    def test_17_conversation_communication_preference(self):
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.COMMUNICATION,
                key="style",
                value="concise",
            ),
        )
        section = _load_personal_section("alice", "considering my preferences")
        self.assertIn("[communication]", section)
        self.assertIn("concise", section)

    def test_18_conversation_preference(self):
        upsert_profile_entry("alice", _pref(value="Python"))
        section = _load_personal_section("alice", "what do you remember about me")
        self.assertIn("[preference]", section)
        self.assertIn("Python", section)

    def test_19_conversation_max_4(self):
        for i in range(6):
            upsert_profile_entry(
                "alice",
                _pref(key=f"prefer_c_{i}", value=f"C{i}"),
            )
        section = _load_personal_section("alice", "my preferences")
        # Numbered lines under Personal memory
        lines = [ln for ln in section.splitlines() if ln[:1].isdigit()]
        self.assertLessEqual(len(lines), 4)

    def test_20_conversation_thread_precedence(self):
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.COMMUNICATION,
                key="style",
                value="concise",
            ),
        )
        record_conversation_turn(
            "alice",
            "sess-p4",
            user_text="Use concise answers.",
            assistant_text="OK.",
        )
        # Current instruction asks for detail — query must remain detailed.
        q = "Make it detailed."
        ctx, _status = assemble_respond_context(_owner_plan(), q)
        # Profile may appear in personal section, but query/path is not rewritten.
        self.assertIsInstance(ctx, str)
        # Thread store still has prior turns; profile did not clear them.
        from orchestration.conversation.context import get_conversation_turns

        turns = get_conversation_turns("alice", "sess-p4")
        self.assertTrue(any("concise" in (t.get("text") or "").lower() for t in turns))

    # --- Direct fact ---

    def test_21_direct_fact_profile_lookup(self):
        upsert_profile_entry("alice", _pref(value="Python"))
        ans = try_direct_profile_fact_answer(
            "alice", "What programming language do I prefer?"
        )
        self.assertIsNotNone(ans)
        self.assertIn("Python", ans)

    def test_22_direct_fact_memory_fallback(self):
        save_personal_memory(
            "alice", "Remember that my favorite programming language is Ruby."
        )
        plan = _goal_plan()
        ans = _try_direct_memory_answer(
            plan, "What programming language do I prefer?"
        )
        self.assertIsNotNone(ans)
        self.assertIn("Ruby", ans)

    # --- Safety ---

    def test_23_no_profile_write_from_consumers(self):
        upsert_profile_entry("alice", _pref())
        before = list_profile_entries("alice")
        n_before = len(before.entries)
        assemble_decision_input(_owner_plan(), "Python or Node.js?")
        assemble_plan_input(_owner_plan(), "Plan for my project.")
        _load_personal_section("alice", "my preferences")
        after = list_profile_entries("alice")
        self.assertEqual(len(after.entries), n_before)

    def test_24_owner_isolation(self):
        upsert_profile_entry("alice", _pref(value="Python"))
        upsert_profile_entry(
            "bob",
            _pref(value="Java"),
        )
        alice = profile_strings_for_consumer("alice", "DECISION", limit=4)
        bob = profile_strings_for_consumer("bob", "DECISION", limit=4)
        self.assertTrue(any("Python" in s for s in alice))
        self.assertFalse(any("Java" in s for s in alice))
        self.assertTrue(any("Java" in s for s in bob))
        self.assertFalse(any("Python" in s for s in bob))

    def test_25_expired_entries_excluded(self):
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.TEMPORARY_FACT,
                key="exam_prep",
                value="Preparing for exam",
                expires_at=time.time() - 10,
            ),
        )
        strings = profile_strings_for_consumer("alice", "SITUATION", limit=2)
        self.assertFalse(any("exam" in s.lower() for s in strings))

    def test_26_superseded_entries_excluded(self):
        created = upsert_profile_entry("alice", _pref(value="Python"))
        forget_profile_entry(
            "alice",
            Category.PREFERENCE,
            "prefer_language",
            created.entry.version,
        )
        strings = profile_strings_for_consumer("alice", "DECISION", limit=4)
        self.assertFalse(any("Python" in s for s in strings))

    def test_27_malformed_profile_ignored(self):
        text = format_profile_for_consumer(None)  # type: ignore[arg-type]
        self.assertEqual(text, "")
        res = get_relevant_user_profile("alice", "NOT_A_CONTEXT", limit=4)
        self.assertEqual(res.status, ProfileResultStatus.REJECTED)

    def test_28_flag_off_preserves_previous_path(self):
        upsert_profile_entry("alice", _pref(value="Python"))
        save_personal_memory("alice", "Remember that I prefer Zig.")
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "false"
        try:
            inp = assemble_decision_input(
                _owner_plan(), "Zig or Rust considering my preferences?"
            )
            joined = " ".join(inp.preferences)
            self.assertNotIn("[preference]", joined)
            self.assertTrue(any("Zig" in p for p in inp.preferences))
        finally:
            os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

    def test_29_flag_on_enables_profile_path(self):
        upsert_profile_entry("alice", _pref(value="Python"))
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"
        strings = profile_strings_for_consumer("alice", "DECISION", limit=4)
        self.assertTrue(any("Python" in s for s in strings))

    def test_30_import_safety(self):
        import importlib

        mod = importlib.import_module("orchestration.user_model.resolve")
        src = open(mod.__file__, encoding="utf-8").read()
        for banned in (
            "orchestration.plan.goal_registry",
            "proactive.config",
            "memory.manager",
            "openai",
            "ollama",
        ):
            self.assertNotIn(banned, src)
        # Package import must not require Postgres.
        importlib.import_module("orchestration.decision.assemble")
        importlib.import_module("orchestration.plan.assemble")

    def test_31_no_action_browser_computer_authority(self):
        src = open(
            "orchestration/user_model/resolve.py", encoding="utf-8"
        ).read()
        for banned in (
            "browser",
            "computer.actions",
            "execute_plan",
            "Authorization",
        ):
            self.assertNotIn(banned, src)

    def test_32_no_model_api_call(self):
        upsert_profile_entry("alice", _pref())
        with patch("urllib.request.urlopen", MagicMock()) as urlopen:
            get_relevant_user_profile("alice", "DECISION", query="Python", limit=4)
            assemble_decision_input(_owner_plan(), "Python or Node.js?")
            urlopen.assert_not_called()

    def test_33_situation_model_injects_constraint(self):
        upsert_profile_entry(
            "alice",
            _pref(
                category=Category.CONSTRAINT,
                key="cost",
                value="hard zero cost",
            ),
        )
        q = (
            "Considering what you know about me and my current computer, "
            "what should I focus on?"
        )
        with patch(
            "orchestration.system.observe.collect_system_observation"
        ) as obs:
            from orchestration.system.observe import DiskObservation, SystemObservation

            obs.return_value = SystemObservation(
                timestamp_unix=time.time(),
                cpu_percent=70.0,
                memory_total_gb=16.0,
                memory_used_gb=13.0,
                memory_available_gb=3.0,
                memory_percent=86.0,
                disks=(DiskObservation("C:", 40.0, 200.0, 80.0),),
                os_name="Windows",
                os_version="10",
                architecture="AMD64",
                python_version="3.11",
                ollama_status="running",
                doom_dashboard_status="running",
                health="UNDER_MEMORY_PRESSURE",
            )
            model = build_situation_model(_goal_plan(), q)
        self.assertIsNotNone(model)
        joined = " ".join(model.relevant_memory)
        self.assertIn("hard zero cost", joined)


if __name__ == "__main__":
    unittest.main()
