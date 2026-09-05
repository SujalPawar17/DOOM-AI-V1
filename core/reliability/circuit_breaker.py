"""
DOOM V4.2 — Provider Circuit Breaker
Tracks provider error rates and prevents hammering failing endpoints.
States: CLOSED (normal), OPEN (cooldown/fast-fail), HALF_OPEN (probing).
"""

import time
from enum import Enum
from typing import Dict, Any, Optional


class CircuitState(str, Enum):
    CLOSED = "CLOSED"          # Normal operation, passes requests
    OPEN = "OPEN"              # Cooldown mode, fast-fails requests
    HALF_OPEN = "HALF_OPEN"    # Probe mode, tests if provider recovered


class ProviderCircuitBreaker:
    """
    Circuit breaker per LLM provider to isolate failing backends
    and trigger proactive failover.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0
    ):
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds

        # provider_name -> {consecutive_failures, last_failure_time, state}
        self._providers: Dict[str, Dict[str, Any]] = {}

    def _get_entry(self, provider_name: str) -> Dict[str, Any]:
        return self._providers.setdefault(provider_name, {
            "consecutive_failures": 0,
            "last_failure_time": 0.0,
            "state": CircuitState.CLOSED
        })

    def can_attempt(self, provider_name: str) -> bool:
        """
        Determines if a provider call should be attempted.
        Returns True if CLOSED or if cooldown has elapsed (transitions to HALF_OPEN).
        """
        entry = self._get_entry(provider_name)
        state = entry["state"]

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            elapsed = time.time() - entry["last_failure_time"]
            if elapsed >= self.cooldown_seconds:
                # Cooldown expired: probe provider
                entry["state"] = CircuitState.HALF_OPEN
                print(f"[CIRCUIT BREAKER] Provider '{provider_name}' cooldown expired -> testing in HALF_OPEN state.")
                return True
            return False

        if state == CircuitState.HALF_OPEN:
            return True

        return True

    def record_success(self, provider_name: str) -> None:
        """Records successful response, resetting circuit breaker to CLOSED."""
        entry = self._get_entry(provider_name)
        entry["consecutive_failures"] = 0
        entry["state"] = CircuitState.CLOSED

    def record_failure(self, provider_name: str) -> CircuitState:
        """Records provider error and trips breaker to OPEN if threshold reached."""
        entry = self._get_entry(provider_name)
        entry["consecutive_failures"] += 1
        entry["last_failure_time"] = time.time()

        if entry["consecutive_failures"] >= self.failure_threshold or entry["state"] == CircuitState.HALF_OPEN:
            entry["state"] = CircuitState.OPEN
            print(f"[CIRCUIT BREAKER] TRIPPED -> Provider '{provider_name}' entered OPEN state ({entry['consecutive_failures']} consecutive errors). Cooldown: {self.cooldown_seconds}s.")

        return entry["state"]

    def get_state(self, provider_name: str) -> CircuitState:
        entry = self._get_entry(provider_name)
        return entry["state"]

    def reset(self) -> None:
        self._providers.clear()


# Global singleton instance
provider_circuit_breaker = ProviderCircuitBreaker()
