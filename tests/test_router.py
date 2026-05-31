from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from iris.agent import AgentExecutor, PlannerResult
from iris.integrations.google_vision import GoogleVisionClient
from iris.mac_controller import ActionResult
from iris.perception import ScreenContext
from iris.router import ActionRouter
from iris.safety import SafetyGate
from iris.tools import ToolRegistry


class FakeController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def open_app(self, app_name: str) -> ActionResult:
        self.calls.append(("open_app", app_name))
        return ActionResult("open_app", True, f"opened {app_name}")

    def activate_app(self, app_name: str) -> ActionResult:
        self.calls.append(("activate_app", app_name))
        return ActionResult("activate_app", True, f"activated {app_name}")

    def find_file(self, query: str) -> ActionResult:
        self.calls.append(("find_file", query))
        return ActionResult("find_file", True, "found 1", ["/tmp/example.pdf"])

    def open_url(self, url: str) -> ActionResult:
        self.calls.append(("open_url", url))
        return ActionResult("open_url", True, f"opened {url}")

    def open_url_in_browser(self, url: str, browser: str = "Google Chrome") -> ActionResult:
        self.calls.append(("open_url_in_browser", (url, browser)))
        return ActionResult("open_url_in_browser", True, f"opened {url} in {browser}")

    def browser_navigate_current(self, url: str, browser: str = "Google Chrome") -> ActionResult:
        self.calls.append(("browser_navigate_current", (url, browser)))
        return ActionResult(
            "browser_navigate_current",
            True,
            f"opened {url} in current {browser}",
            {"url": url, "browser": browser},
        )

    def browser_current_page(self, browser: str = "Google Chrome") -> ActionResult:
        self.calls.append(("browser_current_page", browser))
        return ActionResult(
            "browser_current_page",
            True,
            "Current browser page: Example",
            {"title": "Example", "url": "https://example.com", "browser": browser},
        )

    def browser_extract_text(self, browser: str = "Google Chrome") -> ActionResult:
        self.calls.append(("browser_extract_text", browser))
        return ActionResult(
            "browser_extract_text",
            True,
            "I read the visible browser page.",
            {"text": "Example Domain", "browser": browser},
        )

    def browser_click_text(self, text: str, browser: str = "Google Chrome") -> ActionResult:
        self.calls.append(("browser_click_text", (text, browser)))
        return ActionResult("browser_click_text", True, f"clicked {text}")

    def browser_javascript(self, script: str, browser: str = "Google Chrome") -> ActionResult:
        self.calls.append(("browser_javascript", browser))
        return ActionResult("browser_javascript", True, "clicked play")

    def gmail_search(self, query: str, browser: str = "Google Chrome") -> ActionResult:
        self.calls.append(("gmail_search", (query, browser)))
        return ActionResult(
            "gmail_search",
            True,
            f"searched Gmail for {query}",
            {"query": query, "browser": browser},
        )

    def gmail_visible_text(self, browser: str = "Google Chrome") -> ActionResult:
        self.calls.append(("gmail_visible_text", browser))
        return ActionResult(
            "gmail_visible_text",
            True,
            "read Gmail",
            {
                "browser": browser,
                "text": (
                    "From Sleazy E\n"
                    "Here are the images from the event.\n"
                    "Please review the blue logo and the final mockup."
                ),
            },
        )

    def new_browser_tab(self) -> ActionResult:
        self.calls.append(("new_browser_tab", ""))
        return ActionResult("new_browser_tab", True, "opened new tab")

    def spotify_search(self, query: str) -> ActionResult:
        self.calls.append(("spotify_search", query))
        return ActionResult("spotify_search", True, f"spotify {query}")

    def spotify_web_search(self, query: str, browser: str = "Google Chrome") -> ActionResult:
        self.calls.append(("spotify_web_search", (query, browser)))
        return ActionResult("spotify_web_search", True, f"searched web Spotify for {query}")

    def spotify_web_play(self, query: str | None = None, browser: str = "Google Chrome") -> ActionResult:
        self.calls.append(("spotify_web_play", (query, browser)))
        return ActionResult("spotify_web_play", True, "started Spotify web")

    def current_media(self) -> ActionResult:
        self.calls.append(("current_media", ""))
        return ActionResult(
            "current_media",
            True,
            "Burna Boy - Born to Win is playing in Spotify.",
            {"service": "Spotify", "state": "playing", "artist": "Burna Boy", "title": "Born to Win"},
        )

    def spotify_play_pause(self) -> ActionResult:
        self.calls.append(("spotify_play_pause", ""))
        return ActionResult("spotify_play_pause", True, "toggled Spotify")

    def spotify_next(self) -> ActionResult:
        self.calls.append(("spotify_next", ""))
        return ActionResult("spotify_next", True, "next Spotify")

    def spotify_previous(self) -> ActionResult:
        self.calls.append(("spotify_previous", ""))
        return ActionResult("spotify_previous", True, "previous Spotify")

    def set_volume(self, level: int) -> ActionResult:
        self.calls.append(("set_volume", level))
        return ActionResult("set_volume", True, f"volume {level}")

    def change_volume(self, delta: int) -> ActionResult:
        self.calls.append(("change_volume", delta))
        return ActionResult("change_volume", True, f"volume delta {delta}")

    def press_hotkey(self, key: str, modifiers: list[str] | None = None) -> ActionResult:
        self.calls.append(("press_hotkey", (key, modifiers or [])))
        return ActionResult("press_hotkey", True, "pressed hotkey")

    def type_text(self, text: str) -> ActionResult:
        self.calls.append(("type_text", text))
        return ActionResult("type_text", True, "typed text")

    def click(self, x: int, y: int, button: str = "left") -> ActionResult:
        self.calls.append(("click", (x, y, button)))
        return ActionResult("click", True, "clicked")

    def scroll(self, dy: int, dx: int = 0) -> ActionResult:
        self.calls.append(("scroll", (dy, dx)))
        return ActionResult("scroll", True, "scrolled")

    def open_file(self, path: str) -> ActionResult:
        self.calls.append(("open_file", path))
        return ActionResult("open_file", True, f"opened {path}")


class FakeOpenAI:
    available = False


class FakeVisionOpenAI:
    available = True

    def __init__(self) -> None:
        self.screen_prompts: list[str] = []

    def describe_screen(self, screenshot, context: ScreenContext, prompt: str = "") -> str:  # noqa: ANN001
        self.screen_prompts.append(prompt)
        return f"screen: {context.active_app}/{context.active_window}"


class FakeComputerUseOpenAI(FakeVisionOpenAI):
    available = True

    def create_computer_use_response(self, instruction: str, screenshot):  # noqa: ANN001, ANN201
        raise RuntimeError("403 model_not_found computer-use-preview")


class FakeGoogleVision:
    available = True

    def extract_text(self, image_bytes):  # noqa: ANN001, ANN201
        class Result:
            text = "Codex terminal ./iris start --live"

        return Result()

    def label_image(self, image_bytes, max_results: int = 10):  # noqa: ANN001, ANN201
        class Label:
            description = "Text"
            score = 0.98

        return [Label()]


class FakePlanner:
    def __init__(self, *plans: PlannerResult) -> None:
        self.plans = list(plans)
        self.calls: list[dict[str, object]] = []

    def plan(self, **kwargs) -> PlannerResult:  # noqa: ANN003
        self.calls.append(kwargs)
        if self.plans:
            return self.plans.pop(0)
        return PlannerResult(type="final_answer", spoken_response="done")


class FakePerception:
    def screen_context(self) -> ScreenContext:
        return ScreenContext(active_app="Codex", active_window="Iris")

    def capture_screen(self):  # noqa: ANN201
        class FakeScreenshot:
            width = 100
            height = 100
            png = b"png"
            captured_at = datetime.now(timezone.utc)

            def data_url(self) -> str:
                return "data:image/png;base64,cG5n"

        return FakeScreenshot()


class RouterTests(unittest.TestCase):
    def build_router(
        self,
        planner: FakePlanner,
        openai_client=None,
        google_vision=None,
    ) -> tuple[ActionRouter, FakeController, object, AgentExecutor]:
        controller = FakeController()
        openai_client = openai_client or FakeOpenAI()
        google_vision = google_vision or GoogleVisionClient(None)
        safety_gate = SafetyGate(confirm=lambda _: True)
        executor = AgentExecutor(
            planner=planner,  # type: ignore[arg-type]
            registry=ToolRegistry.default(),
            controller=controller,  # type: ignore[arg-type]
            perception=FakePerception(),  # type: ignore[arg-type]
            safety_gate=safety_gate,
            openai_client=openai_client,  # type: ignore[arg-type]
            google_vision=google_vision,  # type: ignore[arg-type]
        )
        router = ActionRouter(
            perception=FakePerception(),  # type: ignore[arg-type]
            controller=controller,  # type: ignore[arg-type]
            safety_gate=safety_gate,
            openai_client=openai_client,  # type: ignore[arg-type]
            google_vision=google_vision,  # type: ignore[arg-type]
            agent_executor=executor,
        )
        return router, controller, openai_client, executor

    def test_open_app_is_selected_by_agent_tool_plan(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="open_app",
                arguments={"app_name": "TextEdit"},
            )
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("open TextEdit")
        self.assertTrue(result.ok)
        self.assertEqual(controller.calls, [("open_app", "TextEdit")])

    def test_generic_app_open_alias_is_selected_by_agent_tool_plan(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="app_open",
                arguments={"app_name": "TextEdit"},
            )
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("open TextEdit")
        self.assertTrue(result.ok)
        self.assertEqual(controller.calls, [("open_app", "TextEdit")])

    def test_browser_open_reuses_current_tab_by_default(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="browser_open",
                arguments={"url": "example.com", "browser": "Google Chrome"},
            )
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("open example.com in the browser")
        self.assertTrue(result.ok)
        self.assertEqual(
            controller.calls,
            [("browser_navigate_current", ("https://example.com", "Google Chrome"))],
        )

    def test_browser_open_explicit_new_tab_uses_browser_open(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="browser_open",
                arguments={"url": "example.com", "browser": "Google Chrome", "new_tab": True},
            )
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("open example.com in a new tab")
        self.assertTrue(result.ok)
        self.assertEqual(
            controller.calls,
            [("open_url_in_browser", ("https://example.com", "Google Chrome"))],
        )

    def test_spotify_browser_request_uses_media_tool_not_router_parser(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="media_search_or_play",
                arguments={
                    "service": "spotify",
                    "query": "Burna Boy Burn Winner",
                    "surface": "browser",
                },
            )
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("open Spotify in Chrome and play Burna Boy Burn Winner")
        self.assertTrue(result.ok)
        self.assertEqual(
            controller.calls,
            [("spotify_web_search", ("Burna Boy Burn Winner", "Google Chrome"))],
        )
        self.assertEqual(
            result.message,
            "I opened Spotify in your browser and searched for Burna Boy Burn Winner.",
        )

    def test_spotify_browser_play_uses_browser_play_tool(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="media_search_or_play",
                arguments={
                    "service": "spotify",
                    "query": "Burna Boy Born to Win",
                    "surface": "browser",
                    "action": "play",
                },
            )
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("open Spotify in my browser and play Burna Boy Born to Win")
        self.assertTrue(result.ok)
        self.assertEqual(
            controller.calls,
            [("spotify_web_play", ("Burna Boy Born to Win", "Google Chrome"))],
        )
        self.assertEqual(
            result.message,
            "I started Spotify in your browser and looked for Burna Boy Born to Win.",
        )

    def test_generic_media_play_uses_recipe_backed_media_tool(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="media_play",
                arguments={
                    "service": "spotify",
                    "query": "Burna Boy Born to Win",
                    "surface": "browser",
                },
            )
        )
        router, controller, _, executor = self.build_router(planner)
        result = router.handle_text("play Born to Win by Burna Boy on Spotify")
        self.assertTrue(result.ok)
        self.assertEqual(
            controller.calls,
            [("spotify_web_play", ("Burna Boy Born to Win", "Google Chrome"))],
        )
        self.assertEqual(executor._session_state["current_recipe"], "spotify_media")
        self.assertEqual(
            result.message,
            "I started Spotify in your browser and looked for Burna Boy Born to Win.",
        )

    def test_audio_current_media_reports_now_playing_metadata(self) -> None:
        planner = FakePlanner(
            PlannerResult(type="tool_call", tool_name="audio_current_media", arguments={})
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("what song is playing?")
        self.assertTrue(result.ok)
        self.assertEqual(controller.calls, [("current_media", "")])
        self.assertIn("Born to Win", result.message)

    @patch("iris.tools._web_search")
    def test_audio_explain_current_song_uses_metadata_then_public_context(self, web_search) -> None:  # noqa: ANN001
        web_search.return_value = [
            {
                "title": "Born to Win meaning",
                "url": "https://example.com/born-to-win",
                "snippet": "The song is about resilience, ambition, and pushing through setbacks.",
            }
        ]
        planner = FakePlanner(
            PlannerResult(type="tool_call", tool_name="audio_explain_current_song", arguments={})
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("listen to the song playing and explain what it means")
        self.assertTrue(result.ok)
        self.assertEqual(controller.calls, [("current_media", "")])
        self.assertIn("Born to Win", result.message)
        self.assertIn("resilience", result.message)

    def test_chrome_javascript_disabled_becomes_retryable_screen_fallback(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="browser_play_media",
                arguments={"service": "spotify", "query": "Born to Win"},
            ),
            PlannerResult(type="final_answer", spoken_response="I’ll need you to click the play button."),
        )
        router, controller, _, _ = self.build_router(planner)

        def disabled_js(query=None, browser="Google Chrome"):  # noqa: ANN001
            controller.calls.append(("spotify_web_play", (query, browser)))
            return ActionResult(
                "spotify_web_play",
                False,
                "Chrome automation is off, so I’ll use screen clicks instead.",
                {"retry_with_screen": True, "reason": "chrome_javascript_disabled"},
            )

        controller.spotify_web_play = disabled_js  # type: ignore[method-assign]
        result = router.handle_text("play Born to Win")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "I’ll need you to click the play button.")
        self.assertEqual(len(planner.calls), 2)
        self.assertEqual(
            controller.calls,
            [("spotify_web_play", ("Born to Win", "Google Chrome"))],
        )

    def test_play_it_uses_recent_media_context(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="media_search_or_play",
                arguments={
                    "service": "spotify",
                    "query": "Burna Boy Born to Win",
                    "surface": "browser",
                },
            ),
            PlannerResult(
                type="tool_call",
                tool_name="media_play_current",
                arguments={"service": "spotify", "surface": "browser"},
            ),
        )
        router, controller, _, _ = self.build_router(planner)
        router.handle_text("open Spotify in browser and search Burna Boy Born to Win")
        result = router.handle_text("play it")
        self.assertTrue(result.ok)
        self.assertEqual(
            controller.calls,
            [
                ("spotify_web_search", ("Burna Boy Born to Win", "Google Chrome")),
                ("spotify_web_play", ("Burna Boy Born to Win", "Google Chrome")),
            ],
        )
        self.assertEqual(result.message, "I tried to start it in Spotify.")

    def test_volume_request_uses_volume_tool_not_router_parser(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="change_volume",
                arguments={"delta": -15},
            )
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("make it quieter")
        self.assertTrue(result.ok)
        self.assertEqual(controller.calls, [("change_volume", -15)])

    def test_natural_chat_can_be_final_answer_from_agent(self) -> None:
        planner = FakePlanner(
            PlannerResult(type="final_answer", spoken_response="I am good, Clinton.")
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("how are you?")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "I am good, Clinton.")
        self.assertEqual(controller.calls, [])

    def test_current_tab_uses_live_screen_tool(self) -> None:
        openai = FakeVisionOpenAI()
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="describe_screen",
                arguments={"prompt": "What am I currently looking at?"},
            )
        )
        router, _, _, _ = self.build_router(planner, openai)
        result = router.handle_text("What am I currently looking at?")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "screen: Codex/Iris")
        self.assertIn("latest Iris live screen-awareness frame", openai.screen_prompts[0])

    def test_screen_tool_prefers_google_vision_when_configured(self) -> None:
        openai = FakeVisionOpenAI()
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="describe_screen",
                arguments={"prompt": "What am I currently looking at?"},
            )
        )
        router, _, _, _ = self.build_router(planner, openai, FakeGoogleVision())
        result = router.handle_text("What am I currently looking at?")
        self.assertTrue(result.ok)
        self.assertIn("Google Vision live screen read", result.message)
        self.assertIn("Codex terminal", result.message)
        self.assertEqual(openai.screen_prompts, [])

    def test_reset_conversation_clears_agent_history(self) -> None:
        planner = FakePlanner(
            PlannerResult(type="final_answer", spoken_response="remembered"),
        )
        router, _, _, executor = self.build_router(planner)
        router.handle_text("remember this")
        self.assertGreater(len(executor._conversation_history), 0)
        reset = router.handle_text("/reset")
        self.assertTrue(reset.ok)
        self.assertEqual(executor._conversation_history, [])

    def test_spoken_approve_without_pending_action_is_clear(self) -> None:
        planner = FakePlanner(PlannerResult(type="final_answer", spoken_response="unused"))
        router, _, _, _ = self.build_router(planner)
        result = router.handle_text("Approved")
        self.assertFalse(result.ok)
        self.assertIn("don't have a pending action", result.message)

    def test_computer_use_model_access_failure_is_human_readable(self) -> None:
        planner = FakePlanner(PlannerResult(type="final_answer", spoken_response="unused"))
        router, _, _, _ = self.build_router(planner, FakeComputerUseOpenAI())
        result = router.run_computer_use("open iCloud Drive settings")
        self.assertFalse(result.ok)
        self.assertIn("can't use the computer-control model", result.message)

    @patch("iris.tools._fetch_text")
    def test_web_research_summarizes_public_results(self, fetch_text) -> None:  # noqa: ANN001
        search_html = """
        <a class="result__a" href="https://example.com/clinton">Clinton Imaro - Example</a>
        <a class="result__snippet">Founder and software builder profile.</a>
        """
        page_html = "<html><body><p>Clinton Imaro is described here as a software builder working on AI products and local agents.</p></body></html>"
        fetch_text.side_effect = [search_html, page_html]
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="web_research",
                arguments={"query": "Clinton Imaro"},
            )
        )
        router, _, _, _ = self.build_router(planner)
        result = router.handle_text("look up Clinton Imaro online and summarize it")
        self.assertTrue(result.ok)
        self.assertIn("Here’s what I found publicly for Clinton Imaro", result.message)
        self.assertIn("software builder", result.message)

    def test_search_web_uses_friendly_message_not_raw_url(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="search_web",
                arguments={"query": "Clinton Imaro"},
            )
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("search the web for Clinton Imaro")
        self.assertTrue(result.ok)
        self.assertEqual(
            controller.calls,
            [("open_url", "https://www.google.com/search?q=Clinton+Imaro")],
        )
        self.assertEqual(result.message, "I searched the web for Clinton Imaro.")
        self.assertNotIn("google dot com", result.message.lower())
        self.assertNotIn("https://", result.message)

    @patch("iris.tools._fetch_text")
    def test_identify_song_summarizes_match_instead_of_opening_google(self, fetch_text) -> None:  # noqa: ANN001
        fetch_text.return_value = """
        <a class="result__a" href="https://lyrics.example/burna">Born Winner Lyrics - Burna Boy</a>
        <a class="result__snippet">Lyrics include long road and standing.</a>
        """
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="identify_song",
                arguments={"lyrics": "long road where I'm standing"},
            )
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("what song is long road where I'm standing?")
        self.assertTrue(result.ok)
        self.assertIn("closest match", result.message)
        self.assertIn("Born Winner Lyrics", result.message)
        self.assertEqual(controller.calls, [])

    def test_gmail_search_is_structured_and_friendly(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="gmail_search",
                arguments={"query": "from:Sleazy OR to:Sleazy"},
            )
        )
        router, controller, _, _ = self.build_router(planner)
        result = router.handle_text("open Gmail and search Sleazy")
        self.assertTrue(result.ok)
        self.assertEqual(
            controller.calls,
            [("gmail_search", ("from:Sleazy OR to:Sleazy", "Google Chrome"))],
        )
        self.assertEqual(result.message, "I searched Gmail for from:Sleazy OR to:Sleazy.")

    def test_gmail_summarize_requires_spoken_approval_then_executes(self) -> None:
        planner = FakePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="gmail_search_and_summarize",
                arguments={"query": "from:Sleazy OR to:Sleazy"},
            )
        )
        router, controller, _, _ = self.build_router(planner)
        pending = router.handle_text("search Gmail messages from Sleazy and summarize them")
        self.assertFalse(pending.ok)
        self.assertIn("needs approval", pending.message)
        self.assertEqual(controller.calls, [])

        approved = router.handle_text("approved")
        self.assertTrue(approved.ok)
        self.assertEqual(
            controller.calls,
            [
                ("gmail_search", ("from:Sleazy OR to:Sleazy", "Google Chrome")),
                ("gmail_visible_text", "Google Chrome"),
            ],
        )
        self.assertIn("From the visible results", approved.message)
        self.assertIn("final mockup", approved.message)


if __name__ == "__main__":
    unittest.main()
