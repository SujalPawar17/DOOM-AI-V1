"""V12.7 Proactive Personal Assistant.

Combines learned knowledge (V12.2), goals (V8.26 registry), events (V12.6), perceptual
context (V12.4) and integrations (V12.3) into *suggestions*. It is controlled, never
autonomous beyond what the action class allows:

    INFORMATIONAL  -> delivered automatically (no action)
    LOW_RISK       -> executed automatically ONLY if the owner explicitly enabled it;
                      otherwise suggested
    MEDIUM_RISK    -> approval required (claimed, single-use)
    HIGH_RISK      -> explicit authorization always (auto settings never apply)
    IRREVERSIBLE   -> explicit authorization + explicit irreversibility confirmation
                      + mandatory verification of the result

Every action executes through the V12.3 IntegrationGateway (identity, Cost Guard,
authorization, bounded execution, verification, experience). The assistant has no
other execution path.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.v12.integrations.framework import (
    ActionSpec, ConnectorRequest, ConnectorResult, ConnectorStatus, IntegrationGateway, IntegrationIdentity,
    RISK_RANK,
)

MAX_INBOX = 64
MAX_PER_CYCLE = 3
DEFAULT_COOLDOWN_S = 6 * 3600.0
STALE_GOAL_AFTER_S = 3 * 86400.0


class ActionClass(str, Enum):
    INFORMATIONAL = "INFORMATIONAL"
    LOW_RISK = "LOW_RISK"
    MEDIUM_RISK = "MEDIUM_RISK"
    HIGH_RISK = "HIGH_RISK"
    IRREVERSIBLE = "IRREVERSIBLE"


class SuggestionKind(str, Enum):
    REMINDER = "REMINDER"
    STALLED_WORKFLOW = "STALLED_WORKFLOW"
    PREPARE_INFO = "PREPARE_INFO"
    CHANGE_DETECTED = "CHANGE_DETECTED"
    NEXT_ACTION = "NEXT_ACTION"


class SuggestionState(str, Enum):
    DELIVERED = "DELIVERED"            # informational, nothing to do
    PROPOSED = "PROPOSED"              # waiting for the user
    AUTO_EXECUTED = "AUTO_EXECUTED"    # low-risk, owner opted in
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    DECLINED = "DECLINED"
    REFUSED = "REFUSED"


@dataclass(frozen=True)
class AssistantPolicy:
    auto_low_risk: bool = False
    quiet: bool = False                     # only informational, nothing proposed
    max_per_cycle: int = MAX_PER_CYCLE
    cooldown_s: float = DEFAULT_COOLDOWN_S


@dataclass(frozen=True)
class Suggestion:
    suggestion_id: str
    owner_id: str
    session_id: str
    kind: SuggestionKind
    action_class: ActionClass
    message: str
    reason: str
    request: Optional[ConnectorRequest]
    state: SuggestionState
    created_at: float
    pending_id: str = ""
    result_status: str = ""
    verified: bool = False


def classify_action(spec: Optional[ActionSpec]) -> ActionClass:
    if spec is None:
        return ActionClass.INFORMATIONAL
    if spec.mutates and not spec.reversible:
        return ActionClass.IRREVERSIBLE
    rank = RISK_RANK.get(spec.risk, 4)
    if rank >= RISK_RANK["HIGH"]:
        return ActionClass.HIGH_RISK
    if rank >= RISK_RANK["MEDIUM"] or spec.mutates:
        return ActionClass.MEDIUM_RISK
    return ActionClass.LOW_RISK


class ProactiveAssistant:

    def __init__(self, gateway: IntegrationGateway, learning: Any = None, clock: Callable[[], float] = time.time,
                 goal_reader: Optional[Callable[[str], Any]] = None):
        self.gateway = gateway
        self.learning = learning
        self._clock = clock
        self._goal_reader = goal_reader or _registry_active_goal
        self._lock = threading.Lock()
        self._policies: Dict[str, AssistantPolicy] = {}
        self._inbox: Dict[str, Suggestion] = {}
        self._last_suggested: Dict[Tuple[str, str], float] = {}
        self._signals: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}

    # -- configuration / signals ---------------------------------------------------

    def set_policy(self, owner_id: str, policy: AssistantPolicy) -> None:
        with self._lock:
            self._policies[owner_id] = policy

    def policy(self, owner_id: str) -> AssistantPolicy:
        with self._lock:
            return self._policies.get(owner_id, AssistantPolicy())

    def note_change(self, owner_id: str, session_id: str, event_type: str, description: str) -> None:
        """Feed a detected change (e.g. from the V12.6 runtime outbox)."""
        with self._lock:
            bucket = self._signals.setdefault((owner_id, session_id), [])
            bucket.append((event_type[:48], description[:160]))
            del bucket[:-16]

    # -- detection -------------------------------------------------------------------

    def run_cycle(self, owner_id: str, session_id: str) -> List[Suggestion]:
        policy = self.policy(owner_id)
        now = self._clock()
        candidates: List[Tuple[str, SuggestionKind, str, str, Optional[ConnectorRequest]]] = []

        with self._lock:
            changes = self._signals.pop((owner_id, session_id), [])
        for event_type, description in changes:
            candidates.append((f"change:{event_type}:{description}", SuggestionKind.CHANGE_DETECTED,
                               f"Heads up: {description}.", f"event {event_type}", None))

        goal = None
        try:
            goal = self._goal_reader(owner_id)
        except Exception:
            goal = None
        if goal is not None:
            title = str(getattr(goal, "title", "") or "your goal")
            last_active = float(getattr(goal, "last_active_at", now) or now)
            if now - last_active > STALE_GOAL_AFTER_S:
                days = int((now - last_active) // 86400)
                candidates.append((f"stale_goal:{getattr(goal, 'goal_id', '')}", SuggestionKind.REMINDER,
                                   f"You haven't worked on '{title}' for {days} days. Want to pick it up?",
                                   "active goal inactive", None))
            steps = list(getattr(goal, "step_titles", ()) or ())
            states = [str(getattr(s, "value", s)) for s in (getattr(goal, "step_states", ()) or ())]
            for step_title, state in zip(steps, states):
                if state in ("PENDING", "IN_PROGRESS"):
                    candidates.append((f"next:{getattr(goal, 'goal_id', '')}:{step_title}", SuggestionKind.NEXT_ACTION,
                                       f"Next step for '{title}': {step_title}.", "first unfinished goal step", None))
                    break

        if self.learning is not None:
            for item in self.learning.explain(owner_id):
                if item["status"] != "PROMOTED":
                    continue
                if item["kind"] == "WORKFLOW" and item["value"] == "unreliable":
                    candidates.append((f"stalled:{item['key']}", SuggestionKind.STALLED_WORKFLOW,
                                       f"A workflow keeps failing ({item['explanation']}) Want help troubleshooting it?",
                                       "learned failure pattern", None))
                if item["kind"] == "HABIT" and "morning" in item["value"]:
                    request = ConnectorRequest.make(owner_id, session_id, "git", "status")
                    candidates.append((f"prepare:{item['key']}", SuggestionKind.PREPARE_INFO,
                                       "You usually start this in the morning; I can check your repository status first.",
                                       "learned habit", request))

        delivered: List[Suggestion] = []
        for dedup_key, kind, message, reason, request in candidates:
            if len(delivered) >= max(0, policy.max_per_cycle):
                break
            key = (owner_id, dedup_key)
            with self._lock:
                if now - self._last_suggested.get(key, -1e18) < policy.cooldown_s:
                    continue
            suggestion = self._materialize(owner_id, session_id, kind, message, reason, request, policy, now)
            if suggestion is None:
                continue
            with self._lock:
                self._last_suggested[key] = now
            delivered.append(suggestion)
        return delivered

    def _materialize(self, owner_id, session_id, kind, message, reason, request, policy, now) -> Optional[Suggestion]:
        spec = None
        if request is not None:
            connector = self.gateway.registry.get(request.connector_id)
            spec = connector.spec.action(request.action) if connector else None
            if spec is None:
                return None  # never propose an undeclared action
        action_class = classify_action(spec)
        if policy.quiet and action_class is not ActionClass.INFORMATIONAL:
            return None
        sid = hashlib.sha256(f"{owner_id}|{session_id}|{kind.value}|{message}|{now}".encode()).hexdigest()[:16]
        state = SuggestionState.DELIVERED if action_class is ActionClass.INFORMATIONAL else SuggestionState.PROPOSED
        suggestion = Suggestion(sid, owner_id, session_id, kind, action_class, message, reason, request, state, now)
        if action_class is ActionClass.LOW_RISK and policy.auto_low_risk:
            suggestion = self._execute(suggestion, authorized_hash="", auto=True)
        self._store(suggestion)
        return suggestion

    # -- user response -----------------------------------------------------------------

    def respond(self, suggestion_id: str, identity: IntegrationIdentity, accept: bool,
                confirm_irreversible: bool = False) -> Suggestion:
        with self._lock:
            suggestion = self._inbox.get(suggestion_id)
        if suggestion is None or identity.owner_id != suggestion.owner_id \
                or identity.session_id != suggestion.session_id:
            raise PermissionError("unknown suggestion for this identity")
        if suggestion.state is not SuggestionState.PROPOSED:
            return suggestion
        if not accept:
            return self._store(replace(suggestion, state=SuggestionState.DECLINED))
        if suggestion.action_class is ActionClass.IRREVERSIBLE and not confirm_irreversible:
            return self._store(replace(suggestion, state=SuggestionState.REFUSED,
                                       result_status="IRREVERSIBLE_CONFIRMATION_REQUIRED"))
        authorized = ""
        if suggestion.action_class in (ActionClass.MEDIUM_RISK, ActionClass.HIGH_RISK, ActionClass.IRREVERSIBLE):
            # The user's explicit acceptance is the approval: request it, then claim it.
            pending = self.gateway.invoke(suggestion.request, identity)
            if pending.status is not ConnectorStatus.APPROVAL_REQUIRED:
                return self._store(replace(suggestion, state=SuggestionState.FAILED,
                                           result_status=pending.status.value))
            authorized, code = self.gateway.approvals.claim(pending.pending_id, identity)
            if code != "OK":
                return self._store(replace(suggestion, state=SuggestionState.FAILED, result_status=code))
        return self._store(self._execute(suggestion, authorized, auto=False, identity=identity))

    def _execute(self, suggestion: Suggestion, authorized_hash: str, auto: bool,
                 identity: Optional[IntegrationIdentity] = None) -> Suggestion:
        identity = identity or IntegrationIdentity(suggestion.owner_id, suggestion.session_id)
        result: ConnectorResult = self.gateway.invoke(suggestion.request, identity, authorized_hash)
        ok = result.ok
        if suggestion.action_class is ActionClass.IRREVERSIBLE and not result.verified:
            ok = False  # irreversible actions are only successful when verified
        state = (SuggestionState.AUTO_EXECUTED if auto else SuggestionState.EXECUTED) if ok else SuggestionState.FAILED
        return replace(suggestion, state=state, result_status=result.status.value, verified=result.verified,
                       pending_id=result.pending_id)

    # -- inbox ---------------------------------------------------------------------------

    def _store(self, suggestion: Suggestion) -> Suggestion:
        with self._lock:
            self._inbox[suggestion.suggestion_id] = suggestion
            while len(self._inbox) > MAX_INBOX:
                oldest = min(self._inbox.values(), key=lambda s: (s.created_at, s.suggestion_id))
                del self._inbox[oldest.suggestion_id]
        return suggestion

    def inbox(self, owner_id: str, session_id: Optional[str] = None) -> List[Suggestion]:
        with self._lock:
            return sorted((s for s in self._inbox.values() if s.owner_id == owner_id
                           and (session_id is None or s.session_id == session_id)),
                          key=lambda s: (s.created_at, s.suggestion_id))

    def propose(self, owner_id: str, session_id: str, request: ConnectorRequest, message: str,
                kind: SuggestionKind = SuggestionKind.NEXT_ACTION) -> Optional[Suggestion]:
        """Explicit proposal of an action (e.g. prepared by the cognitive pipeline)."""
        return self._materialize(owner_id, session_id, kind, message, "explicit proposal", request,
                                 self.policy(owner_id), self._clock())


def _registry_active_goal(owner_id: str) -> Any:
    from orchestration.plan.goal_registry import RegistryStatus, get_active_goal
    res = get_active_goal(owner_id)
    return res.snapshot if res.status is RegistryStatus.OK else None
