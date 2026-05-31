from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import base64
import struct
import tempfile
import threading
import time

from iris.system import run_command, run_osascript


@dataclass(frozen=True)
class Screenshot:
    png: bytes
    width: int | None
    height: int | None
    captured_at: datetime

    def data_url(self) -> str:
        encoded = base64.b64encode(self.png).decode("ascii")
        return f"data:image/png;base64,{encoded}"


@dataclass(frozen=True)
class ScreenContext:
    active_app: str | None
    active_window: str | None


@dataclass(frozen=True)
class LiveScreenFrame:
    screenshot: Screenshot
    context: ScreenContext


def _png_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


class PerceptionService:
    def capture_screen(self) -> Screenshot:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as handle:
            path = Path(handle.name)
            result = run_command(["screencapture", "-x", "-t", "png", str(path)], timeout=20)
            if not result.ok:
                detail = result.stderr or "screencapture failed"
                raise RuntimeError(
                    "Screen capture failed. Grant Screen Recording permission to "
                    "the app that launched Iris, then restart Iris. Usually this "
                    "is Terminal, iTerm, VS Code, or Codex. Run `./iris permissions` "
                    f"to verify. Detail: {detail}"
                )
            data = path.read_bytes()
        if not data:
            raise RuntimeError(
                "Screen capture returned an empty image. Grant Screen Recording "
                "permission to the app that launched Iris, then restart Iris."
            )
        width, height = _png_dimensions(data)
        return Screenshot(
            png=data,
            width=width,
            height=height,
            captured_at=datetime.now(timezone.utc),
        )

    def active_app(self) -> str | None:
        script = (
            'tell application "System Events"\n'
            '  get name of first application process whose frontmost is true\n'
            "end tell"
        )
        result = run_osascript(script, timeout=15)
        return result.stdout if result.ok and result.stdout else None

    def active_window_title(self) -> str | None:
        script = (
            'tell application "System Events"\n'
            '  tell first application process whose frontmost is true\n'
            "    if (count of windows) is greater than 0 then\n"
            "      get name of front window\n"
            "    end if\n"
            "  end tell\n"
            "end tell"
        )
        result = run_osascript(script, timeout=15)
        return result.stdout if result.ok and result.stdout else None

    def screen_context(self) -> ScreenContext:
        return ScreenContext(
            active_app=self.active_app(),
            active_window=self.active_window_title(),
        )


class ScreenAwarenessService:
    def __init__(self, perception: PerceptionService, interval_seconds: float = 1.0) -> None:
        self.perception = perception
        self.interval_seconds = max(0.5, interval_seconds)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._frame: LiveScreenFrame | None = None
        self._error: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="iris-screen-awareness",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)

    def latest(self) -> LiveScreenFrame | None:
        with self._lock:
            return self._frame

    def latest_error(self) -> str | None:
        with self._lock:
            return self._error

    def capture_now(self) -> LiveScreenFrame:
        frame = LiveScreenFrame(
            screenshot=self.perception.capture_screen(),
            context=self.perception.screen_context(),
        )
        with self._lock:
            self._frame = frame
            self._error = None
        return frame

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.capture_now()
            except Exception as exc:
                with self._lock:
                    self._error = str(exc)
            self._stop.wait(self.interval_seconds)
