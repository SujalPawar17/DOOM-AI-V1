"""V12.3 External Integration Framework.

    Intent -> Tool selection -> Capability declaration -> Cost Guard -> Authorization
           -> Connector -> Execution -> Verification -> Outcome -> Experience

Every connector *declares* its capability, actions, risk, cost resource, permissions,
owner scope, session requirement, verification method, timeout and retry policy.
The IntegrationGateway is the only way to run a connector and enforces, in order:

 1. trusted identity             (IntegrationIdentity, never derived from the request)
 2. owner / session match        (request vs identity)
 3. registry lookup              (unknown connector / action -> refused)
 4. enabled                      (cloud connectors are disabled by default)
 5. argument validation          (declared types, required, bounds, forbidden keys)
 6. Cost Guard                   (existing attestations only; unattested -> blocked)
 7. authorization                (MEDIUM+ risk or mutating actions need a single-use,
                                  hash-bound, TTL-limited approval)
 8. bounded execution            (shared bounded worker pool, timeout, retries only
                                  for idempotent actions)
 9. verification                 (connector-declared verification of the result)
10. outcome + experience         (only for attempts that actually executed)

Fail closed: any unexpected error yields a non-success result; no step is skippable.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.cost_guard.types import ResourceRequest, ResourceType

MAX_OUTPUT_CHARS = 4000
MAX_ARG_CHARS = 4000
MAX_PENDING = 256
APPROVAL_TTL_SECONDS = 120
MAX_RETRIES_CAP = 2
_WORKERS = 4
FORBIDDEN_ARG_KEYS = frozenset({
    "shell", "command", "cmd", "exec", "eval", "code", "script", "callback", "lambda",
    "password", "token", "cookie", "csrf", "api_key", "secret", "credential",
})
RISK_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


class ConnectorStatus(str, Enum):
    SUCCESS = "SUCCESS"
    IDENTITY_REQUIRED = "IDENTITY_REQUIRED"
    OWNER_MISMATCH = "OWNER_MISMATCH"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    UNKNOWN_CONNECTOR = "UNKNOWN_CONNECTOR"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    DISABLED = "DISABLED"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    COST_BLOCKED = "COST_BLOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    AUTHORIZATION_INVALID = "AUTHORIZATION_INVALID"
    TIMEOUT = "TIMEOUT"
    CONNECTOR_FAILED = "CONNECTOR_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


EXECUTED_STATUSES = frozenset({ConnectorStatus.SUCCESS, ConnectorStatus.CONNECTOR_FAILED,
                               ConnectorStatus.TIMEOUT, ConnectorStatus.VERIFICATION_FAILED})


class ConnectorError(Exception):
    """Raised by a connector for an expected, user-reportable failure."""


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: type = str
    required: bool = True
    max_len: int = 512
    min_value: Optional[int] = None
    max_value: Optional[int] = None


@dataclass(frozen=True)
class ActionSpec:
    name: str
    params: Tuple[ParamSpec, ...]
    risk: str = "LOW"
    mutates: bool = False
    idempotent: bool = True
    verification: str = "result_present"
    reversible: bool = True  # V12.7: False marks actions that cannot be undone


@dataclass(frozen=True)
class ConnectorSpec:
    connector_id: str
    capability: str
    description: str
    actions: Tuple[ActionSpec, ...]
    cost_resource: Tuple[ResourceType, str, str]  # (type, provider, host) -> existing attestation
    permissions: Tuple[str, ...]
    owner_scope: str = "OWNER_ONLY"
    session_required: bool = True
    timeout_ms: int = 5000
    max_retries: int = 0
    enabled_by_default: bool = True

    def action(self, name: str) -> Optional[ActionSpec]:
        return next((a for a in self.actions if a.name == name), None)


@dataclass(frozen=True)
class IntegrationIdentity:
    """Trusted caller identity (from the session layer, never from the request)."""
    owner_id: str
    session_id: str


@dataclass(frozen=True)
class ConnectorRequest:
    owner_id: str
    session_id: str
    connector_id: str
    action: str
    args: Tuple[Tuple[str, Any], ...] = ()

    @staticmethod
    def make(owner_id: str, session_id: str, connector_id: str, action: str,
             args: Optional[Dict[str, Any]] = None) -> "ConnectorRequest":
        return ConnectorRequest(owner_id, session_id, connector_id, action,
                                tuple(sorted((args or {}).items())))

    def args_dict(self) -> Dict[str, Any]:
        return dict(self.args)

    def request_hash(self) -> str:
        canonical = json.dumps([self.owner_id, self.session_id, self.connector_id, self.action,
                                [[k, v] for k, v in self.args]], sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConnectorResult:
    status: ConnectorStatus
    connector_id: str
    action: str
    request_hash: str
    output: Any = None
    verified: bool = False
    attempts: int = 0
    pending_id: str = ""
    detail: str = ""
    experience_recorded: bool = False

    @property
    def ok(self) -> bool:
        return self.status is ConnectorStatus.SUCCESS and self.verified


class Connector(ABC):
    spec: ConnectorSpec

    @abstractmethod
    def execute(self, action: str, args: Dict[str, Any], identity: IntegrationIdentity) -> Any:
        """Perform the action. Raise ConnectorError for expected failures."""

    def verify(self, action: str, args: Dict[str, Any], output: Any, identity: IntegrationIdentity) -> bool:
        return output is not None


class ConnectorRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._connectors: Dict[str, Connector] = {}
        self._enabled: Dict[str, bool] = {}

    def register(self, connector: Connector, enabled: Optional[bool] = None) -> None:
        spec = connector.spec
        if not spec.connector_id or spec.owner_scope != "OWNER_ONLY":
            raise ValueError("connectors must be owner-scoped and named")
        if spec.max_retries > MAX_RETRIES_CAP:
            raise ValueError("retry policy exceeds the framework cap")
        with self._lock:
            self._connectors[spec.connector_id] = connector
            self._enabled[spec.connector_id] = spec.enabled_by_default if enabled is None else bool(enabled)

    def get(self, connector_id: str) -> Optional[Connector]:
        with self._lock:
            return self._connectors.get(connector_id)

    def is_enabled(self, connector_id: str) -> bool:
        with self._lock:
            return bool(self._enabled.get(connector_id))

    def set_enabled(self, connector_id: str, enabled: bool) -> None:
        with self._lock:
            if connector_id in self._connectors:
                self._enabled[connector_id] = bool(enabled)

    def specs(self) -> List[ConnectorSpec]:
        with self._lock:
            return [c.spec for c in self._connectors.values()]


@dataclass
class _Pending:
    owner_id: str
    session_id: str
    request_hash: str
    expires_at: float
    state: str = "PENDING"


class ConnectorAuthorizationStore:
    """Single-use, hash-bound, TTL-limited approvals for risky / mutating actions."""

    def __init__(self, clock: Callable[[], float] = time.time, ttl: float = APPROVAL_TTL_SECONDS):
        self._lock = threading.Lock()
        self._pending: Dict[str, _Pending] = {}
        self._clock = clock
        self._ttl = ttl

    def _purge_locked(self, now: float) -> None:
        dead = [k for k, p in self._pending.items() if p.state == "USED" or p.expires_at <= now]
        for k in dead:
            del self._pending[k]
        while len(self._pending) >= MAX_PENDING:
            oldest = min(self._pending, key=lambda k: self._pending[k].expires_at)
            del self._pending[oldest]

    def stash(self, request: ConnectorRequest) -> str:
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            for pid, p in self._pending.items():  # identical live request -> same approval
                if (p.state == "PENDING" and p.request_hash == request.request_hash()
                        and p.owner_id == request.owner_id and p.session_id == request.session_id):
                    return pid
            pid = str(uuid.uuid4())
            self._pending[pid] = _Pending(request.owner_id, request.session_id, request.request_hash(),
                                          now + self._ttl)
            return pid

    def claim(self, pending_id: str, identity: IntegrationIdentity) -> Tuple[Optional[str], str]:
        """Approve a pending request (user action). Returns (authorized_request_hash, code).
        The hash is redeemable only because this claim exists; computing it is not enough."""
        now = self._clock()
        with self._lock:
            p = self._pending.get(str(pending_id or ""))
            if p is None:
                return None, "NOT_FOUND"
            if p.state != "PENDING":
                return None, "CONSUMED"
            if p.expires_at <= now:
                del self._pending[pending_id]
                return None, "EXPIRED"
            if p.owner_id != identity.owner_id or p.session_id != identity.session_id:
                return None, "AUTHORIZATION_INVALID"
            p.state = "CLAIMED"
            return p.request_hash, "OK"

    def redeem(self, request_hash: str, identity: IntegrationIdentity) -> bool:
        """Consume a CLAIMED approval for exactly this request + identity (single use)."""
        now = self._clock()
        with self._lock:
            for p in self._pending.values():
                if (p.state == "CLAIMED" and p.request_hash == request_hash and p.expires_at > now
                        and p.owner_id == identity.owner_id and p.session_id == identity.session_id):
                    p.state = "USED"
                    return True
            return False

    def size(self) -> int:
        with self._lock:
            return len(self._pending)


def _redact(value: Any) -> Any:
    from orchestration.conversation.respond import _redact as v8_redact
    if isinstance(value, str):
        text = v8_redact(value)
        return text if len(text) <= MAX_OUTPUT_CHARS else text[:MAX_OUTPUT_CHARS] + "...[TRUNCATED]"
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in list(value)[:500]]
    return value


class IntegrationGateway:

    def __init__(self, registry: ConnectorRegistry, cost_guard: Any = None,
                 approvals: Optional[ConnectorAuthorizationStore] = None,
                 record_experience: Optional[Callable[[ConnectorRequest, ConnectorResult], bool]] = None,
                 clock: Callable[[], float] = time.time):
        if cost_guard is None:
            from core.cost_guard.guard import cost_guard as default_cost_guard
            cost_guard = default_cost_guard
        self.registry = registry
        self.cost_guard = cost_guard
        self.approvals = approvals or ConnectorAuthorizationStore(clock=clock)
        self._record_experience = record_experience if record_experience is not None else record_connector_experience
        self._clock = clock
        self._pool = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="doom-connector")

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)

    # -- main entry ------------------------------------------------------------

    def invoke(self, request: ConnectorRequest, identity: Optional[IntegrationIdentity],
               authorized_request_hash: str = "") -> ConnectorResult:
        rh = request.request_hash()

        def result(status: ConnectorStatus, **kw) -> ConnectorResult:
            return ConnectorResult(status, request.connector_id, request.action, rh, **kw)

        # 1-2. identity, owner, session
        if not isinstance(identity, IntegrationIdentity) or not identity.owner_id:
            return result(ConnectorStatus.IDENTITY_REQUIRED)
        if request.owner_id != identity.owner_id:
            return result(ConnectorStatus.OWNER_MISMATCH)
        connector = self.registry.get(request.connector_id)
        # 3-4. registry, enabled
        if connector is None:
            return result(ConnectorStatus.UNKNOWN_CONNECTOR)
        spec = connector.spec
        if spec.session_required and (not identity.session_id or request.session_id != identity.session_id):
            return result(ConnectorStatus.SESSION_MISMATCH)
        if not self.registry.is_enabled(spec.connector_id):
            return result(ConnectorStatus.DISABLED)
        action = spec.action(request.action)
        if action is None:
            return result(ConnectorStatus.UNKNOWN_ACTION)
        # 5. arguments
        problem = _validate_args(action, request.args_dict())
        if problem:
            return result(ConnectorStatus.INVALID_ARGUMENTS, detail=problem)
        # 6. Cost Guard
        rtype, provider, host = spec.cost_resource
        decision = self.cost_guard.decision(ResourceRequest(
            resource_type=rtype, provider=provider, host=host, capability=spec.capability))
        if not decision.is_allow:
            return result(ConnectorStatus.COST_BLOCKED, detail=str(getattr(decision.reason, "value", "")))
        # 7. authorization
        needs_approval = action.mutates or RISK_RANK.get(action.risk, 4) >= RISK_RANK["MEDIUM"]
        if needs_approval:
            if not authorized_request_hash:
                return result(ConnectorStatus.APPROVAL_REQUIRED, pending_id=self.approvals.stash(request))
            if authorized_request_hash != rh or not self.approvals.redeem(rh, identity):
                return result(ConnectorStatus.AUTHORIZATION_INVALID)
        # 8-9. execute + verify
        outcome = self._run(connector, action, request, identity)  # a used approval is never re-used
        # 10. experience (only for real attempts)
        recorded = False
        if outcome.status in EXECUTED_STATUSES:
            try:
                recorded = bool(self._record_experience(request, outcome))
            except Exception:
                recorded = False
        return ConnectorResult(outcome.status, request.connector_id, request.action, rh,
                               output=outcome.output, verified=outcome.verified, attempts=outcome.attempts,
                               detail=outcome.detail, experience_recorded=recorded)

    def _run(self, connector: Connector, action: ActionSpec, request: ConnectorRequest,
             identity: IntegrationIdentity) -> ConnectorResult:
        spec = connector.spec
        args = request.args_dict()
        max_tries = 1 + (min(spec.max_retries, MAX_RETRIES_CAP) if action.idempotent and not action.mutates else 0)
        attempts, last_detail = 0, ""
        for _ in range(max_tries):
            attempts += 1
            future = self._pool.submit(connector.execute, action.name, dict(args), identity)
            try:
                output = future.result(timeout=spec.timeout_ms / 1000.0)
            except FutureTimeout:
                future.cancel()
                return ConnectorResult(ConnectorStatus.TIMEOUT, spec.connector_id, action.name,
                                       request.request_hash(), attempts=attempts,
                                       detail=f"exceeded {spec.timeout_ms} ms")
            except ConnectorError as exc:
                last_detail = str(exc)[:200]
                continue
            except Exception as exc:
                last_detail = type(exc).__name__
                continue
            try:
                verified = bool(connector.verify(action.name, dict(args), output, identity))
            except Exception:
                verified = False
            status = ConnectorStatus.SUCCESS if verified else ConnectorStatus.VERIFICATION_FAILED
            return ConnectorResult(status, spec.connector_id, action.name, request.request_hash(),
                                   output=_redact(output), verified=verified, attempts=attempts)
        return ConnectorResult(ConnectorStatus.CONNECTOR_FAILED, spec.connector_id, action.name,
                               request.request_hash(), attempts=attempts, detail=_redact(last_detail))


def _validate_args(action: ActionSpec, args: Dict[str, Any]) -> str:
    declared = {p.name: p for p in action.params}
    for key in args:
        if str(key).lower() in FORBIDDEN_ARG_KEYS:
            return f"forbidden argument '{key}'"
        if key not in declared:
            return f"undeclared argument '{key}'"
    for p in action.params:
        if p.name not in args:
            if p.required:
                return f"missing argument '{p.name}'"
            continue
        value = args[p.name]
        if p.type is int and (isinstance(value, bool) or not isinstance(value, int)):
            return f"'{p.name}' must be an integer"
        if p.type is bool and not isinstance(value, bool):
            return f"'{p.name}' must be a boolean"
        if p.type is str:
            if not isinstance(value, str):
                return f"'{p.name}' must be text"
            if len(value) > min(p.max_len, MAX_ARG_CHARS) or "\x00" in value:
                return f"'{p.name}' is too long or malformed"
        if p.type is int:
            if p.min_value is not None and value < p.min_value:
                return f"'{p.name}' is below {p.min_value}"
            if p.max_value is not None and value > p.max_value:
                return f"'{p.name}' is above {p.max_value}"
    return ""


def record_connector_experience(request: ConnectorRequest, result: ConnectorResult) -> bool:
    """Record an executed connector attempt in the V8.28 Goal Experience store."""
    from orchestration.experience.store import create_experience
    outcome = "COMPLETED" if result.ok else "ABANDONED"
    res = create_experience(request.owner_id, {
        "owner_id": request.owner_id,
        "title": f"Connector {request.connector_id}.{request.action}",
        "outcome": outcome,
        "step_summary": (f"{request.connector_id} {request.action.upper()} status:{result.status.value}",),
        "blockers": () if result.ok else (f"connector:{result.status.value.lower()}",),
        "user_note": "",
        "tags": ("v12_connector", f"cap_{request.connector_id}"[:32]),
        "source_goal_id": f"conn_{request.request_hash()[:24]}",
    })
    return str(getattr(getattr(res, "status", None), "value", "")) == "OK"
