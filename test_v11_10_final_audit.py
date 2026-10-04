#!/usr/bin/env python
"""
DOOM V11.10 — Final V11 Acceptance / Freeze audit tests.

Locks in the V11.10 monitor fix and cross-cutting V11 invariants:
- continuous monitor works from the first poll (previously crashed on every poll)
- HARD $0: Cost Guard decisions for local vs paid/unknown providers
- browser automation default OFF
- V11 modules import no network/browser/UI-automation/paid SDKs
- V11 never constructs the V9 VoiceEngine / audio outputs
- V8 audit helper (committed in V11.8 freeze) is best-effort and never raises
"""

import ast
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["PROACTIVE_V8_ENABLED"] = "true"

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.v11 import proactive_behavior  # noqa: E402
from core.v11.proactive_behavior import (  # noqa: E402
    ContinuousMonitoringEnhancement, MonitoringPriority,
)
from core.cost_guard.guard import cost_guard  # noqa: E402
from core.cost_guard.types import ResourceRequest, ResourceType  # noqa: E402

V11_MODULES = sorted(p for p in (ROOT / "core" / "v11").glob("*.py")
                     if not p.name.startswith("test_") and p.name not in ("demo_v11_1_execution.py",))

FORBIDDEN_IMPORTS = {
    "requests", "httpx", "aiohttp", "urllib3", "openai", "anthropic", "boto3",
    "selenium", "playwright", "pyautogui", "pywinauto", "subprocess", "edge_tts",
    "sounddevice", "pygame",
}


class _SyncThread:
    def __init__(self, target=None, daemon=None, **_kwargs):
        self._target = target

    def start(self):
        self._target()

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


class TestMonitorFirstPoll(unittest.TestCase):
    """Regression: _determine_event_priority dereferenced previous_state=None, so every
    poll raised before last_known_states was recorded and the monitor never worked."""

    def test_priority_with_no_previous_state(self):
        monitor = ContinuousMonitoringEnhancement(owner_id="audit_owner")
        priority = monitor._determine_event_priority("goal_state", {"no_active_goal": True}, None)
        self.assertIsInstance(priority, MonitoringPriority)

    def test_polls_succeed_and_record_state(self):
        monitor = ContinuousMonitoringEnhancement(owner_id="audit_owner_poll", session_id="audit_sess")
        states = {
            "goal_state": {"no_active_goal": True},
            "user_model_state": {"entry_count": 0},
            "experience_state": {"experience_count": 0},
            "memory_state": {"memory_count": 0},
        }
        with patch.object(monitor, "_collect_all_states", return_value=states), \
                patch.object(proactive_behavior.threading, "Thread", _SyncThread), \
                patch.object(proactive_behavior, "v11_cognitive_orchestrator") as orch:
            orch.process_cognitive_cycle.return_value = {"success": True}
            monitor._continuous_monitor_cycle()
            self.assertEqual(sorted(monitor.state.last_known_states), sorted(states))
            self.assertEqual(monitor.state.monitoring_errors, 0)
            first_calls = orch.process_cognitive_cycle.call_count
            self.assertLessEqual(first_calls, 1, "single-flight + cooldown bound proactive cycles")
            if first_calls:
                ctx = orch.process_cognitive_cycle.call_args.kwargs["context"]
                self.assertTrue(ctx["monitoring_trigger"])
            # Unchanged state on the next poll creates no new cycle.
            monitor._continuous_monitor_cycle()
            self.assertEqual(orch.process_cognitive_cycle.call_count, first_calls)
            self.assertEqual(monitor.state.monitoring_errors, 0)


class TestHardZeroCost(unittest.TestCase):

    def test_local_allowed_paid_and_unknown_blocked(self):
        allowed = [
            ResourceRequest(resource_type=ResourceType.LLM, provider="ollama", host="localhost"),
            ResourceRequest(resource_type=ResourceType.OTHER, provider="local_filesystem"),
            ResourceRequest(resource_type=ResourceType.VISION, provider="local_uia"),
            ResourceRequest(resource_type=ResourceType.DATABASE, provider="postgres", host="localhost"),
        ]
        blocked = [
            ResourceRequest(resource_type=ResourceType.LLM, provider="openai"),
            ResourceRequest(resource_type=ResourceType.LLM, provider="gemini"),
            ResourceRequest(resource_type=ResourceType.TTS, provider="elevenlabs"),
            ResourceRequest(resource_type=ResourceType.TTS, provider="edge_tts"),
            ResourceRequest(resource_type=ResourceType.LLM, provider="ollama", host="api.example.com"),
            ResourceRequest(resource_type=ResourceType.OTHER, provider="world_act"),
            ResourceRequest(resource_type=ResourceType.OTHER, provider="browser"),
        ]
        for req in allowed:
            self.assertTrue(cost_guard.decision(req).is_allow, req)
        for req in blocked:
            self.assertFalse(cost_guard.decision(req).is_allow, req)


class TestStaticInvariants(unittest.TestCase):

    def test_browser_automation_default_off(self):
        from proactive.config import is_computer_browser_enabled
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROACTIVE_COMPUTER_BROWSER_ENABLED", None)
            self.assertFalse(is_computer_browser_enabled())

    def test_v11_modules_import_nothing_forbidden(self):
        self.assertTrue(V11_MODULES)
        for path in V11_MODULES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    self.assertNotIn(name.split(".")[0], FORBIDDEN_IMPORTS, f"{path.name}: {name}")

    def test_v11_never_constructs_voice_engine(self):
        for path in V11_MODULES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                    self.assertNotIn(name, {"VoiceEngine", "PygameOutput", "SoundDeviceOutput"}, path.name)

    def test_audit_helper_never_raises(self):
        from orchestration.audit import try_record_event
        self.assertIsNone(try_record_event(not_a_real_field=object()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
