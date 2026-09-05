"""
DOOM V4.2 — Task Concurrency & Ownership Lease Manager
Prevents concurrent execution of the same task by multiple workers or processes.
Provides durable leases with automatic heartbeat expiration for safe crash recovery.
"""

import os
import json
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass, field, asdict


@dataclass
class TaskLease:
    task_id: str
    owner_id: str
    acquired_at: float = field(default_factory=time.time)
    heartbeat_at: float = field(default_factory=time.time)
    ttl_seconds: float = 30.0

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.heartbeat_at) > self.ttl_seconds

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TaskConcurrencyManager:
    """
    Manages task ownership leases.
    Guarantees that exactly ONE worker executes a task at any given time.
    """

    def __init__(self, lock_dir: Optional[str] = None):
        if not lock_dir:
            base_dir = os.path.join(os.path.dirname(__file__), "..", "..", "database")
            os.makedirs(base_dir, exist_ok=True)
            self.lock_dir = os.path.join(base_dir, "task_locks")
        else:
            self.lock_dir = lock_dir
        os.makedirs(self.lock_dir, exist_ok=True)

        self._memory_leases: Dict[str, TaskLease] = {}

    def _lock_file_path(self, task_id: str) -> str:
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in task_id)
        return os.path.join(self.lock_dir, f"{safe_name}.lock")

    def acquire_lease(
        self,
        task_id: str,
        owner_id: str,
        ttl_seconds: float = 30.0
    ) -> bool:
        """
        Attempts to acquire or refresh an execution lease on a task.
        Returns: True if lease acquired/held, False if locked by another active worker.
        """
        now = time.time()
        lock_file = self._lock_file_path(task_id)

        # Check existing disk lock
        if os.path.exists(lock_file):
            try:
                with open(lock_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    lease = TaskLease(**data)
                    
                    if lease.owner_id == owner_id:
                        # Refresh lease
                        lease.heartbeat_at = now
                        self._write_lock_file(lock_file, lease)
                        self._memory_leases[task_id] = lease
                        return True
                    
                    if not lease.is_expired:
                        # Owned by another worker and active
                        return False
                    else:
                        # Stale lock from crashed worker: permit takeover
                        print(f"[CONCURRENCY] Stale lease expired for task '{task_id}' (held by {lease.owner_id}). Taking over.")
            except Exception:
                pass

        # Acquire new lease
        lease = TaskLease(
            task_id=task_id,
            owner_id=owner_id,
            acquired_at=now,
            heartbeat_at=now,
            ttl_seconds=ttl_seconds
        )
        self._write_lock_file(lock_file, lease)
        self._memory_leases[task_id] = lease
        return True

    def heartbeat(self, task_id: str, owner_id: str) -> bool:
        """Updates lease heartbeat to prevent expiration during long operations."""
        lock_file = self._lock_file_path(task_id)
        lease = self._memory_leases.get(task_id)
        if lease and lease.owner_id == owner_id:
            lease.heartbeat_at = time.time()
            self._write_lock_file(lock_file, lease)
            return True
        return False

    def release_lease(self, task_id: str, owner_id: str) -> bool:
        """Releases the task lease when execution finishes or pauses."""
        lock_file = self._lock_file_path(task_id)
        self._memory_leases.pop(task_id, None)

        if os.path.exists(lock_file):
            try:
                with open(lock_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("owner_id") == owner_id:
                        os.remove(lock_file)
                        return True
            except Exception:
                try:
                    os.remove(lock_file)
                except OSError:
                    pass
        return True

    def is_locked(self, task_id: str) -> bool:
        """Checks if a task is actively locked by any live worker."""
        lock_file = self._lock_file_path(task_id)
        if os.path.exists(lock_file):
            try:
                with open(lock_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    lease = TaskLease(**data)
                    return not lease.is_expired
            except Exception:
                return False
        return False

    def _write_lock_file(self, path: str, lease: TaskLease) -> None:
        try:
            temp_path = f"{path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(lease.to_dict(), f)
            if os.path.exists(temp_path):
                os.replace(temp_path, path)
        except Exception as e:
            print(f"[CONCURRENCY] Failed to write lock file: {e}")

    def reset(self) -> None:
        """Cleans up memory leases and lock files."""
        self._memory_leases.clear()
        if os.path.exists(self.lock_dir):
            for fname in os.listdir(self.lock_dir):
                if fname.endswith(".lock"):
                    try:
                        os.remove(os.path.join(self.lock_dir, fname))
                    except OSError:
                        pass


# Global singleton instance
task_concurrency_manager = TaskConcurrencyManager()
