from __future__ import annotations

from pathlib import Path

from iris.connectors import load_connectors
from iris.mac_controller import ActionResult
from iris.recipes import load_recipe_file
from iris.safety import SafetyGate
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


def _context(controller: _FakeController) -> ToolContext:
    return ToolContext(
        controller=controller,  # type: ignore[arg-type]
        perception=object(),  # type: ignore[arg-type]
        safety_gate=SafetyGate(confirm=lambda _question: True),
        openai_client=object(),  # type: ignore[arg-type]
        google_vision=object(),  # type: ignore[arg-type]
        session_state={},
        approved_tool_call=True,
    )
