from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable, Literal

from iris.config import IrisConfig
from iris.safety import CancellationToken
from iris.sessions import add_message, ensure_session
from iris.state import open_state
from iris.tasks import (
    add_task_step,
    create_task,
    finish_task,
    get_task,
    heartbeat_task,
    request_cancel,
    start_task,
)
from iris.tracing import list_trace_events, record_trace_event


RunStatus = Literal[
    "queued",
    "running",
    "planning",
    "executing_tool",
    "waiting_approval",
    "blocked",
    "responding",
    "cancelling",
    "cancelled",
    "failed",
    "done",
]
RunMode = Literal["sync", "queued"]
RouterFactory = Callable[[], Any]
RunSubscriber = Callable[["RunEvent"], None]
ACTIVE_STATUSES = {
    "queued",
    "running",
    "planning",
    "executing_tool",
    "waiting_approval",
    "responding",
    "cancelling",
}
TERMINAL_STATUSES = {"blocked", "cancelled", "failed", "done"}


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    event_name: str
    status: RunStatus
    component: str = "runtime"
    task_id: str | None = None
    session_id: str | None = None
    channel: str = ""
    message: str = ""
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["details"] = data["details"] or {}
        return data


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    status: RunStatus
    channel: str
    task_id: str | None = None
    session_id: str | None = None
    current_step: str = ""
    active_tool: str = ""
    message: str = ""
    started_at: str = ""
    updated_at: str = ""
    finished_at: str | None = None
    cancel_requested: bool = False
    approval_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunResult:
    ok: bool
    message: str
    payload: Any | None = None
    run_id: str = ""
    task_id: str | None = None
    session_id: str | None = None
    status: RunStatus = "done"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _ActiveRun:
    snapshot: RunSnapshot
    cancellation_token: CancellationToken
    router: Any | None = None


class RunOrchestrator:
    def __init__(self, *, config: IrisConfig, router_factory: RouterFactory) -> None:
        self.config = config
        self.router_factory = router_factory
        self._active: dict[str, _ActiveRun] = {}
        self._subscribers: list[RunSubscriber] = []
        self._lock = threading.RLock()
        self.recover_stale_runs()

    def recover_stale_runs(self, *, stale_after_seconds: float = 900.0) -> int:
        cutoff = datetime.fromtimestamp(
            time.time() - max(1.0, stale_after_seconds),
            tz=timezone.utc,
        ).isoformat()
        now = _now()
        with open_state(self.config) as db:
            rows = db.execute(
                """
                SELECT run_id, task_id, status, cancel_requested
                FROM agent_runs
                WHERE status IN ('running', 'planning', 'executing_tool', 'responding', 'cancelling')
                  AND updated_at < ?
                """,
                (cutoff,),
            ).fetchall()
            for row in rows:
                status = "cancelled" if int(row["cancel_requested"]) else "failed"
                message = (
                    "Run was cancelled before Iris restarted."
                    if status == "cancelled"
                    else "Run was interrupted before Iris restarted."
                )
                db.execute(
                    """
                    UPDATE agent_runs
                    SET status = ?, current_step = 'recovered', message = ?,
                        updated_at = ?, finished_at = ?
                    WHERE run_id = ?
                    """,
                    (status, message, now, now, row["run_id"]),
                )
                if row["task_id"]:
                    finish_task(
                        db,
                        str(row["task_id"]),
                        status=status,
                        error=None if status == "cancelled" else message,
                    )
            db.commit()
        return len(rows)

    def subscribe(self, callback: RunSubscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def start_run(
        self,
        message: str,
        *,
        channel: str = "terminal",
        session_id: str | None = None,
        mode: RunMode = "sync",
        persist_task: bool = False,
    ) -> RunResult:
        if mode == "queued":
            return self.queue_run(
                message,
                channel=channel,
                session_id=session_id,
            )
        return self._execute_run(
            message,
            channel=channel,
            session_id=session_id,
            persist_task=persist_task,
        )

    def queue_run(
        self,
        message: str,
        *,
        channel: str = "gateway",
        session_id: str | None = None,
    ) -> RunResult:
        with open_state(self.config) as db:
            session_id = ensure_session(
                db,
                session_id=session_id,
                channel=channel,
                title=_title_from_message(message),
            )
            add_message(
                db,
                session_id=session_id,
                role="user",
                content=message,
                metadata={"channel": channel},
            )
            task_id = create_task(
                db,
                session_id=session_id,
                kind="message",
                title=_title_from_message(message),
                input_value={"message": message, "channel": channel},
                status="queued",
            )
        snapshot = RunSnapshot(
            run_id=task_id,
            task_id=task_id,
            session_id=session_id,
            channel=channel,
            status="queued",
            current_step="queued",
            message=message[:220],
            started_at="",
            updated_at=_now(),
        )
        self._upsert_run(snapshot)
        self._emit(
            RunEvent(
                run_id=task_id,
                task_id=task_id,
                session_id=session_id,
                channel=channel,
                event_name="run.queued",
                status="queued",
                message=message[:220],
                details={"channel": channel},
            )
        )
        return RunResult(
            True,
            "Run queued.",
            run_id=task_id,
            task_id=task_id,
            session_id=session_id,
            status="queued",
        )

    def run_existing_task(
        self,
        task: dict[str, Any],
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> RunResult:
        message = _task_message(task)
        task_id = str(task["task_id"])
        if not message:
            with open_state(self.config) as db:
                add_task_step(
                    db,
                    task_id,
                    kind="validate",
                    status="failed",
                    message="Task has no executable message or goal.",
                )
                finish_task(
                    db,
                    task_id,
                    status="failed",
                    error="task has no executable message or goal",
                )
            return RunResult(
                False,
                "Task has no executable message or goal.",
                run_id=task_id,
                task_id=task_id,
                status="failed",
            )
        return self._execute_run(
            message,
            channel=str(
                task.get("input", {}).get("channel") or task.get("kind") or "task"
            ),
            session_id=task.get("session_id"),
            task_id=task_id,
            cancellation_token=cancellation_token,
            persist_task=True,
        )

    def cancel_run(self, run_id: str, reason: str = "Operation cancelled.") -> bool:
        cancelled = False
        task_id = None
        with self._lock:
            active = self._active.get(run_id)
            if active is not None:
                active.cancellation_token.cancel(reason)
                task_id = active.snapshot.task_id
                active.snapshot = _replace_snapshot(
                    active.snapshot,
                    status="cancelling",
                    current_step="cancelling",
                    message=reason,
                    cancel_requested=True,
                )
                cancelled = True
        if cancelled and task_id:
            with open_state(self.config) as db:
                request_cancel(db, task_id)
        if cancelled:
            self._emit(
                RunEvent(
                    run_id=run_id,
                    event_name="cancel.requested",
                    status="cancelling",
                    message=reason,
                )
            )
            return True
        emit_cancel: RunEvent | None = None
        with open_state(self.config) as db:
            row = db.execute(
                "SELECT task_id, status FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is not None:
                now = _now()
                terminal = str(row["status"]) in TERMINAL_STATUSES
                db.execute(
                    """
                    UPDATE agent_runs
                    SET cancel_requested = 1,
                        status = CASE
                          WHEN status IN ('blocked', 'cancelled', 'failed', 'done')
                          THEN status
                          ELSE 'cancelling'
                        END,
                        current_step = CASE
                          WHEN status IN ('blocked', 'cancelled', 'failed', 'done')
                          THEN current_step
                          ELSE 'cancelling'
                        END,
                        message = ?,
                        updated_at = ?
                    WHERE run_id = ?
                    """,
                    (reason, now, run_id),
                )
                db.commit()
                if row["task_id"]:
                    request_cancel(db, str(row["task_id"]))
                if not terminal:
                    emit_cancel = RunEvent(
                        run_id=run_id,
                        task_id=str(row["task_id"]) if row["task_id"] else None,
                        event_name="cancel.requested",
                        status="cancelling",
                        message=reason,
                    )
            else:
                task_cancelled = request_cancel(db, run_id)
                if not task_cancelled:
                    return False
                return True
        if emit_cancel is not None:
            self._emit(emit_cancel)
        return True

    def cancel_active_run(
        self, *, channel: str | None = None, reason: str = "Operation cancelled."
    ) -> bool:
        snapshot = self.get_active_run(channel=channel)
        return bool(snapshot and self.cancel_run(snapshot.run_id, reason))

    def get_active_run(self, *, channel: str | None = None) -> RunSnapshot | None:
        with self._lock:
            for active in self._active.values():
                snapshot = active.snapshot
                if channel is None or snapshot.channel == channel:
                    return snapshot
        return None

    def get_run(self, run_id: str) -> RunSnapshot | None:
        with self._lock:
            active = self._active.get(run_id)
            if active is not None:
                return active.snapshot
        with open_state(self.config) as db:
            row = db.execute(
                """
                SELECT *
                FROM agent_runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is not None:
                return _snapshot_from_run(row)
            task = get_task(db, run_id)
            if task is not None:
                return _snapshot_from_task(task)
            events = list_trace_events(db, run_id=run_id, limit=500)
        if not events:
            return None
        first = events[0]
        last = events[-1]
        return RunSnapshot(
            run_id=run_id,
            status=_trace_status(last.get("status")),
            channel=str((first.get("details") or {}).get("channel") or "unknown"),
            current_step=str(last.get("event_name") or ""),
            message=str(last.get("error") or ""),
            started_at=str(first.get("started_at") or ""),
            updated_at=str(last.get("started_at") or ""),
            finished_at=last.get("finished_at"),
            cancel_requested=_trace_status(last.get("status")) == "cancelled",
        )

    def list_events(self, run_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        with open_state(self.config) as db:
            return list_trace_events(db, run_id=run_id, limit=limit)

    def _execute_run(
        self,
        message: str,
        *,
        channel: str,
        session_id: str | None = None,
        task_id: str | None = None,
        cancellation_token: CancellationToken | None = None,
        persist_task: bool = False,
    ) -> RunResult:
        run_id = task_id or uuid.uuid4().hex
        token = cancellation_token or CancellationToken()
        if persist_task and task_id is None:
            task_id, session_id = self._create_running_task(
                message, channel, session_id
            )
            run_id = task_id
        elif persist_task and task_id is not None:
            self._mark_task_running(task_id, message)
        snapshot = RunSnapshot(
            run_id=run_id,
            task_id=task_id,
            session_id=session_id,
            channel=channel,
            status="running",
            current_step="starting",
            message=message[:220],
            started_at=_now(),
            updated_at=_now(),
        )
        self._upsert_run(snapshot)
        router = self.router_factory()
        with self._lock:
            self._active[run_id] = _ActiveRun(snapshot, token, router)
        self._emit(
            RunEvent(
                run_id=run_id,
                task_id=task_id,
                session_id=session_id,
                channel=channel,
                event_name="run.started",
                status="running",
                message=message[:220],
                details={"channel": channel},
            )
        )
        started_at = time.monotonic()
        try:
            self._set_active_status(run_id, "planning", "planning")
            result = router.handle_text(
                message, cancellation_token=token, run_id=run_id
            )
            status = (
                "cancelled"
                if token.cancelled
                else "done"
                if result.ok
                else _task_status_for_message(result.message)
            )
            final = RunResult(
                result.ok,
                result.message,
                result.payload,
                run_id=run_id,
                task_id=task_id,
                session_id=session_id,
                status=status,
            )
            if persist_task and task_id is not None:
                self._finish_task(task_id, final)
            self._upsert_run(
                _replace_snapshot(
                    snapshot,
                    status=status,
                    current_step="run.finished",
                    message=result.message[:220],
                    finished_at=_now(),
                    cancel_requested=token.cancelled,
                ),
                result=final,
            )
            self._emit(
                RunEvent(
                    run_id=run_id,
                    task_id=task_id,
                    session_id=session_id,
                    channel=channel,
                    event_name="run.finished",
                    status=status,
                    message=result.message[:220],
                    details={"ok": result.ok, "duration_ms": _elapsed_ms(started_at)},
                )
            )
            return final
        except Exception as exc:
            message_text = str(exc)
            final = RunResult(
                False,
                message_text,
                run_id=run_id,
                task_id=task_id,
                session_id=session_id,
                status="failed",
            )
            if persist_task and task_id is not None:
                with open_state(self.config) as db:
                    finish_task(db, task_id, status="failed", error=message_text)
            self._upsert_run(
                _replace_snapshot(
                    snapshot,
                    status="failed",
                    current_step="run.failed",
                    message=message_text,
                    finished_at=_now(),
                ),
                result=final,
            )
            self._emit(
                RunEvent(
                    run_id=run_id,
                    task_id=task_id,
                    session_id=session_id,
                    channel=channel,
                    event_name="run.failed",
                    status="failed",
                    message=message_text,
                    details={"duration_ms": _elapsed_ms(started_at)},
                )
            )
            raise
        finally:
            with self._lock:
                active = self._active.get(run_id)
                if active is not None:
                    active.snapshot = _replace_snapshot(
                        active.snapshot,
                        status="cancelled"
                        if token.cancelled
                        else "done"
                        if "final" in locals() and final.ok
                        else "failed"
                        if "final" in locals()
                        else active.snapshot.status,
                        finished_at=_now(),
                    )
                    self._active.pop(run_id, None)

    def _create_running_task(
        self, message: str, channel: str, session_id: str | None
    ) -> tuple[str, str]:
        with open_state(self.config) as db:
            session_id = ensure_session(
                db,
                session_id=session_id,
                channel=channel,
                title=_title_from_message(message),
            )
            add_message(
                db,
                session_id=session_id,
                role="user",
                content=message,
                metadata={"channel": channel},
            )
            task_id = create_task(
                db,
                session_id=session_id,
                kind="message",
                title=_title_from_message(message),
                input_value={"message": message, "channel": channel},
                status="running",
            )
            heartbeat_task(db, task_id)
        return task_id, session_id

    def _mark_task_running(self, task_id: str, message: str) -> None:
        with open_state(self.config) as db:
            start_task(db, task_id)
            heartbeat_task(db, task_id)
            add_task_step(
                db,
                task_id,
                kind="start",
                status="running",
                message=f"Running task goal: {message[:220]}",
                payload={"message": message},
            )

    def _finish_task(self, task_id: str, result: RunResult) -> None:
        with open_state(self.config) as db:
            if result.session_id:
                add_message(
                    db,
                    session_id=result.session_id,
                    role="assistant",
                    content=result.message,
                    metadata={"task_id": task_id, "ok": result.ok},
                )
            add_task_step(
                db,
                task_id,
                kind="finish",
                status=result.status,
                message=result.message,
                payload={"ok": result.ok, "payload": result.payload},
            )
            finish_task(
                db,
                task_id,
                status=result.status,
                result_value={
                    "message": result.message,
                    "payload": result.payload,
                    "ok": result.ok,
                    "run_id": result.run_id,
                },
                error=None if result.ok else result.message,
            )

    def _set_active_status(
        self, run_id: str, status: RunStatus, current_step: str, active_tool: str = ""
    ) -> None:
        with self._lock:
            active = self._active.get(run_id)
            if active is None:
                return
            active.snapshot = _replace_snapshot(
                active.snapshot,
                status=status,
                current_step=current_step,
                active_tool=active_tool,
            )
            self._upsert_run(active.snapshot)

    def _emit(self, event: RunEvent) -> None:
        with open_state(self.config) as db:
            record_trace_event(
                db,
                run_id=event.run_id,
                event_name=event.event_name,
                component=event.component,
                status=event.status,
                details={
                    **(event.details or {}),
                    "task_id": event.task_id,
                    "session_id": event.session_id,
                    "message": event.message,
                },
            )
        with self._lock:
            subscribers = list(self._subscribers)
            active = self._active.get(event.run_id)
            if active is not None:
                active.snapshot = _replace_snapshot(
                    active.snapshot,
                    status=event.status,
                    current_step=event.event_name,
                    message=event.message or active.snapshot.message,
                    updated_at=_now(),
                    cancel_requested=event.status in {"cancelling", "cancelled"},
                )
                self._upsert_run(active.snapshot)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                continue

    def _upsert_run(
        self, snapshot: RunSnapshot, *, result: RunResult | None = None
    ) -> None:
        now = _now()
        created_at = snapshot.started_at or now
        with open_state(self.config) as db:
            db.execute(
                """
                INSERT INTO agent_runs (
                  run_id, task_id, session_id, channel, status, current_step,
                  active_tool, message, cancel_requested, approval_id, created_at,
                  started_at, updated_at, finished_at, result_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')
                ON CONFLICT(run_id) DO UPDATE SET
                  task_id = excluded.task_id,
                  session_id = excluded.session_id,
                  channel = excluded.channel,
                  status = excluded.status,
                  current_step = excluded.current_step,
                  active_tool = excluded.active_tool,
                  message = excluded.message,
                  cancel_requested = excluded.cancel_requested,
                  approval_id = excluded.approval_id,
                  started_at = COALESCE(agent_runs.started_at, excluded.started_at),
                  updated_at = excluded.updated_at,
                  finished_at = excluded.finished_at,
                  result_json = CASE
                    WHEN excluded.result_json = '{}' THEN agent_runs.result_json
                    ELSE excluded.result_json
                  END
                """,
                (
                    snapshot.run_id,
                    snapshot.task_id,
                    snapshot.session_id,
                    snapshot.channel,
                    snapshot.status,
                    snapshot.current_step,
                    snapshot.active_tool,
                    snapshot.message,
                    1 if snapshot.cancel_requested else 0,
                    snapshot.approval_id,
                    created_at,
                    snapshot.started_at or None,
                    snapshot.updated_at or now,
                    snapshot.finished_at,
                    json.dumps(
                        result.to_dict() if result else {},
                        sort_keys=True,
                        default=str,
                    ),
                ),
            )
            db.commit()


def _snapshot_from_task(task: dict[str, Any]) -> RunSnapshot:
    return RunSnapshot(
        run_id=str(task["task_id"]),
        task_id=str(task["task_id"]),
        session_id=task.get("session_id"),
        channel=str(task.get("input", {}).get("channel") or task.get("kind") or "task"),
        status=_task_status(task.get("status")),
        current_step=_latest_task_step(task),
        message=str(task.get("error") or ""),
        started_at=str(task.get("started_at") or task.get("created_at") or ""),
        updated_at=str(task.get("updated_at") or ""),
        finished_at=task.get("finished_at"),
        cancel_requested=bool(task.get("cancel_requested")),
    )


def _snapshot_from_run(row: sqlite3.Row) -> RunSnapshot:
    return RunSnapshot(
        run_id=str(row["run_id"]),
        task_id=row["task_id"],
        session_id=row["session_id"],
        channel=str(row["channel"]),
        status=_run_status(row["status"]),
        current_step=str(row["current_step"] or ""),
        active_tool=str(row["active_tool"] or ""),
        message=str(row["message"] or ""),
        started_at=str(row["started_at"] or row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
        finished_at=row["finished_at"],
        cancel_requested=bool(row["cancel_requested"]),
        approval_id=row["approval_id"],
    )


def _replace_snapshot(snapshot: RunSnapshot, **changes: Any) -> RunSnapshot:
    data = snapshot.to_dict()
    data.update(changes)
    data["updated_at"] = _now()
    return RunSnapshot(**data)


def _task_status(value: Any) -> RunStatus:
    text = str(value or "failed")
    if text in {
        "queued",
        "running",
        "waiting_approval",
        "blocked",
        "done",
        "failed",
        "cancelled",
    }:
        return text  # type: ignore[return-value]
    return "failed"


def _run_status(value: Any) -> RunStatus:
    text = str(value or "failed")
    if text in ACTIVE_STATUSES or text in TERMINAL_STATUSES:
        return text  # type: ignore[return-value]
    return "failed"


def _trace_status(value: Any) -> RunStatus:
    text = str(value or "")
    if text in {"cancelled", "failed", "done", "running", "waiting_approval"}:
        return text  # type: ignore[return-value]
    if text == "error":
        return "failed"
    if text == "ok":
        return "done"
    return "running"


def _latest_task_step(task: dict[str, Any]) -> str:
    steps = task.get("steps")
    if isinstance(steps, list) and steps:
        return str(steps[-1].get("kind") or "")
    return str(task.get("status") or "")


def _task_message(task: dict[str, Any]) -> str:
    input_value = task.get("input") if isinstance(task.get("input"), dict) else {}
    return str(input_value.get("message") or input_value.get("goal") or "").strip()


def _task_status_for_message(message: str) -> RunStatus:
    lowered = message.lower()
    if "needs approval" in lowered or "approval" in lowered:
        return "waiting_approval"
    if "blocked" in lowered:
        return "blocked"
    if "cancel" in lowered:
        return "cancelled"
    return "failed"


def _title_from_message(message: str) -> str:
    return message.strip().splitlines()[0][:80] or "Iris request"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started_at: float) -> float:
    return round((time.monotonic() - started_at) * 1000.0, 2)
