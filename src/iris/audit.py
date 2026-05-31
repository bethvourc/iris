from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import uuid
from typing import Any

from iris.actions import RiskLevel


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_hash(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_audit(
    db: sqlite3.Connection,
    *,
    actor: str,
    tool: str,
    risk: RiskLevel | str,
    result: str,
    run_id: str | None = None,
    approval_id: str | None = None,
    input_value: Any | None = None,
    output_value: Any | None = None,
    error: str | None = None,
    details: dict[str, Any] | None = None,
) -> str:
    event_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO audit_events (
          event_id, run_id, actor, tool, input_hash, output_hash, risk,
          approval_id, timestamp, result, error, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            run_id,
            actor,
            tool,
            stable_hash(input_value) if input_value is not None else None,
            stable_hash(output_value) if output_value is not None else None,
            str(risk),
            approval_id,
            now_iso(),
            result,
            error,
            json.dumps(details or {}, sort_keys=True, default=str),
        ),
    )
    db.commit()
    return event_id


def list_audit(db: sqlite3.Connection, limit: int = 25) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT event_id, run_id, actor, tool, risk, approval_id, timestamp, result, error, details_json
        FROM audit_events
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]
