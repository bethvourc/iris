from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable

from iris.config import IrisConfig
from iris.state import open_state
from iris.tasks import cancel_requested, claim_next_task, finish_task, heartbeat_task


RouterFactory = Callable[[], Any]


@dataclass(frozen=True)
class SupervisorResult:
    ok: bool
    message: str
    task_id: str | None = None
    status: str | None = None
    payload: Any | None = None


class TaskSupervisor:
    def __init__(self, *, config: IrisConfig, router_factory: RouterFactory) -> None:
        self.config = config
        self.router_factory = router_factory

    def run_next(self) -> SupervisorResult:
        with open_state(self.config) as db:
            task = claim_next_task(db)
        if task is None:
            return SupervisorResult(True, "No queued tasks.")
        task_id = str(task["task_id"])
        if self._cancelled(task_id):
            with open_state(self.config) as db:
                finish_task(db, task_id, status="cancelled", error="cancel requested before start")
            return SupervisorResult(False, "Task was cancelled before it started.", task_id, "cancelled")
        message = self._task_message(task)
        if not message:
            with open_state(self.config) as db:
                finish_task(db, task_id, status="failed", error="task has no executable message or goal")
            return SupervisorResult(False, "Task has no executable message or goal.", task_id, "failed")
        with open_state(self.config) as db:
            heartbeat_task(db, task_id)
        router = self.router_factory()
        try:
            result = router.handle_text(message)
            status = "done" if result.ok else _task_status_for_message(result.message)
            if self._cancelled(task_id) and status == "done":
                status = "cancelled"
            with open_state(self.config) as db:
                finish_task(
                    db,
                    task_id,
                    status=status,  # type: ignore[arg-type]
                    result_value={
                        "message": result.message,
                        "payload": result.payload,
                        "ok": result.ok,
                    },
                    error=None if result.ok else result.message,
                )
            return SupervisorResult(result.ok, result.message, task_id, status, result.payload)
        except Exception as exc:
            with open_state(self.config) as db:
                finish_task(db, task_id, status="failed", error=str(exc))
            return SupervisorResult(False, str(exc), task_id, "failed")

    def run_forever(self, *, poll_interval: float = 2.0) -> None:
        while True:
            result = self.run_next()
            if result.task_id:
                print(f"task {result.task_id}: {result.status} - {result.message}")
            time.sleep(max(0.2, poll_interval))

    def _cancelled(self, task_id: str) -> bool:
        with open_state(self.config) as db:
            return cancel_requested(db, task_id)

    @staticmethod
    def _task_message(task: dict[str, Any]) -> str:
        input_value = task.get("input") if isinstance(task.get("input"), dict) else {}
        return str(
            input_value.get("message")
            or input_value.get("goal")
            or input_value.get("workflow")
            or task.get("title")
            or ""
        ).strip()


def _task_status_for_message(message: str) -> str:
    lowered = message.lower()
    if "needs approval" in lowered or "approval" in lowered:
        return "waiting_approval"
    if "blocked" in lowered:
        return "blocked"
    return "failed"
