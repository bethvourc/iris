from __future__ import annotations

from pathlib import Path

from iris.actions import LocalAction, RiskLevel
from iris.config import IrisConfig
from iris.safety import classify_action
from iris.state import open_state
from iris.tools import ToolContext, _recall, _remember


def test_readonly_args_do_not_trip_approval() -> None:
    decision = classify_action(
        LocalAction("find_file", {"query": "Spotify Installer", "max_results": 20})
    )
    assert decision.risk == RiskLevel.LOW_RISK

    for query in ("draft a message", "open my login page", "send folder"):
        assert (
            classify_action(LocalAction("file_find", {"query": query})).risk
            == RiskLevel.LOW_RISK
        )


def test_sensitive_and_blocked_actions_still_gated() -> None:
    assert classify_action(LocalAction("send_message", {})).risk == RiskLevel.SENSITIVE
    assert classify_action(LocalAction("delete_file", {})).risk == RiskLevel.BLOCKED
    assert (
        classify_action(LocalAction("run_shell", {"cmd": "rm -rf /"})).risk
        == RiskLevel.BLOCKED
    )
    assert (
        classify_action(LocalAction("run_shell", {"cmd": "ls"})).risk
        == RiskLevel.SENSITIVE
    )


def test_explicit_low_risk_overrides_name() -> None:
    decision = classify_action(
        LocalAction("anything", {"text": "install"}, risk=RiskLevel.LOW_RISK)
    )
    assert decision.risk == RiskLevel.LOW_RISK


def test_remember_and_recall_round_trip(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with open_state(config) as db:
        from iris.state import migrate

        migrate(db)
    context = _memory_context(config)

    saved = _remember({"content": "Use the Spotify app, not the browser"}, context)
    assert saved.ok
    assert saved.payload["category"] == "preferences"

    again = _remember(
        {"content": "use the spotify app, not the browser", "category": "preferences"},
        context,
    )
    assert again.payload.get("duplicate") is True

    recalled = _recall({}, context)
    assert recalled.ok
    contents = [item["content"] for item in recalled.payload["memories"]]
    assert "Use the Spotify app, not the browser" in contents


def _memory_context(config: IrisConfig) -> ToolContext:
    return ToolContext(
        controller=object(),  # type: ignore[arg-type]
        perception=object(),  # type: ignore[arg-type]
        safety_gate=object(),  # type: ignore[arg-type]
        openai_client=object(),  # type: ignore[arg-type]
        google_vision=object(),  # type: ignore[arg-type]
        session_state={},
        config=config,
    )


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
