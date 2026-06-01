from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import uuid
from typing import Any, Literal


TaskStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "blocked",
    "done",
    "failed",
    "cancelled",
]

TERMINAL_STATUSES = {"blocked", "done", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_task(
    db: sqlite3.Connection,
    *,
    kind: str,
    title: str,
    session_id: str | None = None,
    input_value: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    status: TaskStatus = "queued",
) -> str:
    task_id = uuid.uuid4().hex
    now = _now()
    started_at = now if status == "running" else None
    db.execute(
        """
        INSERT INTO agent_tasks (
          task_id, session_id, kind, status, title, input_json, result_json,
          created_at, updated_at, started_at, heartbeat_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            session_id,
            kind,
            status,
            title,
            json.dumps(input_value or {}, sort_keys=True, default=str),
            now,
            now,
            started_at,
            now if status == "running" else None,
            json.dumps(metadata or {}, sort_keys=True, default=str),
        ),
    )
    db.commit()
    return task_id


def start_task(db: sqlite3.Connection, task_id: str) -> bool:
    result = db.execute(
        """
        UPDATE agent_tasks
        SET status = 'running', started_at = COALESCE(started_at, ?),
            heartbeat_at = ?, updated_at = ?
        WHERE task_id = ? AND status IN ('queued', 'waiting_approval')
        """,
        (_now(), _now(), _now(), task_id),
    )
    db.commit()
    return result.rowcount > 0


def claim_next_task(
    db: sqlite3.Connection,
    *,
    kinds: tuple[str, ...] = ("message", "agent_background", "workflow"),
) -> dict[str, Any] | None:
    placeholders = ", ".join("?" for _ in kinds)
    row = db.execute(
        f"""
        SELECT * FROM agent_tasks
        WHERE status = 'queued' AND kind IN ({placeholders})
        ORDER BY created_at ASC
        LIMIT 1
        """,
        kinds,
    ).fetchone()
    if row is None:
        return None
    task_id = str(row["task_id"])
    now = _now()
    result = db.execute(
        """
        UPDATE agent_tasks
        SET status = 'running',
            started_at = COALESCE(started_at, ?),
            heartbeat_at = ?,
            updated_at = ?
        WHERE task_id = ? AND status = 'queued'
        """,
        (now, now, now, task_id),
    )
    db.commit()
    if result.rowcount <= 0:
        return None
    claimed = db.execute(
        "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    return _decode(claimed) if claimed else None


def heartbeat_task(db: sqlite3.Connection, task_id: str) -> bool:
    now = _now()
    result = db.execute(
        """
        UPDATE agent_tasks
        SET heartbeat_at = ?, updated_at = ?
        WHERE task_id = ? AND status = 'running'
        """,
        (now, now, task_id),
    )
    db.commit()
    return result.rowcount > 0


def add_task_step(
    db: sqlite3.Connection,
    task_id: str,
    *,
    kind: str,
    status: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> str:
    row = db.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM agent_task_steps WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    sequence = int(row["next_sequence"] if row else 1)
    step_id = uuid.uuid4().hex
    now = _now()
    db.execute(
        """
        INSERT INTO agent_task_steps (
          step_id, task_id, sequence, kind, status, message,
          created_at, updated_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            step_id,
            task_id,
            sequence,
            kind,
            status,
            message,
            now,
            now,
            json.dumps(payload or {}, sort_keys=True, default=str),
        ),
    )
    db.commit()
    return step_id


def finish_task(
    db: sqlite3.Connection,
    task_id: str,
    *,
    status: TaskStatus,
    result_value: dict[str, Any] | None = None,
    error: str | None = None,
) -> bool:
    if status not in TERMINAL_STATUSES and status != "waiting_approval":
        raise ValueError("finish_task status must be terminal or waiting_approval")
    now = _now()
    finished_at = now if status in TERMINAL_STATUSES else None
    row = db.execute(
        "SELECT cancel_requested FROM agent_tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    final_status = (
        "cancelled"
        if row and int(row["cancel_requested"]) and status == "done"
        else status
    )
    result = db.execute(
        """
        UPDATE agent_tasks
        SET status = ?, result_json = ?, error = ?, updated_at = ?, finished_at = ?
        WHERE task_id = ?
        """,
        (
            final_status,
            json.dumps(result_value or {}, sort_keys=True, default=str),
            error,
            now,
            finished_at,
            task_id,
        ),
    )
    db.commit()
    return result.rowcount > 0


def request_cancel(db: sqlite3.Connection, task_id: str) -> bool:
    result = db.execute(
        """
        UPDATE agent_tasks
        SET cancel_requested = 1, updated_at = ?
        WHERE task_id = ? AND status NOT IN ('blocked', 'done', 'failed', 'cancelled')
        """,
        (_now(), task_id),
    )
    db.commit()
    return result.rowcount > 0


def cancel_requested(db: sqlite3.Connection, task_id: str) -> bool:
    row = db.execute(
        "SELECT cancel_requested FROM agent_tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    return bool(row and int(row["cancel_requested"]))


def resume_task(db: sqlite3.Connection, task_id: str) -> bool:
    result = db.execute(
        """
        UPDATE agent_tasks
        SET status = 'queued', cancel_requested = 0, updated_at = ?
        WHERE task_id = ? AND status IN ('failed', 'blocked', 'cancelled', 'waiting_approval')
        """,
        (_now(), task_id),
    )
    db.commit()
    return result.rowcount > 0


def get_task(db: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    if row is None:
        return None
    task = _decode(row)
    task["steps"] = list_task_steps(db, task_id)
    return task


def list_tasks(
    db: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    if status:
        rows = db.execute(
            "SELECT * FROM agent_tasks WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM agent_tasks ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_decode(row) for row in rows]


def list_task_steps(db: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT * FROM agent_task_steps WHERE task_id = ? ORDER BY sequence ASC",
        (task_id,),
    ).fetchall()
    steps = []
    for row in rows:
        data = dict(row)
        try:
            data["payload"] = json.loads(data.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            data["payload"] = {}
        steps.append(data)
    return steps


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("input_json", "result_json", "metadata_json"):
        try:
            data[key.removesuffix("_json")] = json.loads(data.pop(key) or "{}")
        except json.JSONDecodeError:
            data[key.removesuffix("_json")] = {}
    data["cancel_requested"] = bool(data.get("cancel_requested"))
    return data
