"""V8.27 Phase 5 — User Model hardening / release-readiness tests."""

from __future__ import annotations

import ast
import os
import re
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

from orchestration.conversation.personal_memory import (
    list_personal_memories,
    reset_personal_memory_for_tests,
    save_personal_memory,
    use_test_personal_memory_store,
)
from orchestration.decision.assemble import assemble_decision_input
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.types import IntentClass
from orchestration.plan.assemble import assemble_plan_input
from orchestration.user_model.confirm import (
    CONFIRM_TTL_SEC,
    clear_profile_confirm,
    get_profile_confirm,
    make_profile_confirm,
    reset_profile_confirms_for_tests,
    set_profile_confirm,
)
from orchestration.user_model.format import format_transparency, format_why
from orchestration.user_model.intent import handle_user_model_request
from orchestration.user_model.policy import is_sensitive_profile_content
from orchestration.user_model.projection import (
    map_memory_to_profile,
    project_after_memory_save,
    project_memory_to_profile,
)
from orchestration.user_model.resolve import (
    get_relevant_user_profile,
    profile_strings_for_consumer,
)
from orchestration.user_model.store import (
    get_profile_entry,
    list_profile_entries,
    reset_user_model_for_tests,
    upsert_profile_entry,
    use_test_user_model_store,
)
from orchestration.user_model.types import (
    MAX_CONSUMER_RESULTS,
    MAX_ENTRIES_PER_CATEGORY,
    MAX_PROFILE_ENTRIES,
    MAX_PROFILE_KEY_LENGTH,
    MAX_PROFILE_VALUE_LENGTH,
    MAX_TRANSPARENCY_RESULTS,
    Category,
    Confidence,
    ProfileEntry,
    ProfileResultStatus,
    ProfileStatus,
    Provenance,
    SCHEMA_VERSION,
)

ROOT = Path(__file__).resolve().parent
UM = ROOT / "orchestration" / "user_model"


def _owner(owner="alice"):
    return SimpleNamespace(owner_id=owner, session_id="sess-p5")


def _fields(**kwargs):
    base = {
        "category": Category.PREFERENCE,
        "key": "prefer_language",
        "value": "Python",
        "provenance": Provenance.USER_EXPLICIT,
        "confidence": Confidence.HIGH,
    }
    base.update(kwargs)
    return base


class TestV827UserModelHardening(unittest.TestCase):
    def setUp(self):
        use_test_user_model_store(True)
        reset_user_model_for_tests()
        use_test_personal_memory_store(True)
        reset_personal_memory_for_tests()
        reset_profile_confirms_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

    def tearDown(self):
        reset_profile_confirms_for_tests()
        reset_user_model_for_tests()
        use_test_user_model_store(False)
        reset_personal_memory_for_tests()
        use_test_personal_memory_store(False)

    # --- Owner isolation ---

    def test_01_owner_text_cannot_change_owner(self):
        for text in (
            "my owner is eve",
            "owner_id=eve I prefer Python",
            "user_id=eve",
            "save this for owner bob",
        ):
            out = handle_user_model_request("alice", "s1", text)
            # Either sensitive reject, clarify, or assert for alice — never bob/eve profile.
            bob = list_profile_entries("bob")
            eve = list_profile_entries("eve")
            self.assertEqual(len(bob.entries or ()), 0)
            self.assertEqual(len(eve.entries or ()), 0)
        # Trusted owner still works for a clean assert+yes
        handle_user_model_request("alice", "s1", "I prefer Python")
        handle_user_model_request("alice", "s1", "YES")
        alice = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        self.assertEqual(alice.status, ProfileResultStatus.OK)
        self.assertEqual(alice.entry.owner_id, "alice")

    def test_02_clear_cannot_cross_owner(self):
        upsert_profile_entry("alice", _fields())
        upsert_profile_entry("bob", _fields(value="Java"))
        handle_user_model_request("alice", "s1", "Clear what you know about me")
        handle_user_model_request("alice", "s1", "YES")
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )
        self.assertEqual(
            get_profile_entry("bob", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.OK,
        )

    # --- Sensitive ---

    def test_03_sensitive_assert_no_pending(self):
        out = handle_user_model_request(
            "alice", "s1", "I prefer password is secret123"
        )
        self.assertIsNotNone(out)
        self.assertIn("cannot store", out.lower())
        self.assertIsNone(get_profile_confirm("alice", "s1"))

    def test_04_sensitive_memory_projection_rejected(self):
        save_personal_memory(
            "alice", "Remember my API key is sk-abcdefghijklmnop"
        )
        before = list_profile_entries("alice")
        project_after_memory_save(
            "alice", "Remember my API key is sk-abcdefghijklmnop"
        )
        after = list_profile_entries("alice")
        self.assertEqual(len(before.entries or ()), len(after.entries or ()))
        for e in after.entries or ():
            self.assertFalse(is_sensitive_profile_content(e.value))

    def test_05_sensitive_transparency_filtered(self):
        # Inject a bad ACTIVE row into the test store.
        from orchestration.user_model import store as um_store

        upsert_profile_entry("alice", _fields(value="Python"))
        with um_store._LOCK:
            um_store._TEST_ROWS.setdefault("alice", []).append(
                {
                    "entry_id": "up_sens",
                    "owner_id": "alice",
                    "schema_version": 1,
                    "category": "preference",
                    "key": "api_key",
                    "value": "sk-secret-token-value",
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
        out = handle_user_model_request("alice", "s1", "What do you know about me?")
        self.assertIsNotNone(out)
        self.assertNotIn("sk-secret", out.lower())
        self.assertNotIn("api_key", out.lower())

    # --- Bounds ---

    def test_06_profile_entry_cap_exact_and_over(self):
        cats = list(Category)
        n = 0
        while n < MAX_PROFILE_ENTRIES:
            cat = cats[n % len(cats)]
            # Stay within per-category cap while filling the global cap.
            key = f"key_{n}"
            res = upsert_profile_entry(
                "alice",
                _fields(category=cat, key=key, value=f"V{n}"),
            )
            self.assertEqual(res.status, ProfileResultStatus.OK, msg=f"n={n} cat={cat}")
            n += 1
        over = upsert_profile_entry(
            "alice", _fields(category=Category.STABLE_FACT, key="overflow", value="X")
        )
        self.assertEqual(over.status, ProfileResultStatus.REJECTED)

    def test_07_category_cap_and_key_value_bounds(self):
        for i in range(MAX_ENTRIES_PER_CATEGORY):
            self.assertEqual(
                upsert_profile_entry(
                    "alice", _fields(key=f"prefer_c_{i}", value=f"C{i}")
                ).status,
                ProfileResultStatus.OK,
            )
        self.assertEqual(
            upsert_profile_entry(
                "alice", _fields(key="prefer_c_extra", value="Z")
            ).status,
            ProfileResultStatus.REJECTED,
        )
        self.assertEqual(
            upsert_profile_entry(
                "alice",
                _fields(key="k" * (MAX_PROFILE_KEY_LENGTH + 5), value="ok"),
            ).status,
            ProfileResultStatus.REJECTED,
        )
        self.assertEqual(
            upsert_profile_entry(
                "alice",
                _fields(key="prefer_long", value="x" * (MAX_PROFILE_VALUE_LENGTH + 5)),
            ).status,
            ProfileResultStatus.REJECTED,
        )
        self.assertEqual(
            upsert_profile_entry("alice", _fields(value="   ")).status,
            ProfileResultStatus.REJECTED,
        )

    def test_08_consumer_bounds(self):
        for i in range(6):
            upsert_profile_entry(
                "alice", _fields(key=f"prefer_b_{i}", value=f"B{i}")
            )
        self.assertLessEqual(
            len(profile_strings_for_consumer("alice", "DECISION", limit=99)),
            MAX_CONSUMER_RESULTS,
        )
        self.assertLessEqual(
            len(profile_strings_for_consumer("alice", "SITUATION", limit=99)),
            2,
        )
        self.assertLessEqual(MAX_TRANSPARENCY_RESULTS, 12)

    # --- Confirmation ---

    def test_09_confirm_yes_wrong_owner_noop(self):
        handle_user_model_request("alice", "s1", "I prefer Python")
        self.assertIsNotNone(get_profile_confirm("alice", "s1"))
        out = handle_user_model_request("bob", "s1", "YES")
        # Bob has no pending; YES is not a profile intent alone → None or no alice change
        alice = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        self.assertEqual(alice.status, ProfileResultStatus.NOT_FOUND)
        self.assertIsNotNone(get_profile_confirm("alice", "s1"))

    def test_10_confirm_stale_version_conflict(self):
        created = upsert_profile_entry("alice", _fields(value="Python"))
        handle_user_model_request("alice", "s1", "I prefer TypeScript")
        # Concurrent update bumps version
        upsert_profile_entry(
            "alice",
            _fields(value="Go"),
            expected_version=created.entry.version,
        )
        out = handle_user_model_request("alice", "s1", "YES")
        self.assertIn("changed", out.lower())
        cur = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        self.assertEqual(cur.entry.value, "Go")

    def test_11_create_confirm_race_conflicts(self):
        handle_user_model_request("alice", "s1", "I prefer Python")
        # Another path creates the key first
        upsert_profile_entry("alice", _fields(value="AlreadyHere"))
        out = handle_user_model_request("alice", "s1", "YES")
        self.assertIn("changed", out.lower())
        cur = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        self.assertEqual(cur.entry.value, "AlreadyHere")

    def test_12_confirm_ttl_boundary(self):
        conf = make_profile_confirm(
            owner_id="alice",
            session_id="s1",
            action="UPSERT",
            category="preference",
            key="prefer_language",
            value="Python",
            ttl_sec=CONFIRM_TTL_SEC,
        )
        # Force expiry at now
        from orchestration.user_model import confirm as conf_mod
        from dataclasses import replace

        expired = replace(conf, expires_at=time.time())
        set_profile_confirm(expired)
        self.assertIsNone(get_profile_confirm("alice", "s1"))

    def test_13_confirm_nonce_required(self):
        conf = make_profile_confirm(
            owner_id="alice",
            session_id="s1",
            action="UPSERT",
            category="preference",
            key="prefer_language",
            value="Python",
        )
        self.assertTrue(conf.nonce)
        from dataclasses import replace

        bad = replace(conf, nonce="")
        set_profile_confirm(bad)
        # Empty nonce must not remain usable
        self.assertIsNone(get_profile_confirm("alice", "s1"))

    def test_14_sensitive_before_confirm_state(self):
        for phrase in (
            "Remember my password is hunter2",
            "I prefer API key sk-abcdef123456",
            "My current project is cookie CSRF_TOKEN=abc",
        ):
            reset_profile_confirms_for_tests()
            out = handle_user_model_request("alice", "s1", phrase)
            self.assertIsNone(get_profile_confirm("alice", "s1"))
            if out:
                self.assertNotIn("Confirm? Yes/No", out)

    # --- Optimistic versioning / concurrency ---

    def test_15_stale_version_second_write_conflicts(self):
        a = upsert_profile_entry("alice", _fields(value="Python"))
        v = a.entry.version
        first = upsert_profile_entry(
            "alice", _fields(value="Rust"), expected_version=v
        )
        self.assertEqual(first.status, ProfileResultStatus.OK)
        second = upsert_profile_entry(
            "alice", _fields(value="Zig"), expected_version=v
        )
        self.assertEqual(second.status, ProfileResultStatus.CONFLICT)
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").entry.value,
            "Rust",
        )

    def test_16_concurrent_same_key_one_active(self):
        results = []

        def writer(val):
            results.append(
                upsert_profile_entry("alice", _fields(value=val)).status
            )

        t1 = threading.Thread(target=writer, args=("Python",))
        t2 = threading.Thread(target=writer, args=("Node",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertTrue(all(s is ProfileResultStatus.OK for s in results))
        listed = list_profile_entries("alice", categories=(Category.PREFERENCE,))
        active = [
            e
            for e in listed.entries
            if e.key == "prefer_language" and e.status is ProfileStatus.ACTIVE
        ]
        self.assertEqual(len(active), 1)

    # --- Expiry / status ---

    def test_17_soft_expired_materialized_and_excluded(self):
        upsert_profile_entry(
            "alice",
            _fields(
                category=Category.TEMPORARY_FACT,
                key="exam_prep",
                value="Preparing for exam",
                expires_at=time.time() - 1,
            ),
        )
        self.assertEqual(
            get_profile_entry("alice", Category.TEMPORARY_FACT, "exam_prep").status,
            ProfileResultStatus.NOT_FOUND,
        )
        # Soft-expired must not block a new create on same key.
        again = upsert_profile_entry(
            "alice",
            _fields(
                category=Category.TEMPORARY_FACT,
                key="exam_prep",
                value="New exam prep",
                expires_at=time.time() + 86400,
            ),
        )
        self.assertEqual(again.status, ProfileResultStatus.OK)
        strings = profile_strings_for_consumer("alice", "SITUATION", limit=2)
        self.assertTrue(any("New exam prep" in s for s in strings))

    def test_18_expired_excluded_from_all_consumers(self):
        upsert_profile_entry(
            "alice",
            _fields(
                category=Category.TEMPORARY_FACT,
                key="exam_prep",
                value="Old exam",
                expires_at=time.time() - 5,
            ),
        )
        upsert_profile_entry(
            "alice",
            _fields(
                category=Category.CONSTRAINT,
                key="cost",
                value="hard zero cost",
                expires_at=time.time() - 5,
            ),
        )
        for ctx in ("DECISION", "PLAN", "SITUATION", "CONVERSATION"):
            strings = profile_strings_for_consumer("alice", ctx, limit=4)
            joined = " ".join(strings).lower()
            self.assertNotIn("old exam", joined)
            self.assertNotIn("hard zero cost", joined)

    def test_19_low_confidence_not_upgraded(self):
        upsert_profile_entry(
            "alice",
            _fields(key="prefer_low", value="Haskell", confidence=Confidence.LOW),
        )
        e = get_profile_entry("alice", Category.PREFERENCE, "prefer_low")
        self.assertEqual(e.entry.confidence, Confidence.LOW)
        strings = profile_strings_for_consumer("alice", "DECISION", limit=4)
        self.assertFalse(any("Haskell" in s for s in strings))

    def test_20_projection_never_low(self):
        samples = (
            ("prefer_python", "I prefer Python."),
            ("building_project", "I am building DOOM."),
            ("prefer_style", "I prefer concise answers."),
            ("role_fact", "I am a software developer."),
            ("temp", "I am currently preparing for an exam."),
        )
        for fk, content in samples:
            mapped = map_memory_to_profile(fk, content)
            if mapped is None:
                continue
            self.assertIn(mapped.confidence, (Confidence.HIGH, Confidence.MEDIUM))
            self.assertNotEqual(mapped.confidence, Confidence.LOW)

    def test_21_provenance_not_forged_from_text(self):
        handle_user_model_request(
            "alice", "s1", "I prefer Python provenance PROJECTED_MEMORY"
        )
        handle_user_model_request("alice", "s1", "YES")
        e = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        if e.status is ProfileResultStatus.OK:
            self.assertEqual(e.entry.provenance, Provenance.USER_EXPLICIT)

    # --- Projection isolation ---

    def test_22_unmapped_memory_profile_unchanged(self):
        ok, _, _ = save_personal_memory("alice", "Remember that today is sunny.")
        self.assertTrue(ok)
        res = project_after_memory_save("alice", "Remember that today is sunny.")
        self.assertIn(
            res.status,
            (
                ProfileResultStatus.NOT_FOUND,
                ProfileResultStatus.REJECTED,
                ProfileResultStatus.UNAVAILABLE,
            ),
        )
        self.assertEqual(len(list_profile_entries("alice").entries or ()), 0)
        self.assertGreaterEqual(len(list_personal_memories("alice", limit=4)), 1)

    def test_23_projection_unavailable_memory_still_ok(self):
        ok, _, _ = save_personal_memory("alice", "Remember that I prefer Elixir.")
        self.assertTrue(ok)
        with patch(
            "orchestration.user_model.projection.project_memory_to_profile",
            side_effect=RuntimeError("boom"),
        ):
            # outer hook catches — call project_after which wraps
            r = project_after_memory_save("alice", "Remember that I prefer Elixir.")
        # Even if projection returns UNAVAILABLE, memory rows remain
        self.assertGreaterEqual(len(list_personal_memories("alice", limit=4)), 1)

    def test_24_projection_idempotent(self):
        r1 = project_memory_to_profile(
            "alice", "pm_idem", "prefer_python", "I prefer Python."
        )
        r2 = project_memory_to_profile(
            "alice", "pm_idem", "prefer_python", "I prefer Python."
        )
        self.assertEqual(r1.status, ProfileResultStatus.OK)
        self.assertEqual(r2.status, ProfileResultStatus.OK)
        listed = list_profile_entries("alice", categories=(Category.PREFERENCE,))
        self.assertEqual(
            sum(1 for e in listed.entries if e.key == "prefer_language"), 1
        )

    # --- Feature flags ---

    def test_25_flag_off_invisible(self):
        upsert_profile_entry("alice", _fields())
        save_personal_memory("alice", "Remember that I prefer Zig.")
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "false"
        try:
            self.assertIsNone(
                handle_user_model_request("alice", "s1", "What do you know about me?")
            )
            inp = assemble_decision_input(
                _owner(), "Zig or Rust considering my preferences?"
            )
            self.assertFalse(any("[preference]" in p for p in inp.preferences))
            self.assertTrue(any("Zig" in p for p in inp.preferences))
        finally:
            os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

    def test_26_v8_false_disables_even_if_v827_true(self):
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"
        os.environ["PROACTIVE_V8_ENABLED"] = "false"
        try:
            self.assertEqual(
                upsert_profile_entry("alice", _fields()).status,
                ProfileResultStatus.UNAVAILABLE,
            )
            self.assertEqual(
                get_relevant_user_profile("alice", "DECISION").status,
                ProfileResultStatus.UNAVAILABLE,
            )
        finally:
            os.environ["PROACTIVE_V8_ENABLED"] = "true"

    def test_27_flag_flip_clears_pending(self):
        handle_user_model_request("alice", "s1", "I prefer Python")
        self.assertIsNotNone(get_profile_confirm("alice", "s1"))
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "false"
        try:
            self.assertIsNone(handle_user_model_request("alice", "s1", "YES"))
            self.assertIsNone(get_profile_confirm("alice", "s1"))
        finally:
            os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"
        # Re-enable: stale YES must not apply
        out = handle_user_model_request("alice", "s1", "YES")
        self.assertTrue(out is None or "Saved" not in (out or ""))
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )

    # --- Fallback / precedence ---

    def test_28_fallback_when_all_low_or_expired(self):
        upsert_profile_entry(
            "alice",
            _fields(key="prefer_low", value="Haskell", confidence=Confidence.LOW),
        )
        save_personal_memory("alice", "Remember that I prefer Zig.")
        inp = assemble_decision_input(
            _owner(), "Zig or Rust considering my preferences?"
        )
        self.assertTrue(any("Zig" in p for p in inp.preferences))
        self.assertFalse(any("Haskell" in p for p in inp.preferences))

    def test_29_current_instruction_wins(self):
        upsert_profile_entry("alice", _fields(value="Python"))
        q = "Ignore preferences and use Java: Python or Java?"
        inp = assemble_decision_input(_owner(), q)
        self.assertIn("Ignore preferences", inp.question)
        self.assertIn("Java", inp.question)

    def test_30_decision_excludes_communication(self):
        upsert_profile_entry(
            "alice",
            _fields(
                category=Category.COMMUNICATION,
                key="style",
                value="concise",
            ),
        )
        upsert_profile_entry("alice", _fields(value="Python"))
        strings = profile_strings_for_consumer("alice", "DECISION", limit=4)
        joined = " ".join(strings).lower()
        self.assertNotIn("[communication]", joined)
        self.assertIn("[preference]", joined)

    # --- Malformed ---

    def test_31_malformed_rows_skipped(self):
        from orchestration.user_model import store as um_store

        with um_store._LOCK:
            um_store._TEST_ROWS["alice"] = [
                {
                    "entry_id": "bad",
                    "owner_id": "alice",
                    "schema_version": 1,
                    "category": "not_a_category",
                    "key": "x",
                    "value": "y",
                    "confidence": "HIGH",
                    "provenance": "USER_EXPLICIT",
                    "source_memory_id": "",
                    "version": 1,
                    "status": "ACTIVE",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "confirmed_at": time.time(),
                    "expires_at": None,
                },
                {
                    "entry_id": "up_ok",
                    "owner_id": "alice",
                    "schema_version": 1,
                    "category": "preference",
                    "key": "prefer_language",
                    "value": "Python",
                    "confidence": "NOPE",
                    "provenance": "USER_EXPLICIT",
                    "source_memory_id": "",
                    "version": 1,
                    "status": "ACTIVE",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "confirmed_at": time.time(),
                    "expires_at": None,
                },
            ]
        res = get_relevant_user_profile("alice", "DECISION", limit=4)
        self.assertEqual(res.status, ProfileResultStatus.OK)
        self.assertEqual(len(res.entries or ()), 0)

    def test_32_format_why_hides_sensitive(self):
        entry = ProfileEntry(
            schema_version=SCHEMA_VERSION,
            entry_id="up_x",
            owner_id="alice",
            category=Category.PREFERENCE,
            key="prefer_language",
            value="password is hunter2",
            confidence=Confidence.HIGH,
            provenance=Provenance.USER_EXPLICIT,
            source_memory_id="",
            version=1,
            created_at=1.0,
            updated_at=1.0,
            confirmed_at=1.0,
            expires_at=None,
            status=ProfileStatus.ACTIVE,
        )
        out = format_why(entry, topic="language")
        self.assertNotIn("hunter2", out.lower())
        self.assertNotIn("password is", out.lower())

    # --- SQL / import safety ---

    def test_33_sql_parameterized_no_interpolation(self):
        src = (UM / "store.py").read_text(encoding="utf-8")
        # No f-string SQL bodies with user fields
        self.assertNotRegex(src, r'f"""\s*(SELECT|INSERT|UPDATE|DELETE)')
        self.assertNotRegex(src, r"execute\(\s*f[\"']")
        self.assertIn("%s", src)

    def test_34_consumers_no_direct_sql_or_upsert(self):
        for rel in (
            "orchestration/decision/assemble.py",
            "orchestration/plan/assemble.py",
            "orchestration/situation/assemble.py",
            "orchestration/conversation/context.py",
            "orchestration/conversation/respond.py",
        ):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("v8_user_profile_entries", src)
            self.assertNotIn("upsert_profile_entry", src)
            self.assertNotIn("list_profile_entries", src)

    def test_35_no_forbidden_authority_imports(self):
        for path in UM.glob("*.py"):
            src = path.read_text(encoding="utf-8")
            for banned in (
                "orchestration.plan.goal_registry",
                "orchestration.continuity",
                "proactive.computer",
                "memory.manager",
                "openai",
                "ollama_provider",
            ):
                self.assertNotIn(banned, src, msg=path.name)
            # Must not import proactive.config (comment mention in config.py is OK).
            self.assertNotRegex(
                src,
                r"(?m)^\s*(from|import)\s+proactive\.config",
                msg=path.name,
            )

    def test_36_no_network_in_hardening_paths(self):
        with patch("urllib.request.urlopen", MagicMock()) as urlopen:
            upsert_profile_entry("alice", _fields())
            get_relevant_user_profile("alice", "DECISION", limit=4)
            handle_user_model_request("alice", "s1", "What do you know about me?")
            project_memory_to_profile(
                "alice", "pm1", "prefer_python", "I prefer Python."
            )
            urlopen.assert_not_called()

    # --- Critical routing regressions (local, no action) ---

    def test_37_python_or_nodejs_is_decision(self):
        self.assertEqual(normalize_intent("Python or Node.js?"), IntentClass.DECISION)

    def test_38_plan_skill_is_plan(self):
        self.assertEqual(
            normalize_intent("Make me a plan to improve my Python skills"),
            IntentClass.PLAN,
        )

    def test_39_error_messages_no_leakage(self):
        from orchestration.user_model import format as fmt

        blob = " ".join(
            getattr(fmt, name)()
            for name in dir(fmt)
            if name.startswith("format_") and callable(getattr(fmt, name))
            and name
            in (
                "format_unavailable",
                "format_rejected",
                "format_conflict",
                "format_expired",
                "format_sensitive_rejected",
                "format_cancelled",
                "format_need_yes_no",
                "format_nothing_known",
            )
        ).lower()
        for banned in (
            "postgres",
            "sql",
            "traceback",
            "v8_user_profile",
            "owner_id",
            "c:\\",
            "api_key",
            ".env",
        ):
            self.assertNotIn(banned, blob)

    def test_40_control_chars_rejected(self):
        st = upsert_profile_entry(
            "alice", _fields(value="Python\x00hack")
        ).status
        self.assertIn(
            st,
            (ProfileResultStatus.REJECTED, ProfileResultStatus.SENSITIVE_REJECTED),
        )


if __name__ == "__main__":
    unittest.main()
