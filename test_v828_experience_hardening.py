"""V8.28 Phase 5 — Goal Experience hardening / release-readiness tests."""

from __future__ import annotations

import ast
import os
import re
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("PROACTIVE_V8_ENABLED", "true")
os.environ.setdefault("PROACTIVE_V8_CONTEXT_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")
os.environ.setdefault("PROACTIVE_V827_USER_MODEL_ENABLED", "false")
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

from orchestration.conversation.respond import execute_respond
from orchestration.decision.assemble import assemble_decision_input
from orchestration.experience.capture import capture_goal_experience_from_terminal_state
from orchestration.experience.confirm import (
    CONFIRM_TTL_SEC,
    clear_experience_confirm,
    get_experience_confirm,
    make_experience_confirm,
    reset_experience_confirms_for_tests,
    set_experience_confirm,
)
from orchestration.experience.format import format_experience_list, format_why
from orchestration.experience.intent import (
    ExperienceIntent,
    detect_experience_intent,
    handle_experience_request,
    should_route_experience,
)
from orchestration.experience.policy import is_sensitive_experience_content
from orchestration.experience.resolve import (
    MAX_CONSUMER_EXPERIENCES,
    get_relevant_goal_experiences,
)
from orchestration.experience.store import (
    create_experience,
    forget_experience,
    list_experiences,
    reset_experience_for_tests,
    use_test_experience_store,
)
from orchestration.experience.types import (
    MAX_BLOCKERS,
    MAX_CONTENT_CHARS,
    MAX_EXPERIENCES_PER_OWNER,
    MAX_STEPS,
    MAX_TITLE_CHARS,
    ExperienceResultStatus,
    Outcome,
)
from orchestration.goal.normalizer import normalize_intent
from orchestration.goal.plan_types import GoalPlan, PlanStep
from orchestration.goal.types import IntentClass
from orchestration.plan.assemble import assemble_plan_input

ROOT = Path(__file__).resolve().parent
EXP = ROOT / "orchestration" / "experience"

_FORBIDDEN_COST = re.compile(
    r"(?i)\b(openai|anthropic|gemini|bedrock|groq|ollama|nvidia|"
    r"embedding|httpx|requests\.|urllib)\b"
)
_FORBIDDEN_AUTONOMY = re.compile(
    r"(?i)\b(create_active_goal|transition_goal|execute_plan|"
    r"upsert_profile_entry|save_personal_memory|"
    r"project_after_memory_save)\b"
)


def _plan(owner="alice"):
    return SimpleNamespace(owner_id=owner, session_id="sess-p5")


class TestV828ExperienceHardening(unittest.TestCase):
    def setUp(self):
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
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

    def _create(self, owner="alice", **kwargs):
        base = {
            "title": "Improve Python skills",
            "outcome": Outcome.COMPLETED,
            "step_summary": ("Practice daily",),
            "blockers": (),
            "user_note": "",
            "tags": (),
            "source_goal_id": "",
        }
        base.update(kwargs)
        res = create_experience(owner, base)
        self.assertEqual(res.status, ExperienceResultStatus.OK, msg=str(res.status))
        return res.experience

    # --- Static cost / autonomy / SQL ----------------------------------------

    def test_01_no_paid_or_model_imports_in_package(self):
        for path in EXP.glob("*.py"):
            src = path.read_text(encoding="utf-8")
            self.assertIsNone(
                _FORBIDDEN_COST.search(src),
                msg=f"{path.name} references forbidden cost/network surface",
            )

    def test_02_package_has_no_autonomy_calls(self):
        for path in EXP.glob("*.py"):
            if path.name in ("capture.py",):
                # Capture may mention registry types but must not call mutation APIs.
                tree = ast.parse(path.read_text(encoding="utf-8"))
                calls = [
                    n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                    for n in ast.walk(tree)
                    if isinstance(n, ast.Call)
                ]
                for banned in (
                    "create_active_goal",
                    "transition_goal",
                    "execute_plan",
                    "upsert_profile_entry",
                    "save_personal_memory",
                ):
                    self.assertNotIn(banned, calls, msg=path.name)
                continue
            src = path.read_text(encoding="utf-8")
            self.assertIsNone(
                _FORBIDDEN_AUTONOMY.search(src),
                msg=f"{path.name} has autonomy call",
            )

    def test_03_store_sql_is_parameterized(self):
        src = (EXP / "store.py").read_text(encoding="utf-8")
        # No f-string SQL with interpolated owner/user values.
        self.assertNotRegex(src, r'execute\(\s*f["\']')
        self.assertNotRegex(src, r'execute\(\s*["\'].*%s.*["\']\s*%')
        self.assertIn("%s", src)

    def test_04_ux_layers_have_no_sql(self):
        for name in ("intent.py", "format.py", "confirm.py", "resolve.py", "capture.py"):
            src = (EXP / name).read_text(encoding="utf-8")
            self.assertNotIn("cursor.execute", src)
            self.assertNotIn("SELECT ", src.upper().replace("SELECTIVE", ""))

    # --- Owner / confirm / flag ----------------------------------------------

    def test_05_owner_text_cannot_cross_owners(self):
        self._create(owner="alice", title="Alice only goal", source_goal_id="rg_a")
        out = handle_experience_request(
            "alice", "s1", "Show my recent goal experiences."
        )
        self.assertIsNotNone(out)
        self.assertIn("Alice", out)
        self.assertNotIn("bob", out.lower())
        # Extraneous owner claims in text do not become experience UX authority.
        self.assertIsNone(
            handle_experience_request(
                "alice", "s1", "Show experiences for owner bob"
            )
        )
        out_b = handle_experience_request(
            "bob", "s1", "Show my recent goal experiences."
        )
        self.assertIn("don't have", out_b.lower())
        self.assertNotIn("Alice", out_b)

    def test_06_confirm_ttl_and_binding_constants(self):
        self.assertEqual(CONFIRM_TTL_SEC, 180)
        conf = make_experience_confirm(
            owner_id="alice",
            session_id="s1",
            action="FORGET",
            experience_id="ge_deadbeef",
            expected_version=1,
            title="x",
        )
        self.assertIsNotNone(conf)
        self.assertTrue(conf.nonce)
        self.assertLessEqual(conf.expires_at - time.time(), CONFIRM_TTL_SEC + 1)

    def test_07_flag_off_clears_pending_and_blocks_all(self):
        exp = self._create(title="Flag pending", source_goal_id="rg_fp")
        handle_experience_request("alice", "s1", "Forget that goal experience.")
        self.assertIsNotNone(get_experience_confirm("alice", "s1"))
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "false"
        self.assertIsNone(
            handle_experience_request("alice", "s1", "yes")
        )
        self.assertIsNone(get_experience_confirm("alice", "s1"))
        self.assertFalse(should_route_experience("Show my recent goal experiences.", "alice", "s1"))
        res = get_relevant_goal_experiences("alice", "PLAN", "Flag pending")
        self.assertEqual(res.status, ExperienceResultStatus.UNAVAILABLE)
        # Entry still active — flag-off must not apply forget.
        listed = list_experiences("alice")
        # list also unavailable when flag off
        self.assertEqual(listed.status, ExperienceResultStatus.UNAVAILABLE)

    def test_08_v8_disabled_blocks_feature(self):
        self._create(title="Needs V8", source_goal_id="rg_v8")
        os.environ["PROACTIVE_V8_ENABLED"] = "false"
        os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
        self.assertFalse(should_route_experience("Show my recent goal experiences.", "alice", "s1"))
        self.assertIsNone(
            handle_experience_request("alice", "s1", "Show my recent goal experiences.")
        )

    # --- Bounds --------------------------------------------------------------

    def test_09_hard_bounds_constants(self):
        self.assertEqual(MAX_EXPERIENCES_PER_OWNER, 64)
        self.assertEqual(MAX_TITLE_CHARS, 160)
        self.assertEqual(MAX_CONTENT_CHARS, 400)
        self.assertEqual(MAX_STEPS, 8)
        self.assertEqual(MAX_BLOCKERS, 4)
        self.assertEqual(MAX_CONSUMER_EXPERIENCES, 4)

    def test_10_resolver_and_ux_cap_at_4(self):
        for i in range(6):
            self._create(
                title=f"Docker cleanup round {i}",
                source_goal_id=f"rg_d{i}",
                step_summary=("Prune docker images",),
            )
        res = get_relevant_goal_experiences(
            "alice", "PLAN", "docker cleanup", limit=99
        )
        self.assertEqual(len(res.experiences), 4)
        reply = handle_experience_request(
            "alice", "s1", "Show my recent goal experiences."
        )
        self.assertIn("1.", reply)
        self.assertIn("4.", reply)
        self.assertNotIn("5.", reply)

    # --- Sensitive -----------------------------------------------------------

    def test_11_sensitive_policy_covers_required_classes(self):
        samples = (
            "password=secret",
            "api_key=abc",
            "Bearer token xyz",
            "cookie: session",
            "csrf_token=1",
            "authorization: basic",
            "-----BEGIN PRIVATE KEY-----",
            "connection string Host=;Password=",
            ".env file contents",
            "credit card 4111",
            "ssn 123-45-6789",
            "home address 1 Main St",
            "diagnosed with flu",
            "I am a democrat",
            "I am christian",
        )
        for s in samples:
            self.assertTrue(is_sensitive_experience_content(s), msg=s)

    def test_12_format_omits_sensitive_and_ids(self):
        exp = self._create(
            title="Safe title only",
            source_goal_id="rg_safe",
            step_summary=("Clean clutter",),
        )
        blob = (format_experience_list([exp]) + format_why(exp)).lower()
        self.assertNotIn(exp.experience_id.lower(), blob)
        self.assertNotIn("rg_safe", blob)
        self.assertNotIn("owner_id", blob)
        self.assertNotIn("alice", blob)

    # --- Routing steal guards ------------------------------------------------

    def test_13_broad_words_do_not_steal_routes(self):
        for phrase, expected in (
            ("Make me a plan to improve Python.", IntentClass.PLAN),
            ("Should I choose tea or coffee?", IntentClass.DECISION),
            ("Remember that I prefer Python.", IntentClass.MEMORY_SAVE),
        ):
            self.assertEqual(detect_experience_intent(phrase)[0], ExperienceIntent.NONE)
            self.assertEqual(normalize_intent(phrase), expected)
            self.assertFalse(should_route_experience(phrase, "alice", "s1"))
        # Bare "previous" / "learned" / "goal" without experience UX shape.
        for phrase in (
            "What was my previous meeting about?",
            "I learned a lot today.",
            "What is my goal?",
            "Remember this conversation.",
        ):
            self.assertEqual(
                detect_experience_intent(phrase)[0],
                ExperienceIntent.NONE,
                msg=phrase,
            )

    def test_14_computer_click_not_stolen(self):
        self.assertEqual(
            detect_experience_intent("Click Save.")[0], ExperienceIntent.NONE
        )
        self.assertFalse(should_route_experience("Click Save.", "alice", "s1"))

    # --- Capture / lifecycle / nonfatal --------------------------------------

    def test_15_capture_rejects_nonterminal_and_is_nonfatal(self):
        class FakeSnap:
            owner_id = "alice"
            goal_id = "rg_x"
            title = "In progress thing"
            plan_title = ""
            step_titles = ("a",)
            step_states = ("IN_PROGRESS",)
            blocker_summary = ""
            staleness_reason = ""

        out = capture_goal_experience_from_terminal_state(
            FakeSnap(), outcome="IN_PROGRESS"
        )
        self.assertEqual(out.status, ExperienceResultStatus.REJECTED)
        self.assertEqual(list_experiences("alice").experiences, ())

    def test_16_plan_decision_advisory_only(self):
        self._create(title="Improve laptop thermals", source_goal_id="rg_th")
        with patch(
            "orchestration.conversation.personal_memory.search_personal_memories",
            return_value=[],
        ), patch(
            "orchestration.conversation.personal_memory.list_personal_memories",
            return_value=[],
        ):
            q = "Plan next steps for improve laptop thermals"
            pin = assemble_plan_input(_plan(), q)
            self.assertTrue(pin.question.lower().startswith("plan next"))
            self.assertTrue(any("past:" in p.lower() for p in pin.preferences))

            dq = "Should I improve laptop thermals now or later?"
            din = assemble_decision_input(_plan(), dq)
            self.assertTrue(din.question.lower().startswith("should i"))
            self.assertNotEqual(" ".join(din.preferences).lower(), din.question.lower())

    def test_17_respond_ux_does_not_call_models(self):
        self._create(title="Respond path check", source_goal_id="rg_rp")
        plan = GoalPlan(
            plan_id="p1",
            goal_id="g1",
            schema_version="v82.1",
            owner_id="alice",
            session_id="s1",
            computer_session_id="",
            steps=(
                PlanStep(
                    step_id="s1",
                    capability_id="conversation",
                    action="RESPOND",
                    parameters=(("text", "Show my recent goal experiences."),),
                    dependencies=(),
                    verification_required=False,
                    verification_type="",
                    risk="LOW",
                    approval_required=False,
                    retry_count=0,
                    timeout_ms=1000,
                ),
            ),
            plan_risk="LOW",
            approval_required=False,
            provenance="GOAL_PLAN",
            plan_hash="x",
            execution_permitted=False,
            approved=False,
        )
        with patch.object(
            __import__("models.ollama_provider", fromlist=["OllamaProvider"]).OllamaProvider,
            "generate",
            create=True,
        ) as gen:
            status, text = execute_respond(plan.steps[0], plan)
        self.assertIn(status, ("COMPLETED", "OK", "SUCCESS"))
        self.assertIn("Respond path", text)
        gen.assert_not_called()

    def test_18_forget_no_cascade(self):
        self._create(title="Cascade check", source_goal_id="rg_cc")
        with patch(
            "orchestration.plan.goal_registry.transition_goal", create=True
        ) as tg, patch(
            "orchestration.user_model.store.forget_profile_entry", create=True
        ) as fp, patch(
            "orchestration.conversation.personal_memory.delete_personal_memory",
            create=True,
        ) as dm:
            handle_experience_request("alice", "s1", "Forget that goal experience.")
            handle_experience_request("alice", "s1", "yes")
        tg.assert_not_called()
        fp.assert_not_called()
        dm.assert_not_called()

    def test_19_resolver_fail_closed_no_write(self):
        with patch(
            "orchestration.experience.store.list_experiences",
            side_effect=RuntimeError("db down"),
        ), patch(
            "orchestration.experience.store.create_experience"
        ) as c:
            res = get_relevant_goal_experiences("alice", "DECISION", "anything")
        self.assertEqual(res.status, ExperienceResultStatus.UNAVAILABLE)
        c.assert_not_called()

    def test_20_confirm_state_does_not_accumulate(self):
        a = self._create(title="First", source_goal_id="rg_1")
        handle_experience_request("alice", "s1", "Forget that goal experience.")
        p1 = get_experience_confirm("alice", "s1")
        self.assertIsNotNone(p1)
        clear_experience_confirm("alice", "s1")
        reset_experience_for_tests()
        b = self._create(title="Second", source_goal_id="rg_2")
        handle_experience_request("alice", "s1", "Forget that goal experience.")
        p2 = get_experience_confirm("alice", "s1")
        self.assertIsNotNone(p2)
        self.assertEqual(p2.experience_id, b.experience_id)
        self.assertNotEqual(p2.experience_id, a.experience_id)


if __name__ == "__main__":
    unittest.main()
