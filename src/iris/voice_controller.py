"""Voice session lifecycle owner for the gateway.

The gateway serves many threads (`ThreadingHTTPServer`); this controller is
the single place that enforces "at most one live voice session", fans events
out to SSE subscribers, and turns session crashes into terminal events
instead of silently dead threads. HTTP semantics for each error live in
docs/desktop/api-contract.md §3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import queue
import threading
import time
from typing import Any, Callable, Protocol
import uuid

from iris.voice_events import (
    VoiceEvent,
    VoiceEventType,
    VoiceState,
    error_event,
)
from iris.wake_word import Detection, DetectionCallback, WakeWordListener

_LOG = logging.getLogger("iris.voice_controller")

VALID_MODES = ("conversation",)
SUBSCRIBER_QUEUE_SIZE = 256

WakeListenerFactory = Callable[[DetectionCallback], WakeWordListener]
"""Called as factory(on_detected=...) to build a configured wake listener."""


class VoiceSessionLike(Protocol):
    state: str

    def run(self) -> None: ...

    def stop(self) -> None: ...

    def interrupt(self) -> bool: ...


SessionFactory = Callable[..., VoiceSessionLike]
"""Called as factory(event_sink=..., mode=...) to build a session."""


class VoiceControllerError(Exception):
    """Base class; `code` maps to the API error envelope."""

    code = "internal"


class UnknownModeError(VoiceControllerError):
    code = "invalid_request"


class VoiceAlreadyRunningError(VoiceControllerError):
    code = "voice_already_running"

    def __init__(self, session: dict[str, Any]) -> None:
        super().__init__("a voice session is already active")
        self.session = session


class VoiceNotRunningError(VoiceControllerError):
    code = "voice_not_running"

    def __init__(self) -> None:
        super().__init__("no voice session is running")


class VoiceUnavailableError(VoiceControllerError):
    code = "voice_unavailable"


@dataclass(frozen=True)
class SessionDescriptor:
    id: str
    mode: str
    started_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "mode": self.mode, "started_at": self.started_at}


@dataclass
class Subscription:
    id: int
    events: "queue.Queue[VoiceEvent]"
    dropped: int = field(default=0)


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class VoiceController:
    """Owns at most one live voice session and broadcasts its events."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        wake_listener_factory: WakeListenerFactory | None = None,
        modes: tuple[str, ...] = VALID_MODES,
        queue_size: int = SUBSCRIBER_QUEUE_SIZE,
    ) -> None:
        self._factory = session_factory
        self._wake_factory = wake_listener_factory
        self._modes = tuple(modes)
        self._queue_size = queue_size
        self._lock = threading.RLock()
        self._session: VoiceSessionLike | None = None
        self._thread: threading.Thread | None = None
        self._descriptor: SessionDescriptor | None = None
        self._session_ended_seen = False
        self._wake_enabled = False
        self._wake: WakeWordListener | None = None
        self._subs_lock = threading.Lock()
        self._subscriptions: dict[int, Subscription] = {}
        self._next_subscription_id = 1

    # ------------------------------------------------------------ sink

    def emit(self, event: VoiceEvent) -> None:
        """EventSink implementation: sessions emit here; we fan out.

        Never blocks the session thread: slow subscribers drop events
        (the SSE snapshot-on-connect contract makes drops recoverable).
        """
        if event.type == VoiceEventType.SESSION_ENDED:
            self._session_ended_seen = True
        with self._subs_lock:
            subscriptions = list(self._subscriptions.values())
        for subscription in subscriptions:
            try:
                subscription.events.put_nowait(event)
            except queue.Full:
                subscription.dropped += 1
                if subscription.dropped == 1:
                    _LOG.warning(
                        "voice.subscriber_slow",
                        extra={"subscriber_id": subscription.id},
                    )

    def subscribe(self) -> Subscription:
        with self._subs_lock:
            subscription = Subscription(
                id=self._next_subscription_id,
                events=queue.Queue(maxsize=self._queue_size),
            )
            self._next_subscription_id += 1
            self._subscriptions[subscription.id] = subscription
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._subs_lock:
            self._subscriptions.pop(subscription.id, None)

    @property
    def subscriber_count(self) -> int:
        with self._subs_lock:
            return len(self._subscriptions)

    # ------------------------------------------------------- lifecycle

    def start(self, mode: str = "conversation") -> dict[str, Any]:
        """Start a session; returns the descriptor for the 202 response."""
        if mode not in self._modes:
            raise UnknownModeError(f"unknown voice mode: {mode!r}")
        with self._lock:
            if self._session is not None and self._descriptor is not None:
                raise VoiceAlreadyRunningError(self._descriptor.to_dict())
            # The session takes sole use of the microphone; release the wake
            # loop for its duration (resumed in `_run_session`).
            self._stop_wake_locked()
            descriptor = SessionDescriptor(
                id=f"voice-{uuid.uuid4().hex}",
                mode=mode,
                started_at=_utc_now_iso(),
            )
            try:
                session = self._factory(event_sink=self, mode=mode)
            except Exception as exc:
                raise VoiceUnavailableError(str(exc)) from exc
            self._session = session
            self._descriptor = descriptor
            self._session_ended_seen = False
            self._thread = threading.Thread(
                target=self._run_session,
                args=(session, descriptor, time.monotonic()),
                name=f"iris-{descriptor.id}",
                daemon=True,
            )
            self._thread.start()
        _LOG.info(
            "voice.session_started",
            extra={"session_id": descriptor.id, "mode": mode},
        )
        return {**descriptor.to_dict(), "state": VoiceState.CONNECTING}

    def stop(self, timeout: float = 10.0) -> bool:
        """Stop the live session. Idempotent; returns whether one was running."""
        with self._lock:
            session = self._session
            thread = self._thread
        if session is None or thread is None:
            return False
        session.stop()
        thread.join(timeout)
        if thread.is_alive():
            _LOG.warning("voice.session_stop_timeout", extra={"timeout": timeout})
        return True

    def interrupt(self) -> bool:
        """Cut the in-flight assistant response; raises if no session."""
        with self._lock:
            session = self._session
        if session is None:
            raise VoiceNotRunningError()
        return bool(session.interrupt())

    # -------------------------------------------------------- wake word

    def set_wake_word_enabled(self, enabled: bool) -> None:
        """Turn the on-device wake listener on or off (idempotent).

        Honors the "never listen while in a session" rule: enabling during an
        active session arms the listener but leaves it stopped until the
        session ends, when `_run_session` resumes it.
        """
        with self._lock:
            self._wake_enabled = enabled and self._wake_factory is not None
            if not self._wake_enabled:
                self._stop_wake_locked()
            elif self._session is None:
                self._start_wake_locked()

    @property
    def wake_listening(self) -> bool:
        wake = self._wake
        return bool(self._wake_enabled and wake is not None and wake.is_running)

    def _start_wake_locked(self) -> None:
        """Build (once) and start the listener. Caller holds `_lock`."""
        if self._wake_factory is None:
            return
        if self._wake is None:
            self._wake = self._wake_factory(self._handle_wake_detected)
        self._wake.start()

    def _stop_wake_locked(self) -> None:
        """Stop the listener so the mic indicator clears. Caller holds `_lock`."""
        if self._wake is not None:
            self._wake.stop()

    def _handle_wake_detected(self, detection: Detection) -> None:
        """Listener callback: announce the wake so the app opens a session."""
        self.emit(
            VoiceEvent(
                VoiceEventType.WAKE_DETECTED,
                {"model": detection.model, "score": round(detection.score, 3)},
            )
        )

    def status(self) -> dict[str, Any]:
        """Snapshot for GET /voice/status and SSE connect."""
        with self._lock:
            session = self._session
            descriptor = self._descriptor
        if session is None or descriptor is None:
            return {
                "state": VoiceState.IDLE,
                "session": None,
                "subscribers": self.subscriber_count,
                "meeting_active": False,
                "wake_listening": self.wake_listening,
            }
        return {
            "state": getattr(session, "state", VoiceState.CONNECTING),
            "session": descriptor.to_dict(),
            "subscribers": self.subscriber_count,
            "meeting_active": bool(getattr(session, "_meeting_active", False)),
            "wake_listening": self.wake_listening,
        }

    # -------------------------------------------------------- internals

    def _run_session(
        self,
        session: VoiceSessionLike,
        descriptor: SessionDescriptor,
        started_monotonic: float,
    ) -> None:
        try:
            session.run()
        except Exception as exc:
            _LOG.exception(
                "voice.session_crashed", extra={"session_id": descriptor.id}
            )
            self.emit(error_event("voice_session_crashed", str(exc), terminal=True))
        finally:
            ended_cleanly = self._session_ended_seen
            with self._lock:
                self._session = None
                self._thread = None
                self._descriptor = None
                # Hand the microphone back to the wake loop if it's still armed.
                if self._wake_enabled:
                    self._start_wake_locked()
            if not ended_cleanly:
                # The session died before its own finally could report the
                # end (e.g. the websocket connect raised); subscribers must
                # still see a terminal event.
                self.emit(
                    VoiceEvent(
                        VoiceEventType.SESSION_ENDED,
                        {
                            "reason": "error",
                            "duration_seconds": round(
                                time.monotonic() - started_monotonic, 1
                            ),
                        },
                    )
                )
                self.emit(
                    VoiceEvent(
                        VoiceEventType.STATE,
                        {
                            "state": VoiceState.IDLE,
                            "meeting_active": False,
                            "via": "session_end",
                        },
                    )
                )
            _LOG.info(
                "voice.session_finished",
                extra={
                    "session_id": descriptor.id,
                    "ended_cleanly": ended_cleanly,
                },
            )
