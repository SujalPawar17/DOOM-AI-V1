"""
DOOM V3 — Autonomous Task Engine
Maintains the full lifecycle of tasks, step-by-step progress, checklists, and execution traces.
"""

from enum import Enum
from dataclasses import dataclass, field, asdict
import time
import uuid
from typing import List, Dict, Any, Optional
from core.state_machine import state_machine, DoomState


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class TaskStep:
    index: int
    description: str
    status: str = "pending"  # pending, active, completed, failed
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    tool_output: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Task:
    task_id: str
    goal: str
    status: TaskStatus = TaskStatus.CREATED
    progress: int = 0
    current_step: str = "Initializing goal..."
    steps: List[TaskStep] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    models_used: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: time.strftime("%H:%M:%S"))
    start_time: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    result: Optional[str] = None
    error: Optional[str] = None
    user_approval_required: bool = False
    pending_tool_call: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["steps"] = [s.to_dict() if isinstance(s, TaskStep) else s for s in self.steps]
        return d


class TaskEngine:
    """Central manager for active and historical autonomous tasks."""

    def __init__(self):
        self._active_task: Optional[Task] = None
        self._task_history: List[Task] = []
        self._max_history = 30

    @property
    def active_task(self) -> Optional[Task]:
        return self._active_task

    def create_task(self, goal: str) -> Task:
        """Initializes a new autonomous task and marks it active."""
        task_id = f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        task = Task(task_id=task_id, goal=goal, status=TaskStatus.CREATED)
        self._active_task = task
        self._task_history.insert(0, task)
        if len(self._task_history) > self._max_history:
            self._task_history.pop()

        state_machine.transition_to(DoomState.PLANNING, f"Planning: {goal[:40]}...", task_id=task_id)
        return task

    def set_plan_steps(self, step_descriptions: List[str]) -> None:
        """Sets planned steps for the active task."""
        if not self._active_task:
            return
        steps = []
        for idx, desc in enumerate(step_descriptions):
            steps.append(TaskStep(index=idx + 1, description=desc, status="pending"))
        if steps:
            steps[0].status = "active"
            self._active_task.current_step = steps[0].description
        self._active_task.steps = steps
        self._active_task.status = TaskStatus.RUNNING
        self._update_progress()
        state_machine.transition_to(DoomState.EXECUTING, self._active_task.current_step, task_id=self._active_task.task_id)

    def advance_step(self, step_index: int, tool_name: Optional[str] = None, output: Optional[str] = None, success: bool = True) -> None:
        """Marks a step completed and moves to the next."""
        if not self._active_task or not self._active_task.steps:
            return

        for s in self._active_task.steps:
            if s.index == step_index:
                s.status = "completed" if success else "failed"
                s.tool_name = tool_name
                s.tool_output = output
            elif s.index == step_index + 1:
                s.status = "active"
                self._active_task.current_step = s.description

        if tool_name and tool_name not in self._active_task.tools_used:
            self._active_task.tools_used.append(tool_name)

        self._update_progress()

    def record_tool_call(self, tool_name: str, model_name: Optional[str] = None) -> None:
        """Logs a tool or model execution in the active task."""
        if not self._active_task:
            return
        if tool_name and tool_name not in self._active_task.tools_used:
            self._active_task.tools_used.append(tool_name)
        if model_name and model_name not in self._active_task.models_used:
            self._active_task.models_used.append(model_name)

    def complete_task(self, final_result: str) -> None:
        """Marks active task as successfully completed."""
        if not self._active_task:
            return
        self._active_task.status = TaskStatus.COMPLETED
        self._active_task.progress = 100
        self._active_task.result = final_result
        self._active_task.duration_ms = round((time.time() - self._active_task.start_time) * 1000, 2)
        for s in self._active_task.steps:
            if s.status == "pending" or s.status == "active":
                s.status = "completed"

        state_machine.transition_to(DoomState.COMPLETED, "Goal accomplished, Boss.", task_id=self._active_task.task_id)
        # Reset active task reference after brief retention
        completed_id = self._active_task.task_id
        self._active_task = None
        state_machine.transition_to(DoomState.IDLE, "Standing by, Boss.")

    def fail_task(self, error_message: str) -> None:
        """Marks active task as failed."""
        if not self._active_task:
            return
        self._active_task.status = TaskStatus.FAILED
        self._active_task.error = error_message
        self._active_task.duration_ms = round((time.time() - self._active_task.start_time) * 1000, 2)
        state_machine.transition_to(DoomState.ERROR, f"Task failure: {error_message[:40]}", task_id=self._active_task.task_id)
        self._active_task = None
        state_machine.transition_to(DoomState.IDLE, "Standing by, Boss.")

    def require_user_approval(self, tool_name: str, tool_args: Dict[str, Any]) -> None:
        """Pauses task execution until user authorizes a high-risk tool."""
        if not self._active_task:
            return
        self._active_task.status = TaskStatus.WAITING_FOR_USER
        self._active_task.user_approval_required = True
        self._active_task.pending_tool_call = {"name": tool_name, "args": tool_args}
        state_machine.transition_to(
            DoomState.WAITING_FOR_APPROVAL,
            f"Authorization required: {tool_name}",
            task_id=self._active_task.task_id
        )

    def _update_progress(self) -> None:
        if not self._active_task or not self._active_task.steps:
            return
        total = len(self._active_task.steps)
        completed = sum(1 for s in self._active_task.steps if s.status == "completed")
        self._active_task.progress = int((completed / total) * 100)

    def get_active_task_dict(self) -> Optional[Dict[str, Any]]:
        return self._active_task.to_dict() if self._active_task else None

    def get_history_dicts(self, limit: int = 15) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self._task_history[:limit]]


# Global Task Engine instance
task_engine = TaskEngine()
