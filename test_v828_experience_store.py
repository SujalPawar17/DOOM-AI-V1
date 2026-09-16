"""V8.28 Phase 1 — Goal Experience / Outcome Memory foundation tests."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

from orchestration.experience.config import is_v828_goal_experience_enabled
from orchestration.experience.policy import (
    is_sensitive_experience_content,
    is_valid_transition,
    sanitize_title,
    validate_experience_fields,
)
from orchestration.experience.store import (
    create_experience,
    forget_experience,
    get_experience,
    list_experiences,
    reset_experience_for_tests,
    transition_experience,
    update_experience,
    use_test_experience_store,
)
from orchestration.experience.types import (
    MAX_CONTENT_CHARS,
    MAX_EXPERIENCES_PER_OWNER,
    MAX_LIST_RESULTS,
    MAX_STEPS,
    MAX_TITLE_CHARS,
    SCHEMA_VERSION,
    ExperienceResultStatus,
    ExperienceStatus,
    GoalExperience,
    Outcome,
)


class TestV828ExperienceStore(unittest.TestCase):
    def setUp(self):
        use_test_experience_store(True)
        reset_experience_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

    def tearDown(self):
        reset_experience_for_tests()
        use_test_experience_store(False)

    def _fields(self, **kwargs):
        base = {
            "title": "Improve Python skills",
            "outcome": Outcome.COMPLETED,
            "step_summary": ("Clarify outcome", "Practice daily"),
            "blockers": (),
            "user_note": "Finished the checklist.",
            "tags": ("python", "learning"),
            "source_goal_id": "",
        }
        base.update(kwargs)
        return base

    def test_01_type_construction(self):
        e = GoalExperience(
            schema_version=SCHEMA_VERSION,
            experience_id="ge_abc",
            owner_id="alice",
            source_goal_id="ag_1",
            title="Improve Python skills",
            outcome=Outcome.COMPLETED,
            step_summary=("a", "b"),
            blockers=(),
            user_note="done",
            tags=("python",),
            status=ExperienceStatus.ACTIVE_RECORD,
            version=1,
            created_at=1.0,
            updated_at=1.0,
            expires_at=None,
        )
        self.assertEqual(e.outcome, Outcome.COMPLETED)
        with self.assertRaises(Exception):
            e.title = "x"  # type: ignore[misc]

    def test_02_valid_outcomes(self):
        for oc in Outcome:
            res = create_experience("alice", self._fields(outcome=oc, source_goal_id=f"ag_{oc.value.lower()}"))
            self.assertEqual(res.status, ExperienceResultStatus.OK, msg=oc.value)
            self.assertEqual(res.experience.outcome, oc)

    def test_03_valid_statuses_and_transitions(self):
        self.assertTrue(
            is_valid_transition(
                ExperienceStatus.ACTIVE_RECORD, ExperienceStatus.SUPERSEDED
            )
        )
        self.assertTrue(
            is_valid_transition(
                ExperienceStatus.ACTIVE_RECORD, ExperienceStatus.FORGOTTEN
            )
        )
        self.assertFalse(
            is_valid_transition(
                ExperienceStatus.FORGOTTEN, ExperienceStatus.ACTIVE_RECORD
            )
        )
        self.assertFalse(
            is_valid_transition(
                ExperienceStatus.SUPERSEDED, ExperienceStatus.ACTIVE_RECORD
            )
        )

    def test_04_invalid_outcome(self):
        res = create_experience("alice", self._fields(outcome="WON"))
        self.assertEqual(res.status, ExperienceResultStatus.REJECTED)

    def test_05_invalid_status_transition(self):
        created = create_experience("alice", self._fields())
        bad = transition_experience(
            "alice",
            created.experience.experience_id,
            ExperienceStatus.ACTIVE_RECORD,
            expected_version=created.experience.version,
        )
        self.assertEqual(bad.status, ExperienceResultStatus.REJECTED)

    def test_06_bounds_title_and_steps(self):
        long_title = "word " * 80
        res = create_experience("alice", self._fields(title=long_title))
        self.assertEqual(res.status, ExperienceResultStatus.OK)
        self.assertLessEqual(len(res.experience.title), MAX_TITLE_CHARS)
        steps = [f"step {i} detail" for i in range(20)]
        res2 = create_experience(
            "alice",
            self._fields(title="Other", step_summary=steps, source_goal_id="ag_steps"),
        )
        self.assertEqual(res2.status, ExperienceResultStatus.OK)
        self.assertLessEqual(len(res2.experience.step_summary), MAX_STEPS)

    def test_07_empty_title_rejected(self):
        self.assertEqual(
            create_experience("alice", self._fields(title="   ")).status,
            ExperienceResultStatus.REJECTED,
        )

    def test_08_owner_isolation(self):
        a = create_experience("alice", self._fields(source_goal_id="ag_a1"))
        self.assertEqual(a.status, ExperienceResultStatus.OK)
        self.assertEqual(
            get_experience("bob", a.experience.experience_id).status,
            ExperienceResultStatus.NOT_FOUND,
        )
        bob_list = list_experiences("bob")
        self.assertEqual(len(bob_list.experiences or ()), 0)

    def test_09_sensitive_rejection(self):
        for bad in (
            "password is hunter2",
            "API key sk-abcdefghijklmnop",
            "my home address is 123 Main St",
            "SELECT * FROM users",
        ):
            res = create_experience("alice", self._fields(title=bad))
            self.assertEqual(
                res.status,
                ExperienceResultStatus.SENSITIVE_REJECTED,
                msg=bad,
            )
            self.assertTrue(is_sensitive_experience_content(bad))

    def test_10_crud_create_get_list(self):
        created = create_experience("alice", self._fields(source_goal_id="ag_crud"))
        self.assertEqual(created.status, ExperienceResultStatus.OK)
        self.assertTrue(created.experience.experience_id.startswith("ge_"))
        got = get_experience("alice", created.experience.experience_id)
        self.assertEqual(got.status, ExperienceResultStatus.OK)
        self.assertEqual(got.experience.title, "Improve Python skills")
        listed = list_experiences("alice")
        self.assertEqual(listed.status, ExperienceResultStatus.OK)
        self.assertEqual(len(listed.experiences), 1)

    def test_11_update_and_version_conflict(self):
        created = create_experience("alice", self._fields())
        v = created.experience.version
        ok = update_experience(
            "alice",
            created.experience.experience_id,
            {"user_note": "Updated note"},
            expected_version=v,
        )
        self.assertEqual(ok.status, ExperienceResultStatus.OK)
        self.assertEqual(ok.experience.version, v + 1)
        stale = update_experience(
            "alice",
            created.experience.experience_id,
            {"user_note": "Stale"},
            expected_version=v,
        )
        self.assertEqual(stale.status, ExperienceResultStatus.CONFLICT)
        cur = get_experience("alice", created.experience.experience_id)
        self.assertEqual(cur.experience.user_note, "Updated note")

    def test_12_forget_and_supersede(self):
        a = create_experience("alice", self._fields(title="A", source_goal_id="ag_a"))
        b = create_experience("alice", self._fields(title="B", source_goal_id="ag_b"))
        forgot = forget_experience(
            "alice", a.experience.experience_id, expected_version=a.experience.version
        )
        self.assertEqual(forgot.status, ExperienceResultStatus.OK)
        self.assertEqual(forgot.experience.status, ExperienceStatus.FORGOTTEN)
        self.assertEqual(
            get_experience("alice", a.experience.experience_id).status,
            ExperienceResultStatus.NOT_FOUND,
        )
        superceded = transition_experience(
            "alice",
            b.experience.experience_id,
            ExperienceStatus.SUPERSEDED,
            expected_version=b.experience.version,
        )
        self.assertEqual(superceded.status, ExperienceResultStatus.OK)
        self.assertEqual(
            get_experience("alice", b.experience.experience_id).status,
            ExperienceResultStatus.NOT_FOUND,
        )

    def test_13_list_bounds(self):
        for i in range(MAX_LIST_RESULTS + 5):
            create_experience(
                "alice",
                self._fields(title=f"Goal {i}", source_goal_id=f"ag_list_{i}"),
            )
        listed = list_experiences("alice", limit=99)
        self.assertLessEqual(len(listed.experiences), MAX_LIST_RESULTS)

    def test_14_retention_cap(self):
        for i in range(MAX_EXPERIENCES_PER_OWNER + 3):
            res = create_experience(
                "alice",
                self._fields(title=f"Cap {i}", source_goal_id=f"ag_cap_{i}"),
            )
            self.assertEqual(res.status, ExperienceResultStatus.OK, msg=f"i={i}")
        listed = list_experiences("alice", limit=MAX_LIST_RESULTS)
        # Active list is bounded; total ACTIVE_RECORD <= 64 after retention.
        from orchestration.experience import store as st

        with st._LOCK:
            active = [
                r
                for r in st._TEST_ROWS.get("alice", [])
                if r.get("status") == ExperienceStatus.ACTIVE_RECORD.value
            ]
        self.assertLessEqual(len(active), MAX_EXPERIENCES_PER_OWNER)

    def test_15_idempotent_same_source_goal(self):
        r1 = create_experience(
            "alice",
            self._fields(title="First", source_goal_id="ag_same", user_note="one"),
        )
        r2 = create_experience(
            "alice",
            self._fields(title="Second", source_goal_id="ag_same", user_note="two"),
        )
        self.assertEqual(r1.status, ExperienceResultStatus.OK)
        self.assertEqual(r2.status, ExperienceResultStatus.OK)
        self.assertEqual(r1.experience.experience_id, r2.experience.experience_id)
        self.assertEqual(r2.experience.title, "First")
        listed = list_experiences("alice")
        self.assertEqual(len(listed.experiences), 1)

    def test_16_separate_experiences_without_source(self):
        a = create_experience("alice", self._fields(title="One", source_goal_id=""))
        b = create_experience("alice", self._fields(title="Two", source_goal_id=""))
        self.assertNotEqual(a.experience.experience_id, b.experience.experience_id)
        self.assertEqual(len(list_experiences("alice").experiences), 2)

    def test_17_expiry_excluded(self):
        res = create_experience(
            "alice",
            self._fields(expires_at=time.time() - 10, source_goal_id="ag_exp"),
        )
        self.assertEqual(res.status, ExperienceResultStatus.OK)
        self.assertEqual(
            get_experience("alice", res.experience.experience_id).status,
            ExperienceResultStatus.NOT_FOUND,
        )
        listed = list_experiences("alice")
        self.assertEqual(len(listed.experiences), 0)

    def test_18_flag_off(self):
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "false"
        try:
            self.assertFalse(is_v828_goal_experience_enabled())
            self.assertEqual(
                create_experience("alice", self._fields()).status,
                ExperienceResultStatus.UNAVAILABLE,
            )
        finally:
            os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

    def test_19_v8_master_required(self):
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
        os.environ["PROACTIVE_V8_ENABLED"] = "false"
        try:
            self.assertFalse(is_v828_goal_experience_enabled())
            self.assertEqual(
                create_experience("alice", self._fields()).status,
                ExperienceResultStatus.UNAVAILABLE,
            )
        finally:
            os.environ["PROACTIVE_V8_ENABLED"] = "true"

    def test_20_flag_on(self):
        self.assertTrue(is_v828_goal_experience_enabled())
        self.assertEqual(
            create_experience("alice", self._fields()).status,
            ExperienceResultStatus.OK,
        )

    def test_21_db_unavailable_isolation(self):
        use_test_experience_store(False)
        with patch(
            "orchestration.experience.store.ensure_goal_experience_schema",
            return_value=False,
        ):
            res = create_experience("alice", self._fields())
        self.assertEqual(res.status, ExperienceResultStatus.UNAVAILABLE)

    def test_22_malformed_owner(self):
        self.assertEqual(
            create_experience("", self._fields()).status,
            ExperienceResultStatus.REJECTED,
        )
        self.assertEqual(
            create_experience("alice\x00", self._fields()).status,
            ExperienceResultStatus.REJECTED,
        )

    def test_23_invalid_source_goal_id_stripped(self):
        res = create_experience(
            "alice", self._fields(source_goal_id="not_a_registry_id")
        )
        self.assertEqual(res.status, ExperienceResultStatus.OK)
        self.assertEqual(res.experience.source_goal_id, "")

    def test_24_validate_fields_helper(self):
        ok, st = validate_experience_fields(
            owner_id="alice",
            title="Ok",
            outcome=Outcome.PARTIAL,
        )
        self.assertEqual(st, ExperienceResultStatus.OK)
        self.assertIsNotNone(ok)
        bad, st2 = validate_experience_fields(
            owner_id="alice",
            title="password is x",
            outcome=Outcome.COMPLETED,
        )
        self.assertEqual(st2, ExperienceResultStatus.SENSITIVE_REJECTED)

    def test_25_sql_parameterized_store(self):
        src = open("orchestration/experience/store.py", encoding="utf-8").read()
        self.assertNotRegex(src, r"execute\(\s*f[\"']")
        self.assertIn("%s", src)
        self.assertIn("v8_goal_experiences", src)

    def test_26_no_forbidden_imports(self):
        for path in (
            "orchestration/experience/config.py",
            "orchestration/experience/policy.py",
            "orchestration/experience/store.py",
            "orchestration/experience/types.py",
        ):
            text = open(path, encoding="utf-8").read()
            self.assertNotRegex(
                text,
                r"(?m)^\s*(from|import)\s+proactive\.config",
            )
            self.assertNotIn("orchestration.plan.goal_registry", text)
            self.assertNotIn("openai", text)

    def test_27_content_budget(self):
        note = "x" * 200
        steps = ["s" * 80] * 8
        blockers = ["b" * 120] * 4
        res = create_experience(
            "alice",
            self._fields(
                title="T" * 160,
                user_note=note,
                step_summary=steps,
                blockers=blockers,
            ),
        )
        self.assertEqual(res.status, ExperienceResultStatus.OK)
        e = res.experience
        budget = (
            len(e.title)
            + len(e.user_note)
            + sum(len(s) for s in e.step_summary)
            + sum(len(b) for b in e.blockers)
        )
        self.assertLessEqual(budget, MAX_CONTENT_CHARS)

    def test_28_sanitize_word_boundary(self):
        text = "alpha beta gamma delta " * 20
        clipped = sanitize_title(text)
        self.assertLessEqual(len(clipped), MAX_TITLE_CHARS)
        self.assertFalse(clipped.endswith(" "))

    def test_29_import_safety(self):
        import importlib

        mod = importlib.import_module("orchestration.experience")
        self.assertTrue(hasattr(mod, "create_experience"))
        # Package import must not require Postgres.
        importlib.import_module("orchestration.experience.config")

    def test_30_cross_owner_forget_safe(self):
        created = create_experience("alice", self._fields())
        bad = forget_experience(
            "bob",
            created.experience.experience_id,
            expected_version=created.experience.version,
        )
        self.assertEqual(bad.status, ExperienceResultStatus.NOT_FOUND)
        still = get_experience("alice", created.experience.experience_id)
        self.assertEqual(still.status, ExperienceResultStatus.OK)


if __name__ == "__main__":
    unittest.main()
