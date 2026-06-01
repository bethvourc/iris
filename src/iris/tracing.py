from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import uuid
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_trace_event(
    db: sqlite3.Connection,
    *,
    run_id: str,
    event_name: str,
    component: str,
    status: str = "ok",
    started_at: str | None = None,
    finished_at: str | None = None,
    duration_ms: float | None = None,
    error: str | None = None,
    details: dict[str, Any] | None = None,
) -> str:
    trace_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO run_trace_events (
          trace_id, run_id, event_name, component, status, started_at,
          finished_at, duration_ms, error, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trace_id,
            run_id,
            event_name,
            component,
            status,
            started_at or now_iso(),
            finished_at,
            duration_ms,
            error,
            json.dumps(details or {}, sort_keys=True, default=str),
        ),
    )
    db.commit()
    return trace_id


def list_trace_events(
    db: sqlite3.Connection,
    *,
    run_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if run_id:
        rows = db.execute(
            """
            SELECT trace_id, run_id, event_name, component, status, started_at,
                   finished_at, duration_ms, error, details_json
            FROM run_trace_events
            WHERE run_id = ?
            ORDER BY started_at ASC
            LIMIT ?
            """,
            (run_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT trace_id, run_id, event_name, component, status, started_at,
                   finished_at, duration_ms, error, details_json
            FROM run_trace_events
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_decode_details(dict(row)) for row in rows]


def latest_run_id(db: sqlite3.Connection) -> str | None:
    row = db.execute(
        """
        SELECT run_id
        FROM run_trace_events
        ORDER BY started_at DESC
        LIMIT 1
        """
    ).fetchone()
    return str(row["run_id"]) if row else None


def summarize_run_trace(db: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    events = list_trace_events(db, run_id=run_id, limit=500)
    durations = [
        float(event["duration_ms"])
        for event in events
        if event.get("duration_ms") is not None
    ]
    slowest = sorted(
        (event for event in events if event.get("duration_ms") is not None),
        key=lambda event: float(event["duration_ms"]),
        reverse=True,
    )[:5]
    cancelled = any(
        event["status"] == "cancelled" or event["event_name"].startswith("cancel")
        for event in events
    )
    failed = [event for event in events if event["status"] == "error"]
    return {
        "run_id": run_id,
        "event_count": len(events),
        "total_recorded_duration_ms": round(sum(durations), 2),
        "slowest_events": slowest,
        "cancelled": cancelled,
        "errors": failed,
        "events": events,
    }


def _decode_details(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("details_json") or "{}"
    try:
        row["details"] = json.loads(str(raw))
    except json.JSONDecodeError:
        row["details"] = {}
    row.pop("details_json", None)
    return row
