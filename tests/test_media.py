from __future__ import annotations

from pathlib import Path

from iris.config import IrisConfig
from iris.connectors import load_connectors
from iris.mac_controller import ActionResult
from iris.recipes import load_recipe_file
from iris.safety import SafetyGate
from iris.state import open_state
from iris.tracing import list_trace_events
from iris.tools import ToolContext, ToolRegistry, _media_service


class _FakeController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def apple_music_search(self, query: str) -> ActionResult:
        self.calls.append(("apple_music_search", query))
        return ActionResult(
            "apple_music_search",
            True,
            f"I found {query} in Music.",
            {"query": query, "verified_playback": False},
        )

    def apple_music_play(self, query: str) -> ActionResult:
        self.calls.append(("apple_music_play", query))
        return ActionResult(
            "apple_music_play",
            True,
            f"Apple Music is playing {query}.",
            {"query": query, "verified_playback": True},
        )

    def apple_music_play_pause(self) -> ActionResult:
        self.calls.append(("apple_music_play_pause", ""))
        return ActionResult("apple_music_play_pause", True, "started Music")


class _BlockedMusicController(_FakeController):
    def apple_music_play(self, query: str) -> ActionResult:
        self.calls.append(("apple_music_play", query))
        return ActionResult(
            "apple_music_play",
            False,
            "macOS blocked Iris from controlling Music. Allow your terminal app to control Music in System Settings > Privacy & Security > Automation.",
            {"query": query},
        )


def test_media_service_normalizes_apple_music_aliases() -> None:
    assert _media_service("Apple Music") == "apple_music"
    assert _media_service("applemusic") == "apple_music"
    assert _media_service("Music") == "apple_music"


def test_media_play_dispatches_to_apple_music_and_remembers_context() -> None:
    controller = _FakeController()
    context = _context(controller)

    result = ToolRegistry.default().execute_approved(
        "media_play",
        {"service": "Apple Music", "query": "Hot Hot by Famous Pluto"},
        context,
    )

    assert result.ok is True
    assert result.continue_planning is False
    assert controller.calls == [("apple_music_play", "Hot Hot by Famous Pluto")]
    assert context.session_state["last_media"]["service"] == "apple_music"
    assert context.session_state["last_media"]["url"].startswith("music://")


def test_media_search_dispatches_to_apple_music() -> None:
    controller = _FakeController()
    context = _context(controller)

    result = ToolRegistry.default().execute_approved(
        "media_search",
        {"service": "music", "query": "Hot Hot"},
        context,
    )

    assert result.ok is True
    assert controller.calls == [("apple_music_search", "Hot Hot")]


def test_media_play_current_uses_remembered_apple_music_service() -> None:
    controller = _FakeController()
    context = _context(controller)
    context.session_state["last_media"] = {
        "service": "apple_music",
        "query": "Hot Hot",
        "surface": "app",
    }

    result = ToolRegistry.default().execute_approved("media_play", {}, context)

    assert result.ok is True
    assert controller.calls == [("apple_music_play_pause", "")]


def test_apple_music_connector_and_recipe_are_registered() -> None:
    connectors = load_connectors([Path("connectors")])
    apple_music = next(
        item for item in connectors if item.connector_id == "apple-music"
    )

    assert apple_music.name == "Apple Music"
    assert "media_play" in apple_music.tools
    assert apple_music.auth_type == "mac_permission"

    recipes = load_recipe_file(Path("recipes/builtin.json"))
    recipe = next(item for item in recipes if item.name == "apple_music_play_song")

    assert recipe.service == "apple_music"
    assert recipe.required_connectors == ("apple-music",)


def test_apple_music_play_records_safe_trace_events(tmp_path: Path) -> None:
    controller = _FakeController()
    config = _config(tmp_path)
    context = _context(controller, config=config, run_id="run-media")

    result = ToolRegistry.default().execute_approved(
        "media_play",
        {"service": "Apple Music", "query": "Hot Hot by Famous Pluto"},
        context,
    )

    assert result.ok is True
    with open_state(config) as db:
        events = list_trace_events(db, run_id="run-media", limit=20)

    names = [event["event_name"] for event in events]
    assert "media.apple_music.play_attempt" in names
    assert "media.apple_music.verify" in names
    details = [event["details"] for event in events]
    assert all("Hot Hot" not in str(item) for item in details)
    assert any(
        item.get("query_length") == len("Hot Hot by Famous Pluto") for item in details
    )


def test_apple_music_permission_error_records_safe_trace_event(tmp_path: Path) -> None:
    controller = _BlockedMusicController()
    config = _config(tmp_path)
    context = _context(controller, config=config, run_id="run-blocked")

    result = ToolRegistry.default().execute_approved(
        "media_play",
        {"service": "Apple Music", "query": "Hot Hot by Famous Pluto"},
        context,
    )

    assert result.ok is False
    with open_state(config) as db:
        events = list_trace_events(db, run_id="run-blocked", limit=20)

    permission = next(
        event
        for event in events
        if event["event_name"] == "media.apple_music.permission_error"
    )
    assert permission["status"] == "error"
    assert permission["error"] == "music_automation_blocked"
    assert "Hot Hot" not in str(permission["details"])


def test_apple_music_search_records_safe_trace_event(tmp_path: Path) -> None:
    controller = _FakeController()
    config = _config(tmp_path)
    context = _context(controller, config=config, run_id="run-search")

    result = ToolRegistry.default().execute_approved(
        "media_search",
        {"service": "Apple Music", "query": "Hot Hot"},
        context,
    )

    assert result.ok is True
    with open_state(config) as db:
        events = list_trace_events(db, run_id="run-search", limit=20)

    search = next(
        event for event in events if event["event_name"] == "media.apple_music.search"
    )
    assert search["details"]["service"] == "apple_music"
    assert search["details"]["query_length"] == len("Hot Hot")
    assert "Hot Hot" not in str(search["details"])


def _context(
    controller: _FakeController,
    *,
    config: IrisConfig | None = None,
    run_id: str = "",
) -> ToolContext:
    return ToolContext(
        controller=controller,  # type: ignore[arg-type]
        perception=object(),  # type: ignore[arg-type]
        safety_gate=SafetyGate(confirm=lambda _question: True),
        openai_client=object(),  # type: ignore[arg-type]
        google_vision=object(),  # type: ignore[arg-type]
        session_state={},
        approved_tool_call=True,
        config=config,
        run_id=run_id,
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
