from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import uuid
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(
    db: sqlite3.Connection,
    *,
    channel: str = "terminal",
    title: str = "Iris session",
    metadata: dict[str, Any] | None = None,
) -> str:
    session_id = uuid.uuid4().hex
    now = _now()
    db.execute(
        """
        INSERT INTO agent_sessions (
          session_id, channel, title, status, created_at, updated_at, metadata_json
        ) VALUES (?, ?, ?, 'active', ?, ?, ?)
        """,
        (
            session_id,
            channel,
            title,
            now,
            now,
            json.dumps(metadata or {}, sort_keys=True, default=str),
        ),
    )
    db.commit()
    return session_id


def ensure_session(
    db: sqlite3.Connection,
    *,
    session_id: str | None = None,
    channel: str = "terminal",
    title: str = "Iris session",
) -> str:
    if session_id:
        row = db.execute(
            "SELECT session_id FROM agent_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row:
            touch_session(db, session_id)
            return session_id
    return create_session(db, channel=channel, title=title)


def touch_session(db: sqlite3.Connection, session_id: str) -> None:
    db.execute(
        "UPDATE agent_sessions SET updated_at = ? WHERE session_id = ?",
        (_now(), session_id),
    )
    db.commit()


def add_message(
    db: sqlite3.Connection,
    *,
    session_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    message_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO agent_messages (
          message_id, session_id, role, content, created_at, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            session_id,
            role,
            content,
            _now(),
            json.dumps(metadata or {}, sort_keys=True, default=str),
        ),
    )
    touch_session(db, session_id)
    return message_id


def list_sessions(db: sqlite3.Connection, *, limit: int = 25) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT session_id, channel, title, status, created_at, updated_at, metadata_json
        FROM agent_sessions
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_decode(row) for row in rows]


def get_session(db: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT session_id, channel, title, status, created_at, updated_at, metadata_json
        FROM agent_sessions
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    session = _decode(row)
    messages = db.execute(
        """
        SELECT message_id, role, content, created_at, metadata_json
        FROM agent_messages
        WHERE session_id = ?
        ORDER BY created_at ASC
        """,
        (session_id,),
    ).fetchall()
    session["messages"] = [_decode(message) for message in messages]
    return session


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("metadata_json",):
        if key in data:
            try:
                data[key.removesuffix("_json")] = json.loads(data.pop(key) or "{}")
            except json.JSONDecodeError:
                data[key.removesuffix("_json")] = {}
    return data
