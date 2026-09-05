"""
DOOM V4.2 — Central Authoritative Retry Policy
Enforces strict retry budgets and distinguishes transient recoverable errors
from permanent non-retryable failures.
"""

import time
from typing import Dict, Any, Tuple, Optional


class RetryPolicy:
    """
    Central Authoritative Retry Policy for DOOM.
    Controls retry eligibility across steps, tasks, replans, and timeouts.
    """

    MAX_RETRIES_PER_STEP: int = 2
    MAX_TOTAL_RETRIES_PER_TASK: int = 5
    MAX_COGNITIVE_REPLANS: int = 3
    MAX_TOOL_TIMEOUTS: int = 2
    MAX_PROVIDER_FAILOVER_ATTEMPTS: int = 3
    MAX_TASK_WALL_TIME: float = 120.0  # seconds

    def __init__(self):
        # Tracking: task_id -> step_id -> attempt_count
        self._step_attempts: Dict[str, Dict[str, int]] = {}
        # Tracking: task_id -> total_attempts
        self._task_total_attempts: Dict[str, int] = {}
        # Tracking: task_id -> timeout_counts
        self._task_timeout_counts: Dict[str, int] = {}
        # Tracking: task_id -> replan_counts
        self._task_replan_counts: Dict[str, int] = {}

    def is_retryable(self, error: Any) -> bool:
        """Determines if an error is transient and safe to retry."""
        if error is None:
            return False

        err_str = str(error).lower()

        # Definite non-retryable fatal errors
        fatal_indicators = [
            "permission denied",
            "access denied",
            "unauthorized",
            "security block",
            "authorization required",
            "path traversal",
            "invalid argument",
            "missing required argument",
            "user cancelled",
            "cancellation requested",
            "file not found"
        ]
        if any(ind in err_str for ind in fatal_indicators):
            return False

        # Transient retryable indicators
        transient_indicators = [
            "timeout",
            "timed out",
            "connection refused",
            "connection reset",
            "temporary failure",
            "rate limit",
            "429",
            "500",
            "502",
            "503",
            "504",
            "service unavailable"
        ]
        return any(ind in err_str for ind in transient_indicators)

    def should_retry(
        self,
        task_id: str,
        step_id: Any,
        error: Any,
        start_time: float
    ) -> Tuple[bool, str]:
        """
        Evaluates whether a retry should be granted under the central budget.
        Returns: (allow_retry: bool, reason: str)
        """
        step_str = str(step_id)

        # 1. Check wall-time budget
        elapsed = time.time() - start_time
        if elapsed > self.MAX_TASK_WALL_TIME:
            return False, f"Task wall-time budget exceeded ({elapsed:.1f}s > {self.MAX_TASK_WALL_TIME}s)."

        # 2. Check total task retries
        task_retries = self._task_total_attempts.get(task_id, 0)
        if task_retries >= self.MAX_TOTAL_RETRIES_PER_TASK:
            return False, f"Task total retry budget exceeded ({task_retries} >= {self.MAX_TOTAL_RETRIES_PER_TASK})."

        # 3. Check step retries
        step_counts = self._step_attempts.setdefault(task_id, {})
        step_retries = step_counts.get(step_str, 0)
        if step_retries >= self.MAX_RETRIES_PER_STEP:
            return False, f"Step retry budget exceeded ({step_retries} >= {self.MAX_RETRIES_PER_STEP})."

        # 4. Check timeout limits if applicable
        err_str = str(error).lower()
        if "timeout" in err_str or "timed out" in err_str:
            timeouts = self._task_timeout_counts.get(task_id, 0)
            if timeouts >= self.MAX_TOOL_TIMEOUTS:
                return False, f"Maximum tool timeout budget reached ({timeouts} >= {self.MAX_TOOL_TIMEOUTS})."

        # 5. Check error retryability
        if not self.is_retryable(error):
            return False, f"Error is non-retryable: {error}"

        # Grant retry and record attempt
        step_counts[step_str] = step_retries + 1
        self._task_total_attempts[task_id] = task_retries + 1
        if "timeout" in err_str or "timed out" in err_str:
            self._task_timeout_counts[task_id] = self._task_timeout_counts.get(task_id, 0) + 1

        return True, f"Retry granted for step {step_str} (attempt {step_retries + 1}/{self.MAX_RETRIES_PER_STEP})."

    def can_replan(self, task_id: str) -> Tuple[bool, str]:
        """Checks whether another cognitive replan cycle is permitted."""
        count = self._task_replan_counts.get(task_id, 0)
        if count >= self.MAX_COGNITIVE_REPLANS:
            return False, f"Maximum cognitive replans reached ({count} >= {self.MAX_COGNITIVE_REPLANS})."
        self._task_replan_counts[task_id] = count + 1
        return True, f"Replan permitted ({count + 1}/{self.MAX_COGNITIVE_REPLANS})."

    def get_step_retry_count(self, task_id: str, step_id: Any) -> int:
        return self._step_attempts.get(task_id, {}).get(str(step_id), 0)

    def reset_task(self, task_id: str) -> None:
        """Cleans up retry counters for a finished task."""
        self._step_attempts.pop(task_id, None)
        self._task_total_attempts.pop(task_id, None)
        self._task_timeout_counts.pop(task_id, None)
        self._task_replan_counts.pop(task_id, None)


# Global singleton instance
retry_policy = RetryPolicy()
