"""Fail-open OTP helpers for V6.1. Metadata only. Never insight prose."""

from __future__ import annotations

from typing import Optional

from observability.telemetry import emit


def emit_proactive(
    name: str,
    status: str = "ok",
    latency_ms: Optional[float] = None,
    operation: str = "",
    attributes: Optional[dict] = None,
) -> None:
    try:
        emit(
            name,
            "proactive",
            status=status,
            latency_ms=latency_ms,
            component="proactive",
            operation=operation or name,
            attributes=attributes or {},
        )
    except Exception:
        pass
