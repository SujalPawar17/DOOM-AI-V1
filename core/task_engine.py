"""
DOOM V3.3 — Autonomous Task Engine with Checkpoint/Resume
Maintains the full lifecycle of tasks, step-by-step progress, checklists, execution traces,
and persistent checkpoints for crash recovery and provider outage handling.
"""

from enum import Enum
from dataclasses import dataclass, field, asdict
import time
import uuid
import json
import os
from typing import List, Dict, Any, Optional, Callable
from core.state_machine import state_machine, DoomState
from database.postgres_db import postgres_manager


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"                    # V3.3: Paused for provider outage / waiting
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"   # V3.3: Some steps done, others blocked/failed
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"         # V3.3: Cannot proceed due to dependency/provider
    SKIPPED = "skipped"         # V3.3: Idempotent skip (already done)


class VerificationStatus(str, Enum):
    NOT_VERIFIED = "not_verified"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    NOT_APPLICABLE = "not_applicable"


class FinalResponseStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskStep:
    index: int
    description: str
    status: StepStatus = StepStatus.PENDING
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_output: Optional[str] = None
    error: Optional[str] = None
    # V3.3: Verification and artifact tracking
    verification_status: VerificationStatus = VerificationStatus.NOT_VERIFIED
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    retry_count: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["verification_status"] = self.verification_status.value
        return d


@dataclass
class Task:
    task_id: str
    goal: str
    task_type: str = "QUERY"  # DIRECT, QUERY, ACTION, MULTI_STEP, AUTONOMOUS
    status: TaskStatus = TaskStatus.CREATED
    progress: int = 0
    current_step: str = "Initializing goal..."
    steps: List[TaskStep] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    models_used: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: time.strftime("%H:%M:%S"))
    updated_at: str = field(default_factory=lambda: time.strftime("%H:%M:%S"))
    start_time: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    result: Optional[str] = None
    error: Optional[str] = None
    user_approval_required: bool = False
    pending_tool_call: Optional[Dict[str, Any]] = None
    # V3.3: Checkpoint/Resume fields
    plan: Optional[Dict[str, Any]] = None
    checkpoint: Optional[Dict[str, Any]] = None
    final_response_status: FinalResponseStatus = FinalResponseStatus.SUCCESS
    termination_reason: Optional[str] = None
    resume_available: bool = True

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["final_response_status"] = self.final_response_status.value
        d["steps"] = [s.to_dict() if isinstance(s, TaskStep) else s for s in self.steps]
        return d

    def to_checkpoint(self) -> Dict[str, Any]:
        """Serialize task to checkpoint format for persistence."""
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "task_type": self.task_type,
            "status": self.status.value,
            "current_step": self.current_step,
            "completed_steps": [s.to_dict() for s in self.steps if s.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED)],
            "remaining_steps": [s.to_dict() for s in self.steps if s.status in (StepStatus.PENDING, StepStatus.RUNNING)],
            "failed_steps": [s.to_dict() for s in self.steps if s.status == StepStatus.FAILED],
            "blocked_steps": [s.to_dict() for s in self.steps if s.status == StepStatus.BLOCKED],
            "artifacts": [a for s in self.steps for a in s.artifacts],
            "tool_results": [],  # Filled by orchestrator
            "verification_results": [],  # Filled by orchestrator
            "models_used": self.models_used,
            "retry_counts": {f"step_{s.index}": s.retry_count for s in self.steps if s.retry_count > 0},
            "termination_reason": self.termination_reason,
            "created_at": self.created_at,
            "updated_at": time.strftime("%H:%M:%S"),
            "final_response_status": self.final_response_status.value,
            "resume_available": self.resume_available,
        }


class TaskEngine:
    """Central manager for active and historical autonomous tasks with checkpoint persistence."""

    def __init__(self):
        self._active_task: Optional[Task] = None
        self._task_history: List[Task] = []
        self._max_history = 30
        self._checkpoint_dir = os.path.join(os.path.expanduser("~"), ".doom", "checkpoints")
        os.makedirs(self._checkpoint_dir, exist_ok=True)
        self._state_broadcaster: Optional[Callable[[Dict[str, Any]], None]] = None

    def set_state_broadcaster(self, broadcaster: Callable[[Dict[str, Any]], None]) -> None:
        """Registers a callback for broadcasting task state changes to WebSocket clients."""
        self._state_broadcaster = broadcaster

    def _broadcast_task_state(self, event_type: str, **extra) -> None:
        """Broadcasts task state change to registered WebSocket clients."""
        if not self._state_broadcaster or not self._active_task:
            return
        try:
            payload = {
                "type": "task_state",
                "event": event_type,
                "task_id": self._active_task.task_id,
                "status": self._active_task.status.value,
                "current_step": self._active_task.current_step,
                "progress": self._active_task.progress,
                "resume_available": self._active_task.resume_available,
                "timestamp": time.strftime("%H:%M:%S"),
                **extra
            }
            self._state_broadcaster(payload)
        except Exception:
            pass

    @property
    def active_task(self) -> Optional[Task]:
        return self._active_task

    def create_task(self, goal: str, task_type: str = "QUERY") -> Task:
        """Initializes a new autonomous task and marks it active."""
        task_id = f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        task = Task(task_id=task_id, goal=goal, task_type=task_type, status=TaskStatus.CREATED)
        self._active_task = task
        self._task_history.insert(0, task)
        if len(self._task_history) > self._max_history:
            self._task_history.pop()

        state_machine.transition_to(DoomState.PLANNING, f"Planning: {goal[:40]}...", task_id=task_id)
        self._broadcast_task_state("TASK_CREATED", goal=goal, task_type=task_type)
        return task

    def set_plan_steps(self, step_descriptions: List[str], plan: Optional[Dict[str, Any]] = None) -> None:
        """Sets planned steps for the active task."""
        if not self._active_task:
            return
        steps = []
        for idx, desc in enumerate(step_descriptions):
            steps.append(TaskStep(index=idx + 1, description=desc, status=StepStatus.PENDING))
        if steps:
            steps[0].status = StepStatus.RUNNING
            steps[0].started_at = time.strftime("%H:%M:%S")
            self._active_task.current_step = steps[0].description
        self._active_task.steps = steps
        self._active_task.plan = plan
        self._active_task.status = TaskStatus.RUNNING
        self._update_progress()
        state_machine.transition_to(DoomState.EXECUTING, self._active_task.current_step, task_id=self._active_task.task_id)
        self._save_checkpoint()
        self._broadcast_task_state("PLAN_SET", step_count=len(steps))

    def advance_step(self, step_index: int, tool_name: Optional[str] = None, output: Optional[str] = None, success: bool = True, artifacts: Optional[List[Dict[str, Any]]] = None) -> None:
        """Marks a step completed and moves to the next."""
        if not self._active_task or not self._active_task.steps:
            return

        for s in self._active_task.steps:
            if s.index == step_index:
                s.status = StepStatus.SUCCEEDED if success else StepStatus.FAILED
                s.tool_name = tool_name
                s.tool_output = output
                s.error = None if success else output
                s.completed_at = time.strftime("%H:%M:%S")
                if artifacts:
                    s.artifacts = artifacts
            elif s.index == step_index + 1:
                s.status = StepStatus.RUNNING
                s.started_at = time.strftime("%H:%M:%S")
                self._active_task.current_step = s.description

        if tool_name and tool_name not in self._active_task.tools_used:
            self._active_task.tools_used.append(tool_name)

        self._active_task.updated_at = time.strftime("%H:%M:%S")
        self._update_progress()
        self._save_checkpoint()
        self._broadcast_task_state("STEP_ADVANCED", step_index=step_index, tool_name=tool_name, success=success)

    def mark_step_blocked(self, step_index: int, reason: str) -> None:
        """Marks a step as blocked (e.g., waiting for provider)."""
        if not self._active_task or not self._active_task.steps:
            return
        for s in self._active_task.steps:
            if s.index == step_index:
                s.status = StepStatus.BLOCKED
                s.error = reason
                s.completed_at = time.strftime("%H:%M:%S")
        self._active_task.updated_at = time.strftime("%H:%M:%S")
        self._update_progress()
        self._save_checkpoint()
        self._broadcast_task_state("STEP_BLOCKED", step_index=step_index, reason=reason)

    def record_tool_call(self, tool_name: str, model_name: Optional[str] = None) -> None:
        """Logs a tool or model execution in the active task."""
        if not self._active_task:
            return
        if tool_name and tool_name not in self._active_task.tools_used:
            self._active_task.tools_used.append(tool_name)
        if model_name and model_name not in self._active_task.models_used:
            self._active_task.models_used.append(model_name)

    def complete_task(self, final_result: str, final_response_status: FinalResponseStatus = FinalResponseStatus.SUCCESS, termination_reason: Optional[str] = None) -> None:
        """Marks active task as successfully completed."""
        if not self._active_task:
            return
        self._active_task.status = TaskStatus.COMPLETED
        self._active_task.progress = 100
        self._active_task.result = final_result
        self._active_task.final_response_status = final_response_status
        self._active_task.termination_reason = termination_reason
        self._active_task.duration_ms = round((time.time() - self._active_task.start_time) * 1000, 2)
        for s in self._active_task.steps:
            if s.status in (StepStatus.PENDING, StepStatus.RUNNING):
                s.status = StepStatus.SUCCEEDED

        state_machine.transition_to(DoomState.COMPLETED, "Goal accomplished, Boss.", task_id=self._active_task.task_id)
        self._save_checkpoint()
        self._broadcast_task_state("TASK_COMPLETED", result=final_result, final_status=final_response_status.value)
        completed_id = self._active_task.task_id
        self._active_task = None
        state_machine.transition_to(DoomState.IDLE, "Standing by, Boss.")

    def complete_task_partial(self, final_result: str, termination_reason: Optional[str] = None) -> None:
        """Marks active task as partially completed (some steps succeeded, others blocked/failed)."""
        if not self._active_task:
            return
        self._active_task.status = TaskStatus.PARTIAL_SUCCESS
        self._active_task.result = final_result
        self._active_task.final_response_status = FinalResponseStatus.PARTIAL_SUCCESS
        self._active_task.termination_reason = termination_reason
        self._active_task.duration_ms = round((time.time() - self._active_task.start_time) * 1000, 2)

        state_machine.transition_to(DoomState.COMPLETED, f"Partially completed: {termination_reason}", task_id=self._active_task.task_id)
        self._save_checkpoint()
        self._broadcast_task_state("TASK_PARTIAL", result=final_result, termination_reason=termination_reason)
        completed_id = self._active_task.task_id
        self._active_task = None
        state_machine.transition_to(DoomState.IDLE, "Standing by, Boss.")

    def pause_task(self, reason: str) -> None:
        """Pauses the active task (e.g., for provider outage)."""
        if not self._active_task:
            return
        self._active_task.status = TaskStatus.PAUSED
        self._active_task.termination_reason = reason
        self._active_task.final_response_status = FinalResponseStatus.BLOCKED
        self._active_task.resume_available = True
        state_machine.transition_to(DoomState.PROCESSING, f"Paused: {reason}", task_id=self._active_task.task_id)
        self._save_checkpoint()
        self._broadcast_task_state("TASK_PAUSED", reason=reason, resume_available=True)

    def fail_task(self, error_message: str) -> None:
        """Marks active task as failed."""
        if not self._active_task:
            return
        self._active_task.status = TaskStatus.FAILED
        self._active_task.error = error_message
        self._active_task.final_response_status = FinalResponseStatus.FAILED
        self._active_task.duration_ms = round((time.time() - self._active_task.start_time) * 1000, 2)
        state_machine.transition_to(DoomState.ERROR, f"Task failure: {error_message[:40]}", task_id=self._active_task.task_id)
        self._save_checkpoint()
        self._broadcast_task_state("TASK_FAILED", error=error_message)
        self._active_task = None
        state_machine.transition_to(DoomState.IDLE, "Standing by, Boss.")

    def require_user_approval(self, tool_name: str, tool_args: Dict[str, Any]) -> None:
        """Pauses task execution until user authorizes a high-risk tool."""
        if not self._active_task:
            return
        self._active_task.status = TaskStatus.WAITING_FOR_APPROVAL
        self._active_task.user_approval_required = True
        self._active_task.pending_tool_call = {"name": tool_name, "args": tool_args}
        state_machine.transition_to(
            DoomState.WAITING_FOR_APPROVAL,
            f"Authorization required: {tool_name}",
            task_id=self._active_task.task_id
        )
        self._save_checkpoint()

    def resume_task(self, task_id: str) -> Optional[Task]:
        """Resumes a paused/failed task from checkpoint."""
        # Find task in history
        target_task = None
        for t in self._task_history:
            if t.task_id == task_id:
                target_task = t
                break
        
        if not target_task:
            # Try loading from checkpoint file
            target_task = self._load_checkpoint(task_id)
            if not target_task:
                return None
        
        # Restore as active task
        self._active_task = target_task
        self._active_task.status = TaskStatus.RUNNING
        self._active_task.resume_available = False
        self._active_task.updated_at = time.strftime("%H:%M:%S")
        
        # Find first incomplete step
        next_step = None
        for s in self._active_task.steps:
            if s.status in (StepStatus.PENDING, StepStatus.RUNNING, StepStatus.BLOCKED):
                next_step = s
                break
        
        if next_step:
            # V3.3 Security: Check if next action requires approval before resuming
            if next_step.tool_name:
                from tools.base import RiskLevel
                from core.tool_registry import tool_registry
                tool_obj = tool_registry.get_tool(next_step.tool_name)
                if tool_obj and tool_obj.get_effective_risk() in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                    # Re-require approval even on resume
                    next_step.status = StepStatus.BLOCKED
                    self._active_task.status = TaskStatus.WAITING_FOR_APPROVAL
                    self._active_task.user_approval_required = True
                    self._active_task.pending_tool_call = {"name": next_step.tool_name, "args": next_step.tool_args or {}}
                    state_machine.transition_to(
                        DoomState.WAITING_FOR_APPROVAL,
                        f"Resumed: Authorization required for {next_step.tool_name}",
                        task_id=task_id
                    )
                    self._save_checkpoint()
                    self._broadcast_task_state("TASK_RESUMED_APPROVAL_REQUIRED", tool_name=next_step.tool_name)
                    return self._active_task
            
            next_step.status = StepStatus.RUNNING
            next_step.started_at = time.strftime("%H:%M:%S")
            self._active_task.current_step = next_step.description
            state_machine.transition_to(DoomState.EXECUTING, f"Resumed: {next_step.description}", task_id=task_id)
        else:
            # All steps done - verify and complete
            state_machine.transition_to(DoomState.VERIFYING, "Resumed: verifying results", task_id=task_id)
        
        self._save_checkpoint()
        self._broadcast_task_state("TASK_RESUMED", resumed_from_step=next_step.description if next_step else "verification")
        return self._active_task

    def _update_progress(self) -> None:
        if not self._active_task or not self._active_task.steps:
            return
        total = len(self._active_task.steps)
        completed = sum(1 for s in self._active_task.steps if s.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED))
        self._active_task.progress = int((completed / total) * 100) if total > 0 else 0

    def _save_checkpoint(self) -> None:
        """Persists checkpoint to PostgreSQL and local file."""
        if not self._active_task:
            return
        
        checkpoint = self._active_task.to_checkpoint()
        self._active_task.checkpoint = checkpoint
        
        # Save to PostgreSQL task_checkpoints table
        try:
            if postgres_manager.is_connected():
                postgres_manager.save_checkpoint(checkpoint)
        except Exception as e:
            print(f"[TASK ENGINE] PostgreSQL checkpoint save failed: {e}")
        
        # Save to local file (backup)
        try:
            checkpoint_file = os.path.join(self._checkpoint_dir, f"{self._active_task.task_id}.json")
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, indent=2, default=str)
        except Exception as e:
            print(f"[TASK ENGINE] Local checkpoint save failed: {e}")

    def _load_checkpoint(self, task_id: str) -> Optional[Task]:
        """Loads task from checkpoint file."""
        checkpoint_file = os.path.join(self._checkpoint_dir, f"{task_id}.json")
        if not os.path.exists(checkpoint_file):
            return None
        
        try:
            with open(checkpoint_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Reconstruct Task from checkpoint
            task = Task(
                task_id=data["task_id"],
                goal=data["goal"],
                task_type=data["task_type"],
                status=TaskStatus(data["status"]),
                current_step=data["current_step"],
                steps=[],
                tools_used=[],
                models_used=data.get("models_used", []),
                created_at=data["created_at"],
                updated_at=data.get("updated_at", time.strftime("%H:%M:%S")),
                start_time=time.time(),  # Approximate
                termination_reason=data.get("termination_reason"),
                final_response_status=FinalResponseStatus(data.get("final_response_status", "success")),
                resume_available=data.get("resume_available", True),
            )
            
            # Restore steps with enum conversion
            def _restore_steps(step_list):
                for step_data in step_list:
                    # Convert string status to enum
                    if "status" in step_data and isinstance(step_data["status"], str):
                        step_data["status"] = StepStatus(step_data["status"])
                    if "verification_status" in step_data and isinstance(step_data["verification_status"], str):
                        step_data["verification_status"] = VerificationStatus(step_data["verification_status"])
                    step = TaskStep(**step_data)
                    task.steps.append(step)
            
            _restore_steps(data.get("completed_steps", []))
            _restore_steps(data.get("remaining_steps", []))
            _restore_steps(data.get("failed_steps", []))
            _restore_steps(data.get("blocked_steps", []))
            
            # Sort by index
            task.steps.sort(key=lambda s: s.index)
            
            return task
        except Exception as e:
            print(f"[TASK ENGINE] Checkpoint load failed: {e}")
            return None

    def get_task_by_id(self, task_id: str) -> Optional[Task]:
        """Retrieves a task from history by ID."""
        for t in self._task_history:
            if t.task_id == task_id:
                return t
        # Try loading from checkpoint
        return self._load_checkpoint(task_id)

    def get_active_task_dict(self) -> Optional[Dict[str, Any]]:
        return self._active_task.to_dict() if self._active_task else None

    def get_resumable_tasks(self) -> List[Dict[str, Any]]:
        """Returns list of tasks that can be resumed (status in PAUSED, FAILED, PARTIAL_SUCCESS or resume_available=True)."""
        resumable = []
        seen_ids = set()
        
        # Check active task if paused/failed
        if self._active_task and self._active_task.resume_available and self._active_task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.PARTIAL_SUCCESS):
            resumable.append(self._active_task.to_dict())
            seen_ids.add(self._active_task.task_id)
            
        # Check history
        for t in self._task_history:
            if t.task_id not in seen_ids and t.resume_available and t.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.PARTIAL_SUCCESS):
                resumable.append(t.to_dict())
                seen_ids.add(t.task_id)
                
        # Check local checkpoints
        try:
            if os.path.exists(self._checkpoint_dir):
                for f in os.listdir(self._checkpoint_dir):
                    if f.endswith(".json"):
                        tid = f[:-5]
                        if tid not in seen_ids:
                            loaded = self._load_checkpoint(tid)
                            if loaded and loaded.resume_available and loaded.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.PARTIAL_SUCCESS):
                                resumable.append(loaded.to_dict())
                                seen_ids.add(tid)
        except Exception:
            pass
            
        return resumable

    def get_history_dicts(self, limit: int = 15) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self._task_history[:limit]]


# Global Task Engine instance
task_engine = TaskEngine()