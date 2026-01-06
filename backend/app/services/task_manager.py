from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock
from time import monotonic


class TaskCancelledError(RuntimeError):
    pass


@dataclass
class TaskControl:
    cancel_event: Event
    created_at: float


class TaskManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._tasks: dict[str, TaskControl] = {}

    def ensure(self, task_id: str) -> TaskControl:
        tid = (task_id or "").strip()
        if not tid:
            raise ValueError("Missing task_id")

        with self._lock:
            ctrl = self._tasks.get(tid)
            if ctrl is None:
                ctrl = TaskControl(cancel_event=Event(), created_at=monotonic())
                self._tasks[tid] = ctrl
            return ctrl

    def cancel(self, task_id: str) -> None:
        ctrl = self.ensure(task_id)
        ctrl.cancel_event.set()

    def is_cancelled(self, task_id: str) -> bool:
        tid = (task_id or "").strip()
        if not tid:
            return False
        with self._lock:
            ctrl = self._tasks.get(tid)
            return bool(ctrl and ctrl.cancel_event.is_set())

    def cleanup(self, task_id: str) -> None:
        tid = (task_id or "").strip()
        if not tid:
            return
        with self._lock:
            self._tasks.pop(tid, None)


task_manager = TaskManager()

