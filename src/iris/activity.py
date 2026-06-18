"""Day-grouped activity feed over sessions, runs, and meetings.

Server-side aggregation for the desktop main window (and any other client):
shapes per docs/desktop/api-contract.md §5. Item ids are prefixed
(`ses-`/`run-`/`mtg-`) so the detail endpoint can route to the right table;
clients treat them as opaque.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

MAX_DAYS = 90
MAX_LIMIT = 200
# Per-source row bound: a request can never pull more than 3 * _FETCH_CAP
# rows regardless of history size.
_FETCH_CAP = 500

_CONTRACT_STATUSES = {"done", "failed", "cancelled", "blocked"}


class ActivityRequestError(ValueError):
    """Invalid client input (timezone, cursor); maps to 400 invalid_request."""


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _map_run_status(value: str | None) -> str:
    status = (value or "").strip().lower()
    return status if status in _CONTRACT_STATUSES else "running"


def _parse_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    time_part, _, id_part = cursor.partition("|")
    parsed = _parse_dt(time_part)
    if parsed is None or not id_part:
        raise ActivityRequestError("invalid cursor")
    return parsed, id_part


def _encode_cursor(item: dict[str, Any]) -> str:
    return f"{_iso_z(item['_time'])}|{item['id']}"


def _session_items(db: sqlite3.Connection, since: datetime) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT session_id, channel, title, status, updated_at
        FROM agent_sessions
        WHERE updated_at >= ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (since.isoformat(), _FETCH_CAP),
    ).fetchall()
    items = []
    for row in rows:
        time = _parse_dt(row["updated_at"])
        if time is None:
            continue
        items.append(
            {
                "id": f"ses-{row['session_id']}",
                "kind": "voice_session" if row["channel"] == "voice" else "message",
                "_time": time,
                "title": row["title"] or "Iris session",
                "preview": None,  # filled from the last message after paging
                "status": "done",
                "_session_id": row["session_id"],
            }
        )
    return items


def _run_items(db: sqlite3.Connection, since: datetime) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT run_id, status, message, updated_at
        FROM agent_runs
        WHERE updated_at >= ?
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (since.isoformat(), _FETCH_CAP),
    ).fetchall()
    items = []
    for row in rows:
        time = _parse_dt(row["updated_at"])
        if time is None:
            continue
        message = (row["message"] or "").strip()
        items.append(
            {
                "id": f"run-{row['run_id']}",
                "kind": "run",
                "_time": time,
                "title": message[:80] or "Agent run",
                "preview": message or None,
                "status": _map_run_status(row["status"]),
            }
        )
    return items


def _meeting_items(db: sqlite3.Connection, since: datetime) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT meeting_id, title, status, started_at, stopped_at, summary_json
        FROM meetings
        WHERE COALESCE(stopped_at, started_at) >= ?
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (since.isoformat(), _FETCH_CAP),
    ).fetchall()
    items = []
    for row in rows:
        time = _parse_dt(row["stopped_at"]) or _parse_dt(row["started_at"])
        if time is None:
            continue
        try:
            summary = json.loads(row["summary_json"] or "{}").get("summary")
        except ValueError:
            summary = None
        items.append(
            {
                "id": f"mtg-{row['meeting_id']}",
                "kind": "meeting",
                "_time": time,
                "title": row["title"] or "Meeting",
                "preview": summary,
                "status": "done" if row["stopped_at"] else "running",
            }
        )
    return items


def _fill_session_previews(
    db: sqlite3.Connection, items: list[dict[str, Any]]
) -> None:
    for item in items:
        session_id = item.pop("_session_id", None)
        if session_id is None:
            continue
        row = db.execute(
            """
            SELECT content FROM agent_messages
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row is not None:
            item["preview"] = (row["content"] or "")[:200] or None


def _stats(db: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    week_ago = (now - timedelta(days=7)).isoformat()
    sessions_this_week = db.execute(
        "SELECT COUNT(*) FROM agent_sessions WHERE updated_at >= ?", (week_ago,)
    ).fetchone()[0]
    runs_completed = db.execute(
        "SELECT COUNT(*) FROM agent_runs WHERE status = 'done'"
    ).fetchone()[0]
    runs_failed = db.execute(
        "SELECT COUNT(*) FROM agent_runs WHERE status = 'failed'"
    ).fetchone()[0]
    last_session = db.execute(
        "SELECT MAX(updated_at) FROM agent_sessions"
    ).fetchone()[0]
    last_run = db.execute("SELECT MAX(updated_at) FROM agent_runs").fetchone()[0]
    last_active = max(filter(None, (_parse_dt(last_session), _parse_dt(last_run))), default=None)
    return {
        "sessions_this_week": sessions_this_week,
        "runs_completed": runs_completed,
        "runs_failed": runs_failed,
        "last_active_at": _iso_z(last_active) if last_active else None,
    }


def activity_feed(
    db: sqlite3.Connection,
    *,
    days: int = 7,
    limit: int = 50,
    cursor: str | None = None,
    tz: str = "UTC",
    now: datetime | None = None,
) -> dict[str, Any]:
    days = max(1, min(int(days), MAX_DAYS))
    limit = max(1, min(int(limit), MAX_LIMIT))
    try:
        zone = ZoneInfo(tz)
    except Exception as exc:
        raise ActivityRequestError(f"unknown timezone: {tz!r}") from exc
    cursor_key = _parse_cursor(cursor)
    now_utc = now or datetime.now(timezone.utc)
    since = now_utc - timedelta(days=days)

    items = _session_items(db, since) + _run_items(db, since) + _meeting_items(db, since)
    items.sort(key=lambda item: (item["_time"], item["id"]), reverse=True)
    if cursor_key is not None:
        items = [
            item for item in items if (item["_time"], item["id"]) < cursor_key
        ]
    page = items[:limit]
    next_cursor = _encode_cursor(page[-1]) if len(items) > limit and page else None
    _fill_session_previews(db, page)

    today = now_utc.astimezone(zone).date()
    days_out: list[dict[str, Any]] = []
    for item in page:
        local_date = item["_time"].astimezone(zone).date()
        if not days_out or days_out[-1]["date"] != local_date.isoformat():
            if local_date == today:
                label = "today"
            elif local_date == today - timedelta(days=1):
                label = "yesterday"
            else:
                label = None
            days_out.append(
                {"date": local_date.isoformat(), "label": label, "items": []}
            )
        days_out[-1]["items"].append(
            {
                "id": item["id"],
                "kind": item["kind"],
                "time": _iso_z(item["_time"]),
                "title": item["title"],
                "preview": item["preview"],
                "status": item["status"],
            }
        )

    return {
        "stats": _stats(db, now_utc),
        "days": days_out,
        "next_cursor": next_cursor,
    }


def activity_detail(db: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    if item_id.startswith("ses-"):
        return _session_detail(db, item_id.removeprefix("ses-"))
    if item_id.startswith("run-"):
        return _run_detail(db, item_id.removeprefix("run-"))
    if item_id.startswith("mtg-"):
        return _meeting_detail(db, item_id.removeprefix("mtg-"))
    return None


def _session_detail(db: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT session_id, channel, title, updated_at
        FROM agent_sessions WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    messages = db.execute(
        """
        SELECT role, content, created_at FROM agent_messages
        WHERE session_id = ? ORDER BY created_at ASC
        """,
        (session_id,),
    ).fetchall()
    time = _parse_dt(row["updated_at"])
    return {
        "id": f"ses-{session_id}",
        "kind": "voice_session" if row["channel"] == "voice" else "message",
        "time": _iso_z(time) if time else None,
        "status": "done",
        "title": row["title"] or "Iris session",
        "transcript": [
            {
                "role": message["role"],
                "text": message["content"],
                "time": _iso_z(parsed) if (parsed := _parse_dt(message["created_at"])) else None,
            }
            for message in messages
        ],
    }


def _run_detail(db: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT run_id, status, message, updated_at
        FROM agent_runs WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    time = _parse_dt(row["updated_at"])
    message = (row["message"] or "").strip()
    return {
        "id": f"run-{run_id}",
        "kind": "run",
        "time": _iso_z(time) if time else None,
        "status": _map_run_status(row["status"]),
        "title": message[:80] or "Agent run",
        "run": {"run_id": run_id, "events_url": f"/runs/{run_id}/events"},
    }


def _meeting_detail(db: sqlite3.Connection, meeting_id: str) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT meeting_id, title, status, started_at, stopped_at, summary_json
        FROM meetings WHERE meeting_id = ?
        """,
        (meeting_id,),
    ).fetchone()
    if row is None:
        return None
    time = _parse_dt(row["stopped_at"]) or _parse_dt(row["started_at"])
    try:
        summary = json.loads(row["summary_json"] or "{}").get("summary")
    except ValueError:
        summary = None
    return {
        "id": f"mtg-{meeting_id}",
        "kind": "meeting",
        "time": _iso_z(time) if time else None,
        "status": "done" if row["stopped_at"] else "running",
        "title": row["title"] or "Meeting",
        "summary": summary,
    }
