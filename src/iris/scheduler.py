"""Background scheduler that makes Iris proactive.

It periodically runs enabled watch rules (web/file/command/screen/app) and, when
one changes, announces it — spoken through the live session if Iris is awake,
otherwise as a phone push. This is what turns "watch for X then tell me" into
something that actually fires without the user asking again.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
import time

from iris.config import IrisConfig
from iris.notifications import NotificationService
from iris.perception import PerceptionService
from iris.state import open_state
from iris.watchers import list_watches, run_watch_once


class SchedulerService:
    def __init__(
        self,
        config: IrisConfig,
        *,
        perception: PerceptionService | None = None,
        announce: Callable[[str], bool] | None = None,
        poll_seconds: float = 15.0,
    ) -> None:
        self.config = config
        self.perception = perception
        self.announce = announce
        self.poll_seconds = max(2.0, poll_seconds)
        self.notifier = NotificationService(config)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_due: dict[str, float] = {}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="iris-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                print(f"iris> scheduler tick error: {exc}")
            self._stop.wait(self.poll_seconds)

    def _tick(self) -> None:
        with open_state(self.config) as db:
            watches = list_watches(db)
        now = time.monotonic()
        for watch in watches:
            if not watch.get("enabled"):
                continue
            watch_id = str(watch.get("watch_id") or "")
            if not watch_id:
                continue
            interval = float(watch.get("interval_seconds") or 60.0)
            if now < self._next_due.get(watch_id, 0.0):
                continue
            self._next_due[watch_id] = now + max(interval, self.poll_seconds)
            try:
                with open_state(self.config) as db:
                    result = run_watch_once(db, watch_id, perception=self.perception)
            except Exception as exc:
                print(f"iris> watch '{watch.get('name') or watch_id}' failed: {exc}")
                continue
            if getattr(result, "changed", False):
                self._announce(str(watch.get("name") or watch_id), result.summary)

    def _announce(self, name: str, summary: str) -> None:
        spoken = f"Heads up: {name} changed. {summary}"
        if self.announce is not None:
            try:
                if self.announce(spoken):
                    return
            except Exception:
                pass
        self.notifier.send_phone_push(summary, title=f"Iris: {name}")
