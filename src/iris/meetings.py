from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from iris.config import IrisConfig


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_silent_meeting(
    db: sqlite3.Connection,
    config: IrisConfig,
    *,
    title: str | None = None,
    disclosure_spoken: bool = False,
) -> str:
    meeting_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO meetings (
          meeting_id, title, mode, status, consent_required, disclosure_spoken,
          retention_days, started_at
        ) VALUES (?, ?, 'silent', 'running', ?, ?, ?, ?)
        """,
        (
            meeting_id,
            title or "Untitled meeting",
            int(config.meeting_consent_required),
            int(disclosure_spoken),
            config.meeting_retention_days,
            _now(),
        ),
    )
    db.commit()
    return meeting_id


def stop_meeting(
    db: sqlite3.Connection, config: IrisConfig, meeting_id: str | None = None
) -> dict[str, object]:
    row = (
        db.execute(
            "SELECT * FROM meetings WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
        if meeting_id
        else db.execute(
            "SELECT * FROM meetings WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    )
    if row is None:
        raise ValueError("No running meeting found.")
    meeting_dir = config.project_root / "build" / "meetings"
    meeting_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = Path(
        row["transcript_path"] or meeting_dir / f"{row['meeting_id']}.txt"
    )
    if not transcript_path.exists():
        transcript_path.write_text(
            "Transcript capture is not connected yet. This record reserves the meeting artifact.\n",
            encoding="utf-8",
        )
    summary = {
        "summary": "Silent meeting stopped. Live transcription/summarization is the next capture milestone.",
        "decisions": [],
        "action_items": [],
        "owners": [],
        "deadlines": [],
        "risks": [],
        "follow_up_email_draft": "",
    }
    db.execute(
        """
        UPDATE meetings
        SET status = 'completed', stopped_at = ?, transcript_path = ?, summary_json = ?
        WHERE meeting_id = ?
        """,
        (_now(), str(transcript_path), json.dumps(summary), row["meeting_id"]),
    )
    db.commit()
    return {
        "meeting_id": row["meeting_id"],
        "transcript_path": str(transcript_path),
        "summary": summary,
    }


def list_meetings(db: sqlite3.Connection) -> list[dict[str, object]]:
    rows = db.execute(
        "SELECT * FROM meetings ORDER BY started_at DESC LIMIT 50"
    ).fetchall()
    return [dict(row) for row in rows]
