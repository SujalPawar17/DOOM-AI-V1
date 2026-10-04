"""V12.6 Real-Time Cognitive Loop.

    Event sources (timers, system, monitored goals [V11.5], applications, integrations, user)
      -> normalization (RuntimeEvent) -> dedup -> cooldown -> bounded priority queue (backpressure)
      -> single worker -> context update (perceptual system event) -> cognitive cycle
         (monitoring-flagged: full Cost Guard / authorization / verification chain, never
         learned as a user statement, never auto-approved) -> outbox (bounded)

Guarantees: bounded queue with per-owner quotas and priority-aware backpressure; bounded
dedup memory; per (owner, source, type) cooldown; per-minute cycle budget; circuit
breaker on consecutive handler failures; one worker thread, explicit start/stop/restart,
no infinite loops (every wait has a timeout, every tick a work budget); owner/session
carried per event and never mixed. The existing V11.5 monitor is integrated by a
subclass that routes its events into this runtime instead of spawning threads.
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

MAX_QUEUE = 128
MAX_PER_OWNER = 32
DEDUP_WINDOW_S = 300.0
MAX_DEDUP_KEYS = 1024
DEFAULT_COOLDOWN_S = 30.0
MAX_CYCLES_PER_MINUTE = 12
BREAKER_THRESHOLD = 3
BREAKER_PAUSE_S = 60.0
MAX_OUTBOX = 64
TICK_BUDGET = 8


class EventPriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    INFO = 4


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    owner_id: str
    session_id: str
    source: str
    event_type: str
    priority: EventPriority
    description: str
    payload: Tuple[Tuple[str, Any], ...]
    timestamp: float
    dedup_key: str

    @staticmethod
    def make(owner_id: str, session_id: str, source: str, event_type: str, description: str = "",
             priority: EventPriority = EventPriority.NORMAL, payload: Optional[Dict[str, Any]] = None,
             timestamp: Optional[float] = None, dedup_key: str = "") -> "RuntimeEvent":
        scalar = tuple(sorted((str(k)[:32], v if isinstance(v, (str, int, float, bool)) or v is None else str(v))
                              for k, v in (payload or {}).items()))[:12]
        key = dedup_key or hashlib.sha256(json.dumps(
            [owner_id, session_id, source, event_type, description[:120]], sort_keys=True).encode()).hexdigest()[:20]
        ts = float(timestamp if timestamp is not None else time.time())
        eid = hashlib.sha256(f"{key}|{ts}".encode()).hexdigest()[:20]
        return RuntimeEvent(eid, owner_id, session_id, source[:32], event_type[:48], priority,
                            description[:240], scalar, ts, key)


@dataclass
class RuntimeStats:
    submitted: int = 0
    accepted: int = 0
    deduplicated: int = 0
    cooled_down: int = 0
    rejected_backpressure: int = 0
    evicted_backpressure: int = 0
    processed: int = 0
    failures: int = 0
    rate_limited: int = 0
    breaker_trips: int = 0
    timer_events: int = 0


@dataclass
class _Timer:
    name: str
    owner_id: str
    session_id: str
    interval_s: float
    event_type: str
    priority: EventPriority
    next_due: float


class RealtimeRuntime:

    def __init__(self, handler: Callable[[RuntimeEvent], Any], clock: Callable[[], float] = time.time,
                 cooldown_s: float = DEFAULT_COOLDOWN_S, max_cycles_per_minute: int = MAX_CYCLES_PER_MINUTE):
        self._handler = handler
        self._clock = clock
        self._cooldown_s = cooldown_s
        self._max_per_minute = max_cycles_per_minute
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._heap: List[Tuple[int, float, int, RuntimeEvent]] = []
        self._seq = itertools.count()
        self._per_owner: Dict[str, int] = {}
        self._dedup: "OrderedDict[str, float]" = OrderedDict()
        self._last_fire: Dict[Tuple[str, str, str], float] = {}
        self._recent_cycles: Deque[float] = deque()
        self._consecutive_failures = 0
        self._breaker_until = 0.0
        self._timers: Dict[str, _Timer] = {}
        self._outbox: Deque[Dict[str, Any]] = deque(maxlen=MAX_OUTBOX)
        self.stats = RuntimeStats()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # -- intake ---------------------------------------------------------------------

    def submit(self, event: RuntimeEvent) -> str:
        """Returns 'ACCEPTED', 'DEDUPLICATED', 'COOLDOWN' or 'BACKPRESSURE'."""
        now = self._clock()
        with self._cond:
            self.stats.submitted += 1
            for k in [k for k, ts in self._dedup.items() if now - ts > DEDUP_WINDOW_S]:
                del self._dedup[k]
            if event.dedup_key in self._dedup:
                self.stats.deduplicated += 1
                return "DEDUPLICATED"
            cool_key = (event.owner_id, event.source, event.event_type)
            if event.priority > EventPriority.CRITICAL and now - self._last_fire.get(cool_key, -1e18) < self._cooldown_s:
                self.stats.cooled_down += 1
                return "COOLDOWN"
            if self._per_owner.get(event.owner_id, 0) >= MAX_PER_OWNER or len(self._heap) >= MAX_QUEUE:
                if not self._evict_lower_locked(event):
                    self.stats.rejected_backpressure += 1
                    return "BACKPRESSURE"
            self._dedup[event.dedup_key] = now
            while len(self._dedup) > MAX_DEDUP_KEYS:
                self._dedup.popitem(last=False)
            self._last_fire[cool_key] = now
            heapq.heappush(self._heap, (int(event.priority), event.timestamp, next(self._seq), event))
            self._per_owner[event.owner_id] = self._per_owner.get(event.owner_id, 0) + 1
            self.stats.accepted += 1
            self._cond.notify()
            return "ACCEPTED"

    def _evict_lower_locked(self, incoming: RuntimeEvent) -> bool:
        """Make room only by dropping a strictly lower-priority event (same owner first
        when that owner is at quota)."""
        owner_full = self._per_owner.get(incoming.owner_id, 0) >= MAX_PER_OWNER
        candidates = [entry for entry in self._heap
                      if entry[3].priority > incoming.priority
                      and (not owner_full or entry[3].owner_id == incoming.owner_id)]
        if not candidates:
            return False
        victim = max(candidates, key=lambda e: (e[0], -e[1]))
        self._heap.remove(victim)
        heapq.heapify(self._heap)
        self._per_owner[victim[3].owner_id] -= 1
        self.stats.evicted_backpressure += 1
        return True

    def add_timer(self, name: str, owner_id: str, session_id: str, interval_s: float, event_type: str,
                  priority: EventPriority = EventPriority.LOW) -> None:
        if interval_s < 1.0:
            raise ValueError("timer interval must be at least 1 second")
        with self._lock:
            self._timers[name] = _Timer(name, owner_id, session_id, interval_s, event_type, priority,
                                        self._clock() + interval_s)

    def remove_timer(self, name: str) -> None:
        with self._lock:
            self._timers.pop(name, None)

    def _fire_due_timers(self) -> None:
        now = self._clock()
        with self._lock:
            due = [t for t in self._timers.values() if t.next_due <= now]
            for t in due:
                t.next_due = now + t.interval_s  # never catch up in bursts
        for t in due:
            self.stats.timer_events += 1
            self.submit(RuntimeEvent.make(t.owner_id, t.session_id, "timer", t.event_type,
                                          f"timer {t.name}", t.priority, timestamp=now,
                                          dedup_key=f"timer:{t.name}:{int(now)}"))

    # -- processing -------------------------------------------------------------------

    def run_once(self, budget: int = TICK_BUDGET) -> int:
        """Process up to `budget` events. Deterministic; used by the worker and tests."""
        self._fire_due_timers()
        processed = 0
        while processed < budget:
            now = self._clock()
            with self._cond:
                if not self._heap:
                    break
                if now < self._breaker_until:
                    break
                while self._recent_cycles and now - self._recent_cycles[0] > 60.0:
                    self._recent_cycles.popleft()
                if len(self._recent_cycles) >= self._max_per_minute:
                    self.stats.rate_limited += 1
                    break
                _, _, _, event = heapq.heappop(self._heap)
                self._per_owner[event.owner_id] -= 1
                self._recent_cycles.append(now)
            try:
                outcome = self._handler(event)
                with self._lock:
                    self._consecutive_failures = 0
                    self.stats.processed += 1
                    self._outbox.append({"event_id": event.event_id, "owner_id": event.owner_id,
                                         "session_id": event.session_id, "event_type": event.event_type,
                                         "outcome": outcome})
            except Exception as exc:
                with self._lock:
                    self.stats.failures += 1
                    self._consecutive_failures += 1
                    self._outbox.append({"event_id": event.event_id, "owner_id": event.owner_id,
                                         "session_id": event.session_id, "event_type": event.event_type,
                                         "error": type(exc).__name__})
                    if self._consecutive_failures >= BREAKER_THRESHOLD:
                        self._breaker_until = self._clock() + BREAKER_PAUSE_S
                        self._consecutive_failures = 0
                        self.stats.breaker_trips += 1
            processed += 1
        return processed

    def outbox(self, owner_id: str, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(m) for m in self._outbox
                    if m["owner_id"] == owner_id and (session_id is None or m["session_id"] == session_id)]

    def queue_size(self, owner_id: Optional[str] = None) -> int:
        with self._lock:
            return len(self._heap) if owner_id is None else self._per_owner.get(owner_id, 0)

    @property
    def breaker_open(self) -> bool:
        return self._clock() < self._breaker_until

    # -- lifecycle -----------------------------------------------------------------------

    def start(self, tick_s: float = 0.25) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(target=self._loop, args=(tick_s,), daemon=True,
                                            name="doom-realtime-runtime")
            self._worker.start()

    def _loop(self, tick_s: float) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                self.stats.failures += 1
            with self._cond:
                if not self._stop.is_set():
                    self._cond.wait(timeout=tick_s)

    def stop(self, timeout_s: float = 10.0) -> bool:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=timeout_s)
            if worker.is_alive():
                return False
        self._worker = None
        return True

    def restart(self, tick_s: float = 0.25) -> None:
        self.stop()
        self.start(tick_s)

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()


# --- cognitive handler + V11.5 monitor integration ------------------------------------

class CognitiveEventHandler:
    """Turns a runtime event into a perceptual update plus a monitoring-flagged cognitive
    cycle. It never approves anything and never speaks; results go to the outbox."""

    def __init__(self, orchestrator: Any):
        self.orchestrator = orchestrator

    def __call__(self, event: RuntimeEvent) -> Dict[str, Any]:
        normalizer = getattr(self.orchestrator, "normalizer", None)
        if normalizer is not None and hasattr(self.orchestrator, "perceive"):
            self.orchestrator.perceive(normalizer.system_event(
                event.owner_id, event.session_id, event.event_type, dict(event.payload),
                source=event.source, timestamp=event.timestamp))
        result = self.orchestrator.process_cognitive_cycle(
            user_input=f"Event: {event.description or event.event_type}",
            owner_id=event.owner_id, session_id=event.session_id,
            context={"monitoring_trigger": True, "event_id": event.event_id, "event_type": event.event_type,
                     "priority": event.priority.name, "source": event.source},
        )
        execution = (result.get("stages") or {}).get("execution") or {}
        return {"success": bool(result.get("success")), "response_text": result.get("response_text", ""),
                "executed": bool(execution.get("executed")),
                "pending_id": ((result.get("stages") or {}).get("authorization") or {}).get("pending_id")}


def _monitoring_priority(priority_name: str) -> EventPriority:
    return {"CRITICAL": EventPriority.CRITICAL, "HIGH": EventPriority.HIGH, "NORMAL": EventPriority.NORMAL,
            "LOW": EventPriority.LOW}.get(priority_name, EventPriority.INFO)


def runtime_event_from_monitoring(event: Any) -> RuntimeEvent:
    return RuntimeEvent.make(
        owner_id=event.owner_id, session_id=event.session_id, source=f"v11_monitor:{event.source}",
        event_type=event.event_type.value, description=event.description,
        priority=_monitoring_priority(event.priority.name), timestamp=event.timestamp,
        dedup_key=f"v11:{event.state_fingerprint or event.event_id}")


def make_runtime_monitor(runtime: RealtimeRuntime, owner_id: str, session_id: str = "", config: Any = None):
    """The existing V11.5 monitor, with its event handling routed into the runtime queue
    (detection, fingerprinting, dedup, cooldown and rate limiting are all V11.5's)."""
    from core.v11.proactive_behavior import ContinuousMonitoringEnhancement

    class RuntimeAttachedMonitor(ContinuousMonitoringEnhancement):
        def _handle_monitoring_event(self, event):  # noqa: D401 - V11.5 hook
            now = time.time()
            self.state.last_cycle_time = now
            self.state.cycles_in_last_hour.append(now)
            runtime.submit(runtime_event_from_monitoring(event))

    return RuntimeAttachedMonitor(owner_id=owner_id, session_id=session_id, config=config)
