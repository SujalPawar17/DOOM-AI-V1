"""Internal source polling. Never reads command_logs. Flag-gated via ingest."""

from __future__ import annotations

import time

from proactive.ingest import ingest_signal


def poll_internal_sources() -> None:
    try:
        from database.postgres_db import postgres_manager
        if not postgres_manager.is_connected():
            return
        rows = postgres_manager.execute_query(
            """
            SELECT cpu_percent, ram_percent, disk_percent
            FROM system_telemetry ORDER BY recorded_at DESC LIMIT 1
            """
        )
        if isinstance(rows, list) and rows and "error" not in rows[0]:
            disk = float(rows[0].get("disk_percent") or 0)
            if disk >= 80:
                ingest_signal(
                    signal_type="HOST_TELEMETRY",
                    source="system_telemetry",
                    entity_type="host",
                    entity_id="workstation",
                    payload={"disk_percent": int(disk), "cpu_percent": int(rows[0].get("cpu_percent") or 0)},
                    privacy_class="NORMAL",
                )
    except Exception:
        pass
    try:
        from core.reliability.circuit_breaker import CircuitState, provider_circuit_breaker
        state = getattr(provider_circuit_breaker, "_providers", {}) or {}
        for name, entry in list(state.items()) if isinstance(state, dict) else []:
            st = ""
            if isinstance(entry, dict):
                st = str(entry.get("state") or "")
            else:
                st = str(getattr(entry, "state", "") or "")
            if st == CircuitState.OPEN or "OPEN" in str(st).upper():
                ingest_signal(
                    signal_type="PROVIDER_CIRCUIT",
                    source="circuit_breaker",
                    entity_type="provider",
                    entity_id=str(name)[:40],
                    payload={"state": "OPEN"},
                    privacy_class="NORMAL",
                )
    except Exception:
        pass
    try:
        from core.state_machine import state_machine
        last = getattr(state_machine, "_last_changed", None)
        if last and (time.time() - float(last)) > 7 * 86400:
            ingest_signal(
                signal_type="INACTIVITY",
                source="clock",
                entity_type="user",
                entity_id="session",
                payload={"idle_days": 7},
                privacy_class="NORMAL",
            )
    except Exception:
        pass
