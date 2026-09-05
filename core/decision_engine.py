import hashlib
import json
import os
from typing import Dict, Any, List, Optional, Tuple
from tools.base import CanonicalToolResult, MAX_RETRIES_PER_ACTION
from core.path_resolver import canonical_path


class DecisionEngine:
    """
    DOOM V3.2 Pre-Execution Decision & Idempotency Engine.
    Prevents redundant, duplicate, or conflicting tool executions.
    Tracks per-action failure counts and enforces MAX_RETRIES_PER_ACTION.
    """
    def __init__(self):
        # V3.2: Per-action failure tracking — prevents infinite retry loops
        self._retry_counts: Dict[str, int] = {}

    def reset(self):
        """Reset retry counts for a new task."""
        self._retry_counts.clear()

    def record_failure(self, sig: str):
        """Increment failure count for an action signature."""
        self._retry_counts[sig] = self._retry_counts.get(sig, 0) + 1

    def compute_action_signature(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """
        Creates a deterministic idempotency key for side-effecting actions.
        Normalizes target paths and argument semantics.
        """
        action_type = "execute"
        if any(w in tool_name for w in ["write_file", "write_script", "save_file", "create_file"]):
            action_type = "write_file"
        elif any(w in tool_name for w in ["run_python", "execute_terminal", "run_script"]):
            action_type = "run_code"
        elif any(w in tool_name for w in ["read_file", "view_file"]):
            action_type = "read_file"

        target = ""
        for key in ["file_path", "file_name", "code_or_file", "path", "target"]:
            if key in tool_args and isinstance(tool_args[key], str):
                target = canonical_path(tool_args[key]).absolute_path
                break

        # For writing files, hash the code/content to detect duplicate writes
        content_hash = ""
        for key in ["code", "content", "text"]:
            if key in tool_args and isinstance(tool_args[key], str):
                content_hash = hashlib.md5(tool_args[key].strip().encode("utf-8")).hexdigest()[:12]
                break

        sig_str = f"{action_type}:{target}:{content_hash}"
        return hashlib.sha256(sig_str.encode("utf-8")).hexdigest()[:16]

    def should_execute(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        executed_observations: List[CanonicalToolResult],
        already_called_signatures: List[str]
    ) -> Tuple[bool, Optional[str]]:
        """
        Evaluates whether the tool call is safe, necessary, and non-redundant.
        Returns: (allow_execution: bool, skip_reason: Optional[str])
        """
        # 1. Extract target path if any
        target_raw = None
        for key in ["file_path", "file_name", "code_or_file", "path"]:
            if key in tool_args and isinstance(tool_args[key], str):
                target_raw = tool_args[key]
                break

        cpath = canonical_path(target_raw) if target_raw else None

        # 2. Check per-action retry limit (V3.2: DecisionEngine owns this gate)
        sig = self.compute_action_signature(tool_name, tool_args)
        failures = self._retry_counts.get(sig, 0)
        if failures >= MAX_RETRIES_PER_ACTION:
            return False, (
                f"Action '{tool_name}' has failed {failures} time(s) — "
                f"MAX_RETRIES_PER_ACTION ({MAX_RETRIES_PER_ACTION}) reached. Blocking further retries."
            )

        # 3. Check: Is this a redundant duplicate file write?
        if tool_name in ["filesystem_write_file", "coding_write_script"]:
            for obs in executed_observations:
                if obs.success and obs.action == "create_file":
                    prev_path = obs.artifact.get("path") if obs.artifact else None
                    if prev_path and cpath and os.path.abspath(prev_path) == cpath.absolute_path:
                        # Check if an execution has occurred
                        runs_since_write = [
                            o for o in executed_observations
                            if o.action in ["execute_file", "execute_code"]
                        ]
                        # If execution failed, allow re-writing/patching the file!
                        if runs_since_write and not runs_since_write[-1].success:
                            continue

                        # If file already created on disk and has content, check if content changed
                        if cpath.exists and os.path.getsize(cpath.absolute_path) > 0:
                            new_content = tool_args.get("code") or tool_args.get("content") or ""
                            with open(cpath.absolute_path, "r", encoding="utf-8", errors="ignore") as f:
                                existing_content = f.read()
                            if existing_content.strip() and new_content.strip() == existing_content.strip():
                                return False, f"Equivalent write already completed by {obs.tool} to {cpath.relative_path}."
                            elif not runs_since_write and tool_name == "filesystem_write_file" and obs.tool == "coding_write_script":
                                return False, f"Equivalent script creation already completed by {obs.tool} to {cpath.relative_path}."

        # 4. Idempotency Check: Identical action signature already succeeded in this task
        if sig in already_called_signatures:
            # Re-running code is only permitted if the file was modified since last run
            if "run_python" in tool_name:
                # Check if a write occurred AFTER the last execution
                runs = [i for i, obs in enumerate(executed_observations) if obs.action in ["execute_file", "execute_code"]]
                writes = [i for i, obs in enumerate(executed_observations) if obs.action == "create_file"]
                if runs and writes and max(writes) > max(runs):
                    # Permitted: file was modified since last run (retry loop)
                    pass
                else:
                    return False, f"Identical execution already completed with nominal outcome."
            else:
                return False, f"Identical action signature ({sig}) already completed successfully in this task."

        return True, None


decision_engine = DecisionEngine()
