from __future__ import annotations

from pathlib import Path

from iris.config import IrisConfig
from iris.meetings import (
    _parse_summary,
    complete_meeting,
    deliver_meeting_recap,
    start_silent_meeting,
)
from iris.state import connect, migrate, open_state


def test_parse_summary_handles_variants() -> None:
    assert _parse_summary('{"summary": "ok", "decisions": ["d"]}')["summary"] == "ok"
    fenced = '```json\n{"summary": "x", "action_items": [{"task": "t"}]}\n```'
    parsed = _parse_summary(fenced)
    assert parsed["action_items"] == [{"task": "t"}]
    empty = _parse_summary("garbage")
    assert empty["summary"] == "" and empty["action_items"] == []


def test_complete_meeting_persists_and_marks_done(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with connect(config.state_db_path) as db:
        migrate(db)
    with open_state(config) as db:
        meeting_id = start_silent_meeting(db, config, title="Sync")
        result = complete_meeting(
            db,
            config,
            meeting_id=meeting_id,
            transcript="we agreed to ship friday",
            openai_client=None,
        )
        row = db.execute(
            "SELECT status FROM meetings WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
    assert row["status"] == "completed"
    assert Path(str(result["transcript_path"])).read_text().strip() == (
        "we agreed to ship friday"
    )


def test_deliver_recap_drafts_email_and_reminders() -> None:
    emails: list[tuple[str, str]] = []
    reminders: list[str] = []
    result = {
        "title": "Planning",
        "summary": {
            "follow_up_email": "Here is the recap.",
            "action_items": [{"task": "Ship the build"}, {"task": ""}, "Email Beth"],
        },
    }
    delivered = deliver_meeting_recap(
        _config(Path("/tmp")),
        result,
        draft_email=lambda subject, body: emails.append((subject, body)),
        create_reminder=lambda task: reminders.append(task),
    )
    assert delivered["email"] is True
    assert delivered["reminders"] == 2
    assert emails == [("Recap: Planning", "Here is the recap.")]
    assert reminders == ["Ship the build", "Email Beth"]


def _config(tmp_path: Path) -> IrisConfig:
    return IrisConfig(
        project_root=tmp_path,
        openai_api_key=None,
        google_application_credentials=None,
        agent_name="Iris",
        voice="marin",
        toggle_hotkey="<ctrl>+<space>",
        kill_hotkey="<ctrl>+<alt>+k",
        realtime_model="gpt-realtime-2",
        chat_model="gpt-5.5",
        vision_model="gpt-5.5",
        computer_use_model="computer-use-preview",
        computer_use_environment="browser",
        groq_api_key=None,
        fast_intent_model="compound-mini",
        stt_model="whisper-large-v3-turbo",
        realtime_transcription_model="gpt-4o-mini-transcribe",
        screenshot_interval_seconds=0.5,
        gateway_token=None,
        notify_provider="pushover",
        ntfy_server="https://ntfy.sh",
        ntfy_topic=None,
        ntfy_token=None,
        pushover_token=None,
        pushover_user=None,
        notify_timeout_seconds=30.0,
        email_provider="resend",
        resend_api_key=None,
        email_from=None,
        email_to=None,
        state_db_path=tmp_path / "iris.sqlite3",
        autonomy_level="L1",
        meeting_consent_required=True,
        meeting_retention_days=30,
        listen_seconds=2.0,
        wake_poll_seconds=1.2,
        wake_words=("iris", "hey iris"),
        speak_responses=False,
        max_response_chars=220,
    )
