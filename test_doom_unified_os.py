#!/usr/bin/env python
"""DOOM Unified Cognitive OS — integration and architecture tests (HARD $0, no audio)."""

import ast
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["PROACTIVE_V8_ENABLED"] = "true"
os.environ["PROACTIVE_V828_GOAL_EXPERIENCE_ENABLED"] = "true"
os.environ["PROACTIVE_V827_USER_MODEL_ENABLED"] = "true"

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_v11_8_end_to_end_integration import (  # noqa: E402
    V118TestBase, make_plan, make_step, planning_for, COMPUTER_SESSION, OWNER, SESSION,
)
from test_v12_1_response_intelligence import FakeLocalModel  # noqa: E402
from core.v12.adaptive_learning import AdaptiveLearning, LearningStore  # noqa: E402
from core.v12.cognitive_orchestrator import V12CognitiveOrchestrator  # noqa: E402
from core.v12.doom_os import DoomOS, _registry_from_env  # noqa: E402
from core.v12.integrations import ConnectorSandbox, ConnectorRegistry  # noqa: E402
from core.v12.proactive_assistant import SuggestionKind  # noqa: E402
from core.v12.runtime import EventPriority, RuntimeEvent  # noqa: E402
from orchestration.conversation.respond import use_respond_provider_for_tests, reset_respond_provider_for_tests  # noqa: E402


class OSBase(V118TestBase):
    def setUp(self):
        super().setUp()
        self.model = FakeLocalModel(["Paris."] * 20)
        use_respond_provider_for_tests(self.model)
        self.box = ConnectorSandbox(OWNER).__enter__()
        self.spoken = []
        self.os = DoomOS(owner_id=OWNER, session_id=SESSION,
                         orchestrator=V12CognitiveOrchestrator(learning=AdaptiveLearning(store=LearningStore())),
                         registry=self.box.registry,
                         speak=lambda text, lang=None: self.spoken.append(text))

    def tearDown(self):
        self.os.shutdown()
        self.box.__exit__(None, None, None)
        reset_respond_provider_for_tests()
        super().tearDown()


class TestFacadeRouting(OSBase):

    def test_question_goes_through_cognitive_pipeline_and_voice(self):
        with self.spy.hooks():
            reply = self.os.handle_text("What is the capital of France?")
        self.assertEqual((reply.route, reply.text), ("cognitive", "Paris."))
        self.assertEqual(reply.details["intent"], "ANSWER")
        self.assertEqual(self.spoken, ["Paris."])
        self.assertEqual(self.spy.calls, [])

    def test_tool_request_goes_through_gateway(self):
        reply = self.os.handle_text("git status")
        self.assertEqual(reply.route, "connector")
        self.assertEqual(reply.details["status"], "SUCCESS")
        self.assertEqual(reply.text, "The repository is clean.")
        self.assertEqual(self.os.handle_text("What branch am I on?").text, "You're on branch main.")

    def test_unconfigured_connector_falls_back_to_cognition(self):
        os_without = DoomOS(owner_id=OWNER, session_id=SESSION, registry=ConnectorRegistry(),
                            orchestrator=V12CognitiveOrchestrator(learning=AdaptiveLearning(store=LearningStore())))
        try:
            with self.spy.hooks():
                self.assertEqual(os_without.handle_text("git status").route, "cognitive")
        finally:
            os_without.shutdown()

    def test_registry_from_env_grants_nothing_by_default(self):
        with patch.dict(os.environ, {"DOOM_WORKSPACE_ROOT": "", "DOOM_GIT_REPO": "", "DOOM_SQLITE_DB": ""}):
            registry = _registry_from_env("someone")
        self.assertEqual({s.connector_id for s in registry.specs()}, {"github_api"})
        self.assertFalse(registry.is_enabled("github_api"))

    def test_plan_approval_round_trip(self):
        plan = make_plan([make_step("s1", "computer", "CLICK", [("name", "Save")], risk="MEDIUM", approval=True)],
                         goal_id="ag_os_auth", plan_risk="MEDIUM", approval=True, computer_session_id=COMPUTER_SESSION)
        orch = self.os.orchestrator
        with self.spy.hooks(), patch.object(orch, "planning_integration", return_value=planning_for(plan)), \
                patch("orchestration.authorization.verified_computer_session_id", side_effect=lambda o, cs: (cs, "OK")):
            first = self.os.handle_text("Click save")
            pending = first.details["pending_id"]
            self.assertTrue(pending)
            self.assertIn(pending, first.text)
            self.assertEqual(self.spy.calls, [])
            done = self.os.approve(pending, computer_session_id=COMPUTER_SESSION)
            self.assertEqual(done.route, "approval")
            self.assertTrue(done.details["success"])
            self.assertEqual(self.spy.calls, [("computer", "CLICK")])
            self.assertEqual(self.os.approve(pending).route, "rejected", "approvals are single-use")

    def test_output_failure_never_changes_the_reply(self):
        self.os._speak = MagicMock(side_effect=RuntimeError("audio device busy"))
        with self.spy.hooks():
            self.assertEqual(self.os.handle_text("What is the capital of France?").text, "Paris.")


class TestProductionEntryPoint(unittest.TestCase):

    def test_flag_off_uses_v8_core_flag_on_uses_cognitive_os(self):
        import core.commands as commands
        fake_os = MagicMock()
        fake_os.handle_text.return_value.text = "from cognitive os"
        with patch.object(commands, "speak") as speak, patch.object(commands, "stop_speaking"), \
                patch.object(commands.doom_core, "process_request", return_value="from v8 core") as v8, \
                patch("core.v12.doom_os.get_doom_os", return_value=fake_os):
            with patch.dict(os.environ, {"DOOM_COGNITIVE_OS": ""}):
                self.assertEqual(commands.submit_user_input("hello", "en"), "from v8 core")
            fake_os.handle_text.assert_not_called()
            with patch.dict(os.environ, {"DOOM_COGNITIVE_OS": "1"}):
                self.assertEqual(commands.submit_user_input("hello", "en", source="voice"), "from cognitive os")
            fake_os.handle_text.assert_called_once_with("hello", "en", source="voice")
            self.assertEqual(v8.call_count, 1)
            self.assertEqual([c.args[0] for c in speak.call_args_list], ["from v8 core", "from cognitive os"])


class TestProactiveLoop(OSBase):

    def test_event_to_assistant_suggestion(self):
        self.os.runtime.submit(RuntimeEvent.make(OWNER, SESSION, "system", "battery_low", "battery low",
                                                 EventPriority.HIGH))
        with self.spy.hooks():
            self.assertEqual(self.os.tick(), 1)
        inbox = self.os.assistant.inbox(OWNER, SESSION)
        self.assertTrue(any(s.kind is SuggestionKind.CHANGE_DETECTED for s in inbox))
        self.assertEqual(self.spy.calls, [])

    def test_learning_reaches_later_cognition(self):
        with self.spy.hooks():
            self.os.handle_text("I prefer Python over Ruby")
            nxt = self.os.orchestrator.process_cognitive_cycle("Which language?", OWNER, SESSION)
        self.assertEqual(nxt["stages"]["context_fusion"]["fused_context"].context
                         .get("learned_preference_choice_python_vs_ruby"), "Python")


# --- architecture audits ---------------------------------------------------------------

PACKAGES = ("core/v10", "core/v11", "core/v12")


def _modules():
    mods = {}
    for pkg in PACKAGES:
        for path in (ROOT / pkg).rglob("*.py"):
            if path.name.startswith("test_") or "demo_" in path.name:
                continue
            rel = path.relative_to(ROOT).with_suffix("")
            name = ".".join(rel.parts)
            if name.endswith(".__init__"):
                name = name[: -len(".__init__")]
            mods[name] = path
    return mods


def _imports(path):
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return found


class TestArchitecture(unittest.TestCase):

    def setUp(self):
        self.mods = _modules()
        self.graph = {m: {i for i in _imports(p) if i in self.mods and i != m} for m, p in self.mods.items()}

    def test_no_import_cycles(self):
        state, cycles = {}, []

        def dfs(node, stack):
            state[node] = 1
            for nxt in self.graph[node]:
                if state.get(nxt) == 1:
                    cycles.append(stack + [nxt])
                elif state.get(nxt) is None:
                    dfs(nxt, stack + [nxt])
            state[node] = 2

        for m in sorted(self.graph):
            if state.get(m) is None:
                dfs(m, [m])
        self.assertEqual(cycles, [])

    def test_no_upward_dependencies_on_v12(self):
        for m, deps in self.graph.items():
            if m.startswith(("core.v10", "core.v11")):
                self.assertFalse([d for d in deps if d.startswith("core.v12")], m)

    def test_single_execution_paths(self):
        v12 = ROOT / "core" / "v12"
        for path in v12.rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            if path.name != "framework.py":
                self.assertNotIn("connector.execute(", src, path.name)
            if path.name != "computer_interaction.py":
                self.assertNotIn("backend.click(", src, path.name)
                self.assertNotIn("backend.set_text(", src, path.name)
            imports = _imports(path)
            if path.name not in ("connectors.py", "sandbox.py"):
                self.assertFalse({"subprocess", "sqlite3"} & imports, path.name)
            if path.name != "doom_os.py":  # only the facade touches V8 approvals (claim)
                self.assertNotIn("orchestration.executor", imports, path.name)
        # Among the cognitive layers only the V11 execution layer imports the V8 executor entry point.
        def imports_v8_execute_plan(path):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module == "orchestration.executor" \
                        and any(a.name == "execute_plan" for a in node.names):
                    return True
            return False
        callers = sorted(m for m, p in self.mods.items() if imports_v8_execute_plan(p))
        self.assertEqual(callers, ["core.v11.execution_layer"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
