"""Bounded in-process telemetry bus. Subscriber failures never raise to producers."""

from __future__ import annotations

import threading
from collections import deque
from typing import Callable, Deque, List, Optional

from observability.schemas import OperationalEvent

Subscriber = Callable[[OperationalEvent], None]


class TelemetryBus:
    def __init__(self, capacity: int = 1000):
        self.capacity = max(1, capacity)
        self._q: Deque[OperationalEvent] = deque()
        self._lock = threading.Lock()
        self._subscribers: List[Subscriber] = []
        self.dropped = 0
        self.published = 0

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def publish(self, event: OperationalEvent) -> bool:
        """Nonblocking enqueue + notify. Returns False if dropped due to capacity."""
        dropped_now = False
        with self._lock:
            if len(self._q) >= self.capacity:
                self.dropped += 1
                dropped_now = True
                if self._q:
                    self._q.popleft()
            self._q.append(event)
            self.published += 1
            subs = list(self._subscribers)
        for sub in subs:
            try:
                sub(event)
            except Exception:
                pass
        return not dropped_now

    def snapshot(self) -> List[OperationalEvent]:
        with self._lock:
            return list(self._q)

    def clear(self) -> None:
        with self._lock:
            self._q.clear()
            self.dropped = 0
            self.published = 0


telemetry_bus = TelemetryBus()
