"""V8.26 Phase 1 tests: Active Goal Registry persistence foundation."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

# Flag off by default; tests enable explicitly.
os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"

from orchestration.plan.goal_registry import (
    StepState,
    ARCHIVE_RETENTION,
    MAX_BLOCKER_CHARS,
    MAX_DURABLE_VIEW_CHARS,
    MAX_PLAN_TITLE_CHARS,
    MAX_STALENESS_CHARS,
    MAX_STEP_TITLE_CHARS,
    MAX_STEPS,
    MAX_TITLE_CHARS,
    SCHEMA_VERSION,
    GoalLifecycle,
    GoalSnapshot,
    RegistryStatus,
    abandon_goal,
    archive_goal,
    build_snapshot,
    create_active_goal,
    get_active_goal,
    is_sensitive_goal_content,
    mark_stale,
    new_registry_goal_id,
    recover_goal,
    reset_goal_registry_for_tests,
    transition_goal,
    update_active_goal,
    use_test_goal_registry_store,
    validate_snapshot,
)
from proactive.config import is_v826_goal_registry_enabled, is_v8_enabled


def _steps(n=3):
    titles = tuple(f"Step {i}" for i in range(1, n + 1))
    states = tuple(StepState.PENDING for _ in range(n))
    return titles, states


def _snap(owner="alice", **kwargs):
    titles, states = _steps(kwargs.pop("n", 3))
    defaults = dict(
        owner_id=owner,
        title="Improve Python",
        plan_title="Improve Python",
        step_titles=titles,
        step_states=states,
        active_step_index=1,
    )
    defaults.update(kwargs)
    snap, status = build_snapshot(**defaults)
    assert status is RegistryStatus.OK and snap is not None, status
    return snap


class TestV826GoalRegistry(unittest.TestCase):
    def setUp(self):
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V826_GOAL_REGISTRY_ENABLED"] = "true"
        use_test_goal_registry_store(True)
        reset_goal_registry_for_tests()

    def tearDown(self):
        reset_goal_registry_for_tests()
        use_test_goal_registry_store(False)

    # --- Feature flag ---

    def test_01_feature_flag_defaults_false_when_env_unset(self):
        with patch.dict(os.environ, {"PROACTIVE_V8_ENABLED": "true"}, clear=False):
            os.environ.pop("PROACTIVE_V826_GOAL_REGISTRY_ENABLED", None)
            self.assertFalse(is_v826_goal_registry_enabled())

    def test_02_feature_flag_requires_v8_enabled(self):
        with patch.dict(
            os.environ,
            {
                "PROACTIVE_V8_ENABLED": "false",
                "PROACTIVE_V826_GOAL_REGISTRY_ENABLED": "true",
            },
            clear=False,
        ):
            self.assertFalse(is_v8_enabled())
            self.assertFalse(is_v826_goal_registry_enabled())

    def test_02b_disabled_returns_unavailable(self):
        with patch(
            "orchestration.plan.goal_registry._is_v826_goal_registry_enabled",
            return_value=False,
        ):
            res = create_active_goal("alice", _snap())
            self.assertEqual(res.status, RegistryStatus.UNAVAILABLE)

    # --- Snapshot construction / bounds ---

    def test_03_goal_snapshot_valid_construction(self):
        snap = _snap()
        self.assertEqual(snap.schema_version, SCHEMA_VERSION)
        self.assertEqual(snap.lifecycle, GoalLifecycle.ACTIVE)
        self.assertEqual(len(snap.step_titles), 3)

    def test_04_bounds_validation_title(self):
        snap, status = build_snapshot(
            owner_id="alice",
            title="T" * (MAX_TITLE_CHARS + 1),
            plan_title="Plan",
            step_titles=("A",),
            step_states=(StepState.PENDING,),
        )
        # build_snapshot clips then validates — over-limit input is clipped to valid
        # Explicit over-limit after clip is prevented; use validate on raw oversized:
        raw = GoalSnapshot(
            schema_version=SCHEMA_VERSION,
            goal_id="rg_x",
            owner_id="alice",
            title="T" * (MAX_TITLE_CHARS + 1),
            lifecycle=GoalLifecycle.ACTIVE,
            plan_title="Plan",
            step_titles=("A",),
            step_states=(StepState.PENDING,),
            active_step_index=1,
            blocker_summary="",
            staleness_reason="",
            durable_view="",
            version=1,
            created_at=1.0,
            updated_at=1.0,
            last_active_at=1.0,
        )
        self.assertEqual(validate_snapshot(raw), RegistryStatus.REJECTED)

    def test_05_step_count_max_six(self):
        titles = tuple(f"S{i}" for i in range(1, MAX_STEPS + 2))
        states = tuple(StepState.PENDING for _ in titles)
        snap, status = build_snapshot(
            owner_id="alice",
            title="T",
            plan_title="P",
            step_titles=titles,
            step_states=states,
        )
        self.assertIsNone(snap)
        self.assertEqual(status, RegistryStatus.REJECTED)

    def test_06_step_title_max_80(self):
        raw = GoalSnapshot(
            schema_version=SCHEMA_VERSION,
            goal_id="rg_x",
            owner_id="alice",
            title="T",
            lifecycle=GoalLifecycle.ACTIVE,
            plan_title="P",
            step_titles=("X" * (MAX_STEP_TITLE_CHARS + 1),),
            step_states=(StepState.PENDING,),
            active_step_index=1,
            blocker_summary="",
            staleness_reason="",
            durable_view="",
            version=1,
            created_at=1.0,
            updated_at=1.0,
            last_active_at=1.0,
        )
        self.assertEqual(validate_snapshot(raw), RegistryStatus.REJECTED)

    def test_07_title_max_80(self):
        self.assertEqual(MAX_TITLE_CHARS, 80)
        self.assertEqual(MAX_PLAN_TITLE_CHARS, 80)

    def test_08_blocker_max_120(self):
        raw = _snap()
        bad = GoalSnapshot(
            schema_version=raw.schema_version,
            goal_id=raw.goal_id,
            owner_id=raw.owner_id,
            title=raw.title,
            lifecycle=raw.lifecycle,
            plan_title=raw.plan_title,
            step_titles=raw.step_titles,
            step_states=raw.step_states,
            active_step_index=raw.active_step_index,
            blocker_summary="B" * (MAX_BLOCKER_CHARS + 1),
            staleness_reason="",
            durable_view="",
            version=1,
            created_at=1.0,
            updated_at=1.0,
            last_active_at=1.0,
        )
        self.assertEqual(validate_snapshot(bad), RegistryStatus.REJECTED)

    def test_09_staleness_max_120(self):
        raw = _snap()
        bad = GoalSnapshot(
            schema_version=raw.schema_version,
            goal_id=raw.goal_id,
            owner_id=raw.owner_id,
            title=raw.title,
            lifecycle=raw.lifecycle,
            plan_title=raw.plan_title,
            step_titles=raw.step_titles,
            step_states=raw.step_states,
            active_step_index=raw.active_step_index,
            blocker_summary="",
            staleness_reason="S" * (MAX_STALENESS_CHARS + 1),
            durable_view="",
            version=1,
            created_at=1.0,
            updated_at=1.0,
            last_active_at=1.0,
        )
        self.assertEqual(validate_snapshot(bad), RegistryStatus.REJECTED)

    def test_10_durable_view_max_300(self):
        raw = _snap()
        bad = GoalSnapshot(
            schema_version=raw.schema_version,
            goal_id=raw.goal_id,
            owner_id=raw.owner_id,
            title=raw.title,
            lifecycle=raw.lifecycle,
            plan_title=raw.plan_title,
            step_titles=raw.step_titles,
            step_states=raw.step_states,
            active_step_index=raw.active_step_index,
            blocker_summary="",
            staleness_reason="",
            durable_view="D" * (MAX_DURABLE_VIEW_CHARS + 1),
            version=1,
            created_at=1.0,
            updated_at=1.0,
            last_active_at=1.0,
        )
        self.assertEqual(validate_snapshot(bad), RegistryStatus.REJECTED)

    def test_11_invalid_step_state_rejected(self):
        with self.assertRaises(Exception):
            StepState("NOT_A_STATE")

    def test_12_step_vector_length_mismatch_rejected(self):
        snap, status = build_snapshot(
            owner_id="alice",
            title="T",
            plan_title="P",
            step_titles=("A", "B"),
            step_states=(StepState.PENDING,),
        )
        self.assertEqual(status, RegistryStatus.REJECTED)

    # --- CRUD ---

    def test_13_create_active_goal_success(self):
        snap = _snap()
        res = create_active_goal("alice", snap)
        self.assertEqual(res.status, RegistryStatus.OK)
        self.assertIsNotNone(res.snapshot)
        self.assertEqual(res.snapshot.lifecycle, GoalLifecycle.ACTIVE)

    def test_14_get_active_goal_success(self):
        snap = _snap()
        create_active_goal("alice", snap)
        res = get_active_goal("alice")
        self.assertEqual(res.status, RegistryStatus.OK)
        self.assertEqual(res.snapshot.goal_id, snap.goal_id)

    def test_15_not_found(self):
        res = get_active_goal("nobody")
        self.assertEqual(res.status, RegistryStatus.NOT_FOUND)

    def test_16_single_active_invariant(self):
        a = _snap(goal_id=new_registry_goal_id())
        b = _snap(goal_id=new_registry_goal_id())
        self.assertEqual(create_active_goal("alice", a).status, RegistryStatus.OK)
        self.assertEqual(create_active_goal("alice", b).status, RegistryStatus.CONFLICT)

    def test_17_same_goal_id_idempotency(self):
        gid = new_registry_goal_id()
        a = _snap(goal_id=gid)
        self.assertEqual(create_active_goal("alice", a).status, RegistryStatus.OK)
        again = _snap(goal_id=gid, title="Improve Python")
        res = create_active_goal("alice", again)
        self.assertEqual(res.status, RegistryStatus.OK)
        self.assertGreaterEqual(res.snapshot.version, 2)

    def test_18_different_goal_conflict(self):
        self.test_16_single_active_invariant()

    def test_19_owner_isolation(self):
        create_active_goal("alice", _snap(owner="alice"))
        create_active_goal("bob", _snap(owner="bob", goal_id=new_registry_goal_id()))
        a = get_active_goal("alice")
        b = get_active_goal("bob")
        self.assertEqual(a.status, RegistryStatus.OK)
        self.assertEqual(b.status, RegistryStatus.OK)
        self.assertNotEqual(a.snapshot.goal_id, b.snapshot.goal_id)
        self.assertEqual(a.snapshot.owner_id, "alice")
        self.assertEqual(b.snapshot.owner_id, "bob")

    def test_20_update_success(self):
        snap = _snap()
        created = create_active_goal("alice", snap).snapshot
        titles = created.step_titles
        states = (StepState.COMPLETED, StepState.PENDING, StepState.PENDING)
        updated, status = build_snapshot(
            owner_id="alice",
            goal_id=created.goal_id,
            title=created.title,
            plan_title=created.plan_title,
            step_titles=titles,
            step_states=states,
            active_step_index=2,
            version=created.version,
            created_at=created.created_at,
        )
        self.assertEqual(status, RegistryStatus.OK)
        res = update_active_goal("alice", created.goal_id, updated, created.version)
        self.assertEqual(res.status, RegistryStatus.OK)
        self.assertEqual(res.snapshot.step_states[0], StepState.COMPLETED)
        self.assertEqual(res.snapshot.version, created.version + 1)

    def test_21_optimistic_version_conflict(self):
        snap = _snap()
        created = create_active_goal("alice", snap).snapshot
        updated, _ = build_snapshot(
            owner_id="alice",
            goal_id=created.goal_id,
            title=created.title,
            plan_title=created.plan_title,
            step_titles=created.step_titles,
            step_states=created.step_states,
            active_step_index=1,
            version=created.version,
            created_at=created.created_at,
        )
        res = update_active_goal("alice", created.goal_id, updated, expected_version=999)
        self.assertEqual(res.status, RegistryStatus.CONFLICT)

    def test_22_lifecycle_transition_validation(self):
        snap = _snap()
        created = create_active_goal("alice", snap).snapshot
        # COMPLETED -> ACTIVE invalid
        bad = transition_goal(
            "alice",
            created.goal_id,
            GoalLifecycle.COMPLETED,
            GoalLifecycle.ACTIVE,
            "nope",
            created.version,
        )
        self.assertEqual(bad.status, RegistryStatus.REJECTED)

    def test_23_abandon(self):
        created = create_active_goal("alice", _snap()).snapshot
        res = abandon_goal("alice", created.goal_id, created.version)
        self.assertEqual(res.status, RegistryStatus.OK)
        self.assertEqual(res.snapshot.lifecycle, GoalLifecycle.ABANDONED)
        self.assertEqual(get_active_goal("alice").status, RegistryStatus.NOT_FOUND)

    def test_24_stale(self):
        created = create_active_goal("alice", _snap()).snapshot
        res = mark_stale("alice", created.goal_id, "pivoted", created.version)
        self.assertEqual(res.status, RegistryStatus.OK)
        self.assertEqual(res.snapshot.lifecycle, GoalLifecycle.STALE)
        self.assertIn("pivoted", res.snapshot.staleness_reason)
        self.assertEqual(get_active_goal("alice").status, RegistryStatus.NOT_FOUND)

    def test_25_completed(self):
        created = create_active_goal("alice", _snap(n=2)).snapshot
        # Must make steps terminal before archive
        states = (StepState.COMPLETED, StepState.SKIPPED)
        updated, _ = build_snapshot(
            owner_id="alice",
            goal_id=created.goal_id,
            title=created.title,
            plan_title=created.plan_title,
            step_titles=created.step_titles,
            step_states=states,
            active_step_index=0,
            version=created.version,
            created_at=created.created_at,
        )
        upd = update_active_goal("alice", created.goal_id, updated, created.version)
        self.assertEqual(upd.status, RegistryStatus.OK)
        res = archive_goal("alice", created.goal_id, upd.snapshot.version)
        self.assertEqual(res.status, RegistryStatus.OK)
        self.assertEqual(res.snapshot.lifecycle, GoalLifecycle.COMPLETED)

    def test_26_unsupported_schema_version(self):
        raw = GoalSnapshot(
            schema_version=99,
            goal_id="rg_x",
            owner_id="alice",
            title="T",
            lifecycle=GoalLifecycle.ACTIVE,
            plan_title="P",
            step_titles=("A",),
            step_states=(StepState.PENDING,),
            active_step_index=1,
            blocker_summary="",
            staleness_reason="",
            durable_view="",
            version=1,
            created_at=1.0,
            updated_at=1.0,
            last_active_at=1.0,
        )
        self.assertEqual(validate_snapshot(raw), RegistryStatus.VERSION_UNSUPPORTED)
        self.assertEqual(create_active_goal("alice", raw).status, RegistryStatus.VERSION_UNSUPPORTED)

    def test_27_invalid_snapshot_empty_owner(self):
        snap, status = build_snapshot(
            owner_id="",
            title="T",
            plan_title="P",
            step_titles=("A",),
            step_states=(StepState.PENDING,),
        )
        self.assertEqual(status, RegistryStatus.REJECTED)

    def test_28_sensitive_content_rejection(self):
        self.assertTrue(is_sensitive_goal_content("api_key=secret123"))
        snap, status = build_snapshot(
            owner_id="alice",
            title="Learn api_key=secret123",
            plan_title="Plan",
            step_titles=("A",),
            step_states=(StepState.PENDING,),
        )
        self.assertEqual(status, RegistryStatus.REJECTED)

    def test_29_postgres_unavailable_handling(self):
        with patch(
            "orchestration.plan.goal_registry._USE_TEST_STORE",
            False,
        ), patch(
            "orchestration.plan.goal_registry.ensure_goal_registry_schema",
            return_value=False,
        ):
            res = create_active_goal("alice", _snap())
            self.assertEqual(res.status, RegistryStatus.UNAVAILABLE)

    def test_30_test_store_isolation_reset(self):
        create_active_goal("alice", _snap())
        self.assertEqual(get_active_goal("alice").status, RegistryStatus.OK)
        reset_goal_registry_for_tests()
        self.assertEqual(get_active_goal("alice").status, RegistryStatus.NOT_FOUND)

    def test_31_no_user_text_owner_identity(self):
        # API requires explicit owner_id arg; empty rejected
        res = create_active_goal("", _snap())
        self.assertEqual(res.status, RegistryStatus.REJECTED)

    def test_32_no_execution_authority(self):
        import ast
        from pathlib import Path

        path = Path(__file__).resolve().parent / "orchestration" / "plan" / "goal_registry.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(node.module.startswith("proactive.computer"))
                self.assertNotIn("executor", node.module or "")
                self.assertFalse(node.module.startswith("orchestration.task"))

    def test_33_no_network_cloud_calls(self):
        src = open(
            os.path.join("orchestration", "plan", "goal_registry.py"),
            encoding="utf-8",
        ).read()
        for banned in ("requests.", "httpx", "openai", "urllib.request", "aiohttp"):
            self.assertNotIn(banned, src)

    def test_34_hard_zero_no_ollama(self):
        src = open(
            os.path.join("orchestration", "plan", "goal_registry.py"),
            encoding="utf-8",
        ).read()
        self.assertNotIn("ollama", src.lower())
        self.assertNotIn("authorize_llm", src)

    def test_35_recover_goal(self):
        snap = _snap()
        create_active_goal("alice", snap)
        res = recover_goal("alice")
        self.assertEqual(res.status, RegistryStatus.OK)
        self.assertEqual(res.snapshot.goal_id, snap.goal_id)

    def test_36_stale_resume_transition(self):
        created = create_active_goal("alice", _snap()).snapshot
        stale = mark_stale("alice", created.goal_id, "ttl", created.version)
        self.assertEqual(stale.status, RegistryStatus.OK)
        resumed = transition_goal(
            "alice",
            created.goal_id,
            GoalLifecycle.STALE,
            GoalLifecycle.ACTIVE,
            "resume",
            stale.snapshot.version,
        )
        self.assertEqual(resumed.status, RegistryStatus.OK)
        self.assertEqual(resumed.snapshot.lifecycle, GoalLifecycle.ACTIVE)

    def test_37_completed_requires_terminal_steps(self):
        created = create_active_goal("alice", _snap()).snapshot
        res = archive_goal("alice", created.goal_id, created.version)
        self.assertEqual(res.status, RegistryStatus.REJECTED)

    def test_38_owner_mismatch_on_update(self):
        created = create_active_goal("alice", _snap(owner="alice")).snapshot
        other, _ = build_snapshot(
            owner_id="bob",
            goal_id=created.goal_id,
            title=created.title,
            plan_title=created.plan_title,
            step_titles=created.step_titles,
            step_states=created.step_states,
            active_step_index=1,
            version=created.version,
            created_at=created.created_at,
        )
        res = update_active_goal("bob", created.goal_id, other, created.version)
        self.assertIn(
            res.status,
            (RegistryStatus.OWNER_MISMATCH, RegistryStatus.CONFLICT, RegistryStatus.NOT_FOUND),
        )

    def test_39_new_registry_goal_id_stable_prefix(self):
        gid = new_registry_goal_id()
        self.assertTrue(gid.startswith("rg_"))
        self.assertNotEqual(gid, new_registry_goal_id())

    def test_40_archive_retention_constant(self):
        self.assertEqual(ARCHIVE_RETENTION, 5)


if __name__ == "__main__":
    unittest.main()
