from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field, asdict
from enum import Enum
import time
import signal
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError


class RiskLevel(str, Enum):
    SAFE = "SAFE"            # Read-only telemetry, search, status, calculation
    LOW = "LOW"              # Opening safe apps, creating temp files
    MEDIUM = "MEDIUM"        # Writing scripts, terminal commands, installing packages
    HIGH = "HIGH"            # Deleting files, modifying system configuration
    CRITICAL = "CRITICAL"    # Destructive operations, disk wiping, security bypass


class TerminationReason(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    USER_APPROVAL_REQUIRED = "USER_APPROVAL_REQUIRED"
    MAX_STEPS_REACHED = "MAX_STEPS_REACHED"
    MAX_RETRIES_REACHED = "MAX_RETRIES_REACHED"
    MAX_RETRIES_EXCEEDED = "MAX_RETRIES_EXCEEDED"
    UNRECOVERABLE_ERROR = "UNRECOVERABLE_ERROR"
    PARTIAL_COMPLETION = "PARTIAL_COMPLETION"
    CANCELLED = "CANCELLED"


class FinalResponseStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class CanonicalToolResult:
    """
    Standardized result structure required by DOOM V3.2 architecture.
    Every tool execution produces this normalized observation format.
    """
    tool: str
    success: bool
    action: str = "execute"
    target: str = ""
    artifact: Optional[Dict[str, Any]] = None  # e.g. {"path": "...", "size": 417, "name": "...", "exists": true}
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    output: str = ""  # Human readable summary
    error_type: Optional[str] = None  # "TIMEOUT", "EXCEPTION", "VALIDATION", etc.

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "success": self.success,
            "action": self.action,
            "target": self.target,
            "artifact": self.artifact or {},
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_ms": round(self.duration_ms, 2),
            "metadata": self.metadata or {},
            "output": self.output,
            "error_type": self.error_type
        }


@dataclass
class ToolResult:
    success: bool
    output: str
    data: Optional[Any] = None
    error: Optional[str] = None
    action: str = "execute"
    artifact: Optional[Dict[str, Any]] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    exit_code: int = 0
    target: str = ""

    def __str__(self) -> str:
        if self.success:
            return self.output
        return f"Tool Error: {self.error or self.output}"

    def to_canonical(self, tool_name: str, duration_ms: float = 0.0) -> CanonicalToolResult:
        stdout_val = self.stdout or (self.output if self.success else "")
        stderr_val = self.stderr or (self.error or (self.output if not self.success else ""))
        meta = self.data if isinstance(self.data, dict) else ({"raw_data": self.data} if self.data else {})
        artifact_val = self.artifact or (meta.get("artifact") if isinstance(meta, dict) else None)
        if not artifact_val and isinstance(meta, dict) and "path" in meta:
            artifact_val = {"path": meta.get("path"), "size": meta.get("size") or meta.get("length") or 0}

        return CanonicalToolResult(
            tool=tool_name,
            success=self.success,
            action=self.action or "execute",
            target=self.target,
            artifact=artifact_val or {},
            stdout=stdout_val,
            stderr=stderr_val,
            exit_code=self.exit_code,
            duration_ms=duration_ms or self.duration_ms,
            metadata=meta or {},
            output=self.output,
            error_type=self.error if not self.success else None
        )


def execute_with_timeout(func, timeout_seconds: int, *args, **kwargs):
    """
    Execute a function with a hard timeout using ThreadPoolExecutor.
    Returns (result, timed_out: bool).
    NOTE: Uses shutdown(wait=False) so the caller unblocks immediately on timeout.
    The background thread may continue running until it naturally completes (Python
    threads cannot be forcibly killed), but the orchestrator will not block on it.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, *args, **kwargs)
    try:
        result = future.result(timeout=timeout_seconds)
        executor.shutdown(wait=False)
        return result, False
    except FuturesTimeoutError:
        executor.shutdown(wait=False)  # Do NOT wait for thread — unblock immediately
        return None, True
    except Exception as e:
        executor.shutdown(wait=False)
        raise e



MAX_AGENT_STEPS = 6
MAX_TOOL_CALLS = 8
MAX_RETRIES_PER_ACTION = 2


class BaseTool(ABC):
    """
    Standardized Tool Interface for DOOM V3.2 Personal AI OS.
    Includes capability-awareness metadata for intelligent pre-execution decisions.
    """
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}
    permission_level: str = "safe"  # Legacy: safe, moderate, sensitive, dangerous
    risk_level: RiskLevel = RiskLevel.SAFE
    timeout: int = 30  # Maximum execution time in seconds

    # Capability-awareness metadata (DOOM V3.2)
    purpose: str = ""
    category: str = "general"
    inputs: Dict[str, Any] = {}
    outputs: Dict[str, Any] = {}
    side_effects: List[str] = []
    when_to_use: str = ""
    do_not_use_when: str = ""
    mutually_exclusive_with: List[str] = []
    dependencies: List[str] = []

    @abstractmethod
    def _execute_impl(self, **kwargs) -> ToolResult:
        """Execute the tool action and return a standardized ToolResult. Override this method."""
        pass

    def execute(self, **kwargs) -> ToolResult:
        """Execute with hard timeout enforcement. Returns structured timeout observation on failure."""
        start_t = time.time()
        try:
            result, timed_out = execute_with_timeout(self._execute_impl, self.timeout, **kwargs)
            if timed_out:
                duration = (time.time() - start_t) * 1000
                return ToolResult(
                    success=False,
                    output=f"Tool '{self.name}' timed out after {self.timeout} seconds",
                    action="timeout",
                    error="TIMEOUT",
                    duration_ms=duration,
                    exit_code=-1,
                    target=kwargs.get("file_path") or kwargs.get("file_name") or kwargs.get("code_or_file") or ""
                )
            return result
        except Exception as e:
            duration = (time.time() - start_t) * 1000
            return ToolResult(
                success=False,
                output=f"Tool '{self.name}' failed with exception: {e}",
                action="exception",
                error=str(e),
                duration_ms=duration,
                exit_code=-1,
                target=kwargs.get("file_path") or kwargs.get("file_name") or kwargs.get("code_or_file") or ""
            )

    def get_effective_risk(self) -> RiskLevel:
        """Returns the normalized risk level for security enforcement."""
        legacy_map = {
            "safe": RiskLevel.SAFE,
            "standard": RiskLevel.LOW,
            "moderate": RiskLevel.MEDIUM,
            "sensitive": RiskLevel.HIGH,
            "dangerous": RiskLevel.CRITICAL
        }
        # If tool explicitly defines a non-default risk_level, respect it
        if hasattr(self, "risk_level") and isinstance(self.risk_level, RiskLevel) and self.risk_level != RiskLevel.SAFE:
            return self.risk_level

        # If permission_level is set, map it
        perm_risk = legacy_map.get(str(self.permission_level).lower(), RiskLevel.SAFE)
        if perm_risk != RiskLevel.SAFE:
            return perm_risk

        return getattr(self, "risk_level", RiskLevel.SAFE)

    def to_json_schema(self) -> Dict[str, Any]:
        """Convert tool to OpenAI/Groq/Gemini JSON function calling format"""
        # Append capability hints into description so LLM selects the correct tool
        desc = self.description
        if self.do_not_use_when:
            desc += f" (DO NOT USE WHEN: {self.do_not_use_when})"
        if self.when_to_use:
            desc += f" (USE WHEN: {self.when_to_use})"

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": desc,
                "parameters": self.parameters or {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        }
