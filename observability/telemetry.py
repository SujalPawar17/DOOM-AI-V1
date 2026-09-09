"""Fail-open telemetry.emit — never raises to callers."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from core.reliability.correlation import (
    bind_request,
    get_current_correlation,
    reset_request,
)
from observability.bus import telemetry_bus
from observability.metrics import metrics_registry
from observability.redactor import redact_event
from observability.schemas import OperationalEvent, SchemaValidationError, classify_error
from observability.sinks import postgres_sink

_wired = False


def _ensure_wired() -> None:
    global _wired
    if _wired:
        return
    telemetry_bus.subscribe(metrics_registry.on_event)
    telemetry_bus.subscribe(postgres_sink.on_event)
    _wired = True


def emit(
    name: str,
    category: str,
    status: str = "ok",
    latency_ms: Optional[float] = None,
    component: str = "",
    operation: str = "",
    error_type: Optional[str] = None,
    retryable: Optional[bool] = None,
    attributes: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        _ensure_wired()
        ctx = get_current_correlation()
        event = OperationalEvent(
            doom_request_id=ctx.doom_request_id,
            task_id=ctx.task_id,
            cognitive_cycle_id=ctx.cognitive_cycle_id,
            step_id=ctx.step_id,
            tool_execution_id=ctx.tool_execution_id,
            provider_call_id=ctx.provider_call_id,
            category=category,
            name=name,
            status=status,
            latency_ms=latency_ms,
            component=component,
            operation=operation,
            error_type=error_type,
            retryable=retryable,
            attributes=dict(attributes or {}),
        )
        safe = redact_event(event)
        if safe is None:
            metrics_registry.inc("telemetry_dropped_total")
            telemetry_bus.dropped += 1
            return
        ok = telemetry_bus.publish(safe)
        if not ok:
            metrics_registry.inc("telemetry_dropped_total")
    except SchemaValidationError:
        try:
            metrics_registry.inc("telemetry_dropped_total")
        except Exception:
            pass
    except Exception:
        try:
            metrics_registry.inc("telemetry_dropped_total")
        except Exception:
            pass


@contextmanager
def request_scope() -> Iterator:
    ctx = bind_request()
    t0 = time.perf_counter()
    emit("request.started", "request", component="entry", operation="bind")
    try:
        yield ctx
        emit(
            "request.completed",
            "request",
            status="ok",
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            component="entry",
            operation="complete",
        )
    except Exception as exc:
        emit(
            "request.completed",
            "request",
            status="error",
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            component="entry",
            operation="complete",
            error_type=classify_error(exc),
            retryable=False,
        )
        raise
    finally:
        reset_request()
