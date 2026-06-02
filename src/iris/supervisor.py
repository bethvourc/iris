from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable

from iris.config import IrisConfig
from iris.runtime import RunOrchestrator
from iris.safety import CancellationToken
from iris.state import open_state
from iris.tasks import (
    add_task_step,
    cancel_requested,
    claim_next_task,
    finish_task,
    heartbeat_task,
)


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
        self.orchestrator = RunOrchestrator(
            config=config,
            router_factory=router_factory,
        )

    def run_next(self) -> SupervisorResult:
        with open_state(self.config) as db:
            task = claim_next_task(db)
        if task is None:
            return SupervisorResult(True, "No queued tasks.")
        task_id = str(task["task_id"])
        self._log_step(
            task_id, kind="claim", status="running", message="Task claimed by worker."
        )
        if self._cancelled(task_id):
            with open_state(self.config) as db:
                add_task_step(
                    db,
                    task_id,
                    kind="cancel",
                    status="cancelled",
                    message="Task was cancelled before it started.",
                )
                finish_task(
                    db,
                    task_id,
                    status="cancelled",
                    error="cancel requested before start",
                )
            return SupervisorResult(
                False, "Task was cancelled before it started.", task_id, "cancelled"
            )
        cancellation_token = CancellationToken()
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_until_stopped,
            args=(task_id, heartbeat_stop, cancellation_token),
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            result = self.orchestrator.run_existing_task(
                task,
                cancellation_token=cancellation_token,
            )
            status = result.status
            if self._cancelled(task_id) and status == "done":
                status = "cancelled"
            return SupervisorResult(
                result.ok, result.message, task_id, status, result.payload
            )
        except Exception as exc:
            with open_state(self.config) as db:
                add_task_step(
                    db,
                    task_id,
                    kind="error",
                    status="failed",
                    message=str(exc),
                )
                finish_task(db, task_id, status="failed", error=str(exc))
            return SupervisorResult(False, str(exc), task_id, "failed")
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1.0)

    def run_forever(self, *, poll_interval: float = 2.0) -> None:
        while True:
            result = self.run_next()
            if result.task_id:
                print(f"task {result.task_id}: {result.status} - {result.message}")
            time.sleep(max(0.2, poll_interval))

    def _cancelled(self, task_id: str) -> bool:
        with open_state(self.config) as db:
            return cancel_requested(db, task_id)

    def _heartbeat_until_stopped(
        self,
        task_id: str,
        stop_event: threading.Event,
        cancellation_token: CancellationToken,
    ) -> None:
        beat = 0
        while not stop_event.wait(3.0):
            beat += 1
            with open_state(self.config) as db:
                heartbeat_task(db, task_id)
                if beat == 1 or beat % 5 == 0:
                    add_task_step(
                        db,
                        task_id,
                        kind="heartbeat",
                        status="running",
                        message="Task is still running.",
                        payload={"beat": beat},
                    )
            if self._cancelled(task_id):
                cancellation_token.cancel("Task cancellation requested.")
                with open_state(self.config) as db:
                    add_task_step(
                        db,
                        task_id,
                        kind="cancel",
                        status="running",
                        message="Cancel requested; waiting for the active step to finish.",
                    )

    def _log_step(self, task_id: str, *, kind: str, status: str, message: str) -> None:
        with open_state(self.config) as db:
            add_task_step(db, task_id, kind=kind, status=status, message=message)

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
