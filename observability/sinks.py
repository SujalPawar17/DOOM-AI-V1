"""Optional PostgreSQL sink with bounded retention. Fail-open.

Enqueue on the telemetry path; flush asynchronously. Database failure never
raises to the agent.
"""

from __future__ import annotations

import json
import threading
import time
from typing import List

from observability.schemas import OperationalEvent

MAX_ROWS = 10000
RETENTION_DAYS = 7
MAX_BUFFER = 512


class PostgresOperationalSink:
    def __init__(self):
        self._buf: List[OperationalEvent] = []
        self._lock = threading.Lock()
        self._batch_size = 32
        self.enabled = True
        self._thread = threading.Thread(target=self._flush_loop, name="otp-pg-sink", daemon=True)
        self._thread.start()

    def on_event(self, event: OperationalEvent) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._buf.append(event)
            overflow = len(self._buf) - MAX_BUFFER
            if overflow > 0:
                del self._buf[:overflow]

    def _flush_loop(self) -> None:
        while True:
            try:
                time.sleep(0.25)
                if self.enabled:
                    self.flush()
            except Exception:
                pass

    def flush(self) -> None:
        with self._lock:
            batch = list(self._buf)
            self._buf.clear()
        if not batch:
            return
        try:
            from database.postgres_db import postgres_manager
            if not postgres_manager.is_connected():
                return
            conn = postgres_manager.get_connection()
            if not conn:
                return
            try:
                with conn.cursor() as cur:
                    for ev in batch:
                        d = ev.to_dict()
                        cur.execute(
                            """
                            INSERT INTO operational_events (
                                event_id, ts_unix_ms, doom_request_id, task_id,
                                cognitive_cycle_id, step_id, tool_execution_id,
                                provider_call_id, category, name, status, latency_ms,
                                component, operation, error_type, retryable, attributes
                            ) VALUES (
                                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                            ) ON CONFLICT (event_id) DO NOTHING
                            """,
                            (
                                d["event_id"], d["ts_unix_ms"], d["doom_request_id"], d["task_id"],
                                d["cognitive_cycle_id"], d["step_id"], d["tool_execution_id"],
                                d["provider_call_id"], d["category"], d["name"], d["status"],
                                d["latency_ms"], d["component"], d["operation"], d["error_type"],
                                d["retryable"], json.dumps(d["attributes"]),
                            ),
                        )
                    cur.execute(
                        """
                        DELETE FROM operational_events
                        WHERE ctid IN (
                            SELECT ctid FROM operational_events
                            ORDER BY ts_unix_ms DESC
                            OFFSET %s
                        )
                        """,
                        (MAX_ROWS,),
                    )
                    cutoff_ms = int((time.time() - RETENTION_DAYS * 86400) * 1000)
                    cur.execute(
                        "DELETE FROM operational_events WHERE ts_unix_ms < %s",
                        (cutoff_ms,),
                    )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            finally:
                postgres_manager.release_connection(conn)
        except Exception:
            pass


postgres_sink = PostgresOperationalSink()
