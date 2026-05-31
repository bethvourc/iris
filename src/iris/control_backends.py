from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import shutil
import socket
from typing import Any

from iris.config import IrisConfig
from iris.system import run_osascript


@dataclass(frozen=True)
class ControlBackendStatus:
    backend_id: str
    name: str
    available: bool
    priority: int
    detail: str
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def control_backend_status(config: IrisConfig | None = None) -> list[dict[str, Any]]:
    statuses = [
        _screen_capture_status(),
        _accessibility_status(),
        _browser_cdp_status(),
        _browser_applescript_status(),
        _computer_use_status(config),
        _coordinate_status(),
    ]
    return [status.to_dict() for status in statuses]


def _screen_capture_status() -> ControlBackendStatus:
    available = bool(shutil.which("screencapture"))
    return ControlBackendStatus(
        "screen_capture",
        "Screen Capture",
        available,
        10,
        "screencapture is available" if available else "screencapture is missing",
    )


def _accessibility_status() -> ControlBackendStatus:
    if not shutil.which("osascript"):
        return ControlBackendStatus("accessibility", "Accessibility Tree", False, 20, "osascript is missing")
    result = run_osascript(
        """
try
  tell application "System Events" to return UI elements enabled
on error errMsg
  return "error:" & errMsg
end try
""",
        timeout=5,
    )
    enabled = result.ok and result.stdout.strip().lower() == "true"
    detail = "Accessibility is enabled" if enabled else (result.stdout or result.stderr or "Accessibility is not enabled")
    return ControlBackendStatus("accessibility", "Accessibility Tree", enabled, 20, detail)


def _browser_cdp_status() -> ControlBackendStatus:
    raw_url = os.getenv("IRIS_CHROME_CDP_URL", "http://127.0.0.1:9222")
    host = "127.0.0.1"
    port = 9222
    try:
        without_scheme = raw_url.split("://", 1)[-1]
        host_port = without_scheme.split("/", 1)[0]
        if ":" in host_port:
            host, port_text = host_port.rsplit(":", 1)
            port = int(port_text)
    except Exception:
        pass
    available = False
    try:
        with socket.create_connection((host, port), timeout=0.2):
            available = True
    except OSError:
        available = False
    detail = "Chrome DevTools Protocol is reachable" if available else "Chrome DevTools Protocol is not reachable"
    return ControlBackendStatus(
        "browser_cdp",
        "Browser CDP",
        available,
        30,
        detail,
        {"url": raw_url},
    )


def _browser_applescript_status() -> ControlBackendStatus:
    if not shutil.which("osascript"):
        return ControlBackendStatus("browser_applescript", "Browser AppleScript", False, 40, "osascript is missing")
    result = run_osascript(
        """
try
  tell application "Google Chrome"
    if it is running then return "running"
    return "installed"
  end tell
on error errMsg
  return "error:" & errMsg
end try
""",
        timeout=5,
    )
    text = result.stdout.strip()
    available = result.ok and not text.lower().startswith("error:")
    return ControlBackendStatus(
        "browser_applescript",
        "Browser AppleScript",
        available,
        40,
        text or result.stderr or "Google Chrome AppleScript unavailable",
    )


def _computer_use_status(config: IrisConfig | None) -> ControlBackendStatus:
    available = bool(config and config.openai_api_key and config.computer_use_model)
    detail = "OpenAI computer-use configured" if available else "OpenAI computer-use is not configured"
    return ControlBackendStatus("computer_use", "OpenAI Computer Use", available, 80, detail)


def _coordinate_status() -> ControlBackendStatus:
    available = bool(shutil.which("osascript"))
    detail = "Coordinate fallback available" if available else "Coordinate fallback unavailable"
    return ControlBackendStatus("coordinates", "Coordinate Fallback", available, 100, detail)
