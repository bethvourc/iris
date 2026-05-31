from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import uuid
from typing import Any

from iris.actions import RiskLevel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_approval(
    db: sqlite3.Connection,
    *,
    action_name: str,
    preview: str,
    risk: RiskLevel = RiskLevel.SENSITIVE,
    run_id: str | None = None,
    ttl_seconds: int = 3600,
    details: dict[str, Any] | None = None,
) -> str:
    approval_id = uuid.uuid4().hex
    created_at = _now()
    db.execute(
        """
        INSERT INTO approvals (
          approval_id, run_id, action_name, risk, status, preview,
          created_at, expires_at, details_json
        ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (
            approval_id,
            run_id,
            action_name,
            str(risk),
            preview,
            created_at.isoformat(),
            (created_at + timedelta(seconds=ttl_seconds)).isoformat(),
            json.dumps(details or {}, sort_keys=True, default=str),
        ),
    )
    db.commit()
    return approval_id


def list_approvals(db: sqlite3.Connection, status: str = "pending") -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT approval_id, run_id, action_name, risk, status, preview, created_at, expires_at, decided_at, details_json
        FROM approvals
        WHERE status = ?
        ORDER BY created_at DESC
        """,
        (status,),
    ).fetchall()
    return [dict(row) for row in rows]


def decide_approval(db: sqlite3.Connection, approval_id: str, status: str) -> bool:
    if status not in {"approved", "denied"}:
        raise ValueError("approval status must be approved or denied")
    result = db.execute(
        """
        UPDATE approvals
        SET status = ?, decided_at = ?
        WHERE approval_id = ? AND status = 'pending'
        """,
        (status, _now().isoformat(), approval_id),
    )
    db.commit()
    return result.rowcount > 0
