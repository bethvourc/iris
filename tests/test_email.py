from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import iris.email_sender as email_sender
from iris.config import IrisConfig
from iris.email_sender import EmailService


def test_unconfigured_email_is_unavailable() -> None:
    service = EmailService(_config())
    assert service.available is False
    result = service.send(subject="hi", body="there")
    assert result.ok is False
    assert "not configured" in result.detail.lower()


def test_send_posts_to_resend(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Resp:
        status = 200

    @contextmanager
    def _fake_urlopen(request, timeout=20.0):  # noqa: ANN001
        captured["url"] = request.full_url
        captured["headers"] = request.headers
        captured["data"] = request.data
        yield _Resp()

    monkeypatch.setattr(email_sender, "urlopen", _fake_urlopen)
    service = EmailService(
        _config(
            resend_api_key="re_test", email_from="iris@me.dev", email_to="me@me.dev"
        )
    )
    result = service.send(subject="Recap", body="Notes")
    assert result.ok is True
    assert captured["url"] == "https://api.resend.com/emails"
    assert b"Recap" in captured["data"]
    # Authorization header set (urllib capitalizes header keys)
    assert any("Authorization" == k for k in captured["headers"])


def _config(**overrides) -> IrisConfig:
    base = dict(
        project_root=Path("/tmp"),
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
        state_db_path=Path("/tmp/iris.sqlite3"),
        autonomy_level="L1",
        meeting_consent_required=True,
        meeting_retention_days=30,
        listen_seconds=2.0,
        wake_poll_seconds=1.2,
        wake_words=("iris", "hey iris"),
        speak_responses=False,
        max_response_chars=220,
    )
    base.update(overrides)
    return IrisConfig(**base)
