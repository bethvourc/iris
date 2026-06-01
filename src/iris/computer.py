from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from iris.config import IrisConfig
from iris.control_backends import control_backend_status
from iris.mac_controller import ActionResult, MacController
from iris.perception import (
    LiveScreenFrame,
    PerceptionService,
    ScreenAwarenessService,
    Screenshot,
)


@dataclass(frozen=True)
class ComputerObservation:
    active_app: str | None
    active_window: str | None
    screenshot_width: int | None = None
    screenshot_height: int | None = None
    screenshot_captured_at: datetime | None = None
    browser_title: str | None = None
    browser_url: str | None = None
    source: str = "computer_backend"
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "active_app": self.active_app,
            "active_window": self.active_window,
            "screenshot_width": self.screenshot_width,
            "screenshot_height": self.screenshot_height,
            "screenshot_captured_at": self.screenshot_captured_at.isoformat()
            if self.screenshot_captured_at
            else None,
            "browser_title": self.browser_title,
            "browser_url": self.browser_url,
            "error": self.error,
        }


class ComputerBackend:
    """One local computer interface for agent observe/act loops."""

    def __init__(
        self,
        *,
        controller: MacController,
        perception: PerceptionService,
        screen_awareness: ScreenAwarenessService | None = None,
        browser: str = "Google Chrome",
    ) -> None:
        self.controller = controller
        self.perception = perception
        self.screen_awareness = screen_awareness
        self.browser = browser

    def set_screen_awareness(
        self, screen_awareness: ScreenAwarenessService | None
    ) -> None:
        self.screen_awareness = screen_awareness

    def observe(
        self, *, include_browser: bool = True, capture_if_needed: bool = False
    ) -> ComputerObservation:
        frame: LiveScreenFrame | None = None
        error: str | None = None
        if self.screen_awareness is not None:
            frame = self.screen_awareness.latest()
            error = self.screen_awareness.latest_error()
            if frame is None and capture_if_needed:
                try:
                    frame = self.screen_awareness.capture_now()
                    error = None
                except Exception as exc:
                    error = str(exc)
        if frame is not None:
            active_app = frame.context.active_app
            active_window = frame.context.active_window
        else:
            active_app, active_window = self._active_context()
        screenshot = frame.screenshot if frame is not None else None
        browser_title = None
        browser_url = None
        if include_browser:
            current_page = getattr(self.controller, "browser_current_page", None)
            if callable(current_page):
                page = current_page(self.browser)
                if page.ok and isinstance(page.payload, dict):
                    browser_title = str(page.payload.get("title") or "") or None
                    browser_url = str(page.payload.get("url") or "") or None
        return ComputerObservation(
            active_app=active_app,
            active_window=active_window,
            screenshot_width=screenshot.width if screenshot else None,
            screenshot_height=screenshot.height if screenshot else None,
            screenshot_captured_at=screenshot.captured_at if screenshot else None,
            browser_title=browser_title,
            browser_url=browser_url,
            error=error,
        )

    def screenshot(self) -> Screenshot:
        if self.screen_awareness is not None:
            frame = self.screen_awareness.latest()
            if frame is not None:
                return frame.screenshot
            return self.screen_awareness.capture_now().screenshot
        return self.perception.capture_screen()

    def open_app(self, app_name: str) -> ActionResult:
        return self.controller.open_app(app_name)

    def activate_app(self, app_name: str) -> ActionResult:
        return self.controller.activate_app(app_name)

    def open_url(
        self, url: str, browser: str = "Google Chrome", *, new_tab: bool = False
    ) -> ActionResult:
        if new_tab:
            return self.controller.open_url_in_browser(url, browser)
        return self.controller.browser_navigate_current(url, browser)

    def browser_current_page(self, browser: str = "Google Chrome") -> ActionResult:
        return self.controller.browser_current_page(browser)

    def browser_extract_text(self, browser: str = "Google Chrome") -> ActionResult:
        return self.controller.browser_extract_text(browser)

    def browser_click_text(
        self, text: str, browser: str = "Google Chrome"
    ) -> ActionResult:
        return self.controller.browser_click_text(text, browser)

    def type_text(self, text: str) -> ActionResult:
        return self.controller.type_text(text)

    def press_hotkey(
        self, key: str, modifiers: list[str] | None = None
    ) -> ActionResult:
        return self.controller.press_hotkey(key, modifiers or [])

    def click(self, x: int, y: int, button: str = "left") -> ActionResult:
        return self.controller.click(x, y, button)

    def scroll(self, dy: int, dx: int = 0) -> ActionResult:
        return self.controller.scroll(dy, dx)

    def wait(self, seconds: float) -> ActionResult:
        return self.controller.wait(seconds)

    def pause_current_media(self) -> ActionResult:
        pause = getattr(self.controller, "pause_current_media", None)
        if callable(pause):
            return pause()
        return self.controller.spotify_play_pause()

    def observed_at(self) -> datetime:
        return datetime.now(timezone.utc)

    def backend_health(self, config: IrisConfig | None = None) -> list[dict[str, Any]]:
        return control_backend_status(config)

    def _active_context(self) -> tuple[str | None, str | None]:
        screen_context = getattr(self.perception, "screen_context", None)
        if callable(screen_context):
            context = screen_context()
            return context.active_app, context.active_window
        active_app = getattr(self.perception, "active_app", None)
        active_window = getattr(self.perception, "active_window_title", None)
        return (
            active_app() if callable(active_app) else None,
            active_window() if callable(active_window) else None,
        )
