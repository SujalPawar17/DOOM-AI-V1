"""DOOM Unified Cognitive OS facade (completion program, phase 10).

One object that wires the complete architecture together:

    INPUT (text / voice transcript / events)
      -> PERCEPTION (V12.4) -> CONTEXT (V10.1 + V11 caller context) -> MEMORY / USER MODEL
      -> GOAL UNDERSTANDING -> REASONING -> DECISION -> PLANNING          (V10, frozen)
      -> COST GUARD -> AUTHORIZATION -> EXECUTION -> VERIFICATION        (V8 / V11 / V12.3 / V12.5)
      -> RESPONSE GENERATION (V12.1) -> VOICE / TEXT OUTPUT (V9, frozen)
      -> OUTCOME -> EXPERIENCE (V8.28 / V11.2) -> LEARNING (V12.2)
      -> PROACTIVE LOOP (V11.5 monitor -> V12.6 runtime -> V12.7 assistant)

Routing rule: every user input goes either to the declared-connector path (V12.3
gateway, only for deterministic tool proposals) or to the V12 cognitive cycle. There
is no third path and nothing calls a connector or executor directly.

Safe defaults: connectors are registered only for workspaces explicitly configured
via environment (DOOM_WORKSPACE_ROOT, DOOM_GIT_REPO, DOOM_SQLITE_DB); computer
control is disabled unless a UI backend is injected; the background runtime and
monitor start only when start() is called; voice output is whatever `speak` callable
the caller provides (production: the frozen V9 speak()).
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from core.v12.cognitive_orchestrator import V12CognitiveOrchestrator
from core.v12.computer_interaction import ComputerInteractionController, UIBackend
from core.v12.integrations import (
    ConnectorRegistry, ConnectorRequest, ConnectorStatus, IntegrationGateway, IntegrationIdentity,
    LocalFilesystemConnector, LocalGitConnector, LocalSqliteConnector, GithubCloudConnector, ToolSelector,
)
from core.v12.proactive_assistant import ProactiveAssistant
from core.v12.runtime import CognitiveEventHandler, RealtimeRuntime, make_runtime_monitor

MAX_PENDING_ROUTES = 128


@dataclass(frozen=True)
class DoomReply:
    text: str
    route: str                    # "cognitive" | "connector" | "approval" | "rejected"
    details: Dict[str, Any] = field(default_factory=dict)


def default_owner_id() -> str:
    return os.getenv("DOOM_OWNER_ID", "sujal").strip() or "sujal"


def default_session_id() -> str:
    return os.getenv("DOOM_SESSION_ID", "local").strip() or "local"


def _registry_from_env(owner_id: str) -> ConnectorRegistry:
    registry = ConnectorRegistry()
    workspace = os.getenv("DOOM_WORKSPACE_ROOT", "").strip()
    repo = os.getenv("DOOM_GIT_REPO", "").strip()
    database = os.getenv("DOOM_SQLITE_DB", "").strip()
    if workspace and os.path.isdir(workspace):
        registry.register(LocalFilesystemConnector({owner_id: workspace}))
    if repo and os.path.isdir(repo):
        registry.register(LocalGitConnector({owner_id: repo}))
    if database and os.path.isfile(database):
        registry.register(LocalSqliteConnector({owner_id: database}))
    registry.register(GithubCloudConnector())  # declared, disabled, Cost-Guard-blocked
    return registry


def describe_connector_result(result: Any) -> str:
    """Deterministic, truthful description of a gateway result."""
    status = result.status
    if status is ConnectorStatus.SUCCESS and result.verified:
        out = result.output or {}
        if "branch" in out:
            return f"You're on branch {out['branch']}."
        if "porcelain" in out:
            changes = [line for line in out["porcelain"] if not line.startswith("##")]
            return "The repository is clean." if not changes else f"There are {len(changes)} changed files."
        if "commits" in out:
            return "Recent commits: " + "; ".join(out["commits"][:5]) + "."
        if "entries" in out:
            entries = out["entries"]
            return ("The folder is empty." if not entries
                    else f"{len(entries)} items: " + ", ".join(entries[:10]) + ("…" if len(entries) > 10 else ""))
        if "text" in out:
            return out["text"][:600]
        if "rows" in out:
            return f"The query returned {len(out['rows'])} rows."
        if "written" in out:
            return f"Saved {out['written']} and verified it."
        return "Done, and verified."
    messages = {
        ConnectorStatus.APPROVAL_REQUIRED: f"That needs your approval first. Nothing has been done yet. "
                                            f"Approval reference: {result.pending_id}.",
        ConnectorStatus.COST_BLOCKED: "I can't do that under the zero-cost policy. Nothing was done.",
        ConnectorStatus.DISABLED: "That integration is turned off.",
        ConnectorStatus.UNKNOWN_CONNECTOR: "That integration isn't set up.",
        ConnectorStatus.INVALID_ARGUMENTS: "I couldn't use those details.",
        ConnectorStatus.TIMEOUT: "That took too long, so I stopped waiting. It may not have completed.",
        ConnectorStatus.VERIFICATION_FAILED: "I tried, but couldn't verify it worked, so I'm not treating it as done.",
        ConnectorStatus.AUTHORIZATION_INVALID: "That approval isn't valid for this request.",
    }
    return messages.get(status, "I couldn't complete that.")


class DoomOS:

    def __init__(self, owner_id: Optional[str] = None, session_id: Optional[str] = None,
                 orchestrator: Optional[V12CognitiveOrchestrator] = None,
                 registry: Optional[ConnectorRegistry] = None,
                 ui_backend: Optional[UIBackend] = None,
                 speak: Optional[Callable[..., Any]] = None):
        self.owner_id = owner_id or default_owner_id()
        self.session_id = session_id or default_session_id()
        self.orchestrator = orchestrator or V12CognitiveOrchestrator()
        self.registry = registry or _registry_from_env(self.owner_id)
        self.gateway = IntegrationGateway(self.registry)
        self.selector = ToolSelector()
        self.computer = ComputerInteractionController(ui_backend) if ui_backend is not None else None
        self.runtime = RealtimeRuntime(CognitiveEventHandler(self.orchestrator))
        self.assistant = ProactiveAssistant(self.gateway, learning=self.orchestrator.learning)
        self.monitor = None
        self._speak = speak
        self._lock = threading.Lock()
        self._pending: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    def identity(self, owner_id: Optional[str] = None, session_id: Optional[str] = None) -> IntegrationIdentity:
        return IntegrationIdentity(owner_id or self.owner_id, session_id or self.session_id)

    # -- input --------------------------------------------------------------------------

    def handle_text(self, text: str, lang: Optional[str] = None, *, source: str = "text",
                    owner_id: Optional[str] = None, session_id: Optional[str] = None) -> DoomReply:
        owner, session = owner_id or self.owner_id, session_id or self.session_id
        text = str(text or "").strip()
        if not text:
            return self._out(DoomReply("Standing by.", "rejected"), lang)
        proposal = self.selector.propose(text, owner, session)
        if proposal is not None and self.registry.get(proposal.connector_id) is not None:
            result = self.gateway.invoke(proposal, self.identity(owner, session))
            if result.status is ConnectorStatus.APPROVAL_REQUIRED:
                self._remember(result.pending_id, {"kind": "connector", "request": proposal})
            return self._out(DoomReply(describe_connector_result(result), "connector",
                                       {"status": result.status.value, "pending_id": result.pending_id}), lang)
        cycle = self.orchestrator.process_cognitive_cycle(user_input=text, owner_id=owner, session_id=session,
                                                          lang=lang, context={"input_source": source})
        pending_id = ((cycle.get("stages") or {}).get("authorization") or {}).get("pending_id")
        if pending_id:
            self._remember(pending_id, {"kind": "plan", "text": text, "owner": owner, "session": session})
        stage = (cycle.get("stages") or {}).get("response_generation") or {}
        return self._out(DoomReply(cycle.get("response_text") or "I couldn't produce a response.", "cognitive",
                                   {"success": bool(cycle.get("success")), "intent": stage.get("intent"),
                                    "pending_id": pending_id, "cycle_id": cycle.get("cycle_id")}), lang)

    def approve(self, pending_id: str, lang: Optional[str] = None, *, computer_session_id: str = "") -> DoomReply:
        """The user's explicit approval of a pending request (connector or plan)."""
        with self._lock:
            route = self._pending.pop(str(pending_id or ""), None)
        if route is None:
            return self._out(DoomReply("I don't have a pending request with that reference.", "rejected"), lang)
        if route["kind"] == "connector":
            request: ConnectorRequest = route["request"]
            identity = self.identity(request.owner_id, request.session_id)
            authorized, code = self.gateway.approvals.claim(pending_id, identity)
            if code != "OK":
                return self._out(DoomReply(f"That approval can't be used ({code}).", "rejected"), lang)
            result = self.gateway.invoke(request, identity, authorized)
            return self._out(DoomReply(describe_connector_result(result), "approval",
                                       {"status": result.status.value}), lang)
        from orchestration.authorization import claim_authorization
        from orchestration.executor import ExecutionIdentity
        claim, code = claim_authorization(pending_id, ExecutionIdentity(
            owner_id=route["owner"], session_id=route["session"], computer_session_id=computer_session_id))
        if claim is None:
            return self._out(DoomReply(f"That approval can't be used ({code}).", "rejected"), lang)
        cycle = self.orchestrator.process_cognitive_cycle(
            user_input=route["text"], owner_id=route["owner"], session_id=route["session"], lang=lang,
            authorized_plan_hash=claim.authorized_plan_hash,
            computer_session_id=claim.identity.computer_session_id)
        return self._out(DoomReply(cycle.get("response_text") or "", "approval",
                                   {"success": bool(cycle.get("success"))}), lang)

    def _remember(self, pending_id: str, route: Dict[str, Any]) -> None:
        with self._lock:
            self._pending[pending_id] = route
            while len(self._pending) > MAX_PENDING_ROUTES:
                self._pending.popitem(last=False)

    def _out(self, reply: DoomReply, lang: Optional[str]) -> DoomReply:
        if self._speak is not None and reply.text:
            try:
                self._speak(reply.text, lang=lang)
            except Exception:
                pass  # output failure never changes what was decided or done
        return reply

    # -- proactive loop -----------------------------------------------------------------

    def tick(self) -> int:
        """One deterministic proactive step: process events, feed changes to the
        assistant, run the assistant for the local owner/session."""
        processed = self.runtime.run_once()
        for item in self.runtime.outbox(self.owner_id, self.session_id)[-processed:] if processed else []:
            self.assistant.note_change(self.owner_id, self.session_id, item["event_type"],
                                       item["event_type"].replace("_", " "))
        self.assistant.run_cycle(self.owner_id, self.session_id)
        return processed

    def start(self, with_monitor: bool = False) -> None:
        self.runtime.start()
        if with_monitor and self.monitor is None:
            self.monitor = make_runtime_monitor(self.runtime, self.owner_id, self.session_id)
            self.monitor.start()

    def shutdown(self) -> None:
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None
        self.runtime.stop()
        self.gateway.shutdown()
        if self.computer is not None:
            self.computer.shutdown()


_OS_LOCK = threading.Lock()
_OS: Optional[DoomOS] = None


def cognitive_os_enabled() -> bool:
    return os.getenv("DOOM_COGNITIVE_OS", "").strip().lower() in ("1", "true", "yes", "on")


def get_doom_os() -> DoomOS:
    global _OS
    with _OS_LOCK:
        if _OS is None:
            _OS = DoomOS()
        return _OS
