from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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
        detail = "Managed Chrome CDP is reachable." if reachable else "Managed Chrome CDP is not reachable."
        return ActionResult(
            "managed_chrome_status",
            reachable,
            detail,
            {
                "base_url": self.config.base_url,
                "host": self.config.host,
                "port": self.config.port,
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
        if self.is_reachable():
            return self.status()
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

    def launch(self) -> ActionResult:
        self.config.user_data_dir.mkdir(parents=True, exist_ok=True)
        args = [
            "open",
            "-na",
            self.config.app_name,
            "--args",
            f"--remote-debugging-port={self.config.port}",
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


def managed_chrome_status() -> dict[str, Any]:
    result = ManagedChrome().status()
    payload = result.payload if isinstance(result.payload, dict) else {}
    return {"available": result.ok, "detail": result.detail, **payload}
