"""
DOOM V4.2 — Correlation & Trace Context
Maintains end-to-end trace context for requests, tasks, cognitive cycles,
steps, operations, and tool executions without logging sensitive data or CoT.
"""

import uuid
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass, field, asdict
import contextvars

@dataclass
class CorrelationContext:
    doom_request_id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:10]}")
    task_id: Optional[str] = None
    cognitive_cycle_id: Optional[str] = None
    step_id: Optional[str] = None
    operation_id: Optional[str] = None
    tool_execution_id: Optional[str] = None
    provider_call_id: Optional[str] = None
    verification_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def new_cycle(self, cycle_num: int) -> 'CorrelationContext':
        updated = CorrelationContext(
            doom_request_id=self.doom_request_id,
            task_id=self.task_id,
            cognitive_cycle_id=f"cycle_{cycle_num}_{uuid.uuid4().hex[:6]}",
            step_id=self.step_id,
            operation_id=self.operation_id,
            tool_execution_id=self.tool_execution_id,
            provider_call_id=self.provider_call_id,
            verification_id=self.verification_id,
            created_at=self.created_at
        )
        set_current_correlation(updated)
        return updated

    def new_step(self, step_identifier: Any) -> 'CorrelationContext':
        updated = CorrelationContext(
            doom_request_id=self.doom_request_id,
            task_id=self.task_id,
            cognitive_cycle_id=self.cognitive_cycle_id,
            step_id=str(step_identifier),
            operation_id=f"op_{uuid.uuid4().hex[:8]}",
            tool_execution_id=None,
            provider_call_id=self.provider_call_id,
            verification_id=self.verification_id,
            created_at=self.created_at
        )
        set_current_correlation(updated)
        return updated

    def new_tool_execution(self, tool_name: str) -> 'CorrelationContext':
        updated = CorrelationContext(
            doom_request_id=self.doom_request_id,
            task_id=self.task_id,
            cognitive_cycle_id=self.cognitive_cycle_id,
            step_id=self.step_id,
            operation_id=self.operation_id,
            tool_execution_id=f"exec_{tool_name}_{uuid.uuid4().hex[:6]}",
            provider_call_id=self.provider_call_id,
            verification_id=self.verification_id,
            created_at=self.created_at
        )
        set_current_correlation(updated)
        return updated

    def new_provider_call(self, provider_name: str) -> 'CorrelationContext':
        updated = CorrelationContext(
            doom_request_id=self.doom_request_id,
            task_id=self.task_id,
            cognitive_cycle_id=self.cognitive_cycle_id,
            step_id=self.step_id,
            operation_id=self.operation_id,
            tool_execution_id=self.tool_execution_id,
            provider_call_id=f"pc_{provider_name}_{uuid.uuid4().hex[:6]}",
            verification_id=self.verification_id,
            created_at=self.created_at
        )
        set_current_correlation(updated)
        return updated

    def new_verification(self) -> 'CorrelationContext':
        updated = CorrelationContext(
            doom_request_id=self.doom_request_id,
            task_id=self.task_id,
            cognitive_cycle_id=self.cognitive_cycle_id,
            step_id=self.step_id,
            operation_id=self.operation_id,
            tool_execution_id=self.tool_execution_id,
            provider_call_id=self.provider_call_id,
            verification_id=f"ver_{uuid.uuid4().hex[:8]}",
            created_at=self.created_at
        )
        set_current_correlation(updated)
        return updated

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doom_request_id": self.doom_request_id,
            "task_id": self.task_id,
            "cognitive_cycle_id": self.cognitive_cycle_id,
            "step_id": self.step_id,
            "operation_id": self.operation_id,
            "tool_execution_id": self.tool_execution_id,
            "provider_call_id": self.provider_call_id,
            "verification_id": self.verification_id,
            "elapsed_ms": round((time.time() - self.created_at) * 1000, 2)
        }

# Global contextvar for async/thread context tracking
_current_context: contextvars.ContextVar[Optional[CorrelationContext]] = contextvars.ContextVar(
    "doom_correlation_context", default=None
)

def get_current_correlation() -> CorrelationContext:
    ctx = _current_context.get()
    if ctx is None:
        ctx = CorrelationContext()
        _current_context.set(ctx)
    return ctx

def set_current_correlation(ctx: Optional[CorrelationContext]) -> None:
    _current_context.set(ctx)


def bind_request(task_id: Optional[str] = None) -> CorrelationContext:
    """Always start a fresh request context (prevents cross-request leakage)."""
    ctx = CorrelationContext(task_id=task_id)
    _current_context.set(ctx)
    return ctx


def reset_request() -> None:
    """Clear correlation so the next request cannot inherit this one."""
    _current_context.set(None)
