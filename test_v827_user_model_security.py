"""V8.27 Phase 1 — User Model security tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

from orchestration.user_model.policy import is_sensitive_profile_content, validate_entry_fields
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
    Category,
    ProfileResultStatus,
    Provenance,
)


class TestV827UserModelSecurity(unittest.TestCase):
    def setUp(self):
        use_test_user_model_store(True)
        reset_user_model_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

    def tearDown(self):
        reset_user_model_for_tests()
        use_test_user_model_store(False)

    def test_01_owner_isolation_read(self):
        upsert_profile_entry(
            "alice",
            {
                "category": Category.PREFERENCE,
                "key": "prefer_language",
                "value": "Python",
                "provenance": Provenance.USER_EXPLICIT,
            },
        )
        self.assertEqual(
            get_profile_entry("bob", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )
        self.assertEqual(list_profile_entries("bob").entries, ())

    def test_02_owner_isolation_update(self):
        created = upsert_profile_entry(
            "alice",
            {
                "category": Category.PREFERENCE,
                "key": "prefer_language",
                "value": "Python",
            },
        )
        # Bob cannot update Alice's key via his owner scope (creates bob's own or rejects)
        bob = upsert_profile_entry(
            "bob",
            {
                "category": Category.PREFERENCE,
                "key": "prefer_language",
                "value": "Hacked",
            },
            expected_version=created.entry.version,
        )
        # expected_version with no bob entry → CONFLICT
        self.assertEqual(bob.status, ProfileResultStatus.CONFLICT)
        alice = get_profile_entry("alice", Category.PREFERENCE, "prefer_language")
        self.assertEqual(alice.entry.value, "Python")

    def test_03_owner_isolation_forget(self):
        created = upsert_profile_entry(
            "alice",
            {
                "category": Category.PREFERENCE,
                "key": "prefer_language",
                "value": "Python",
            },
        )
        bad = forget_profile_entry(
            "bob", Category.PREFERENCE, "prefer_language", created.entry.version
        )
        self.assertEqual(bad.status, ProfileResultStatus.NOT_FOUND)
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.OK,
        )

    def test_04_owner_isolation_clear_category(self):
        upsert_profile_entry(
            "alice",
            {
                "category": Category.PREFERENCE,
                "key": "prefer_language",
                "value": "Python",
            },
        )
        clear_profile("bob")
        forget_profile_category("bob", Category.PREFERENCE)
        self.assertEqual(
            get_profile_entry("alice", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.OK,
        )

    def test_05_password_rejected(self):
        res = upsert_profile_entry(
            "alice",
            {
                "category": Category.STABLE_FACT,
                "key": "password_note",
                "value": "Remember my password is abc123",
            },
        )
        self.assertEqual(res.status, ProfileResultStatus.SENSITIVE_REJECTED)

    def test_06_api_key_rejected(self):
        self.assertTrue(is_sensitive_profile_content("api_key=sk-abcdef1234567890"))
        res = upsert_profile_entry(
            "alice",
            {
                "category": Category.PREFERENCE,
                "key": "prefer_tool",
                "value": "api_key=sk-abcdef1234567890",
            },
        )
        self.assertEqual(res.status, ProfileResultStatus.SENSITIVE_REJECTED)

    def test_07_sql_injection_rejected(self):
        res = upsert_profile_entry(
            "alice",
            {
                "category": Category.PREFERENCE,
                "key": "prefer_query",
                "value": "SELECT * FROM users WHERE 1=1",
            },
        )
        self.assertEqual(res.status, ProfileResultStatus.SENSITIVE_REJECTED)

    def test_08_malformed_owner(self):
        for owner in ("", "   ", "\x00alice"):
            res = upsert_profile_entry(
                owner,
                {
                    "category": Category.PREFERENCE,
                    "key": "prefer_language",
                    "value": "Python",
                },
            )
            self.assertEqual(res.status, ProfileResultStatus.REJECTED, owner)

    def test_09_oversized_value(self):
        fields, st = validate_entry_fields(
            owner_id="alice",
            category="preference",
            key="prefer_language",
            value="x" * 500,
        )
        self.assertEqual(st, ProfileResultStatus.OK)
        self.assertEqual(len(fields["value"]), 160)

    def test_10_invalid_enums(self):
        for conf, prov, status in (
            ("ULTRA", "USER_EXPLICIT", "ACTIVE"),
            ("HIGH", "SURVEILLANCE", "ACTIVE"),
            ("HIGH", "USER_EXPLICIT", "HIDDEN"),
        ):
            fields, st = validate_entry_fields(
                owner_id="alice",
                category="preference",
                key="prefer_language",
                value="Python",
                confidence=conf,
                provenance=prov,
                status=status,
            )
            self.assertEqual(st, ProfileResultStatus.REJECTED)

    def test_11_health_trait_rejected(self):
        res = upsert_profile_entry(
            "alice",
            {
                "category": Category.STABLE_FACT,
                "key": "health_note",
                "value": "I have a medical diagnosis of X",
            },
        )
        self.assertEqual(res.status, ProfileResultStatus.SENSITIVE_REJECTED)

    def test_12_owner_mismatch_in_fields(self):
        res = upsert_profile_entry(
            "alice",
            {
                "owner_id": "eve",
                "category": Category.PREFERENCE,
                "key": "prefer_language",
                "value": "Python",
            },
        )
        self.assertEqual(res.status, ProfileResultStatus.REJECTED)
        self.assertEqual(
            get_profile_entry("eve", Category.PREFERENCE, "prefer_language").status,
            ProfileResultStatus.NOT_FOUND,
        )

    def test_13_cookie_csrf_rejected(self):
        for value in ("session cookie=abc", "csrf_token=deadbeef", "Authorization: Bearer x"):
            res = upsert_profile_entry(
                "alice",
                {
                    "category": Category.PREFERENCE,
                    "key": "prefer_x",
                    "value": value,
                },
            )
            self.assertEqual(res.status, ProfileResultStatus.SENSITIVE_REJECTED, value)

    def test_14_no_partial_sensitive_store(self):
        before = list_profile_entries("alice")
        upsert_profile_entry(
            "alice",
            {
                "category": Category.PREFERENCE,
                "key": "prefer_language",
                "value": "password is secret123",
            },
        )
        after = list_profile_entries("alice")
        self.assertEqual(before.entries, after.entries)


if __name__ == "__main__":
    unittest.main()
