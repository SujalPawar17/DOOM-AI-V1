"""Derived WorldSnapshot over V5. TTL. Never command_logs. Fail to empty."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from proactive.config import OWNER_ID, SNAPSHOT_TTL_SECONDS


@dataclass
class WorldSnapshot:
    generated_at: float
    valid_until: float
    owner_id: str = OWNER_ID
    projects: List[Dict[str, Any]] = field(default_factory=list)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    lifecycle: List[Dict[str, Any]] = field(default_factory=list)
    experiences: List[Dict[str, Any]] = field(default_factory=list)
    host: Dict[str, Any] = field(default_factory=dict)
    circuit: Dict[str, Any] = field(default_factory=dict)
    last_request_at: Optional[float] = None
    partial: bool = False
    sources: List[str] = field(default_factory=list)

    def is_fresh(self) -> bool:
        return time.time() < self.valid_until


_cache: Optional[WorldSnapshot] = None


def _q(sql: str, params=None) -> list:
    try:
        from database.postgres_db import postgres_manager
        if not postgres_manager.is_connected():
            return []
        rows = postgres_manager.execute_query(sql, params)
        if isinstance(rows, list) and rows and "error" not in rows[0]:
            return rows
    except Exception:
        pass
    return []


def build_world_snapshot(force: bool = False) -> WorldSnapshot:
    global _cache
    now = time.time()
    if not force and _cache is not None and _cache.is_fresh():
        return _cache
    snap = WorldSnapshot(
        generated_at=now,
        valid_until=now + SNAPSHOT_TTL_SECONDS,
        owner_id=OWNER_ID,
    )
    try:
        snap.projects = _q(
            """
            SELECT project_id, lifecycle_status, privacy_class
            FROM projects WHERE privacy_class = 'NORMAL'
            ORDER BY updated_at DESC NULLS LAST LIMIT 20
            """
        )
        snap.sources.append("projects")
    except Exception:
        snap.partial = True
    try:
        snap.tasks = _q(
            """
            SELECT task_id, status FROM task_checkpoints
            ORDER BY updated_at DESC LIMIT 20
            """
        )
        snap.sources.append("task_checkpoints")
    except Exception:
        snap.partial = True
    try:
        snap.lifecycle = _q(
            """
            SELECT memory_id, event_type, created_at FROM memory_lifecycle_events
            ORDER BY created_at DESC LIMIT 20
            """
        )
        snap.sources.append("memory_lifecycle_events")
    except Exception:
        snap.partial = True
    try:
        snap.experiences = _q(
            """
            SELECT experience_id, project_id, outcome_status FROM experiences
            ORDER BY created_at DESC LIMIT 20
            """
        )
        snap.sources.append("experiences")
    except Exception:
        snap.partial = True
    try:
        host_rows = _q(
            """
            SELECT cpu_percent, ram_percent, disk_percent, EXTRACT(EPOCH FROM recorded_at) AS ts
            FROM system_telemetry ORDER BY recorded_at DESC LIMIT 1
            """
        )
        snap.host = host_rows[0] if host_rows else {}
        snap.sources.append("system_telemetry")
    except Exception:
        snap.partial = True
    try:
        from core.reliability.circuit_breaker import CircuitState, provider_circuit_breaker
        open_n = 0
        providers = getattr(provider_circuit_breaker, "_providers", {}) or {}
        for _name, entry in providers.items():
            st = entry.get("state") if isinstance(entry, dict) else None
            if st == CircuitState.OPEN or str(st).upper() == "OPEN":
                open_n += 1
        snap.circuit = {"open_count": open_n}
        snap.sources.append("circuit_breaker")
    except Exception:
        snap.partial = True
        snap.circuit = {"open_count": 0}
    try:
        from core.state_machine import state_machine
        snap.last_request_at = getattr(state_machine, "_last_changed", None)
    except Exception:
        pass
    _cache = snap
    return snap


def invalidate_snapshot() -> None:
    global _cache
    _cache = None
