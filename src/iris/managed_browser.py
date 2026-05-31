from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import socket
import time
from typing import Any
from urllib import request

from iris.mac_controller import ActionResult
from iris.system import run_command


@dataclass(frozen=True)
class ManagedBrowserConfig:
    host: str = "127.0.0.1"
    port: int = 9222
    user_data_dir: Path = Path.home() / ".iris" / "chrome-cdp"
    app_name: str = "Google Chrome"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def allowed_origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    @classmethod
    def from_env(cls) -> "ManagedBrowserConfig":
        raw_url = os.getenv("IRIS_CHROME_CDP_URL", "http://127.0.0.1:9222")
        host = "127.0.0.1"
        port = 9222
        try:
            host_port = raw_url.split("://", 1)[-1].split("/", 1)[0]
            if ":" in host_port:
                host, port_text = host_port.rsplit(":", 1)
                port = int(port_text)
        except Exception:
            pass
        return cls(
            host=host,
            port=port,
            user_data_dir=Path(os.getenv("IRIS_CHROME_CDP_PROFILE", str(Path.home() / ".iris" / "chrome-cdp"))).expanduser(),
            app_name=os.getenv("IRIS_CHROME_APP_NAME", "Google Chrome"),
        )


class ManagedChrome:
    def __init__(self, config: ManagedBrowserConfig | None = None) -> None:
        self.config = config or ManagedBrowserConfig.from_env()

    def status(self) -> ActionResult:
        reachable = self.is_reachable()
        websocket_ready = self._websocket_ready() if reachable else None
        ok = reachable and websocket_ready is not False
        if reachable and websocket_ready is False:
            detail = (
                "Managed Chrome CDP is reachable, but browser control is blocked by "
                "Chrome's remote origin policy. Quit the Iris-managed Chrome window, "
                "then run `./iris browser start-cdp`."
            )
        else:
            detail = "Managed Chrome CDP is reachable." if reachable else "Managed Chrome CDP is not reachable."
        return ActionResult(
            "managed_chrome_status",
            ok,
            detail,
            {
                "base_url": self.config.base_url,
                "host": self.config.host,
                "port": self.config.port,
                "allowed_origin": self.config.allowed_origin,
                "websocket_ready": websocket_ready,
                "profile": str(self.config.user_data_dir),
            },
        )

    def is_reachable(self, *, timeout: float = 0.25) -> bool:
        try:
            with socket.create_connection((self.config.host, self.config.port), timeout=timeout):
                return True
        except OSError:
            return False

    def ensure_running(self, *, wait_seconds: float = 8.0) -> ActionResult:
        current = self.status()
        if current.ok:
            return current
        if self.is_reachable() and isinstance(current.payload, dict) and current.payload.get("websocket_ready") is False:
            restarted = self.restart(wait_seconds=wait_seconds)
            if restarted.ok:
                return restarted
            return current
        if self.is_reachable():
            return current
        launch = self.launch()
        if not launch.ok:
            return launch
        deadline = time.monotonic() + max(0.5, wait_seconds)
        while time.monotonic() < deadline:
            if self._version_ready():
                return self.status()
            time.sleep(0.2)
        return ActionResult(
            "managed_chrome_ensure_running",
            False,
            "Chrome was launched, but CDP did not become reachable in time.",
            launch.payload,
        )

    def restart(self, *, wait_seconds: float = 8.0) -> ActionResult:
        stopped = self.stop()
        if not stopped.ok:
            return stopped
        time.sleep(0.5)
        launch = self.launch()
        if not launch.ok:
            return launch
        deadline = time.monotonic() + max(0.5, wait_seconds)
        while time.monotonic() < deadline:
            status = self.status()
            if status.ok:
                return ActionResult(
                    "managed_chrome_restart",
                    True,
                    "Restarted Iris-managed Chrome with CDP enabled.",
                    status.payload,
                )
            time.sleep(0.25)
        return ActionResult(
            "managed_chrome_restart",
            False,
            "Chrome relaunched, but CDP browser control did not become ready in time.",
            launch.payload,
        )

    def stop(self) -> ActionResult:
        pattern = f"user-data-dir={self.config.user_data_dir}"
        result = run_command(["pkill", "-f", pattern], timeout=5)
        ok = result.ok or result.returncode == 1
        detail = "Stopped Iris-managed Chrome." if result.ok else "No Iris-managed Chrome process was running."
        if not ok:
            detail = result.stderr or result.stdout or "Could not stop Iris-managed Chrome."
        return ActionResult(
            "managed_chrome_stop",
            ok,
            detail,
            {"profile": str(self.config.user_data_dir), "pattern": pattern},
        )

    def launch(self) -> ActionResult:
        self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
        args = [
            "open",
            "-na",
            self.config.app_name,
            "--args",
            f"--remote-debugging-port={self.config.port}",
            f"--remote-allow-origins={self.config.allowed_origin}",
            f"--user-data-dir={self.config.user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        result = run_command(args, timeout=12)
        return ActionResult(
            "managed_chrome_launch",
            result.ok,
            result.stderr or result.stdout or f"Launched {self.config.app_name} with CDP.",
            {
                "base_url": self.config.base_url,
                "allowed_origin": self.config.allowed_origin,
                "profile": str(self.config.user_data_dir),
                "args": args,
            },
        )

    def _version_ready(self) -> bool:
        try:
            with request.urlopen(f"{self.config.base_url}/json/version", timeout=0.5) as response:
                return response.status == 200
        except Exception:
            return False

    def _websocket_ready(self) -> bool | None:
        try:
            with request.urlopen(f"{self.config.base_url}/json/version", timeout=0.5) as response:
                data = json.loads(response.read().decode("utf-8"))
            ws_url = str(data.get("webSocketDebuggerUrl") or "")
            if not ws_url:
                return None
            import websocket  # type: ignore

            ws = websocket.create_connection(ws_url, timeout=0.75, origin=self.config.base_url)
            ws.close()
            return True
        except Exception as exc:
            detail = str(exc).lower()
            if "handshake status 403" in detail or "remote-allow-origins" in detail:
                return False
            return None


def managed_chrome_status() -> dict[str, Any]:
    result = ManagedChrome().status()
    payload = result.payload if isinstance(result.payload, dict) else {}
    return {"available": result.ok, "detail": result.detail, **payload}
