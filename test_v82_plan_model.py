"""V8.2 typed plan model tests. Validation only. Voice/STT untouched."""
from __future__ import annotations

import math
import os
import unittest
from dataclasses import FrozenInstanceError, replace

os.environ.setdefault("PROACTIVE_V8_ENABLED", "false")

from orchestration.goal.kernel import process_goal
from orchestration.goal.plan_errors import PlanValidationError
from orchestration.goal.plan_hashing import plan_hash
from orchestration.goal.plan_registry import MAX_STEPS, PLAN_SCHEMA_VERSION
from orchestration.goal.plan_validator import build_goal_plan, validate_plan_hash


def _v8_on():
    os.environ["PROACTIVE_V8_ENABLED"] = "true"


def _goal():
    _v8_on()
    return process_goal("hello").goal


def _step(step_id="s1", capability_id="conversation", action="RESPOND", **kw):
    d = {
        "step_id": step_id,
        "capability_id": capability_id,
        "action": action,
        "parameters": kw.pop("parameters", {}),
        "dependencies": kw.pop("dependencies", ()),
        "verification_required": kw.pop("verification_required", False),
        "verification_type": kw.pop("verification_type", ""),
        "retry_count": kw.pop("retry_count", 0),
        "timeout_ms": kw.pop("timeout_ms", 8000),
    }
    d.update(kw)
    return d


class TestV82PlanModel(unittest.TestCase):
    def test_valid_conversation_plan(self):
        goal = _goal()
        plan = build_goal_plan(goal, [_step()])
        self.assertEqual(plan.schema_version, PLAN_SCHEMA_VERSION)
        self.assertEqual(plan.goal_id, goal.goal_id)
        self.assertFalse(plan.execution_permitted)
        self.assertFalse(plan.approved)
        self.assertEqual(plan.plan_risk, "LOW")
        self.assertFalse(plan.approval_required)
        validate_plan_hash(plan, goal)

    def test_immutable(self):
        plan = build_goal_plan(_goal(), [_step()])
        with self.assertRaises(FrozenInstanceError):
            plan.plan_risk = "LOW"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            plan.steps[0].capability_id = "filesystem"  # type: ignore[misc]
        with self.assertRaises(AttributeError):
            plan.steps[0].parameters.append(("x", "y"))  # type: ignore[attr-defined]

    def test_unknown_capability_and_forbidden_keys(self):
        goal = _goal()
        with self.assertRaises(PlanValidationError) as ctx:
            build_goal_plan(goal, [_step(capability_id="shell", action="RUN")])
        self.assertEqual(ctx.exception.code, "UNKNOWN_CAPABILITY")
        for cap in ("subprocess", "eval", "ALL_TOOLS", "TaskEngine", "computer_tools"):
            with self.assertRaises(PlanValidationError):
                build_goal_plan(goal, [_step(capability_id=cap, action="RUN")])

    def test_forbidden_parameters(self):
        goal = _goal()
        fs = _step(
            capability_id="filesystem", action="READ_FILE",
            parameters={"path": "/tmp/a", "command": "rm"},
        )
        with self.assertRaises(PlanValidationError) as ctx:
            build_goal_plan(goal, [fs])
        self.assertEqual(ctx.exception.code, "FORBIDDEN_PARAMETER")
        with self.assertRaises(PlanValidationError):
            build_goal_plan(goal, [_step(
                capability_id="filesystem", action="READ_FILE",
                parameters={"path": lambda: None},
            )])
        with self.assertRaises(PlanValidationError):
            build_goal_plan(goal, [_step(
                capability_id="filesystem", action="READ_FILE",
                parameters={"path": {"nested": "x"}},
            )])

    def test_cycles_self_and_depth_and_max_steps(self):
        goal = _goal()
        with self.assertRaises(PlanValidationError) as ctx:
            build_goal_plan(goal, [_step("a", dependencies=("a",))])
        self.assertEqual(ctx.exception.code, "SELF_DEPENDENCY")
        cyclic = [
            _step("a", dependencies=("b",)),
            _step("b", dependencies=("a",)),
        ]
        with self.assertRaises(PlanValidationError) as ctx:
            build_goal_plan(goal, cyclic)
        self.assertEqual(ctx.exception.code, "CYCLIC_DEPENDENCY")
        chain = []
        ids = [f"n{i}" for i in range(6)]
        for i, sid in enumerate(ids):
            deps = (ids[i + 1],) if i + 1 < len(ids) else ()
            chain.append(_step(sid, dependencies=deps))
        with self.assertRaises(PlanValidationError) as ctx:
            build_goal_plan(goal, chain)
        self.assertEqual(ctx.exception.code, "DEPENDENCY_TOO_DEEP")
        too_many = [_step(f"s{i}") for i in range(MAX_STEPS + 1)]
        with self.assertRaises(PlanValidationError) as ctx:
            build_goal_plan(goal, too_many)
        self.assertEqual(ctx.exception.code, "TOO_MANY_STEPS")

    def test_retry_and_timeout(self):
        goal = _goal()
        with self.assertRaises(PlanValidationError) as ctx:
            build_goal_plan(goal, [_step(
                capability_id="filesystem", action="DELETE_FILE",
                parameters={"path": "a.txt"}, retry_count=1,
            )])
        self.assertEqual(ctx.exception.code, "MUTATION_RETRY_FORBIDDEN")
        with self.assertRaises(PlanValidationError) as ctx:
            build_goal_plan(goal, [_step(retry_count=-1)])
        self.assertEqual(ctx.exception.code, "INVALID_RETRY")
        with self.assertRaises(PlanValidationError):
            build_goal_plan(goal, [_step(timeout_ms=0)])
        with self.assertRaises(PlanValidationError):
            build_goal_plan(goal, [_step(timeout_ms=10**9)])
        with self.assertRaises(PlanValidationError):
            build_goal_plan(goal, [_step(timeout_ms=math.inf)])
        ok = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="READ_FILE",
            parameters={"path": "a.txt"}, retry_count=2, timeout_ms=1000,
        )])
        self.assertEqual(ok.steps[0].retry_count, 2)

    def test_risk_strongest_wins(self):
        goal = _goal()
        plan = build_goal_plan(goal, [
            _step("r", capability_id="filesystem", action="READ_FILE", parameters={"path": "a"}, risk="LOW"),
            _step("d", capability_id="filesystem", action="DELETE_FILE", parameters={"path": "a"}, risk="LOW"),
        ], claimed_plan_risk="LOW")
        self.assertEqual(plan.steps[1].risk, "HIGH")
        self.assertEqual(plan.plan_risk, "HIGH")
        self.assertTrue(plan.approval_required)
        self.assertFalse(plan.approved)

    def test_hash_stable_and_sensitive_to_change(self):
        goal = _goal()
        a = build_goal_plan(goal, [_step()], plan_id="p1")
        b = build_goal_plan(goal, [_step()], plan_id="p2")
        self.assertEqual(a.plan_hash, b.plan_hash)
        self.assertNotEqual(a.plan_id, b.plan_id)
        c = build_goal_plan(goal, [_step(parameters={})])
        self.assertEqual(a.plan_hash, c.plan_hash)
        d = build_goal_plan(goal, [_step(
            capability_id="filesystem", action="READ_FILE", parameters={"path": "a.txt"},
        )])
        self.assertNotEqual(a.plan_hash, d.plan_hash)
        tampered = replace(a, plan_hash="0" * 64)
        with self.assertRaises(PlanValidationError) as ctx:
            validate_plan_hash(tampered, goal)
        self.assertEqual(ctx.exception.code, "PLAN_HASH_MISMATCH")

    def test_verification_is_data(self):
        goal = _goal()
        plan = build_goal_plan(goal, [_step(
            capability_id="verification",
            action="TARGET_EXISTS",
            parameters={"path": "a"},
            verification_required=True,
            verification_type="TARGET_EXISTS",
        )])
        self.assertTrue(plan.steps[0].verification_required)
        self.assertFalse(plan.execution_permitted)

    def test_unknown_goal_intent_rejected(self):
        _v8_on()
        bad = process_goal("please handle this").goal
        with self.assertRaises(PlanValidationError) as ctx:
            build_goal_plan(bad, [_step()])
        self.assertEqual(ctx.exception.code, "INVALID_GOAL_INTENT")

    def test_public_never_executable(self):
        plan = build_goal_plan(_goal(), [_step()])
        pub = plan.as_public()
        self.assertFalse(pub["execution_permitted"])
        self.assertFalse(pub["approved"])
        self.assertEqual(plan.execution_permitted, False)


if __name__ == "__main__":
    unittest.main()
