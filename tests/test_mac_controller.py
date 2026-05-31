from __future__ import annotations

import unittest
from unittest.mock import patch

from iris.computer import ComputerBackend
from iris.mac_controller import MacController
from iris.perception import ScreenContext
from iris.safety import SafetyGate
from iris.system import CommandResult


class FakeBackendController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def browser_current_page(self, browser: str = "Google Chrome"):
        self.calls.append(("browser_current_page", browser))
        from iris.mac_controller import ActionResult

        return ActionResult(
            "browser_current_page",
            True,
            "Example",
            {"title": "Example", "url": "https://example.com"},
        )

    def type_text(self, text: str):
        self.calls.append(("type_text", text))
        from iris.mac_controller import ActionResult

        return ActionResult("type_text", True, "typed")


class FakeBackendPerception:
    def screen_context(self) -> ScreenContext:
        return ScreenContext(active_app="Chrome", active_window="Example")


class MacControllerTests(unittest.TestCase):
    @patch("iris.mac_controller.run_osascript")
    def test_spotify_play_pause_uses_player_state_commands(self, run_osascript) -> None:  # noqa: ANN001
        captured_scripts: list[str] = []

        def fake_run(script: str, *, timeout: float = 10) -> CommandResult:
            captured_scripts.append(script)
            return CommandResult(["osascript"], 0, "paused Spotify", "")

        run_osascript.side_effect = fake_run
        controller = MacController(SafetyGate(confirm=lambda _: True))
        result = controller.spotify_play_pause()
        self.assertTrue(result.ok)
        self.assertEqual(result.detail, "paused Spotify")
        self.assertEqual(len(captured_scripts), 1)
        self.assertIn("if player state is playing then", captured_scripts[0])
        self.assertNotIn("playpause", captured_scripts[0])

    def test_computer_backend_observes_app_window_and_browser_state(self) -> None:
        controller = FakeBackendController()
        backend = ComputerBackend(
            controller=controller,  # type: ignore[arg-type]
            perception=FakeBackendPerception(),  # type: ignore[arg-type]
        )
        observation = backend.observe()
        self.assertEqual(observation.active_app, "Chrome")
        self.assertEqual(observation.active_window, "Example")
        self.assertEqual(observation.browser_url, "https://example.com")
        self.assertEqual(controller.calls, [("browser_current_page", "Google Chrome")])

    def test_computer_backend_routes_typing_through_controller(self) -> None:
        controller = FakeBackendController()
        backend = ComputerBackend(
            controller=controller,  # type: ignore[arg-type]
            perception=FakeBackendPerception(),  # type: ignore[arg-type]
        )
        result = backend.type_text("hello")
        self.assertTrue(result.ok)
        self.assertEqual(controller.calls, [("type_text", "hello")])


if __name__ == "__main__":
    unittest.main()
