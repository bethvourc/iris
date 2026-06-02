from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid
import wave

from iris.config import IrisConfig

_SUMMARY_INSTRUCTIONS = (
    "You are summarizing a meeting transcript. Return ONLY a JSON object with keys: "
    "summary (2-4 sentence plain-English recap), decisions (array of strings), "
    "action_items (array of {task, owner, due} objects; owner/due may be empty), "
    "risks (array of strings), follow_up_email (a short, ready-to-send recap email "
    "body addressed to the user). Be faithful to the transcript; do not invent."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_summary() -> dict[str, object]:
    return {
        "summary": "",
        "decisions": [],
        "action_items": [],
        "risks": [],
        "follow_up_email": "",
    }


def _parse_summary(raw: str) -> dict[str, object]:
    text = (raw or "").strip()
    if not text:
        return _empty_summary()
    try:
        parsed = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return _empty_summary()
        try:
            parsed = json.loads(text[start : end + 1])
        except Exception:
            return _empty_summary()
    if not isinstance(parsed, dict):
        return _empty_summary()
    summary = _empty_summary()
    summary.update({k: parsed[k] for k in summary if k in parsed})
    return summary


def transcribe_audio(config: IrisConfig, audio_path: Path) -> str:
    """Transcribe a recorded meeting to text. Best-effort across providers."""
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        return ""
    if config.groq_api_key:
        try:
            from groq import Groq  # type: ignore

            client = Groq(api_key=config.groq_api_key)
            with audio_path.open("rb") as handle:
                result = client.audio.transcriptions.create(
                    file=(audio_path.name, handle.read()),
                    model=config.stt_model,
                )
            return str(getattr(result, "text", "") or "").strip()
        except Exception:
            pass
    if config.openai_api_key:
        try:
            from openai import OpenAI  # type: ignore

            client = OpenAI(api_key=config.openai_api_key)
            with audio_path.open("rb") as handle:
                result = client.audio.transcriptions.create(
                    file=handle, model="whisper-1"
                )
            return str(getattr(result, "text", "") or "").strip()
        except Exception:
            pass
    return ""


def summarize_meeting(
    config: IrisConfig, openai_client, transcript: str
) -> dict[str, object]:
    """Summarize a transcript into decisions/action items/recap email via the model."""
    if not transcript.strip() or openai_client is None:
        return _empty_summary()
    if not getattr(openai_client, "available", False):
        return _empty_summary()
    try:
        client = openai_client._get_client()
        response = client.responses.create(
            model=config.chat_model,
            instructions=_SUMMARY_INSTRUCTIONS,
            input=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": transcript[:24000]}],
                }
            ],
            max_output_tokens=900,
        )
        return _parse_summary(openai_client.output_text(response))
    except Exception:
        return _empty_summary()


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


def complete_meeting(
    db: sqlite3.Connection,
    config: IrisConfig,
    *,
    meeting_id: str,
    transcript: str,
    openai_client=None,
) -> dict[str, object]:
    """Persist transcript, summarize it, and mark the meeting completed."""
    row = db.execute(
        "SELECT * FROM meetings WHERE meeting_id = ?", (meeting_id,)
    ).fetchone()
    if row is None:
        raise ValueError("No meeting found.")
    meeting_dir = config.project_root / "build" / "meetings"
    meeting_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = Path(row["transcript_path"] or meeting_dir / f"{meeting_id}.txt")
    transcript_path.write_text(transcript or "(no speech captured)", encoding="utf-8")
    summary = summarize_meeting(config, openai_client, transcript)
    db.execute(
        """
        UPDATE meetings
        SET status = 'completed', stopped_at = ?, transcript_path = ?, summary_json = ?
        WHERE meeting_id = ?
        """,
        (_now(), str(transcript_path), json.dumps(summary), meeting_id),
    )
    db.commit()
    return {
        "meeting_id": meeting_id,
        "title": row["title"],
        "transcript_path": str(transcript_path),
        "summary": summary,
    }


def deliver_meeting_recap(
    config: IrisConfig,
    result: dict[str, object],
    *,
    draft_email: Callable[[str, str], object] | None = None,
    create_reminder: Callable[[str], object] | None = None,
) -> dict[str, object]:
    """Turn a completed meeting into outward artifacts: emailed recap + reminders.

    Side effects are injected so this stays testable and decoupled from tools.
    """
    summary = result.get("summary") if isinstance(result, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    title = str(result.get("title") or "Meeting")
    delivered: dict[str, object] = {"reminders": 0, "email": False}

    email_body = str(summary.get("follow_up_email") or "").strip()
    if email_body and draft_email is not None:
        try:
            draft_email(f"Recap: {title}", email_body)
            delivered["email"] = True
        except Exception:
            delivered["email"] = False

    action_items = summary.get("action_items")
    if isinstance(action_items, list) and create_reminder is not None:
        for item in action_items:
            task = ""
            if isinstance(item, dict):
                task = str(item.get("task") or "").strip()
            elif isinstance(item, str):
                task = item.strip()
            if not task:
                continue
            try:
                create_reminder(task)
                delivered["reminders"] = int(delivered["reminders"]) + 1
            except Exception:
                pass
    return delivered


class MeetingRecorder:
    """Records microphone audio to a WAV file in the background.

    Audio I/O is isolated here so the rest of the pipeline stays testable. Note:
    this captures the default input device; capturing the far side of a video
    call requires a loopback/aggregate device (e.g. BlackHole) set as input.
    """

    def __init__(self, audio_path: Path, *, sample_rate: int = 16000) -> None:
        self.audio_path = audio_path
        self.sample_rate = sample_rate
        self._frames: list[bytes] = []
        self._stream = None
        self._lock = threading.Lock()

    def start(self) -> bool:
        try:
            import sounddevice  # type: ignore
        except Exception:
            return False

        def _callback(indata, _frames, _time, _status) -> None:  # noqa: ANN001
            with self._lock:
                self._frames.append(bytes(indata))

        try:
            self._stream = sounddevice.RawInputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                callback=_callback,
            )
            self._stream.start()
            return True
        except Exception:
            self._stream = None
            return False

    def stop(self) -> Path | None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._lock:
            data = b"".join(self._frames)
            self._frames = []
        if not data:
            return None
        self.audio_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(self.audio_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(self.sample_rate)
            handle.writeframes(data)
        return self.audio_path


def list_meetings(db: sqlite3.Connection) -> list[dict[str, object]]:
    rows = db.execute(
        "SELECT * FROM meetings ORDER BY started_at DESC LIMIT 50"
    ).fetchall()
    return [dict(row) for row in rows]
