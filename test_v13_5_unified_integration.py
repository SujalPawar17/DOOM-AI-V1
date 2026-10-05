#!/usr/bin/env python
"""DOOM V13.5 — Unified integration: one DoomOS lifecycle through the V13.1 production
entry point (core.commands -> V13 rollout -> DoomOS), covering flows A–L, plus
long-session, concurrency and isolation checks. HARD $0, no audio, no real desktop."""

import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from test_v13_1_production_os import RolloutBase  # noqa: E402
from test_v11_8_end_to_end_integration import make_plan, make_step, planning_for, COMPUTER_SESSION, OWNER, SESSION  # noqa: E402
from test_v13_2_multimodal_understanding import NoVision, docx, png  # noqa: E402
from core.v12.doom_os import MAX_PENDING_ROUTES  # noqa: E402
from core.v12.multimodal import MAX_ITEMS_PER_SESSION  # noqa: E402
from core.v12.proactive_assistant import SuggestionKind  # noqa: E402
from core.v12.runtime import EventPriority, RuntimeEvent  # noqa: E402
from orchestration.user_model.confirm import reset_profile_confirms_for_tests  # noqa: E402
from orchestration.user_model.store import reset_user_model_for_tests, use_test_user_model_store  # noqa: E402
from core.v13.multimodal_understanding import (  # noqa: E402
    DocumentUnderstanding, ImageUnderstanding, Status, document_percept, image_percept,
)

PIPELINE = ("input_normalization", "context_fusion", "memory_user_model", "goal_understanding",
            "reasoning_decision", "planning", "cost_guard_check", "authorization", "execution",
            "verification", "response_generation", "voice_output", "outcome_memory_update",
            "experience_integration", "perception", "adaptive_learning")


class LifecycleBase(RolloutBase):

    def setUp(self):
        super().setUp()
        # Hermetic V8.27 profile: confirmed statements must never reach the real durable store.
        use_test_user_model_store(True)
        reset_profile_confirms_for_tests()
        self.cycles = []

    def tearDown(self):
        super().tearDown()
        reset_profile_confirms_for_tests()
        reset_user_model_for_tests()
        use_test_user_model_store(False)

    def os_(self):
        """The DoomOS instance the rollout built (built on first use), with cycle recording."""
        if not self.created:
            self.say("hello")
        doom = self.created[-1]
        orch = doom.orchestrator
        if not getattr(orch, "_v135_recorded", False):
            original = orch.process_cognitive_cycle

            def recorded(*a, **k):
                result = original(*a, **k)
                self.cycles.append(result)
                return result
            orch.process_cognitive_cycle = recorded
            orch._v135_recorded = True
        return doom

    def plan_reply(self, plan, text):
        doom = self.os_()
        with patch.object(doom.orchestrator, "planning_integration", return_value=planning_for(plan)), \
                patch("orchestration.authorization.verified_computer_session_id", side_effect=lambda o, cs: (cs, "OK")):
            return self.say(text)

    def last_stages(self):
        return self.cycles[-1]["stages"]


class TestLifecycleFlows(LifecycleBase):

    def test_flows_a_to_l_in_one_lifecycle(self):
        doom = self.os_()
        self.spoken.clear()

        # A. Informational: full pipeline, no adapter, one voice response.
        self.assertEqual(self.say("What is the capital of France?"), "Here you go.")
        stages = self.last_stages()
        for stage in PIPELINE:
            self.assertIn(stage, stages, stage)
        self.assertEqual(self.spy.calls, [])

        # B. Successful plan: real executor, real verification, experience recorded.
        ok = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "x")])], goal_id="ag_v135_ok")
        self.assertEqual(self.plan_reply(ok, "summarize my notes"), "Deterministic local response.")
        stages = self.last_stages()
        self.assertTrue(stages["execution"]["executed"])
        self.assertEqual(stages["execution"]["execution_result"].plan_hash, ok.plan_hash)
        self.assertTrue(stages["verification"]["verification_result"]["verified"])
        self.assertEqual(stages["outcome_memory_update"]["cycle_outcome"], "EXECUTION_SUCCEEDED")
        self.assertEqual(self.spy.calls, [("conversation", "RESPOND")])

        # C. Failed plan: reported truthfully, never verified, never "done".
        self.spy.overrides["conversation"] = "STEP_FAILED"
        bad = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "y")])], goal_id="ag_v135_bad")
        reply = self.plan_reply(bad, "do the failing thing")
        self.assertIn("couldn't complete", reply)
        self.assertFalse(self.cycles[-1]["success"])
        self.assertFalse(self.last_stages()["verification"]["verification_result"]["verified"])
        self.assertEqual(self.last_stages()["outcome_memory_update"]["cycle_outcome"], "EXECUTION_FAILED")
        self.spy.overrides.pop("conversation")

        # D. Approval-required computer action: nothing before approval; exactly once after.
        click = make_plan([make_step("s1", "computer", "CLICK", [("name", "Save")], risk="MEDIUM", approval=True)],
                          goal_id="ag_v135_auth", plan_risk="MEDIUM", approval=True,
                          computer_session_id=COMPUTER_SESSION)
        reply = self.plan_reply(click, "click save")
        self.assertIn("needs your approval", reply)
        self.assertNotIn(("computer", "CLICK"), self.spy.calls)
        pending = self.last_stages()["authorization"]["pending_id"]
        self.assertTrue(pending)
        intruder = doom.approve(pending, owner_id="intruder", computer_session_id=COMPUTER_SESSION)
        self.assertEqual(intruder.route, "rejected")
        with self.spy.hooks(), \
                patch.object(doom.orchestrator, "planning_integration", return_value=planning_for(click)), \
                patch("orchestration.authorization.verified_computer_session_id", side_effect=lambda o, cs: (cs, "OK")):
            done = doom.approve(pending, computer_session_id=COMPUTER_SESSION)
            again = doom.approve(pending, computer_session_id=COMPUTER_SESSION)
        self.assertTrue(done.details["success"])
        self.assertEqual(again.route, "rejected", "single-use approval")
        self.assertEqual(self.spy.calls.count(("computer", "CLICK")), 1, "exactly once")

        # E. Cost-blocked: real Cost Guard, no adapter, no execution id.
        cost = make_plan([make_step("s1", "world_act", "RUN", [("title", "x")], risk="HIGH")],
                         goal_id="ag_v135_cost", plan_risk="HIGH")
        calls_before = list(self.spy.calls)
        self.assertIn("zero-cost policy", self.plan_reply(cost, "run the remote job"))
        self.assertTrue(self.last_stages()["cost_guard_check"]["skip_execution"])
        self.assertFalse(self.last_stages()["execution"]["executed"])
        self.assertEqual(self.spy.calls, calls_before)

        # F. Connector operation: declared gateway, verified result.
        self.assertEqual(self.say("git status"), "The repository is clean.")

        # G. Learning statement reaches later cognition.
        # The frozen V8.27 profile layer asks for explicit confirmation (owner+session
        # scoped, short TTL); short inputs are held until YES/NO, so the user answers it.
        self.assertIn("Confirm? Yes/No", self.say("I prefer Python over Ruby"))
        self.assertEqual(self.say("What is the time?"), "Please reply YES to confirm or NO to cancel.")
        self.assertNotIn("Please reply", self.say("yes"))
        self.say("Which language should I use?")
        fused = self.last_stages()["context_fusion"]["fused_context"].context
        self.assertEqual(fused.get("learned_preference_choice_python_vs_ruby"), "Python")

        # H. Image understanding: measured analysis enters Context Fusion (no fake semantics).
        image = png((30, 60, 200))
        analysis = ImageUnderstanding(vision=NoVision()).analyze(image, describe=True)
        self.assertEqual(analysis.status, Status.OK)
        self.assertTrue(doom.orchestrator.perceive(
            image_percept(doom.orchestrator.normalizer, OWNER, SESSION, image, analysis)))
        self.say("What did I just share?")
        fused = self.last_stages()["context_fusion"]["fused_context"].context
        self.assertIn("low-level analysis", fused.get("percept_1", ""))

        # I. Document understanding: extracted, redacted text enters Context Fusion.
        data = docx(["Agenda: launch review", "api_key=sk_live_ABCDEF1234567890"])
        extraction = DocumentUnderstanding().extract("agenda.docx", data)
        self.assertEqual(extraction.status, Status.OK)
        doom.orchestrator.perceive(document_percept(doom.orchestrator.normalizer, OWNER, SESSION, extraction, data))
        self.say("Summarize the document I shared.")
        fused = self.last_stages()["context_fusion"]["fused_context"].context
        joined = " ".join(str(v) for k, v in fused.items() if str(k).startswith("percept_"))
        self.assertIn("Agenda: launch review", joined)
        self.assertNotIn("sk_live_ABCDEF1234567890", joined)

        # J. Runtime event processed by the real runtime (cognitive event handler).
        calls_before = list(self.spy.calls)
        doom.runtime.submit(RuntimeEvent.make(OWNER, SESSION, "system", "battery_low", "battery low",
                                              EventPriority.HIGH))
        # K. Proactive: the event becomes an assistant suggestion; nothing executes.
        with self.spy.hooks():
            self.assertEqual(doom.tick(), 1)
        self.assertTrue(any(e["event_type"] == "battery_low" for e in doom.runtime.outbox(OWNER, SESSION)))
        self.assertTrue(any(s.kind is SuggestionKind.CHANGE_DETECTED
                            for s in doom.assistant.inbox(OWNER, SESSION)))
        self.assertEqual(self.spy.calls, calls_before, "proactive path never executes")

        # Delivery: exactly one spoken response per user input (A,B,C,D,E,F,G x4,H,I = 12).
        self.assertEqual(len(self.spoken), 12)
        self.v8.assert_not_called()

        # L. Restart / reinitialization: fresh instance, no stale approvals, no thread growth.
        baseline = threading.active_count()
        self.rollout.restart()
        self.assertEqual(self.say("hello again"), "Here you go.")
        self.assertEqual(len(self.created), 2)
        self.assertIsNot(self.created[-1], doom)
        self.assertEqual(self.created[-1].approve(pending).route, "rejected", "no approval survives restart")
        time.sleep(0.2)
        self.assertLessEqual(threading.active_count(), baseline)


class TestIsolationAndBounds(LifecycleBase):

    def test_cross_owner_and_cross_session_isolation(self):
        doom = self.os_()
        image = png((200, 30, 30))
        analysis = ImageUnderstanding(vision=NoVision()).analyze(image)
        doom.orchestrator.perceive(image_percept(doom.orchestrator.normalizer, OWNER, SESSION, image, analysis))
        doom.handle_text("I prefer tea over coffee")
        with self.spy.hooks():
            other = doom.orchestrator.process_cognitive_cycle("Which drink?", "other_owner", SESSION)
            other_session = doom.orchestrator.process_cognitive_cycle("Which drink?", OWNER, "other_session")
        for cycle in (other, other_session):
            fused = cycle["stages"]["context_fusion"]["fused_context"].context
            self.assertFalse([k for k in fused if str(k).startswith("percept_")], "no cross-scope percepts")
        fused = other["stages"]["context_fusion"]["fused_context"].context
        self.assertNotIn("learned_preference_choice_tea_vs_coffee", fused, "no cross-owner learning")

    def test_long_session_bounds(self):
        doom = self.os_()
        baseline = threading.active_count()
        click = make_plan([make_step("s1", "computer", "CLICK", [("name", "Save")], risk="MEDIUM", approval=True)],
                          goal_id="ag_v135_long", plan_risk="MEDIUM", approval=True,
                          computer_session_id=COMPUTER_SESSION)
        for i in range(MAX_PENDING_ROUTES + 20):
            self.plan_reply(click, f"click save {i}")
        self.assertLessEqual(len(doom._pending), MAX_PENDING_ROUTES)
        for i in range(MAX_ITEMS_PER_SESSION + 10):
            img = png((i % 255, 10, 10), size=(16, 16))
            a = ImageUnderstanding(vision=NoVision()).analyze(img)
            doom.orchestrator.perceive(image_percept(doom.orchestrator.normalizer, OWNER, SESSION, img, a))
        self.assertLessEqual(len(doom.orchestrator.perception.items(OWNER, SESSION)), MAX_ITEMS_PER_SESSION)
        for i in range(300):
            doom.runtime.submit(RuntimeEvent.make(OWNER, SESSION, "system", "tick", f"t{i}", EventPriority.LOW))
        with self.spy.hooks():
            for _ in range(20):
                doom.tick()
        self.assertEqual(doom.runtime.queue_size(OWNER), 0)
        self.assertNotIn(("computer", "CLICK"), self.spy.calls, "nothing ran without approval")
        self.assertLessEqual(threading.active_count(), baseline + 1)

    def test_concurrent_requests_single_instance_single_response_each(self):
        replies, errors = [], []

        def worker(n):
            try:
                with self.spy.hooks():
                    replies.append(self.rollout.route(f"question {n}", "en", "text", self.v8).route)
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(repr(exc))
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(replies, ["cognitive"] * 8)
        self.assertEqual(len(self.created), 1, "one DoomOS instance under concurrent first use")
        self.v8.assert_not_called()
        self.assertEqual(self.spy.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
