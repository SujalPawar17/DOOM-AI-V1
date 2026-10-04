"""V12.5 Controlled Computer Interaction.

    Computer intent -> perception (observe) -> action plan -> Cost Guard -> Authorization
                    -> stale-observation check -> action -> observation -> verification
                    -> rollback on failure (where defined) -> result + audit

Every action in the catalog declares: capability, risk, authorization requirement,
expected result, verification condition, timeout and rollback/failure behaviour.

Hard rules (not configurable):
- browser windows are never acted on (browser automation stays OFF)
- password / sensitive fields are never typed into and never read
- targets must be visible, enabled and resolve uniquely (no hidden or guessed targets)
- no key combinations, no free-form input injection, bounded text
- mutating actions need a claimed, single-use, plan-bound approval
- an emergency stop blocks every action until cleared
- deterministic simulated UI backend first; real desktop integration goes through the
  existing authorized V8 computer path (not enabled by this layer)
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from core.cost_guard.types import ResourceRequest, ResourceType
from core.v12.integrations.framework import (
    ConnectorAuthorizationStore, ConnectorRequest, IntegrationIdentity,
)

MAX_TYPE_CHARS = 256
MAX_AUDIT = 256
_SENSITIVE_FIELD = ("password", "passcode", "pin", "otp", "secret", "token", "credential", "api key",
                    "cvv", "card number", "ssn")


class UIRole(str, Enum):
    BUTTON = "button"
    INPUT = "input"
    LABEL = "label"
    CHECKBOX = "checkbox"


@dataclass(frozen=True)
class UIElement:
    element_id: str
    role: UIRole
    name: str
    value: str = ""
    enabled: bool = True
    visible: bool = True
    is_password: bool = False


@dataclass(frozen=True)
class UISnapshot:
    window_title: str
    app_kind: str          # e.g. "app", "browser", "system"
    elements: Tuple[UIElement, ...]

    @property
    def observation_hash(self) -> str:
        payload = [self.window_title, self.app_kind,
                   [[e.element_id, e.role.value, e.name, e.value, e.enabled, e.visible] for e in self.elements]]
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def find(self, name: str, role: Optional[UIRole] = None) -> List[UIElement]:
        return [e for e in self.elements if e.name == name and (role is None or e.role is role)]


class UIBackend(ABC):
    @abstractmethod
    def observe(self) -> UISnapshot: ...

    @abstractmethod
    def click(self, element_id: str) -> None: ...

    @abstractmethod
    def set_text(self, element_id: str, text: str) -> None: ...


class ComputerActionKind(str, Enum):
    OBSERVE = "OBSERVE"
    READ_VALUE = "READ_VALUE"
    CLICK = "CLICK"
    TYPE = "TYPE"


@dataclass(frozen=True)
class ComputerActionSpec:
    kind: ComputerActionKind
    capability: str
    risk: str
    requires_authorization: bool
    expected_result: str
    verification: str
    timeout_ms: int
    on_failure: str


CATALOG: Dict[ComputerActionKind, ComputerActionSpec] = {
    ComputerActionKind.OBSERVE: ComputerActionSpec(
        ComputerActionKind.OBSERVE, "computer_observe", "LOW", False,
        "a fresh snapshot of the active window", "snapshot has an observation hash", 2000,
        "report failure; nothing changed"),
    ComputerActionKind.READ_VALUE: ComputerActionSpec(
        ComputerActionKind.READ_VALUE, "computer_observe", "LOW", False,
        "the visible value of a non-sensitive element", "element exists and is not sensitive", 2000,
        "report failure; nothing changed"),
    ComputerActionKind.CLICK: ComputerActionSpec(
        ComputerActionKind.CLICK, "computer_act", "MEDIUM", True,
        "the target's declared effect is observable", "post-action snapshot satisfies the expectation", 3000,
        "no retry; report failure (a click cannot be undone)"),
    ComputerActionKind.TYPE: ComputerActionSpec(
        ComputerActionKind.TYPE, "computer_act", "MEDIUM", True,
        "the input contains exactly the typed text", "post-action value equals the requested text", 3000,
        "no retry; restore the previous value, then report failure"),
}


class ComputerStatus(str, Enum):
    SUCCESS = "SUCCESS"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    AUTHORIZATION_INVALID = "AUTHORIZATION_INVALID"
    COST_BLOCKED = "COST_BLOCKED"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    TARGET_NOT_INTERACTABLE = "TARGET_NOT_INTERACTABLE"
    SENSITIVE_TARGET = "SENSITIVE_TARGET"
    BROWSER_BLOCKED = "BROWSER_BLOCKED"
    INVALID_REQUEST = "INVALID_REQUEST"
    STALE_OBSERVATION = "STALE_OBSERVATION"
    TIMEOUT = "TIMEOUT"
    ACTION_FAILED = "ACTION_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    OWNER_MISMATCH = "OWNER_MISMATCH"


@dataclass(frozen=True)
class ComputerIntent:
    owner_id: str
    session_id: str
    kind: ComputerActionKind
    target_name: str = ""
    target_role: Optional[UIRole] = None
    text: str = ""
    expect_label: str = ""        # CLICK: name of a label whose value must change to expect_value
    expect_value: str = ""


@dataclass(frozen=True)
class ComputerActionPlan:
    owner_id: str
    session_id: str
    spec: ComputerActionSpec
    element_id: str
    target_name: str
    text: str
    expect_label: str
    expect_value: str
    precondition_hash: str
    plan_hash: str

    def as_request(self) -> ConnectorRequest:
        """Bind approvals to the exact plan (target, text, expectation, precondition)."""
        return ConnectorRequest.make(self.owner_id, self.session_id, "computer", self.spec.kind.value,
                                     {"plan_hash": self.plan_hash})


@dataclass(frozen=True)
class ComputerResult:
    status: ComputerStatus
    kind: ComputerActionKind
    plan: Optional[ComputerActionPlan] = None
    output: Any = None
    verified: bool = False
    rolled_back: bool = False
    pending_id: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is ComputerStatus.SUCCESS and self.verified


def _is_sensitive(element: UIElement) -> bool:
    lowered = element.name.lower()
    return element.is_password or any(term in lowered for term in _SENSITIVE_FIELD)


class ComputerInteractionController:

    def __init__(self, backend: UIBackend, cost_guard: Any = None,
                 approvals: Optional[ConnectorAuthorizationStore] = None, clock=time.time):
        if cost_guard is None:
            from core.cost_guard.guard import cost_guard as default_cost_guard
            cost_guard = default_cost_guard
        self.backend = backend
        self.cost_guard = cost_guard
        self.approvals = approvals or ConnectorAuthorizationStore(clock=clock)
        self._clock = clock
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._audit: List[Dict[str, Any]] = []
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="doom-computer")

    # -- control ----------------------------------------------------------------

    def emergency_stop(self) -> None:
        self._stopped.set()

    def clear_emergency_stop(self) -> None:
        self._stopped.clear()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)

    def audit_log(self, owner_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._audit if e["owner_id"] == owner_id]

    def _record(self, owner_id: str, kind: ComputerActionKind, status: ComputerStatus, target: str) -> None:
        with self._lock:
            self._audit.append({"owner_id": owner_id, "kind": kind.value, "status": status.value,
                                "target": target[:64], "at": self._clock()})
            del self._audit[:-MAX_AUDIT]

    # -- perception + planning -----------------------------------------------------

    def _observe(self) -> UISnapshot:
        return self._bounded(self.backend.observe, CATALOG[ComputerActionKind.OBSERVE].timeout_ms)

    def _bounded(self, fn, timeout_ms: int, *args):
        future = self._pool.submit(fn, *args)
        return future.result(timeout=timeout_ms / 1000.0)

    def plan(self, intent: ComputerIntent) -> Tuple[Optional[ComputerActionPlan], ComputerResult]:
        spec = CATALOG.get(intent.kind)
        refusal = lambda status, detail="": (None, ComputerResult(status, intent.kind, detail=detail))  # noqa: E731
        if spec is None or not intent.owner_id or not intent.session_id:
            return refusal(ComputerStatus.INVALID_REQUEST)
        if self._stopped.is_set():
            return refusal(ComputerStatus.EMERGENCY_STOPPED)
        try:
            snapshot = self._observe()
        except FutureTimeout:
            return refusal(ComputerStatus.TIMEOUT, "observation timed out")
        except Exception as exc:
            return refusal(ComputerStatus.ACTION_FAILED, type(exc).__name__)
        if snapshot.app_kind == "browser":
            return refusal(ComputerStatus.BROWSER_BLOCKED, "browser automation is off")
        if intent.kind is ComputerActionKind.OBSERVE:
            plan = ComputerActionPlan(intent.owner_id, intent.session_id, spec, "", "", "", "", "",
                                      snapshot.observation_hash, snapshot.observation_hash)
            return plan, ComputerResult(ComputerStatus.SUCCESS, intent.kind, plan=plan)
        matches = [e for e in snapshot.find(intent.target_name, intent.target_role)]
        if not matches:
            return refusal(ComputerStatus.TARGET_NOT_FOUND)
        if len(matches) > 1:
            return refusal(ComputerStatus.TARGET_AMBIGUOUS)
        element = matches[0]
        if _is_sensitive(element):
            return refusal(ComputerStatus.SENSITIVE_TARGET, "sensitive fields are never read or typed into")
        if not (element.visible and element.enabled):
            return refusal(ComputerStatus.TARGET_NOT_INTERACTABLE)
        if intent.kind is ComputerActionKind.TYPE:
            if element.role is not UIRole.INPUT:
                return refusal(ComputerStatus.INVALID_REQUEST, "TYPE requires an input")
            if not intent.text or len(intent.text) > MAX_TYPE_CHARS or any(ord(c) < 32 for c in intent.text):
                return refusal(ComputerStatus.INVALID_REQUEST, "text must be 1-256 printable characters")
        if intent.kind is ComputerActionKind.CLICK:
            if element.role not in (UIRole.BUTTON, UIRole.CHECKBOX):
                return refusal(ComputerStatus.INVALID_REQUEST, "CLICK requires a button or checkbox")
            if not intent.expect_label or not snapshot.find(intent.expect_label, UIRole.LABEL):
                return refusal(ComputerStatus.INVALID_REQUEST, "CLICK needs an observable expected result")
        payload = json.dumps([intent.owner_id, intent.session_id, intent.kind.value, element.element_id,
                              intent.text, intent.expect_label, intent.expect_value,
                              snapshot.observation_hash], sort_keys=True)
        plan = ComputerActionPlan(
            owner_id=intent.owner_id, session_id=intent.session_id, spec=spec, element_id=element.element_id,
            target_name=element.name, text=intent.text, expect_label=intent.expect_label,
            expect_value=intent.expect_value, precondition_hash=snapshot.observation_hash,
            plan_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest())
        return plan, ComputerResult(ComputerStatus.SUCCESS, intent.kind, plan=plan)

    # -- execution -----------------------------------------------------------------

    def execute(self, plan: ComputerActionPlan, identity: IntegrationIdentity,
                authorized_hash: str = "") -> ComputerResult:
        kind = plan.spec.kind
        result = lambda status, **kw: self._finish(plan, ComputerResult(status, kind, plan=plan, **kw))  # noqa: E731
        if not isinstance(identity, IntegrationIdentity) or identity.owner_id != plan.owner_id \
                or identity.session_id != plan.session_id:
            return result(ComputerStatus.OWNER_MISMATCH)
        if self._stopped.is_set():
            return result(ComputerStatus.EMERGENCY_STOPPED)
        decision = self.cost_guard.decision(ResourceRequest(
            resource_type=ResourceType.VISION, provider="local_uia", capability=plan.spec.capability))
        if not decision.is_allow:
            return result(ComputerStatus.COST_BLOCKED)
        if plan.spec.requires_authorization:
            request = plan.as_request()
            if not authorized_hash:
                return result(ComputerStatus.APPROVAL_REQUIRED, pending_id=self.approvals.stash(request))
            if authorized_hash != request.request_hash() or not self.approvals.redeem(authorized_hash, identity):
                return result(ComputerStatus.AUTHORIZATION_INVALID)
        try:
            before = self._observe()
        except FutureTimeout:
            return result(ComputerStatus.TIMEOUT, detail="observation timed out")
        if before.app_kind == "browser":
            return result(ComputerStatus.BROWSER_BLOCKED)
        if kind is ComputerActionKind.OBSERVE:
            return result(ComputerStatus.SUCCESS, verified=True,
                          output={"window": before.window_title,
                                  "elements": [(e.role.value, e.name) for e in before.elements if e.visible]})
        if before.observation_hash != plan.precondition_hash:
            return result(ComputerStatus.STALE_OBSERVATION, detail="the screen changed since the plan was made")
        element = next((e for e in before.elements if e.element_id == plan.element_id), None)
        if element is None:
            return result(ComputerStatus.TARGET_NOT_FOUND)
        if kind is ComputerActionKind.READ_VALUE:
            return result(ComputerStatus.SUCCESS, verified=True, output={"value": element.value})
        previous_value = element.value
        try:
            if kind is ComputerActionKind.CLICK:
                self._bounded(self.backend.click, plan.spec.timeout_ms, plan.element_id)
            else:
                self._bounded(self.backend.set_text, plan.spec.timeout_ms, plan.element_id, plan.text)
        except FutureTimeout:
            return result(ComputerStatus.TIMEOUT, detail="action timed out")
        except Exception as exc:
            return result(ComputerStatus.ACTION_FAILED, detail=type(exc).__name__)
        after = self._observe()
        if kind is ComputerActionKind.CLICK:
            label = next(iter(after.find(plan.expect_label, UIRole.LABEL)), None)
            verified = label is not None and label.value == plan.expect_value
            if not verified:
                return result(ComputerStatus.VERIFICATION_FAILED,
                              detail="the expected result was not observed (a click cannot be undone)")
            return result(ComputerStatus.SUCCESS, verified=True, output={plan.expect_label: label.value})
        target_after = next((e for e in after.elements if e.element_id == plan.element_id), None)
        if target_after is not None and target_after.value == plan.text:
            return result(ComputerStatus.SUCCESS, verified=True, output={"value": target_after.value})
        rolled_back = False
        try:
            self._bounded(self.backend.set_text, plan.spec.timeout_ms, plan.element_id, previous_value)
            restored = next((e for e in self._observe().elements if e.element_id == plan.element_id), None)
            rolled_back = restored is not None and restored.value == previous_value
        except Exception:
            rolled_back = False
        return result(ComputerStatus.VERIFICATION_FAILED, rolled_back=rolled_back,
                      detail="typed text was not confirmed; previous value restored" if rolled_back
                      else "typed text was not confirmed; restore failed")

    def _finish(self, plan: ComputerActionPlan, res: ComputerResult) -> ComputerResult:
        self._record(plan.owner_id, plan.spec.kind, res.status, plan.target_name)
        return res


# --- deterministic simulated UI (mirrors the Safe Test Window contract) ----------

class SimulatedUIBackend(UIBackend):
    """In-memory window: DOOM_TEST_BUTTON (READY -> CLICKED), DOOM_TEST_INPUT, a password
    field and a status label. Faults can be injected for testing."""

    def __init__(self, app_kind: str = "app"):
        self.app_kind = app_kind
        self._lock = threading.Lock()
        self.status = "READY"
        self.input_value = ""
        self.password_value = "do-not-read"
        self.click_has_effect = True
        self.type_has_effect = True
        self.delay_s = 0.0
        self.extra: List[UIElement] = []
        self.clicks = 0

    def observe(self) -> UISnapshot:
        with self._lock:
            elements = (
                UIElement("btn1", UIRole.BUTTON, "DOOM_TEST_BUTTON"),
                UIElement("in1", UIRole.INPUT, "DOOM_TEST_INPUT", value=self.input_value),
                UIElement("pw1", UIRole.INPUT, "Password", value=self.password_value, is_password=True),
                UIElement("lbl1", UIRole.LABEL, "Status", value=self.status),
            ) + tuple(self.extra)
            return UISnapshot("DOOM Safe Test Window", self.app_kind, elements)

    def click(self, element_id: str) -> None:
        if self.delay_s:
            time.sleep(self.delay_s)
        with self._lock:
            self.clicks += 1
            if element_id == "btn1" and self.click_has_effect:
                self.status = "CLICKED"

    def set_text(self, element_id: str, text: str) -> None:
        if self.delay_s:
            time.sleep(self.delay_s)
        with self._lock:
            if element_id == "in1" and (self.type_has_effect or text == "" or text == self.input_value):
                self.input_value = text
            elif element_id == "in1" and not self.type_has_effect:
                self.input_value = text[:-1]  # simulate a dropped character
                self.type_has_effect = True   # the restore that follows succeeds
