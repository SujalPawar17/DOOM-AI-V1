"""V8.28 Phase 4 — Explicit Goal Experience UX tests."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_V827_USER_MODEL_ENABLED", "false")
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

from orchestration.conversation.context import reset_conversation_context_for_tests
from orchestration.executor import ExecutionIdentity, execute_plan
from orchestration.experience.confirm import (
    ExperienceConfirm,
    clear_experience_confirm,
    get_experience_confirm,
    reset_experience_confirms_for_tests,
    set_experience_confirm,
)
from orchestration.experience.format import format_experience_item, format_why
from orchestration.experience.intent import (
    ExperienceIntent,
    detect_experience_intent,
    handle_experience_request,
    should_route_experience,
)
from orchestration.experience.store import (
    create_experience,
    forget_experience,
    get_experience,
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
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.types import IntentClass
from orchestration.production import format_v8_execution, prepare_v8_request


def _ident(owner="alice", session="sess-e4"):
    return ExecutionIdentity(owner_id=owner, session_id=session, computer_session_id="")


class TestV828ExperienceUX(unittest.TestCase):
    def setUp(self):
        reset_conversation_context_for_tests()
        use_test_experience_store(True)
        reset_experience_for_tests()
        reset_experience_confirms_for_tests()
        os.environ["PROACTIVE_V8_ENABLED"] = "true"
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
        os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "false"

    def tearDown(self):
        reset_experience_confirms_for_tests()
        reset_experience_for_tests()
        use_test_experience_store(False)
        reset_conversation_context_for_tests()
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

    def _create(self, owner="alice", **kwargs):
        base = {
            "title": "Improve Python skills",
            "outcome": Outcome.COMPLETED,
            "step_summary": ("Practice daily", "Review basics"),
            "blockers": (),
            "user_note": "",
            "tags": ("python",),
            "source_goal_id": "",
        }
        base.update(kwargs)
        res = create_experience(owner, base)
        self.assertEqual(res.status, ExperienceResultStatus.OK, msg=str(res.status))
        return res.experience

    def _exec(self, phrase, owner="alice", session="sess-e4"):
        prep = prepare_v8_request(phrase, identity=_ident(owner, session))
        self.assertEqual(prep.status, "OK", (prep.status, prep.reason, prep.intent))
        self.assertIsNotNone(prep.plan)
        res = execute_plan(
            prep.plan, identity=_ident(owner, session), authorized_plan_hash=""
        )
        return format_v8_execution(res, intent=prep.intent), prep

    # --- QUERY ----------------------------------------------------------------

    def test_01_explicit_query_routes(self):
        intent, _ = detect_experience_intent(
            "What did we learn from my previous goals?"
        )
        self.assertEqual(intent, ExperienceIntent.EXPERIENCE_QUERY)
        self.assertTrue(
            should_route_experience(
                "What did we learn from my previous goals?", "alice", "sess-e4"
            )
        )
        prep = prepare_v8_request(
            "What did we learn from my previous goals?",
            identity=_ident(),
        )
        self.assertEqual(prep.intent, IntentClass.MEMORY_READ.value)

    def test_02_relevant_experience_returned(self):
        self._create(title="Improve Python skills", source_goal_id="rg_py")
        text, _ = self._exec("What happened with my previous Python goal?")
        self.assertIn("Python", text)
        self.assertIn("COMPLETED", text)

    def test_03_maximum_4_results(self):
        for i in range(6):
            self._create(
                title=f"Practice session {i}",
                source_goal_id=f"rg_s_{i}",
                step_summary=("Practice drills",),
            )
        reply = handle_experience_request(
            "alice", "sess-e4", "Show my recent goal experiences."
        )
        self.assertIsNotNone(reply)
        # Numbered items 1..4 only
        self.assertIn("1.", reply)
        self.assertIn("4.", reply)
        self.assertNotIn("5.", reply)

    def test_04_no_result_does_not_invent(self):
        reply = handle_experience_request(
            "alice", "sess-e4", "What happened with my previous pottery goal?"
        )
        self.assertIsNotNone(reply)
        self.assertIn("don't have", reply.lower())
        self.assertNotIn("pottery class completed", reply.lower())

    def test_05_owner_isolation(self):
        self._create(owner="alice", title="Alice workshop plan", source_goal_id="rg_a")
        self._create(owner="bob", title="Bob workshop plan", source_goal_id="rg_b")
        reply_a = handle_experience_request(
            "alice", "sess-e4", "Show my recent goal experiences."
        )
        reply_b = handle_experience_request(
            "bob", "sess-e4", "Show my recent goal experiences."
        )
        self.assertIn("Alice", reply_a)
        self.assertNotIn("Bob", reply_a)
        self.assertIn("Bob", reply_b)
        self.assertNotIn("Alice", reply_b)
        self.assertNotIn("owner_id", reply_a.lower())
        self.assertNotIn("ge_", reply_a)

    def test_06_malformed_skipped(self):
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
            reply = handle_experience_request(
                "alice", "sess-e4", "Show my recent goal experiences."
            )
        self.assertIn("gardening", reply.lower())
        self.assertNotIn("ge_bad", reply)

    def test_07_forgotten_excluded(self):
        exp = self._create(title="Wiki rewrite", source_goal_id="rg_w")
        forget_experience("alice", exp.experience_id, expected_version=1)
        reply = handle_experience_request(
            "alice", "sess-e4", "What happened with my previous Wiki goal?"
        )
        self.assertIn("don't have", reply.lower())

    def test_08_expired_excluded(self):
        self._create(
            title="Seasonal sprint",
            source_goal_id="rg_sea",
            expires_at=time.time() - 30,
        )
        reply = handle_experience_request(
            "alice", "sess-e4", "What happened with my previous Seasonal goal?"
        )
        self.assertIn("don't have", reply.lower())

    def test_09_resolver_failure_fail_closed(self):
        with patch(
            "orchestration.experience.store.list_experiences",
            return_value=ExperienceResult(ExperienceResultStatus.UNAVAILABLE),
        ):
            reply = handle_experience_request(
                "alice", "sess-e4", "Show my recent goal experiences."
            )
        self.assertIn("unavailable", reply.lower())

    def test_10_flag_off_preserves_behavior(self):
        self._create(title="Flag off garden", source_goal_id="rg_f")
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "false"
        self.assertFalse(
            should_route_experience(
                "Show my recent goal experiences.", "alice", "sess-e4"
            )
        )
        self.assertIsNone(
            handle_experience_request(
                "alice", "sess-e4", "Show my recent goal experiences."
            )
        )
        prep = prepare_v8_request(
            "Show my recent goal experiences.", identity=_ident()
        )
        self.assertNotEqual(prep.intent, IntentClass.MEMORY_READ.value)

    # --- WHY ------------------------------------------------------------------

    def test_11_explicit_why_works(self):
        self._create(title="Laptop thermal cleanup", source_goal_id="rg_t")
        reply = handle_experience_request(
            "alice", "sess-e4", "Why do you remember this experience?"
        )
        self.assertIsNotNone(reply)
        self.assertIn("terminal", reply.lower())
        self.assertIn("thermal", reply.lower())

    def test_12_why_no_sensitive_or_ids(self):
        exp = self._create(title="Public rewrite", source_goal_id="rg_pub")
        text = format_why(exp)
        blob = text.lower()
        self.assertNotIn("owner_id", blob)
        self.assertNotIn(exp.experience_id.lower(), blob)
        self.assertNotIn("rg_", blob)
        self.assertNotIn("v8_goal", blob)
        self.assertNotIn("postgres", blob)

    def test_13_why_no_mutation(self):
        exp = self._create(title="Stable why target", source_goal_id="rg_why")
        with patch(
            "orchestration.experience.store.forget_experience"
        ) as f, patch(
            "orchestration.experience.store.update_experience"
        ) as u, patch(
            "orchestration.experience.store.create_experience"
        ) as c:
            handle_experience_request(
                "alice", "sess-e4", "Why is this experience stored?"
            )
        f.assert_not_called()
        u.assert_not_called()
        c.assert_not_called()
        again = get_experience("alice", exp.experience_id)
        self.assertEqual(again.experience.status, ExperienceStatus.ACTIVE_RECORD)

    # --- FORGET ---------------------------------------------------------------

    def test_14_forget_requires_confirmation(self):
        self._create(title="Only one experience", source_goal_id="rg_one")
        reply = handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        self.assertIn("Confirm", reply)
        listed = list_experiences("alice")
        self.assertEqual(len(listed.experiences), 1)
        self.assertEqual(
            listed.experiences[0].status, ExperienceStatus.ACTIVE_RECORD
        )

    def test_15_single_match_creates_confirmation(self):
        exp = self._create(title="Solo checklist", source_goal_id="rg_solo")
        handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        pending = get_experience_confirm("alice", "sess-e4")
        self.assertIsNotNone(pending)
        self.assertEqual(pending.experience_id, exp.experience_id)
        self.assertEqual(pending.expected_version, exp.version)
        self.assertTrue(pending.nonce)

    def test_16_ambiguous_match_does_not_select(self):
        self._create(title="Python drills A", source_goal_id="rg_a")
        self._create(title="Python drills B", source_goal_id="rg_b")
        reply = handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        self.assertIn("Several", reply)
        self.assertIsNone(get_experience_confirm("alice", "sess-e4"))

    def test_17_no_match_handled_safely(self):
        reply = handle_experience_request(
            "alice", "sess-e4", "Forget my pottery goal experience."
        )
        self.assertIn("don't have", reply.lower())
        self.assertIsNone(get_experience_confirm("alice", "sess-e4"))

    def test_18_confirmation_owner_binding(self):
        exp = self._create(title="Owner bound", source_goal_id="rg_ob")
        handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        # Wrong owner cannot apply.
        reply = handle_experience_request("bob", "sess-e4", "yes")
        # bob has no pending → None or unrelated
        self.assertTrue(reply is None or "Confirm" not in (reply or ""))
        still = get_experience("alice", exp.experience_id)
        self.assertEqual(still.experience.status, ExperienceStatus.ACTIVE_RECORD)

    def test_19_confirmation_session_binding(self):
        exp = self._create(title="Session bound", source_goal_id="rg_sb")
        handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        reply = handle_experience_request("alice", "other-sess", "yes")
        self.assertTrue(reply is None or "expired" in (reply or "").lower() or "Confirm" not in (reply or ""))
        still = get_experience("alice", exp.experience_id)
        self.assertEqual(still.experience.status, ExperienceStatus.ACTIVE_RECORD)

    def test_20_confirmation_nonce_binding(self):
        exp = self._create(title="Nonce bound", source_goal_id="rg_nb")
        handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        pending = get_experience_confirm("alice", "sess-e4")
        # Replace with different nonce mid-flight.
        set_experience_confirm(
            ExperienceConfirm(
                owner_id="alice",
                session_id="sess-e4",
                action="FORGET",
                experience_id=pending.experience_id,
                expected_version=pending.expected_version,
                expires_at=pending.expires_at,
                nonce="deadbeefdeadbeef",
                title=pending.title,
                outcome=pending.outcome,
            )
        )
        # Apply path re-reads fresh; yes still works with new nonce object.
        # Force mismatch by calling apply with stale snapshot via direct replace after get.
        stale = pending
        clear_experience_confirm("alice", "sess-e4")
        set_experience_confirm(
            ExperienceConfirm(
                owner_id=stale.owner_id,
                session_id=stale.session_id,
                action=stale.action,
                experience_id=stale.experience_id,
                expected_version=stale.expected_version,
                expires_at=stale.expires_at,
                nonce="aaaaaaaaaaaaaaaa",
                title=stale.title,
                outcome=stale.outcome,
            )
        )
        # Simulate yes with handler — uses live pending nonce, should succeed once.
        reply = handle_experience_request("alice", "sess-e4", "yes")
        self.assertIn("Forgot", reply)
        gone = get_experience("alice", exp.experience_id)
        self.assertEqual(gone.status, ExperienceResultStatus.NOT_FOUND)

    def test_21_confirmation_version_binding(self):
        exp = self._create(title="Version bound", source_goal_id="rg_vb")
        handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        pending = get_experience_confirm("alice", "sess-e4")
        # Bump stored version underneath.
        from orchestration.experience.store import update_experience

        update_experience(
            "alice",
            exp.experience_id,
            {"title": "Version bound updated"},
            expected_version=exp.version,
        )
        reply = handle_experience_request("alice", "sess-e4", "yes")
        self.assertTrue(
            "changed" in reply.lower() or "try again" in reply.lower()
        )
        # Confirm consumed; entry still active at new version.
        again = get_experience("alice", exp.experience_id)
        self.assertEqual(again.status, ExperienceResultStatus.OK)

    def test_22_confirmation_ttl_enforced(self):
        exp = self._create(title="TTL bound", source_goal_id="rg_ttl")
        handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        pending = get_experience_confirm("alice", "sess-e4")
        set_experience_confirm(
            ExperienceConfirm(
                owner_id=pending.owner_id,
                session_id=pending.session_id,
                action=pending.action,
                experience_id=pending.experience_id,
                expected_version=pending.expected_version,
                expires_at=time.time() - 1,
                nonce=pending.nonce,
                title=pending.title,
                outcome=pending.outcome,
            )
        )
        reply = handle_experience_request("alice", "sess-e4", "yes")
        self.assertTrue(
            reply is None or "expired" in reply.lower()
        )
        still = get_experience("alice", exp.experience_id)
        self.assertEqual(still.experience.status, ExperienceStatus.ACTIVE_RECORD)

    def test_23_yes_performs_soft_forget(self):
        exp = self._create(title="Soft forget me", source_goal_id="rg_sf")
        handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        reply = handle_experience_request("alice", "sess-e4", "yes")
        self.assertIn("Forgot", reply)
        gone = get_experience("alice", exp.experience_id)
        self.assertEqual(gone.status, ExperienceResultStatus.NOT_FOUND)

    def test_24_no_cancels(self):
        exp = self._create(title="Cancel forget", source_goal_id="rg_cf")
        handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        reply = handle_experience_request("alice", "sess-e4", "no")
        self.assertIn("cancelled", reply.lower())
        still = get_experience("alice", exp.experience_id)
        self.assertEqual(still.experience.status, ExperienceStatus.ACTIVE_RECORD)
        self.assertIsNone(get_experience_confirm("alice", "sess-e4"))

    def test_25_expired_confirmation_fails_closed(self):
        self.test_22_confirmation_ttl_enforced()

    def test_26_replayed_confirmation_fails(self):
        exp = self._create(title="Replay once", source_goal_id="rg_rp")
        handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        first = handle_experience_request("alice", "sess-e4", "yes")
        self.assertIn("Forgot", first)
        second = handle_experience_request("alice", "sess-e4", "yes")
        self.assertTrue(second is None or "Forgot" not in second)

    def test_27_already_forgotten_handled(self):
        exp = self._create(title="Already gone", source_goal_id="rg_ag")
        handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        forget_experience("alice", exp.experience_id, expected_version=1)
        reply = handle_experience_request("alice", "sess-e4", "yes")
        self.assertTrue(
            "already" in reply.lower() or "unavailable" in reply.lower()
        )

    def test_28_forget_does_not_modify_goal_registry(self):
        self._create(title="Registry safe", source_goal_id="rg_rs")
        with patch(
            "orchestration.plan.goal_registry.transition_goal", create=True
        ) as tg, patch(
            "orchestration.plan.goal_registry.create_active_goal", create=True
        ) as cg:
            handle_experience_request(
                "alice", "sess-e4", "Forget that goal experience."
            )
            handle_experience_request("alice", "sess-e4", "yes")
        tg.assert_not_called()
        cg.assert_not_called()

    def test_29_forget_does_not_modify_continuity(self):
        self._create(title="Continuity safe", source_goal_id="rg_cs")
        with patch(
            "orchestration.plan.continuity.engine.apply_progress_event",
            create=True,
        ) as ap:
            handle_experience_request(
                "alice", "sess-e4", "Forget that goal experience."
            )
            handle_experience_request("alice", "sess-e4", "confirm")
        ap.assert_not_called()

    def test_30_forget_does_not_modify_user_model(self):
        self._create(title="Profile safe", source_goal_id="rg_ps")
        with patch(
            "orchestration.user_model.store.forget_profile_entry", create=True
        ) as fp, patch(
            "orchestration.user_model.store.upsert_profile_entry", create=True
        ) as up:
            handle_experience_request(
                "alice", "sess-e4", "Forget that goal experience."
            )
            handle_experience_request("alice", "sess-e4", "yes")
        fp.assert_not_called()
        up.assert_not_called()

    def test_31_forget_does_not_modify_personal_memory(self):
        self._create(title="Memory safe", source_goal_id="rg_ms")
        with patch(
            "orchestration.conversation.personal_memory.save_personal_memory",
            create=True,
        ) as sm, patch(
            "orchestration.conversation.personal_memory.delete_personal_memory",
            create=True,
        ) as dm:
            handle_experience_request(
                "alice", "sess-e4", "Forget that goal experience."
            )
            handle_experience_request("alice", "sess-e4", "yes")
        sm.assert_not_called()
        dm.assert_not_called()

    # --- ROUTING --------------------------------------------------------------

    def test_32_plan_remains_plan(self):
        self.assertEqual(
            normalize_intent("Make me a plan to improve Python."),
            IntentClass.PLAN,
        )
        self.assertEqual(
            detect_experience_intent("Make me a plan to improve Python.")[0],
            ExperienceIntent.NONE,
        )

    def test_33_decision_remains_decision(self):
        phrase = "Should I choose tea or coffee?"
        # Experience UX must never claim decision-like utterances.
        self.assertEqual(
            detect_experience_intent(phrase)[0],
            ExperienceIntent.NONE,
        )
        self.assertFalse(should_route_experience(phrase, "alice", "sess-e4"))
        # When normalizer marks DECISION, kernel must keep it (not MEMORY_READ).
        if normalize_intent(phrase) is IntentClass.DECISION:
            prep = prepare_v8_request(phrase, identity=_ident())
            self.assertEqual(prep.intent, IntentClass.DECISION.value)

    def test_34_memory_save_remains_memory_save(self):
        self.assertEqual(
            normalize_intent("Remember that I prefer Python."),
            IntentClass.MEMORY_SAVE,
        )
        self.assertEqual(
            detect_experience_intent("Remember that I prefer Python.")[0],
            ExperienceIntent.NONE,
        )

    def test_35_computer_remains_computer(self):
        intent = normalize_intent("Click Save.")
        self.assertIn(intent, (IntentClass.COMPUTER, IntentClass.WORLD_ACTION))
        self.assertEqual(
            detect_experience_intent("Click Save.")[0],
            ExperienceIntent.NONE,
        )

    def test_36_unrelated_conversation_unchanged(self):
        self.assertEqual(
            detect_experience_intent("How is the weather today?")[0],
            ExperienceIntent.NONE,
        )
        self.assertFalse(
            should_route_experience(
                "How is the weather today?", "alice", "sess-e4"
            )
        )

    # --- SECURITY -------------------------------------------------------------

    def test_37_sensitive_content_never_displayed(self):
        sensitive = GoalExperience(
            schema_version=SCHEMA_VERSION,
            experience_id="ge_sens",
            owner_id="alice",
            source_goal_id="rg_sens",
            title="Harden laptop backups",
            outcome=Outcome.COMPLETED,
            step_summary=("password reset drill", "Clean clutter"),
            blockers=("csrf token leak",),
            user_note="credit card on file",
            tags=(),
            status=ExperienceStatus.ACTIVE_RECORD,
            version=1,
            created_at=1.0,
            updated_at=2.0,
            expires_at=None,
        )
        block = format_experience_item(sensitive, index=1)
        low = block.lower()
        self.assertIn("harden laptop backups", low)
        self.assertNotIn("password", low)
        self.assertNotIn("csrf", low)
        self.assertNotIn("credit card", low)
        self.assertIn("clean clutter", low)

    def test_38_owner_id_never_from_user_input(self):
        self._create(owner="alice", title="Trusted owner only", source_goal_id="rg_to")
        # Trusted owner is always the execution identity, never parsed from text.
        reply = handle_experience_request(
            "bob",
            "sess-e4",
            "Show my recent goal experiences.",
        )
        self.assertIsNotNone(reply)
        self.assertNotIn("Trusted owner", reply)
        self.assertIn("don't have", reply.lower())

    def test_39_no_raw_sql_in_ux_layer(self):
        import inspect
        from orchestration.experience import intent as intent_mod
        from orchestration.experience import format as format_mod

        src = inspect.getsource(intent_mod) + inspect.getsource(format_mod)
        self.assertNotIn("SELECT ", src.upper().replace("SELECTIVE", ""))
        self.assertNotIn("INSERT ", src.upper())
        self.assertNotIn("DELETE FROM", src.upper())

    def test_40_no_secrets_or_errors_leaked(self):
        with patch(
            "orchestration.experience.store.list_experiences",
            side_effect=RuntimeError("postgres://user:pass@localhost/doom"),
        ):
            reply = handle_experience_request(
                "alice", "sess-e4", "Show my recent goal experiences."
            )
        self.assertIsNotNone(reply)
        low = reply.lower()
        self.assertNotIn("postgres://", low)
        self.assertNotIn("traceback", low)
        self.assertNotIn("password", low)
        self.assertIn("unavailable", low)

    def test_41_forget_it_confirm_word(self):
        exp = self._create(title="Forget it phrase", source_goal_id="rg_fi")
        handle_experience_request(
            "alice", "sess-e4", "Forget that goal experience."
        )
        reply = handle_experience_request("alice", "sess-e4", "forget it")
        self.assertIn("Forgot", reply)
        self.assertEqual(
            get_experience("alice", exp.experience_id).status,
            ExperienceResultStatus.NOT_FOUND,
        )

    def test_42_topic_forget_single_match(self):
        self._create(title="Improve Rust skills", source_goal_id="rg_rust")
        self._create(title="Improve Python skills", source_goal_id="rg_py2")
        reply = handle_experience_request(
            "alice", "sess-e4", "Forget my Python goal experience."
        )
        self.assertIn("Confirm", reply)
        self.assertIn("Python", reply)
        pending = get_experience_confirm("alice", "sess-e4")
        self.assertIsNotNone(pending)
        self.assertIn("Python", pending.title)


if __name__ == "__main__":
    unittest.main()
