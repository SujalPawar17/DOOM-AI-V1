#!/usr/bin/env python
"""DOOM V13.1 — Production Cognitive OS controlled-rollout tests (no audio, HARD $0)."""

import os
import statistics
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"
ROOT = os.path.abspath(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from test_v11_8_end_to_end_integration import (  # noqa: E402
    V118TestBase, make_plan, make_step, planning_for, COMPUTER_SESSION, OWNER, SESSION,
)
from test_v12_1_response_intelligence import FakeLocalModel  # noqa: E402
import core.commands as commands  # noqa: E402
from core.v12.adaptive_learning import AdaptiveLearning, LearningStore  # noqa: E402
from core.v12.cognitive_orchestrator import V12CognitiveOrchestrator  # noqa: E402
from core.v12.doom_os import DoomOS  # noqa: E402
from core.v12.integrations import ConnectorSandbox  # noqa: E402
from core.v13 import cognitive_os_rollout as rollout_mod  # noqa: E402
from core.v13.cognitive_os_rollout import CognitiveOSRollout, OSState, SAFE_FAILURE_TEXT  # noqa: E402
from orchestration.conversation.respond import use_respond_provider_for_tests, reset_respond_provider_for_tests  # noqa: E402

METRICS = {}


class RolloutBase(V118TestBase):
    def setUp(self):
        super().setUp()
        use_respond_provider_for_tests(FakeLocalModel(["Here you go."] * 100))
        self.box = ConnectorSandbox(OWNER).__enter__()
        self.created = []

        def factory():
            d = DoomOS(owner_id=OWNER, session_id=SESSION, registry=self.box.registry,
                       orchestrator=V12CognitiveOrchestrator(learning=AdaptiveLearning(store=LearningStore())))
            self.created.append(d)
            return d

        self.factory = factory
        self.rollout = CognitiveOSRollout(factory=factory)
        self.spoken = []
        self.v8 = MagicMock(return_value="v8 core reply")
        self.patches = [
            patch.object(commands, "speak", side_effect=lambda t, lang=None: self.spoken.append(t)),
            patch.object(commands, "stop_speaking"),
            patch.object(commands.doom_core, "process_request", self.v8),
            patch.object(rollout_mod, "get_rollout", return_value=self.rollout),
            patch.dict(os.environ, {"DOOM_COGNITIVE_OS": "1"}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.rollout.shutdown()
        self.box.__exit__(None, None, None)
        reset_respond_provider_for_tests()
        super().tearDown()

    def say(self, text):
        with self.spy.hooks():
            return commands.submit_user_input(text, "en", source="voice")


class TestModes(RolloutBase):

    def test_off_is_unchanged_v8(self):
        with patch.dict(os.environ, {"DOOM_COGNITIVE_OS": ""}):
            self.assertEqual(self.say("hello"), "v8 core reply")
        self.assertEqual(self.created, [], "DoomOS is never built in off mode")
        self.assertEqual(self.spoken, ["v8 core reply"])

    def test_on_routes_to_cognitive_os_with_single_voice_response(self):
        t0 = time.perf_counter()
        first = self.say("What is the capital of France?")
        cold_ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(first, "Here you go.")
        self.assertEqual(self.spoken, ["Here you go."], "exactly one spoken response")
        self.v8.assert_not_called()
        steady = []
        for i in range(20):
            t = time.perf_counter()
            self.say(f"Explain topic {i}")
            steady.append((time.perf_counter() - t) * 1000)
        self.assertEqual(len(self.created), 1, "single DoomOS instance")
        self.assertEqual(len(self.spoken), 21)
        st = self.rollout.status()
        self.assertEqual((st["state"], st["init_count"], st["routes"]["cognitive"]), ("READY", 1, 21))
        METRICS.update(init_ms=st["init_ms"], first_request_ms=round(cold_ms, 1),
                       steady_p50_ms=round(statistics.median(steady), 1), steady_max_ms=round(max(steady), 1))


class TestFlows(RolloutBase):

    def _plan_reply(self, plan, text):
        orch_patch = None
        try:
            with self.spy.hooks():
                self.rollout._ensure_os()
                doom = self.created[0]
                with patch.object(doom.orchestrator, "planning_integration", return_value=planning_for(plan)), \
                        patch("orchestration.authorization.verified_computer_session_id",
                              side_effect=lambda o, cs: (cs, "OK")):
                    return commands.submit_user_input(text, "en", source="text")
        finally:
            pass

    def test_successful_failed_approval_and_cost_blocked_plans(self):
        ok = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "x")])], goal_id="ag_v131_ok")
        self.assertEqual(self._plan_reply(ok, "do it"), "Deterministic local response.")
        self.spy.overrides["conversation"] = "STEP_FAILED"
        bad = make_plan([make_step("s1", "conversation", "RESPOND", [("text", "y")])], goal_id="ag_v131_bad")
        self.assertIn("couldn't complete", self._plan_reply(bad, "do the other"))
        self.spy.overrides.pop("conversation")
        click = make_plan([make_step("s1", "computer", "CLICK", [("name", "Save")], risk="MEDIUM", approval=True)],
                          goal_id="ag_v131_auth", plan_risk="MEDIUM", approval=True,
                          computer_session_id=COMPUTER_SESSION)
        reply = self._plan_reply(click, "click save")
        self.assertIn("needs your approval", reply)
        self.assertNotIn(("computer", "CLICK"), self.spy.calls, "no action before approval")
        cost = make_plan([make_step("s1", "world_act", "RUN", [("title", "x")], risk="HIGH")],
                         goal_id="ag_v131_cost", plan_risk="HIGH")
        self.assertIn("zero-cost policy", self._plan_reply(cost, "run job"))
        self.assertEqual(self.spy.calls.count(("conversation", "RESPOND")), 2)
        self.v8.assert_not_called()
        self.assertEqual(len(self.spoken), 4, "one spoken response per input")

    def test_connector_operation(self):
        self.assertEqual(self.say("git status"), "The repository is clean.")


class TestFailureContainment(RolloutBase):

    def test_init_failure_falls_back_to_v8_with_backoff(self):
        calls = []

        def broken():
            calls.append(1)
            raise RuntimeError("init failed")

        clock = [0.0]
        self.rollout = CognitiveOSRollout(factory=broken, clock=lambda: clock[0], backoff_s=60)
        with patch.object(rollout_mod, "get_rollout", return_value=self.rollout):
            self.assertEqual(self.say("hello"), "v8 core reply")
            self.assertEqual(self.say("hello again"), "v8 core reply")
            self.assertEqual(len(calls), 1, "no re-initialization storm inside the backoff window")
            clock[0] += 61
            self.say("later")
            self.assertEqual(len(calls), 2)
        st = self.rollout.status()
        self.assertEqual((st["state"], st["last_error"], st["routes"]["v8_fallback"]), ("DEGRADED", "RuntimeError", 3))
        self.assertEqual(len(self.spoken), 3)

    def test_mid_request_failure_never_reruns_on_v8(self):
        self.rollout._ensure_os()
        with patch.object(self.created[0], "handle_text", side_effect=RuntimeError("boom")):
            reply = self.say("do something")
        self.assertEqual(reply, SAFE_FAILURE_TEXT)
        self.v8.assert_not_called()
        self.assertEqual(self.spoken, [SAFE_FAILURE_TEXT])
        self.assertEqual(self.rollout.status()["routes"]["cognitive_error"], 1)


class TestLifecycle(RolloutBase):

    def test_concurrent_first_use_builds_one_instance(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.rollout._ensure_os()[0])) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.created), 1)
        self.assertEqual(len({id(r) for r in results}), 1)

    def test_shutdown_restart_and_repeated_initialization_leave_no_threads(self):
        baseline = threading.active_count()
        for i in range(5):
            self.say(f"hello {i}")
            self.created[-1].start()  # proactive runtime thread, as a production boot might
            self.rollout.restart()
            self.assertEqual(self.rollout.status()["state"], "NOT_INITIALIZED")
        time.sleep(0.2)
        self.assertEqual(len(self.created), 5)
        self.assertLessEqual(threading.active_count(), baseline)
        self.say("after restarts")
        self.assertEqual(self.rollout.status()["state"], "READY")

    def test_rollout_never_starts_a_monitor(self):
        self.say("hello")
        self.assertIsNone(self.created[0].monitor)
        self.assertFalse(self.created[0].runtime.running)


def _print_metrics():
    if METRICS:
        print("\nV13.1 METRICS", METRICS)


if __name__ == "__main__":
    import atexit
    atexit.register(_print_metrics)
    unittest.main(verbosity=2)
