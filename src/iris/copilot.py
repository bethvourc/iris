"""Proactive co-pilot: watches the screen and speaks up when it can help.

Cost and annoyance are the real risks, so this is bounded on every axis: it
only reacts when the screen actually changes, gates on cheap keyword signals
before spending a model call, throttles how often it interjects, and never
repeats the same suggestion. The model decides whether something is worth
mentioning; this just decides when to ask it.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
import time

_INTERESTING_MARKERS = (
    "error",
    "exception",
    "traceback",
    "failed",
    "failure",
    "cannot",
    "could not",
    "unable to",
    "denied",
    "not found",
    "undefined",
    "panic",
    "fatal",
    "warning",
    "timed out",
    "timeout",
    "refused",
    "403",
    "404",
    "500",
    "502",
    "stack trace",
)


def looks_interesting(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _INTERESTING_MARKERS)


class CoPilotService:
    def __init__(
        self,
        *,
        signature: Callable[[], str],
        read_text: Callable[[], str],
        advise: Callable[[str], str],
        announce: Callable[[str], bool],
        poll_seconds: float = 6.0,
        min_interval_seconds: float = 25.0,
    ) -> None:
        self.signature = signature
        self.read_text = read_text
        self.advise = advise
        self.announce = announce
        self.poll_seconds = max(2.0, poll_seconds)
        self.min_interval_seconds = min_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_signature = ""
        self._last_advice = ""
        self._last_interject = 0.0

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.active:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="iris-copilot", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                print(f"iris> co-pilot tick error: {exc}")
            self._stop.wait(self.poll_seconds)

    def tick(self, *, now: float | None = None) -> str | None:
        signature = self.signature()
        if not signature or signature == self._last_signature:
            return None
        self._last_signature = signature
        text = self.read_text()
        if not text or not looks_interesting(text):
            return None
        moment = now if now is not None else time.monotonic()
        if moment - self._last_interject < self.min_interval_seconds:
            return None
        advice = (self.advise(text) or "").strip()
        if not advice or advice.upper() == "NONE":
            return None
        if advice == self._last_advice:
            return None
        if self.announce(advice):
            self._last_advice = advice
            self._last_interject = moment
            return advice
        return None
