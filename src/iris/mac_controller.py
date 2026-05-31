from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, quote_plus
import re
import shutil
import time
from typing import Any

from iris.actions import LocalAction, RiskLevel
from iris.safety import SafetyGate
from iris.system import applescript_string, run_command, run_osascript


def _load_quartz():
    try:  # pragma: no cover - depends on optional macOS runtime package
        import Quartz  # type: ignore

        return Quartz
    except Exception:  # pragma: no cover - non-mac or missing PyObjC
        return None


def _scale_point_for_main_display(quartz, x: int, y: int) -> tuple[float, float]:
    """Convert screenshot pixel coordinates to the main display coordinate space."""

    try:
        display_id = quartz.CGMainDisplayID()
        bounds = quartz.CGDisplayBounds(display_id)
        pixel_width = float(quartz.CGDisplayPixelsWide(display_id))
        pixel_height = float(quartz.CGDisplayPixelsHigh(display_id))
        bounds_width = float(bounds.size.width)
        bounds_height = float(bounds.size.height)
        origin_x = float(bounds.origin.x)
        origin_y = float(bounds.origin.y)
    except Exception:
        return float(x), float(y)
    if pixel_width <= 0 or pixel_height <= 0 or bounds_width <= 0 or bounds_height <= 0:
        return float(x), float(y)
    return (
        origin_x + (float(x) * bounds_width / pixel_width),
        origin_y + (float(y) * bounds_height / pixel_height),
    )


SPECIAL_KEY_CODES = {
    "return": 36,
    "enter": 36,
    "tab": 48,
    "space": 49,
    "delete": 51,
    "escape": 53,
    "esc": 53,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
}

MODIFIER_NAMES = {
    "cmd": "command down",
    "command": "command down",
    "shift": "shift down",
    "alt": "option down",
    "option": "option down",
    "ctrl": "control down",
    "control": "control down",
}


@dataclass(frozen=True)
class ActionResult:
    action: str
    ok: bool
    detail: str = ""
    payload: Any | None = None


class MacController:
    def __init__(self, safety_gate: SafetyGate) -> None:
        self.safety_gate = safety_gate

    def wait(self, seconds: float) -> ActionResult:
        self.safety_gate.allow(
            LocalAction("wait", {"seconds": seconds}, f"wait {seconds:.2f}s")
        )
        time.sleep(seconds)
        return ActionResult("wait", True, f"waited {seconds:.2f}s")

    def open_app(self, app_name: str) -> ActionResult:
        self.safety_gate.allow(LocalAction("open_app", {"app_name": app_name}))
        result = run_command(["open", "-a", app_name], timeout=10)
        return ActionResult(
            "open_app",
            result.ok,
            result.stderr or result.stdout or f"requested {app_name}",
        )

    def activate_app(self, app_name: str) -> ActionResult:
        self.safety_gate.allow(LocalAction("activate_app", {"app_name": app_name}))
        script = f"tell application {applescript_string(app_name)} to activate"
        result = run_osascript(script, timeout=8)
        if result.ok:
            return ActionResult("activate_app", True, f"activated {app_name}")
        return self.open_app(app_name)

    def open_url(self, url: str) -> ActionResult:
        self.safety_gate.allow(
            LocalAction("open_url", {"url": url}, risk=RiskLevel.LOW_RISK)
        )
        result = run_command(["open", url], timeout=10)
        return ActionResult("open_url", result.ok, result.stderr or result.stdout or f"opened {url}")

    def open_url_in_browser(self, url: str, browser: str = "Google Chrome") -> ActionResult:
        self.safety_gate.allow(
            LocalAction(
                "open_url_in_browser",
                {"url": url, "browser": browser},
                risk=RiskLevel.LOW_RISK,
            )
        )
        result = run_command(["open", "-a", browser, url], timeout=10)
        if result.ok:
            return ActionResult("open_url_in_browser", True, f"opened {url} in {browser}")
        fallback = self.open_url(url)
        if fallback.ok:
            return ActionResult(
                "open_url_in_browser",
                True,
                f"opened {url} in the default browser",
                fallback.payload,
            )
        return ActionResult("open_url_in_browser", False, result.stderr or fallback.detail)

    def browser_javascript(
        self,
        script: str,
        browser: str = "Google Chrome",
    ) -> ActionResult:
        self.safety_gate.allow(
            LocalAction(
                "browser_javascript",
                {"browser": browser, "script_preview": script[:160]},
                "run JavaScript in the active browser tab",
            )
        )
        osa = (
            f"tell application {applescript_string(browser)}\n"
            "  if not (exists front window) then return \"no front window\"\n"
            f"  execute active tab of front window javascript {applescript_string(script)}\n"
            "end tell"
        )
        result = run_osascript(osa, timeout=15)
        return ActionResult(
            "browser_javascript",
            result.ok,
            result.stderr or result.stdout or "ran browser JavaScript",
        )

    def browser_automation_available(self, browser: str = "Google Chrome") -> ActionResult:
        if browser.lower() != "google chrome":
            return ActionResult(
                "browser_automation_available",
                True,
                f"{browser} automation preflight not required.",
                {"available": True, "browser": browser},
            )
        result = self.browser_javascript("(() => 'iris-js-ok')()", browser)
        if result.ok and "iris-js-ok" in result.detail:
            return ActionResult(
                "browser_automation_available",
                True,
                "Chrome browser automation is ready.",
                {"available": True, "browser": browser},
            )
        if _chrome_javascript_disabled(result.detail):
            return ActionResult(
                "browser_automation_available",
                False,
                "Chrome automation is off, so I’ll use screen clicks instead.",
                {"available": False, "browser": browser, "reason": "chrome_javascript_disabled"},
            )
        return ActionResult(
            "browser_automation_available",
            False,
            _human_browser_error(result.detail),
            {"available": False, "browser": browser},
        )

    def browser_current_page(self, browser: str = "Google Chrome") -> ActionResult:
        script = (
            f"tell application {applescript_string(browser)}\n"
            "  if not (exists front window) then return \"\"\n"
            "  set pageTitle to title of active tab of front window\n"
            "  set pageUrl to URL of active tab of front window\n"
            "  return pageTitle & \"\\n\" & pageUrl\n"
            "end tell"
        )
        result = run_osascript(script, timeout=8)
        if not result.ok:
            return ActionResult("browser_current_page", False, _human_browser_error(result.stderr))
        lines = result.stdout.splitlines()
        title = lines[0] if lines else ""
        url = lines[1] if len(lines) > 1 else ""
        return ActionResult(
            "browser_current_page",
            True,
            f"Current browser page: {title or url or 'unknown'}",
            {"title": title, "url": url, "browser": browser},
        )

    def browser_navigate_current(self, url: str, browser: str = "Google Chrome") -> ActionResult:
        normalized_url = url
        self.safety_gate.allow(
            LocalAction(
                "browser_navigate_current",
                {"url": normalized_url, "browser": browser},
                risk=RiskLevel.LOW_RISK,
            )
        )
        script = (
            f"tell application {applescript_string(browser)}\n"
            "  activate\n"
            "  if not (exists front window) then make new window\n"
            f"  set URL of active tab of front window to {applescript_string(normalized_url)}\n"
            "end tell"
        )
        result = run_osascript(script, timeout=10)
        if result.ok:
            return ActionResult(
                "browser_navigate_current",
                True,
                f"opened {normalized_url} in the current {browser} tab",
                {"url": normalized_url, "browser": browser},
            )
        fallback = self.open_url_in_browser(normalized_url, browser)
        if fallback.ok:
            return ActionResult(
                "browser_navigate_current",
                True,
                f"opened {normalized_url} in {browser}",
                fallback.payload,
            )
        return ActionResult("browser_navigate_current", False, _human_browser_error(result.stderr))

    def browser_extract_text(self, browser: str = "Google Chrome", max_chars: int = 12000) -> ActionResult:
        script = f"""
(() => {{
  const root = document.querySelector('main') || document.body;
  const text = (root && (root.innerText || root.textContent) || '').trim();
  return text.slice(0, {int(max_chars)});
}})()
"""
        result = self.browser_javascript(script, browser)
        if result.ok:
            return ActionResult(
                "browser_extract_text",
                True,
                "I read the visible browser page.",
                {"text": result.detail, "browser": browser},
            )
        return ActionResult("browser_extract_text", False, _human_browser_error(result.detail))

    def browser_click_text(self, text: str, browser: str = "Google Chrome") -> ActionResult:
        script = r"""
needle => {
  const wanted = (needle || '').toLowerCase();
  const visible = element => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const candidates = [...document.querySelectorAll('button,a,[role="button"],input[type="button"],input[type="submit"]')];
  const match = candidates.find(element => visible(element) && ((element.getAttribute('aria-label') || element.innerText || element.value || '').toLowerCase().includes(wanted)));
  if (!match) return 'not found';
  match.click();
  return 'clicked';
}
"""
        result = self.browser_javascript(f"({script})({applescript_string(text)})", browser)
        if result.ok and "clicked" in result.detail.lower():
            return ActionResult("browser_click_text", True, f"clicked {text}", {"text": text})
        if result.ok:
            return ActionResult("browser_click_text", False, f"I could not find {text} on the page.")
        return ActionResult("browser_click_text", False, _human_browser_error(result.detail))

    def new_browser_tab(self) -> ActionResult:
        return self.press_hotkey("t", ["command"])

    def set_volume(self, level: int) -> ActionResult:
        bounded = max(0, min(100, level))
        self.safety_gate.allow(LocalAction("set_volume", {"level": bounded}))
        result = run_osascript(f"set volume output volume {bounded}", timeout=8)
        return ActionResult("set_volume", result.ok, result.stderr or f"volume set to {bounded}")

    def set_app_volume(self, app_name: str, level: int) -> ActionResult:
        bounded = max(0, min(100, level))
        app = app_name.strip() or "Spotify"
        self.safety_gate.allow(
            LocalAction(
                "set_app_volume",
                {"app_name": app, "level": bounded},
                risk=RiskLevel.LOW_RISK,
            )
        )
        result = run_osascript(
            f'tell application {applescript_string(app)} to set sound volume to {bounded}',
            timeout=8,
        )
        if result.ok:
            return ActionResult(
                "set_app_volume",
                True,
                f"{app} volume set to {bounded}",
                {"app_name": app, "level": bounded},
            )
        return ActionResult(
            "set_app_volume",
            False,
            result.stderr or f"{app} does not expose scriptable volume control.",
            {"app_name": app, "level": bounded},
        )

    def change_volume(self, delta: int) -> ActionResult:
        self.safety_gate.allow(LocalAction("change_volume", {"delta": delta}))
        script = (
            "set currentVolume to output volume of (get volume settings)\n"
            f"set nextVolume to currentVolume + ({int(delta)})\n"
            "if nextVolume is greater than 100 then set nextVolume to 100\n"
            "if nextVolume is less than 0 then set nextVolume to 0\n"
            "set volume output volume nextVolume\n"
            "return nextVolume"
        )
        result = run_osascript(script, timeout=8)
        return ActionResult("change_volume", result.ok, result.stderr or f"volume {result.stdout}")

    def spotify_search(self, query: str) -> ActionResult:
        self.safety_gate.allow(LocalAction("spotify_search", {"query": query}))
        result = self.open_url(f"spotify:search:{quote_plus(query)}")
        if result.ok:
            return ActionResult("spotify_search", True, f"opened Spotify search for {query}")
        fallback = run_command(["open", "-a", "Spotify"], timeout=10)
        return ActionResult("spotify_search", fallback.ok, fallback.stderr or result.detail)

    def spotify_web_search(
        self,
        query: str,
        browser: str = "Google Chrome",
    ) -> ActionResult:
        url = f"https://open.spotify.com/search/{quote_plus(query)}"
        result = self.open_url_in_browser(url, browser)
        if result.ok:
            return ActionResult(
                "spotify_web_search",
                True,
                f"I opened Spotify in your browser and searched for {query}.",
                {"url": url, "query": query, "browser": browser},
            )
        return ActionResult("spotify_web_search", False, result.detail)

    def spotify_web_play(
        self,
        query: str | None = None,
        browser: str = "Google Chrome",
    ) -> ActionResult:
        if query:
            opened = self.spotify_web_search(query, browser)
            if not opened.ok:
                return opened
            time.sleep(2.5)
        script = r"""
(() => {
  const lower = value => (value || '').toString().toLowerCase();
  const visible = element => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const buttons = [...document.querySelectorAll('button')].filter(visible);
  const playButtons = buttons.filter(button => {
    const label = lower(button.getAttribute('aria-label') || button.textContent);
    return label.includes('play') && !label.includes('pause');
  });
  const preferred = playButtons.find(button => {
    const label = lower(button.getAttribute('aria-label') || button.textContent);
    return label.includes('burn') || label.includes('boy') || label.includes('win');
  }) || playButtons[0];
  if (preferred) {
    preferred.click();
    return 'clicked play';
  }
  const resultLink = [...document.querySelectorAll('a[href*="/track/"], a[href*="/album/"]')].find(visible);
  if (resultLink) {
    resultLink.click();
    return 'opened first result';
  }
  return 'no playable result found';
})()
"""
        preflight = self.browser_automation_available(browser)
        if not preflight.ok:
            return ActionResult(
                "spotify_web_play",
                False,
                preflight.detail,
                {"query": query, "browser": browser, "retry_with_screen": True, **(preflight.payload or {})},
            )
        result = self.browser_javascript(script, browser)
        detail = result.detail.lower()
        if result.ok and ("clicked play" in detail or "opened first result" in detail):
            return ActionResult(
                "spotify_web_play",
                True,
                "I tried to start it in Spotify.",
                {"query": query, "browser": browser, "browser_result": result.detail},
            )
        if result.ok and "no playable result found" in detail:
            return ActionResult(
                "spotify_web_play",
                False,
                "I opened Spotify, but I could not find a visible play button yet. You may need to log in or pick the result.",
                {"query": query, "browser": browser, "browser_result": result.detail},
            )
        return ActionResult(
            "spotify_web_play",
            result.ok,
            _human_browser_error(result.detail)
            or "Chrome automation is off, so I’ll use screen clicks instead.",
            {"query": query, "browser": browser},
        )

    def current_media(self) -> ActionResult:
        spotify = run_osascript(
            """
try
  tell application "Spotify"
    if it is running then
      set trackName to name of current track
      set artistName to artist of current track
      set playerState to player state as text
      return "Spotify|" & playerState & "|" & artistName & "|" & trackName
    end if
  end tell
end try
return ""
""",
            timeout=8,
        )
        if spotify.ok and spotify.stdout:
            parts = spotify.stdout.split("|")
            if len(parts) >= 4:
                service, state, artist, title = parts[:4]
                return ActionResult(
                    "current_media",
                    True,
                    f"{artist} - {title} is {state} in {service}.",
                    {"service": service, "state": state, "artist": artist, "title": title},
                )
        music = run_osascript(
            """
try
  tell application "Music"
    if it is running then
      set trackName to name of current track
      set artistName to artist of current track
      set playerState to player state as text
      return "Music|" & playerState & "|" & artistName & "|" & trackName
    end if
  end tell
end try
return ""
""",
            timeout=8,
        )
        if music.ok and music.stdout:
            parts = music.stdout.split("|")
            if len(parts) >= 4:
                service, state, artist, title = parts[:4]
                return ActionResult(
                    "current_media",
                    True,
                    f"{artist} - {title} is {state} in {service}.",
                    {"service": service, "state": state, "artist": artist, "title": title},
                )
        return ActionResult("current_media", False, "I can't detect active media metadata yet.")

    def gmail_search(self, query: str, browser: str = "Google Chrome") -> ActionResult:
        self.safety_gate.allow(
            LocalAction(
                "gmail_search",
                {"query": query, "browser": browser},
                risk=RiskLevel.LOW_RISK,
            )
        )
        encoded = quote(query, safe="")
        url = f"https://mail.google.com/mail/u/0/#search/{encoded}"
        result = self.open_url_in_browser(url, browser)
        if result.ok:
            return ActionResult(
                "gmail_search",
                True,
                f"I searched Gmail for {query}.",
                {"query": query, "browser": browser, "url": url},
            )
        return ActionResult("gmail_search", False, result.detail)

    def gmail_visible_text(self, browser: str = "Google Chrome") -> ActionResult:
        script = r"""
(() => {
  const visible = element => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const roots = [
    document.querySelector('[role="main"]'),
    document.querySelector('.AO'),
    document.body
  ].filter(Boolean);
  const text = roots
    .filter(visible)
    .map(element => element.innerText || element.textContent || '')
    .join('\n')
    .replace(/\s+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  return text.slice(0, 12000);
})()
"""
        result = self.browser_javascript(script, browser)
        if result.ok:
            text = result.detail or ""
            return ActionResult(
                "gmail_visible_text",
                True,
                "I read the visible Gmail content.",
                {"browser": browser, "text": text},
            )
        return ActionResult("gmail_visible_text", False, result.detail)

    def spotify_play_pause(self) -> ActionResult:
        self.safety_gate.allow(LocalAction("spotify_play_pause"))
        return self.pause_current_media(toggle=True, preferred_service="Spotify")

    def pause_current_media(
        self,
        *,
        toggle: bool = False,
        preferred_service: str = "",
    ) -> ActionResult:
        self.safety_gate.allow(LocalAction("pause_current_media"))
        services = [preferred_service] if preferred_service else []
        services.extend(service for service in ["Spotify", "Music"] if service not in services)
        for service in services:
            script = f"""
try
  tell application "{service}"
    if it is running then
      if player state is playing then
        pause
        return "paused {service}"
      else if {str(toggle).lower()} then
        play
        return "started {service}"
      else
        return "{service} is not playing"
      end if
    end if
  end tell
end try
return ""
"""
            result = run_osascript(script, timeout=8)
            if result.ok and result.stdout:
                return ActionResult("pause_current_media", True, result.stdout)
            if not result.ok and result.stderr and service == preferred_service:
                continue
        return ActionResult(
            "pause_current_media",
            False,
            "I couldn't find controllable media in Spotify or Music.",
        )

    def spotify_next(self) -> ActionResult:
        self.safety_gate.allow(LocalAction("spotify_next"))
        result = run_osascript('tell application "Spotify" to next track', timeout=8)
        return ActionResult("spotify_next", result.ok, result.stderr or "skipped Spotify track")

    def spotify_previous(self) -> ActionResult:
        self.safety_gate.allow(LocalAction("spotify_previous"))
        result = run_osascript('tell application "Spotify" to previous track', timeout=8)
        return ActionResult("spotify_previous", result.ok, result.stderr or "went to previous Spotify track")

    def open_file(self, path: str) -> ActionResult:
        self.safety_gate.allow(LocalAction("open_file", {"path": path}, risk=RiskLevel.LOW_RISK))
        result = run_command(["open", path], timeout=10)
        return ActionResult("open_file", result.ok, result.stderr or result.stdout or f"opened {path}")

    def type_text(self, text: str) -> ActionResult:
        self.safety_gate.allow(
            LocalAction("type_text", {"text_preview": text[:80]}, f"type text: {text[:40]}")
        )
        script = (
            'tell application "System Events"\n'
            f"  keystroke {applescript_string(text)}\n"
            "end tell"
        )
        result = run_osascript(script, timeout=20)
        return ActionResult("type_text", result.ok, result.stderr or "typed text")

    def press_hotkey(self, key: str, modifiers: list[str] | None = None) -> ActionResult:
        modifiers = modifiers or []
        self.safety_gate.allow(
            LocalAction("press_hotkey", {"key": key, "modifiers": modifiers})
        )
        normalized_key = key.lower()
        modifier_parts = [MODIFIER_NAMES[m.lower()] for m in modifiers if m.lower() in MODIFIER_NAMES]
        using = ""
        if modifier_parts:
            using = " using {" + ", ".join(modifier_parts) + "}"
        if normalized_key in SPECIAL_KEY_CODES:
            script = (
                'tell application "System Events"\n'
                f"  key code {SPECIAL_KEY_CODES[normalized_key]}{using}\n"
                "end tell"
            )
        else:
            script = (
                'tell application "System Events"\n'
                f"  keystroke {applescript_string(key)}{using}\n"
                "end tell"
            )
        result = run_osascript(script, timeout=10)
        return ActionResult("press_hotkey", result.ok, result.stderr or "pressed hotkey")

    def click(self, x: int, y: int, button: str = "left") -> ActionResult:
        self.safety_gate.allow(LocalAction("click", {"x": x, "y": y, "button": button}))
        quartz = _load_quartz()
        if quartz is None:
            return ActionResult(
                "click",
                False,
                "PyObjC Quartz is not installed; run `uv sync` and grant Accessibility.",
            )
        button = button.lower()
        if button == "right":
            down = quartz.kCGEventRightMouseDown
            up = quartz.kCGEventRightMouseUp
            q_button = quartz.kCGMouseButtonRight
        else:
            down = quartz.kCGEventLeftMouseDown
            up = quartz.kCGEventLeftMouseUp
            q_button = quartz.kCGMouseButtonLeft
        point = _scale_point_for_main_display(quartz, x, y)
        for event_type in (down, up):
            event = quartz.CGEventCreateMouseEvent(None, event_type, point, q_button)
            quartz.CGEventPost(quartz.kCGHIDEventTap, event)
            time.sleep(0.05)
        return ActionResult("click", True, f"clicked {button} at ({x}, {y})")

    def scroll(self, dy: int, dx: int = 0) -> ActionResult:
        self.safety_gate.allow(LocalAction("scroll", {"dy": dy, "dx": dx}))
        quartz = _load_quartz()
        if quartz is None:
            return ActionResult(
                "scroll",
                False,
                "PyObjC Quartz is not installed; run `uv sync` and grant Accessibility.",
            )
        event = quartz.CGEventCreateScrollWheelEvent(None, 2, int(dy), int(dx))
        quartz.CGEventPost(quartz.kCGHIDEventTap, event)
        return ActionResult("scroll", True, f"scrolled dy={dy} dx={dx}")

    def spotlight_search(self, query: str, submit: bool = True) -> list[ActionResult]:
        self.safety_gate.allow(
            LocalAction("spotlight_search", {"query": query, "submit": submit})
        )
        results = [
            self.press_hotkey("space", ["command"]),
            self.wait(0.4),
            self.type_text(query),
        ]
        if submit:
            results.extend([self.wait(0.2), self.press_hotkey("return")])
        return results

    def find_file(self, query: str, max_results: int = 20) -> ActionResult:
        self.safety_gate.allow(
            LocalAction("find_file", {"query": query, "max_results": max_results})
        )
        if not shutil.which("mdfind"):
            return ActionResult("find_file", False, "mdfind is not available", [])
        result = run_command(["mdfind", query], timeout=15)
        paths = result.stdout.splitlines()[:max_results] if result.stdout else []
        return ActionResult(
            "find_file",
            result.ok,
            f"found {len(paths)} result(s)",
            paths,
        )


def _chrome_javascript_disabled(detail: str) -> bool:
    lowered = detail.lower()
    return "javascript through applescript is turned off" in lowered or "allow javascript from apple events" in lowered


def _human_browser_error(detail: str) -> str:
    if not detail:
        return ""
    if _chrome_javascript_disabled(detail):
        return "Chrome automation is off, so I’ll use screen clicks instead."
    cleaned = re.sub(r"^\d+:\d+:\s*execution error:\s*", "", detail).strip()
    cleaned = re.sub(r"\s*For more information: https?://\S+", "", cleaned).strip()
    cleaned = cleaned.replace("Google Chrome got an error: ", "")
    return cleaned or detail
