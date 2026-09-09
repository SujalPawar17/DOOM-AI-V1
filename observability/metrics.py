"""Canonical metrics derived from OperationalEvents."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Dict, List

from observability.schemas import OperationalEvent


class MetricsRegistry:
    def __init__(self, histogram_bound: int = 256):
        self._lock = threading.Lock()
        self.counters: Dict[str, int] = defaultdict(int)
        self.gauges: Dict[str, float] = {}
        self._hist: Dict[str, List[float]] = defaultdict(list)
        self._histogram_bound = histogram_bound

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.counters[name] += n

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            samples = self._hist[name]
            samples.append(float(value))
            if len(samples) > self._histogram_bound:
                del samples[: len(samples) - self._histogram_bound]

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self.gauges[name] = float(value)

    def get(self, name: str) -> int:
        with self._lock:
            return int(self.counters.get(name, 0))

    def histogram_avg(self, name: str) -> float:
        with self._lock:
            samples = self._hist.get(name) or []
            if not samples:
                return 0.0
            return sum(samples) / len(samples)

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "gauges": dict(self.gauges),
                "histograms": {k: list(v) for k, v in self._hist.items()},
            }

    def reset(self) -> None:
        with self._lock:
            self.counters.clear()
            self.gauges.clear()
            self._hist.clear()

    def on_event(self, event: OperationalEvent) -> None:
        lat = event.latency_ms
        if event.name == "request.completed" or event.name == "request.started" and event.status != "ok":
            pass
        if event.category == "request":
            if event.name.endswith("started"):
                self.inc("request_total")
            if event.name.endswith("completed"):
                if event.status == "ok":
                    self.inc("request_success_total")
                else:
                    self.inc("request_failure_total")
                if lat is not None:
                    self.observe("request_latency", lat)
        if event.category == "cognitive" and lat is not None:
            if event.attributes.get("stage") == "plan" or "plan" in event.name:
                self.observe("planning_latency", lat)
            else:
                self.observe("cognitive_latency", lat)
        if event.category == "tool":
            if event.status in ("error", "timeout"):
                self.inc("tool_failure_total")
            if lat is not None:
                self.observe("tool_latency", lat)
        if event.category == "provider":
            if event.status in ("error", "timeout"):
                self.inc("provider_failure_total")
            if lat is not None:
                self.observe("provider_latency", lat)
            if event.status == "skipped" and event.attributes.get("circuit_skipped"):
                self.set_gauge("circuit_open", 1.0)
        if event.category == "memory" and lat is not None:
            if event.attributes.get("retrieval_mode") or "retrieval" in event.name:
                self.observe("memory_retrieval_latency", lat)
            if "embed" in event.name:
                self.observe("embedding_latency", lat)
        if event.category == "vector" and lat is not None:
            self.observe("embedding_latency", lat)
        if event.category == "verify":
            if event.status in ("error",):
                self.inc("verification_failure_total")
            if lat is not None:
                self.observe("verification_latency", lat)
        if event.category == "retry":
            if "retry" in event.name:
                self.inc("retry_total")
            if "fallback" in event.name:
                self.inc("fallback_total")
        if event.category == "task":
            if event.name == "task.completed":
                self.inc("task_completion_total")
            elif event.name == "task.partial_success":
                self.inc("task_partial_success_total")
            elif event.name == "task.failed":
                self.inc("task_failed_total")


metrics_registry = MetricsRegistry()
