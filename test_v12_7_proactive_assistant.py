#!/usr/bin/env python
"""DOOM V12.7 — Proactive Personal Assistant tests (every action class; HARD $0)."""

import os
import sys
import time
import unittest
from types import SimpleNamespace

os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.cost_guard.types import ResourceType  # noqa: E402
from core.v12.integrations import (  # noqa: E402
    ActionSpec, Connector, ConnectorRequest, ConnectorSandbox, ConnectorSpec, ConnectorStatus,
    IntegrationIdentity, ParamSpec,
)
from core.v12.proactive_assistant import (  # noqa: E402
    ActionClass, AssistantPolicy, ProactiveAssistant, SuggestionKind, SuggestionState, classify_action,
)
from core.v12.adaptive_learning import AdaptiveLearning, LearningStore  # noqa: E402
from orchestration.experience.store import use_test_experience_store, reset_experience_for_tests  # noqa: E402

OWNER, SESSION = "pa_owner", "pa_sess"
ME = IntegrationIdentity(OWNER, SESSION)
DAY = 86400.0


class Clock:
    def __init__(self, t=time.mktime((2026, 10, 6, 8, 0, 0, 0, 0, -1))):
        self.t = t

    def __call__(self):
        return self.t


class RiskyConnector(Connector):
    """Test connector with HIGH-risk and IRREVERSIBLE actions on an in-memory store."""

    def __init__(self):
        self.store = {"old_logs": "x" * 10}
        self.deploys = 0
        self.verify_purge = True
        self.spec = ConnectorSpec(
            connector_id="ops", capability="ops", description="test ops",
            actions=(
                ActionSpec("deploy", (), risk="HIGH", mutates=True, idempotent=False),
                ActionSpec("purge", (ParamSpec("name", str),), risk="MEDIUM", mutates=True, idempotent=False,
                           reversible=False, verification="item_absent"),
            ),
            cost_resource=(ResourceType.OTHER, "local_filesystem", ""), permissions=("ops",))

    def execute(self, action, args, identity):
        if action == "deploy":
            self.deploys += 1
            return {"deployed": self.deploys}
        self.store.pop(args["name"], None)
        return {"purged": args["name"]}

    def verify(self, action, args, output, identity):
        if action == "purge":
            return self.verify_purge and args["name"] not in self.store
        return True


class Base(unittest.TestCase):
    def setUp(self):
        use_test_experience_store(True)
        reset_experience_for_tests()
        self.box = ConnectorSandbox(OWNER).__enter__()
        self.ops = RiskyConnector()
        self.box.registry.register(self.ops)
        self.clock = Clock()
        self.learning = AdaptiveLearning(store=LearningStore(), clock=self.clock)
        self.goal = None
        self.pa = ProactiveAssistant(self.box.gateway, learning=self.learning, clock=self.clock,
                                     goal_reader=lambda owner: self.goal if owner == OWNER else None)

    def tearDown(self):
        self.box.__exit__(None, None, None)
        reset_experience_for_tests()

    def propose(self, connector, action, **args):
        return self.pa.propose(OWNER, SESSION, ConnectorRequest.make(OWNER, SESSION, connector, action, args), "do it")


class TestClassification(unittest.TestCase):

    def test_classes(self):
        self.assertEqual(classify_action(None), ActionClass.INFORMATIONAL)
        self.assertEqual(classify_action(ActionSpec("r", (), risk="LOW")), ActionClass.LOW_RISK)
        self.assertEqual(classify_action(ActionSpec("w", (), risk="LOW", mutates=True)), ActionClass.MEDIUM_RISK)
        self.assertEqual(classify_action(ActionSpec("w", (), risk="MEDIUM")), ActionClass.MEDIUM_RISK)
        self.assertEqual(classify_action(ActionSpec("d", (), risk="HIGH")), ActionClass.HIGH_RISK)
        self.assertEqual(classify_action(ActionSpec("p", (), risk="LOW", mutates=True, reversible=False)),
                         ActionClass.IRREVERSIBLE)


class TestActionClasses(Base):

    def test_informational_is_delivered_automatically_without_action(self):
        self.pa.note_change(OWNER, SESSION, "disk_low", "disk space is below 5 GB")
        [s] = self.pa.run_cycle(OWNER, SESSION)
        self.assertEqual((s.action_class, s.state), (ActionClass.INFORMATIONAL, SuggestionState.DELIVERED))
        self.assertIsNone(s.request)

    def test_low_risk_is_only_suggested_by_default(self):
        s = self.propose("git", "status")
        self.assertEqual((s.action_class, s.state), (ActionClass.LOW_RISK, SuggestionState.PROPOSED))
        done = self.pa.respond(s.suggestion_id, ME, accept=True)
        self.assertEqual(done.state, SuggestionState.EXECUTED)
        self.assertTrue(done.verified)

    def test_low_risk_auto_executes_only_when_configured(self):
        self.pa.set_policy(OWNER, AssistantPolicy(auto_low_risk=True))
        s = self.propose("git", "status")
        self.assertEqual(s.state, SuggestionState.AUTO_EXECUTED)
        self.assertEqual(s.result_status, "SUCCESS")

    def test_medium_risk_requires_approval_even_with_auto(self):
        self.pa.set_policy(OWNER, AssistantPolicy(auto_low_risk=True))
        s = self.propose("filesystem", "write_text", path="plan.txt", content="draft")
        self.assertEqual((s.action_class, s.state), (ActionClass.MEDIUM_RISK, SuggestionState.PROPOSED))
        self.assertFalse(os.path.exists(os.path.join(self.box.workspace, "plan.txt")))
        declined = self.pa.respond(s.suggestion_id, ME, accept=False)
        self.assertEqual(declined.state, SuggestionState.DECLINED)
        self.assertFalse(os.path.exists(os.path.join(self.box.workspace, "plan.txt")))

        s2 = self.propose("filesystem", "write_text", path="plan2.txt", content="draft")
        done = self.pa.respond(s2.suggestion_id, ME, accept=True)
        self.assertEqual(done.state, SuggestionState.EXECUTED)
        self.assertTrue(done.verified)
        # Responding again never re-executes.
        self.assertEqual(self.pa.respond(s2.suggestion_id, ME, accept=True).state, SuggestionState.EXECUTED)

    def test_high_risk_always_needs_explicit_authorization(self):
        self.pa.set_policy(OWNER, AssistantPolicy(auto_low_risk=True))
        s = self.propose("ops", "deploy")
        self.assertEqual((s.action_class, s.state), (ActionClass.HIGH_RISK, SuggestionState.PROPOSED))
        self.assertEqual(self.ops.deploys, 0)
        # A direct gateway call without authorization is refused.
        self.assertEqual(self.box.gateway.invoke(s.request, ME).status, ConnectorStatus.APPROVAL_REQUIRED)
        self.assertEqual(self.ops.deploys, 0)
        done = self.pa.respond(s.suggestion_id, ME, accept=True)
        self.assertEqual(done.state, SuggestionState.EXECUTED)
        self.assertEqual(self.ops.deploys, 1)

    def test_irreversible_needs_confirmation_and_verification(self):
        s = self.propose("ops", "purge", name="old_logs")
        self.assertEqual(s.action_class, ActionClass.IRREVERSIBLE)
        refused = self.pa.respond(s.suggestion_id, ME, accept=True)
        self.assertEqual((refused.state, refused.result_status),
                         (SuggestionState.REFUSED, "IRREVERSIBLE_CONFIRMATION_REQUIRED"))
        self.assertIn("old_logs", self.ops.store)

        s2 = self.propose("ops", "purge", name="old_logs")
        done = self.pa.respond(s2.suggestion_id, ME, accept=True, confirm_irreversible=True)
        self.assertEqual(done.state, SuggestionState.EXECUTED)
        self.assertTrue(done.verified)
        self.assertNotIn("old_logs", self.ops.store)

    def test_irreversible_without_verification_is_failure(self):
        self.ops.verify_purge = False
        s = self.propose("ops", "purge", name="old_logs")
        done = self.pa.respond(s.suggestion_id, ME, accept=True, confirm_irreversible=True)
        self.assertEqual(done.state, SuggestionState.FAILED)
        self.assertEqual(done.result_status, ConnectorStatus.VERIFICATION_FAILED.value)

    def test_wrong_identity_cannot_respond(self):
        s = self.propose("ops", "deploy")
        with self.assertRaises(PermissionError):
            self.pa.respond(s.suggestion_id, IntegrationIdentity("intruder", SESSION), accept=True)
        with self.assertRaises(PermissionError):
            self.pa.respond(s.suggestion_id, IntegrationIdentity(OWNER, "other"), accept=True)
        self.assertEqual(self.ops.deploys, 0)

    def test_undeclared_actions_are_never_proposed(self):
        self.assertIsNone(self.propose("git", "push"))
        self.assertIsNone(self.propose("nope", "x"))


class TestDetectors(Base):

    def test_stale_goal_reminder_and_next_step(self):
        self.goal = SimpleNamespace(goal_id="g1", title="Launch website", last_active_at=self.clock.t - 5 * DAY,
                                    step_titles=("Write copy", "Deploy"), step_states=("COMPLETED", "PENDING"))
        kinds = {s.kind: s for s in self.pa.run_cycle(OWNER, SESSION)}
        self.assertIn("5 days", kinds[SuggestionKind.REMINDER].message)
        self.assertEqual(kinds[SuggestionKind.NEXT_ACTION].message, "Next step for 'Launch website': Deploy.")
        self.assertTrue(all(s.action_class is ActionClass.INFORMATIONAL for s in kinds.values()))

    def test_stalled_workflow_from_learned_failures(self):
        exps = [SimpleNamespace(experience_id=f"e{i}", owner_id=OWNER, outcome="ABANDONED",
                                step_summary=("filesystem WRITE_TEXT status:STEP_FAILED",), tags=(),
                                blockers=("failed_step:s1",), created_at=self.clock.t - i * 3600)
                for i in range(3)]
        self.learning.observe_experiences(OWNER, exps)
        [s] = [s for s in self.pa.run_cycle(OWNER, SESSION) if s.kind is SuggestionKind.STALLED_WORKFLOW]
        self.assertIn("keeps failing", s.message)

    def test_habit_prepares_information_with_low_risk_action(self):
        exps = [SimpleNamespace(experience_id=f"h{i}", owner_id=OWNER, outcome="COMPLETED",
                                step_summary=("git STATUS status:SUCCESS",), tags=(), blockers=(),
                                created_at=time.mktime((2026, 10, 1 + i, 8, 30, 0, 0, 0, -1))) for i in range(3)]
        self.learning.observe_experiences(OWNER, exps)
        [s] = [s for s in self.pa.run_cycle(OWNER, SESSION) if s.kind is SuggestionKind.PREPARE_INFO]
        self.assertEqual((s.action_class, s.state), (ActionClass.LOW_RISK, SuggestionState.PROPOSED))
        self.assertEqual(s.request.connector_id, "git")

    def test_cooldown_bounds_quiet_and_isolation(self):
        for i in range(6):
            self.pa.note_change(OWNER, SESSION, f"evt{i}", f"change {i}")
        first = self.pa.run_cycle(OWNER, SESSION)
        self.assertEqual(len(first), 3, "bounded per cycle")
        self.pa.note_change(OWNER, SESSION, "evt0", "change 0")
        self.assertEqual(self.pa.run_cycle(OWNER, SESSION), [], "cooldown suppresses repeats")
        self.assertEqual(self.pa.inbox("someone_else"), [])
        self.pa.set_policy(OWNER, AssistantPolicy(quiet=True))
        self.assertIsNone(self.propose("git", "status"), "quiet mode proposes no actions")
        self.pa.note_change(OWNER, SESSION, "fresh", "new change")
        self.assertEqual(len(self.pa.run_cycle(OWNER, SESSION)), 1, "informational still delivered")

    def test_inbox_is_bounded(self):
        for i in range(100):
            self.clock.t += 1
            self.propose("git", "status")
        self.assertLessEqual(len(self.pa.inbox(OWNER)), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
