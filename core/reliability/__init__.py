"""
DOOM V4.2 — Reliability & Hardening Subsystem
Authoritative modules for Idempotency, Retry Budgets, Concurrency Locks,
Plan Validation, Input Firewalls, Circuit Breakers, and Correlation Tracing.
"""

from core.reliability.correlation import (
    CorrelationContext, get_current_correlation, set_current_correlation
)
from core.reliability.idempotency import (
    IdempotencyManager, IdempotencyReceipt, ExecutionState, idempotency_manager
)
from core.reliability.retry_policy import (
    RetryPolicy, retry_policy
)
from core.reliability.plan_validator import (
    PlanValidator, plan_validator
)
from core.reliability.input_validator import (
    ToolInputValidator, tool_input_validator
)
from core.reliability.concurrency import (
    TaskConcurrencyManager, TaskLease, task_concurrency_manager
)
from core.reliability.circuit_breaker import (
    ProviderCircuitBreaker, CircuitState, provider_circuit_breaker
)

__all__ = [
    "CorrelationContext",
    "get_current_correlation",
    "set_current_correlation",
    "IdempotencyManager",
    "IdempotencyReceipt",
    "ExecutionState",
    "idempotency_manager",
    "RetryPolicy",
    "retry_policy",
    "PlanValidator",
    "plan_validator",
    "ToolInputValidator",
    "tool_input_validator",
    "TaskConcurrencyManager",
    "TaskLease",
    "task_concurrency_manager",
    "ProviderCircuitBreaker",
    "CircuitState",
    "provider_circuit_breaker",
]
