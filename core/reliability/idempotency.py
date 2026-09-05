"""
DOOM V4.2 — Idempotent Tool Execution Layer
Guarantees ONE SIDE EFFECT = ONE LOGICAL OPERATION.
Tracks idempotency keys, in-flight claims, durable receipts, and reconciliation states.
"""

import os
import json
import time
import hashlib
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field, asdict
from tools.base import CanonicalToolResult
from core.path_resolver import canonical_path


class ExecutionState(str, Enum):
    CLAIMED = "CLAIMED"                    # Key acquired, tool dispatch in-flight
    COMPLETED = "COMPLETED"                # Successfully executed, receipt persisted
    FAILED_BEFORE_SIDE_EFFECT = "FAILED_BEFORE_SIDE_EFFECT"  # Safe to retry
    FAILED_WITH_POSSIBLE_SIDE_EFFECT = "FAILED_WITH_POSSIBLE_SIDE_EFFECT"  # Must verify before retry
    UNKNOWN = "UNKNOWN"                    # Ambiguous/interrupted state; reconciliation required
    RECONCILED = "RECONCILED"              # External state verified and adopted


SIDE_EFFECTING_ACTIONS = {"create_file", "write_file", "patch_file", "delete_file", "create_directory"}


@dataclass
class IdempotencyReceipt:
    idempotency_key: str
    task_id: str
    step_id: str
    operation_id: str
    logical_action: str
    tool_name: str
    canonical_arguments: Dict[str, Any]
    state: ExecutionState
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    output: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    success: bool = False
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_canonical_result(self) -> CanonicalToolResult:
        art = self.artifacts[0] if self.artifacts else None
        return CanonicalToolResult(
            tool=self.tool_name,
            success=self.success,
            action=self.logical_action,
            output=self.output,
            stdout=self.stdout,
            stderr=self.stderr,
            exit_code=self.exit_code,
            artifact=art,
            metadata={"idempotent_cached": True, "idempotency_key": self.idempotency_key}
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


class IdempotencyManager:
    """
    Authoritative idempotency ledger for side-effecting operations.
    Persists receipts to disk to survive crashes and enforces the invariant:
    ONE SIDE EFFECT = ONE LOGICAL OPERATION.
    """

    def __init__(self, ledger_path: Optional[str] = None):
        if not ledger_path:
            base_dir = os.path.join(os.path.dirname(__file__), "..", "..", "database")
            os.makedirs(base_dir, exist_ok=True)
            self.ledger_path = os.path.join(base_dir, "idempotency_ledger.json")
        else:
            self.ledger_path = ledger_path

        self._ledger: Dict[str, IdempotencyReceipt] = {}
        self._load_ledger()

    def _canonicalize_args(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Normalizes file paths and arguments for deterministic hashing."""
        canonical = {}
        for k, v in sorted(tool_args.items()):
            if k in ("file_path", "file_name", "code_or_file", "path", "target") and isinstance(v, str):
                try:
                    cp = canonical_path(v)
                    canonical[k] = cp.absolute_path
                except Exception:
                    canonical[k] = str(v)
            elif isinstance(v, str):
                canonical[k] = v.strip()
            else:
                canonical[k] = v
        return canonical

    def compute_idempotency_key(
        self,
        task_id: str,
        step_id: Any,
        logical_action: str,
        tool_args: Dict[str, Any]
    ) -> str:
        """
        Computes deterministic idempotency key.
        NEVER derived from retry attempt number. Retries use the SAME key.
        """
        norm_args = self._canonicalize_args(tool_args)
        args_repr = json.dumps(norm_args, sort_keys=True, default=str)
        payload = f"{task_id}::{step_id}::{logical_action}::{args_repr}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def claim(
        self,
        key: str,
        task_id: str,
        step_id: Any,
        logical_action: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        operation_id: Optional[str] = None
    ) -> Tuple[bool, Optional[IdempotencyReceipt]]:
        """
        Attempts to claim an idempotency key.
        Returns:
            (can_execute: bool, existing_receipt: Optional[IdempotencyReceipt])
        """
        if key in self._ledger:
            existing = self._ledger[key]
            if existing.state in (ExecutionState.COMPLETED, ExecutionState.RECONCILED):
                # Completed: Return cached receipt without executing again
                return False, existing
            elif existing.state == ExecutionState.CLAIMED:
                # In-flight/pending: If active within 30s, block duplicate execution
                if time.time() - existing.started_at < 30.0:
                    return False, existing
                else:
                    # Stale pending claim (likely process crashed during execution): mark UNKNOWN
                    existing.state = ExecutionState.UNKNOWN
                    self._save_ledger()
                    return False, existing
            elif existing.state == ExecutionState.FAILED_BEFORE_SIDE_EFFECT:
                # Safe to retry: update claim
                existing.state = ExecutionState.CLAIMED
                existing.started_at = time.time()
                self._save_ledger()
                return True, None
            elif existing.state in (ExecutionState.FAILED_WITH_POSSIBLE_SIDE_EFFECT, ExecutionState.UNKNOWN):
                # Cannot blindly retry without verification!
                return False, existing

        # Check if identical completed side-effecting operation already exists
        if logical_action in SIDE_EFFECTING_ACTIONS:
            norm_args = self._canonicalize_args(tool_args)
            for existing in self._ledger.values():
                if (
                    existing.state in (ExecutionState.COMPLETED, ExecutionState.RECONCILED)
                    and existing.logical_action == logical_action
                    and existing.tool_name == tool_name
                    and existing.canonical_arguments == norm_args
                ):
                    return False, existing

        # First execution: Claim key
        receipt = IdempotencyReceipt(
            idempotency_key=key,
            task_id=task_id,
            step_id=str(step_id),
            operation_id=operation_id or f"op_{time.time_ns()}",
            logical_action=logical_action,
            tool_name=tool_name,
            canonical_arguments=self._canonicalize_args(tool_args),
            state=ExecutionState.CLAIMED,
            started_at=time.time()
        )
        self._ledger[key] = receipt
        self._save_ledger()
        return True, None

    def record_receipt(
        self,
        key: str,
        result: CanonicalToolResult,
        artifacts: Optional[List[Dict[str, Any]]] = None,
        failed_before_side_effect: bool = False
    ) -> IdempotencyReceipt:
        """Stores final execution receipt and marks key COMPLETED or FAILED."""
        receipt = self._ledger.get(key)
        if not receipt:
            receipt = IdempotencyReceipt(
                idempotency_key=key,
                task_id="unknown",
                step_id="unknown",
                operation_id=f"op_{time.time_ns()}",
                logical_action=result.action,
                tool_name=result.tool,
                canonical_arguments={},
                state=ExecutionState.CLAIMED
            )
            self._ledger[key] = receipt

        receipt.completed_at = time.time()
        receipt.success = result.success
        receipt.output = result.output
        receipt.stdout = result.stdout
        receipt.stderr = result.stderr
        receipt.exit_code = result.exit_code
        if artifacts:
            receipt.artifacts = artifacts
        elif result.artifact:
            receipt.artifacts = [result.artifact]

        if result.success:
            if receipt.state != ExecutionState.RECONCILED:
                receipt.state = ExecutionState.COMPLETED
        else:
            if failed_before_side_effect:
                receipt.state = ExecutionState.FAILED_BEFORE_SIDE_EFFECT
            else:
                receipt.state = ExecutionState.FAILED_WITH_POSSIBLE_SIDE_EFFECT

        self._save_ledger()
        return receipt

    def mark_reconciled(
        self,
        key: str,
        artifacts: Optional[List[Dict[str, Any]]] = None,
        output: str = "State verified externally"
    ) -> IdempotencyReceipt:
        """Marks an ambiguous/pending operation as verified and reconciled without re-executing."""
        receipt = self._ledger.get(key)
        if not receipt:
            receipt = IdempotencyReceipt(
                idempotency_key=key,
                task_id="reconciled",
                step_id="reconciled",
                operation_id=f"op_{time.time_ns()}",
                logical_action="reconciled",
                tool_name="verifier",
                canonical_arguments={},
                state=ExecutionState.RECONCILED
            )
            self._ledger[key] = receipt

        receipt.state = ExecutionState.RECONCILED
        receipt.success = True
        receipt.completed_at = time.time()
        receipt.output = output
        if artifacts:
            receipt.artifacts = artifacts
        self._save_ledger()
        return receipt

    def get_receipt(self, key: str) -> Optional[IdempotencyReceipt]:
        return self._ledger.get(key)

    def _load_ledger(self) -> None:
        if os.path.exists(self.ledger_path):
            try:
                with open(self.ledger_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, item in data.items():
                        state_val = item.get("state", "COMPLETED")
                        try:
                            state_enum = ExecutionState(state_val)
                        except ValueError:
                            state_enum = ExecutionState.COMPLETED
                        item["state"] = state_enum
                        self._ledger[k] = IdempotencyReceipt(**item)
            except Exception as e:
                print(f"[IDEMPOTENCY] Warning: Failed to load ledger from {self.ledger_path}: {e}")

    def _save_ledger(self) -> None:
        try:
            temp_path = f"{self.ledger_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                serializable = {k: v.to_dict() for k, v in self._ledger.items()}
                json.dump(serializable, f, indent=2, default=str)
            if os.path.exists(temp_path):
                try:
                    os.replace(temp_path, self.ledger_path)
                except OSError:
                    # Windows fallback: write directly if replace is locked
                    with open(self.ledger_path, "w", encoding="utf-8") as f:
                        json.dump(serializable, f, indent=2, default=str)
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass
        except Exception as e:
            print(f"[IDEMPOTENCY] Warning: Failed to save ledger: {e}")

    def reset(self) -> None:
        """Clears memory ledger and local storage."""
        self._ledger.clear()
        if os.path.exists(self.ledger_path):
            try:
                os.remove(self.ledger_path)
            except OSError:
                pass


# Global singleton instance
idempotency_manager = IdempotencyManager()
