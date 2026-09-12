"""V8.3 bounded planner tests. Proposal only. Voice/STT untouched."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")
os.environ.setdefault("PROACTIVE_COMPUTER_ENABLED", "false")

from orchestration.goal.kernel import process_goal
from orchestration.goal.plan_registry import MAX_DEPENDENCY_DEPTH, MAX_STEPS
from orchestration.goal.planner import MAX_PLANNING_ATTEMPTS, plan_goal, planner_bounds
from orchestration.goal.planner_errors import PlannerStatus


def _v8_on():
    os.environ["PROACTIVE_V8_ENABLED"] = "true"


def _v8_off():
    os.environ["PROACTIVE_V8_ENABLED"] = "false"


def _computer_on():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "true"
    os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "true"


def _computer_off():
    os.environ["PROACTIVE_COMPUTER_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_CLICK_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_OBSERVE_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_BROWSER_ENABLED"] = "false"
    os.environ["PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"] = "false"


class TestV83BoundedPlanner(unittest.TestCase):
    def setUp(self):
        _v8_off()
        _computer_off()

    def tearDown(self):
        _v8_off()
        _computer_off()

    def test_disabled(self):
        _v8_on()
        goal = process_goal("hello").goal
        _v8_off()
        out = plan_goal(goal)
        self.assertEqual(out.status, PlannerStatus.V8_DISABLED)
        self.assertIsNone(out.plan)

    def test_conversation_deterministic(self):
        _v8_on()
        goal = process_goal("hello").goal
        a = plan_goal(goal)
        b = plan_goal(goal)
        self.assertEqual(a.status, PlannerStatus.SUCCESS)
        self.assertEqual(a.plan.plan_hash, b.plan.plan_hash)
        self.assertEqual(a.plan.steps[0].capability_id, "conversation")
        self.assertEqual(a.plan.steps[0].action, "RESPOND")
        self.assertFalse(a.plan.execution_permitted)
        self.assertFalse(a.plan.approved)
        self.assertLessEqual(len(a.plan.steps), MAX_STEPS)
        self.assertEqual(a.attempts, MAX_PLANNING_ATTEMPTS)

    def test_memory_read_plan(self):
        _v8_on()
        goal = process_goal("read my saved memory about X").goal
        out = plan_goal(goal)
        self.assertEqual(out.status, PlannerStatus.SUCCESS)
        step = out.plan.steps[0]
        self.assertEqual(step.capability_id, "memory_read")
        self.assertEqual(step.action, "RETRIEVE")
        params = dict(step.parameters)
        self.assertIn("query", params)
        self.assertNotIn("command", params)
        self.assertFalse(out.plan.execution_permitted)

    def test_unknown_ambiguous_invalid(self):
        _v8_on()
        unknown = plan_goal(process_goal("please handle this").goal)
        self.assertEqual(unknown.status, PlannerStatus.UNSUPPORTED_INTENT)
        amb = plan_goal(process_goal("open this").goal)
        self.assertEqual(amb.status, PlannerStatus.UNSUPPORTED_INTENT)
        self.assertEqual(plan_goal("not-a-spec").status, PlannerStatus.INVALID_GOAL)

    def test_computer_unavailable_vs_unplannable(self):
        _v8_on()
        _computer_off()
        off = plan_goal(process_goal("open notepad").goal)
        self.assertEqual(off.status, PlannerStatus.CAPABILITY_UNAVAILABLE)
        _computer_on()
        on = plan_goal(process_goal("open notepad").goal)
        self.assertEqual(on.status, PlannerStatus.PLANNING_UNAVAILABLE)
        self.assertIsNone(on.plan)

    def test_browser_fs_world_not_faked(self):
        _v8_on()
        os.environ["PROACTIVE_COMPUTER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_BROWSER_ENABLED"] = "true"
        os.environ["PROACTIVE_COMPUTER_FILESYSTEM_ENABLED"] = "true"
        os.environ["PROACTIVE_ENABLED"] = "true"
        os.environ["PROACTIVE_ACT_ENABLED"] = "true"
        os.environ["PROACTIVE_ACT_CALENDAR_HOLD_ENABLED"] = "true"
        self.assertEqual(plan_goal(process_goal("open this website").goal).status, PlannerStatus.PLANNING_UNAVAILABLE)
        self.assertEqual(plan_goal(process_goal("list this folder").goal).status, PlannerStatus.PLANNING_UNAVAILABLE)
        self.assertEqual(plan_goal(process_goal("create a calendar hold").goal).status, PlannerStatus.PLANNING_UNAVAILABLE)
        os.environ.pop("PROACTIVE_ENABLED", None)
        os.environ.pop("PROACTIVE_ACT_ENABLED", None)
        os.environ.pop("PROACTIVE_ACT_CALENDAR_HOLD_ENABLED", None)

    def test_no_capability_escalation(self):
        _v8_on()
        conv = plan_goal(process_goal("hello").goal).plan
        caps = {s.capability_id for s in conv.steps}
        self.assertEqual(caps, {"conversation"})
        self.assertNotIn("filesystem", caps)
        mem = plan_goal(process_goal("read my saved memory about X").goal).plan
        self.assertEqual({s.capability_id for s in mem.steps}, {"memory_read"})
        self.assertNotIn("filesystem", {s.action for s in mem.steps})

    def test_no_invented_actions_or_tools(self):
        _v8_on()
        plan = plan_goal(process_goal("hello").goal).plan
        blob = str(plan.as_public())
        for token in ("ALL_TOOLS", "TaskEngine", "subprocess", "shell", "CLICK", "DELETE_FILE"):
            self.assertNotIn(token, blob)
        self.assertEqual(plan.steps[0].action, "RESPOND")

    def test_risk_and_approval_not_suppressed(self):
        _v8_on()
        plan = plan_goal(process_goal("hello").goal).plan
        self.assertEqual(plan.plan_risk, "LOW")
        self.assertFalse(plan.approved)
        self.assertFalse(plan.execution_permitted)

    def test_verification_not_claimed_success(self):
        _v8_on()
        pub = plan_goal(process_goal("hello").goal).as_public()
        self.assertNotIn("VERIFIED", str(pub))
        self.assertFalse(pub["execution_permitted"])

    def test_bounds(self):
        b = planner_bounds()
        self.assertEqual(b["max_plan_steps"], 16)
        self.assertEqual(b["max_dependency_depth"], 4)
        self.assertEqual(b["max_planning_attempts"], 1)
        self.assertEqual(MAX_STEPS, 16)
        self.assertEqual(MAX_DEPENDENCY_DEPTH, 4)
        self.assertEqual(MAX_PLANNING_ATTEMPTS, 1)

    def test_proposal_never_executes(self):
        _v8_on()
        out = plan_goal(process_goal("hello").goal)
        self.assertFalse(out.as_public()["execution_permitted"])
        self.assertIsNotNone(out.plan)
        self.assertEqual(len(out.plan.steps[0].dependencies), 0)


if __name__ == "__main__":
    unittest.main()
