"""V8.27 Phase 1 — User Model store foundation tests."""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

from orchestration.user_model.config import is_v827_user_model_enabled
from orchestration.user_model.policy import (
    default_confidence_for_category,
    default_expiry_for_category,
    sanitize_profile_key,
    sanitize_profile_value,
    validate_entry_fields,
)
from orchestration.user_model.store import (
    clear_profile,
    forget_profile_category,
    forget_profile_entry,
    get_profile_entry,
    list_profile_entries,
    reset_user_model_for_tests,
    upsert_profile_entry,
    use_test_user_model_store,
)
from orchestration.user_model.types import (
    MAX_ENTRIES_PER_CATEGORY,
    MAX_LIST_RESULTS,
    MAX_PROFILE_ENTRIES,
    MAX_PROFILE_KEY_LENGTH,
    MAX_PROFILE_VALUE_LENGTH,
    SCHEMA_VERSION,
    Category,
    Confidence,
    ProfileEntry,
    ProfileResultStatus,
    ProfileStatus,
    Provenance,
)


class TestV827UserModelStore(unittest.TestCase):
    def setUp(self):
        use_test_user_model_store(True)
        reset_user_model_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

    def tearDown(self):
        reset_user_model_for_tests()
        use_test_user_model_store(False)

    def _fields(self, **kwargs):
        base = {
            "category": Category.PREFERENCE,
            "key": "prefer_language",
            "value": "Python",
            "provenance": Provenance.USER_EXPLICIT,
        }
        base.update(kwargs)
        return base

    def test_01_profile_entry_frozen(self):
        e = ProfileEntry(
            schema_version=SCHEMA_VERSION,
            entry_id="up_abc",
            owner_id="alice",
            category=Category.PREFERENCE,
            key="prefer_language",
            value="Python",
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
        with self.assertRaises(Exception):
            e.value = "TypeScript"  # type: ignore[misc]

    def test_02_enums(self):
        self.assertEqual(len(Category), 6)
        self.assertEqual(set(c.value for c in Confidence), {"HIGH", "MEDIUM", "LOW"})
        self.assertEqual(
            set(p.value for p in Provenance),
            {"USER_EXPLICIT", "PROJECTED_MEMORY", "USER_CONFIRMED"},
        )

    def test_03_bounds_constants(self):
        self.assertEqual(MAX_PROFILE_ENTRIES, 32)
        self.assertEqual(MAX_ENTRIES_PER_CATEGORY, 8)
        self.assertEqual(MAX_PROFILE_VALUE_LENGTH, 160)
        self.assertEqual(MAX_PROFILE_KEY_LENGTH, 96)
        self.assertEqual(MAX_LIST_RESULTS, 12)

    def test_04_key_regex(self):
        self.assertEqual(sanitize_profile_key("Prefer-Language"), "prefer_language")
        fields, st = validate_entry_fields(
            owner_id="alice", category="preference", key="BAD KEY!!", value="ok"
        )
        # sanitize strips invalid → may become badkey or empty-ish
        self.assertIn(st, (ProfileResultStatus.OK, ProfileResultStatus.REJECTED))

    def test_05_value_sanitization(self):
        self.assertEqual(sanitize_profile_value("  hello\x00  world  "), "hello world")
        long_v = "x" * 200
        self.assertEqual(len(sanitize_profile_value(long_v)), 160)

    def test_06_create_read(self):
        res = upsert_profile_entry("alice", self._fields())
        self.assertEqual(res.status, ProfileResultStatus.OK)
        self.assertIsNotNone(res.entry)
        self.assertTrue(res.entry.entry_id.startswith("up_"))
        self.assertEqual(res.entry.version, 1)
        self.assertEqual(res.entry.confidence, Confidence.HIGH)
        got = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        self.assertEqual(got.status, ProfileResultStatus.OK)
        self.assertEqual(got.entry.value, "Python")

    def test_07_update_version_increment(self):
        first = upsert_profile_entry("alice", self._fields())
        second = upsert_profile_entry(
            "alice",
            self._fields(value="TypeScript"),
            expected_version=first.entry.version,
        )
        self.assertEqual(second.status, ProfileResultStatus.OK)
        self.assertEqual(second.entry.version, 2)
        self.assertEqual(second.entry.value, "TypeScript")

    def test_08_version_conflict(self):
        first = upsert_profile_entry("alice", self._fields())
        bad = upsert_profile_entry(
            "alice",
            self._fields(value="Rust"),
            expected_version=first.entry.version + 9,
        )
        self.assertEqual(bad.status, ProfileResultStatus.CONFLICT)
        got = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        self.assertEqual(got.entry.value, "Python")

    def test_09_duplicate_active_same_key_updates(self):
        upsert_profile_entry("alice", self._fields())
        upsert_profile_entry("alice", self._fields(value="Go"))
        listed = list_profile_entries("alice", categories=(Category.PREFERENCE,))
        self.assertEqual(len(listed.entries), 1)
        self.assertEqual(listed.entries[0].value, "Go")

    def test_10_supersede_forget(self):
        created = upsert_profile_entry("alice", self._fields())
        forgot = forget_profile_entry(
            "alice", Category.PREFERENCE, "prefer_language", created.entry.version
        )
        self.assertEqual(forgot.status, ProfileResultStatus.OK)
        self.assertEqual(forgot.entry.status, ProfileStatus.SUPERSEDED)
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )

    def test_11_forget_version_conflict(self):
        created = upsert_profile_entry("alice", self._fields())
        bad = forget_profile_entry(
            "alice", Category.PREFERENCE, "prefer_language", created.entry.version + 1
        )
        self.assertEqual(bad.status, ProfileResultStatus.CONFLICT)

    def test_12_expiry_excluded_from_normal_read(self):
        res = upsert_profile_entry(
            "alice",
            self._fields(
                category=Category.TEMPORARY_FACT,
                key="exam_prep",
                value="Preparing for exam",
                expires_at=time.time() - 10,
            ),
        )
        self.assertEqual(res.status, ProfileResultStatus.OK)
        self.assertEqual(
            get_profile_entry("alice", Category.TEMPORARY_FACT, "exam_prep").status,
            ProfileResultStatus.NOT_FOUND,
        )
        listed = list_profile_entries("alice", include_expired=True)
        self.assertEqual(listed.status, ProfileResultStatus.OK)
        self.assertTrue(any(e.key == "exam_prep" for e in listed.entries))

    def test_13_category_defaults(self):
        self.assertEqual(
            default_confidence_for_category(Category.TEMPORARY_FACT), Confidence.MEDIUM
        )
        self.assertEqual(
            default_confidence_for_category(Category.PREFERENCE), Confidence.HIGH
        )
        now = time.time()
        self.assertIsNone(default_expiry_for_category(Category.PREFERENCE, now))
        self.assertGreater(default_expiry_for_category(Category.PROJECT, now), now)
        self.assertGreater(
            default_expiry_for_category(Category.TEMPORARY_FACT, now), now
        )

    def test_14_project_gets_default_expiry(self):
        res = upsert_profile_entry(
            "alice",
            {
                "category": Category.PROJECT,
                "key": "current_project",
                "value": "DOOM",
            },
        )
        self.assertEqual(res.status, ProfileResultStatus.OK)
        self.assertIsNotNone(res.entry.expires_at)
        self.assertGreater(res.entry.expires_at, time.time())

    def test_15_category_limit(self):
        for i in range(MAX_ENTRIES_PER_CATEGORY):
            r = upsert_profile_entry(
                "alice",
                self._fields(key=f"prefer_item_{i}", value=f"v{i}"),
            )
            self.assertEqual(r.status, ProfileResultStatus.OK, i)
        overflow = upsert_profile_entry(
            "alice",
            self._fields(key="prefer_item_overflow", value="nope"),
        )
        self.assertEqual(overflow.status, ProfileResultStatus.REJECTED)

    def test_16_owner_limit(self):
        # Fill with mixed categories to hit owner cap (4 categories * 8 = 32)
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
                    {
                        "category": cat,
                        "key": f"k_{cat.value}_{i}",
                        "value": f"v{i}",
                    },
                )
                self.assertEqual(r.status, ProfileResultStatus.OK)
                n += 1
        self.assertEqual(n, MAX_PROFILE_ENTRIES)
        overflow = upsert_profile_entry(
            "alice",
            {
                "category": Category.STABLE_FACT,
                "key": "role",
                "value": "developer",
            },
        )
        self.assertEqual(overflow.status, ProfileResultStatus.REJECTED)

    def test_17_list_limit_capped(self):
        for i in range(5):
            upsert_profile_entry(
                "alice",
                self._fields(key=f"prefer_x_{i}", value=f"v{i}"),
            )
        listed = list_profile_entries("alice", limit=100)
        self.assertLessEqual(len(listed.entries), MAX_LIST_RESULTS)

    def test_18_forget_category(self):
        upsert_profile_entry("alice", self._fields(key="prefer_a", value="A"))
        upsert_profile_entry("alice", self._fields(key="prefer_b", value="B"))
        upsert_profile_entry(
            "alice",
            {"category": Category.PROJECT, "key": "current_project", "value": "DOOM"},
        )
        res = forget_profile_category("alice", Category.PREFERENCE)
        self.assertEqual(res.status, ProfileResultStatus.OK)
        self.assertEqual(
            list_profile_entries("alice", categories=(Category.PREFERENCE,)).entries,
            (),
        )
        self.assertEqual(
            get_profile_entry("alice", Category.PROJECT, "current_project").status,
            ProfileResultStatus.OK,
        )

    def test_19_clear_profile(self):
        upsert_profile_entry("alice", self._fields())
        upsert_profile_entry(
            "alice",
            {"category": Category.PROJECT, "key": "current_project", "value": "DOOM"},
        )
        res = clear_profile("alice")
        self.assertEqual(res.status, ProfileResultStatus.OK)
        self.assertEqual(list_profile_entries("alice").entries, ())

    def test_20_invalid_category_rejected(self):
        res = upsert_profile_entry(
            "alice",
            {"category": "personality", "key": "trait", "value": "curious"},
        )
        self.assertEqual(res.status, ProfileResultStatus.REJECTED)

    def test_21_unsupported_schema_rejected(self):
        fields, st = validate_entry_fields(
            owner_id="alice",
            category="preference",
            key="prefer_language",
            value="Python",
            schema_version=99,
        )
        self.assertEqual(st, ProfileResultStatus.REJECTED)
        self.assertIsNone(fields)

    def test_22_flag_off_unavailable(self):
        upsert_profile_entry("alice", self._fields())
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "false"
        self.assertFalse(is_v827_user_model_enabled())
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.UNAVAILABLE,
        )
        self.assertEqual(
            upsert_profile_entry("alice", self._fields(value="X")).status,
            ProfileResultStatus.UNAVAILABLE,
        )
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

    def test_23_flag_requires_v8(self):
        os.environ["PROACTIVE_V8_ENABLED"] = "false"
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"
        self.assertFalse(is_v827_user_model_enabled())
        os.environ["PROACTIVE_V8_ENABLED"] = "true"

    def test_24_flag_on(self):
        self.assertTrue(is_v827_user_model_enabled())

    def test_25_import_safety(self):
        import subprocess
        import sys

        script = (
            "import orchestration.user_model as um\n"
            "import orchestration.user_model.types\n"
            "import orchestration.user_model.policy\n"
            "import orchestration.user_model.store\n"
            "import orchestration.user_model.config\n"
            "print('OK')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if False
            else None,
            timeout=30,
        )
        # Run from repo root
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_26_empty_value_rejected(self):
        res = upsert_profile_entry(
            "alice", self._fields(value="   ")
        )
        self.assertEqual(res.status, ProfileResultStatus.REJECTED)

    def test_27_malformed_owner_rejected(self):
        res = upsert_profile_entry("", self._fields())
        self.assertEqual(res.status, ProfileResultStatus.REJECTED)

    def test_28_scoped_preference_keys_independent(self):
        upsert_profile_entry(
            "alice",
            self._fields(key="prefer_language_backend", value="Python"),
        )
        upsert_profile_entry(
            "alice",
            self._fields(key="prefer_language_frontend", value="TypeScript"),
        )
        listed = list_profile_entries("alice", categories=(Category.PREFERENCE,))
        self.assertEqual(len(listed.entries), 2)


if __name__ == "__main__":
    unittest.main()
