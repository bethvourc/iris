from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from iris.voice import RealtimeSpeechSession
from iris.voice_controller import (
    Subscription,
    UnknownModeError,
    VoiceAlreadyRunningError,
    VoiceController,
    VoiceNotRunningError,
    VoiceUnavailableError,
)
from iris.voice_events import VoiceEvent, VoiceEventType, VoiceState


class FakeSession:
    """Blocks in run() until stop(); emits like the real session would."""

    def __init__(
        self,
        event_sink,
        *,
        crash: Exception | None = None,
        emit_session_ended: bool = True,
        interrupt_result: bool = True,
    ) -> None:
        self.sink = event_sink
        self.state = VoiceState.IDLE
        self.crash = crash
        self.emit_session_ended = emit_session_ended
        self.interrupt_result = interrupt_result
        self.interrupts = 0
        self._stop = threading.Event()
        self.running = threading.Event()

    def run(self) -> None:
        if self.crash is not None:
            raise self.crash
        self.state = VoiceState.LISTENING
        self.sink.emit(
            VoiceEvent(VoiceEventType.STATE, {"state": VoiceState.LISTENING})
        )
        self.running.set()
        self._stop.wait(timeout=10)
        if self.emit_session_ended:
            self.sink.emit(
                VoiceEvent(VoiceEventType.SESSION_ENDED, {"reason": "stopped"})
            )
            self.sink.emit(
                VoiceEvent(VoiceEventType.STATE, {"state": VoiceState.IDLE})
            )

    def stop(self) -> None:
        self._stop.set()

    def interrupt(self) -> bool:
        self.interrupts += 1
        return self.interrupt_result


def _controller(**session_kwargs) -> tuple[VoiceController, list[FakeSession]]:
    built: list[FakeSession] = []

    def factory(*, event_sink, mode):  # noqa: ANN001, ARG001
        session = FakeSession(event_sink, **session_kwargs)
        built.append(session)
        return session

    return VoiceController(session_factory=factory), built


def _drain(subscription: Subscription, count: int, timeout: float = 2.0) -> list[VoiceEvent]:
    events = []
    for _ in range(count):
        events.append(subscription.events.get(timeout=timeout))
    return events


def test_start_returns_connecting_descriptor() -> None:
    controller, sessions = _controller()
    descriptor = controller.start()
    try:
        assert descriptor["state"] == VoiceState.CONNECTING
        assert descriptor["mode"] == "conversation"
        assert descriptor["id"].startswith("voice-")
        assert descriptor["started_at"].endswith("Z")
        assert sessions[0].running.wait(timeout=2)
    finally:
        controller.stop()


def test_double_start_raises_with_live_session_descriptor() -> None:
    controller, sessions = _controller()
    descriptor = controller.start()
    sessions[0].running.wait(timeout=2)
    try:
        with pytest.raises(VoiceAlreadyRunningError) as excinfo:
            controller.start()
        assert excinfo.value.session["id"] == descriptor["id"]
        assert excinfo.value.code == "voice_already_running"
    finally:
        controller.stop()


def test_stop_is_idempotent_and_leaves_no_threads() -> None:
    controller, sessions = _controller()
    controller.start()
    sessions[0].running.wait(timeout=2)
    thread = controller._thread

    assert controller.stop() is True
    assert controller.stop() is False
    assert thread is not None and not thread.is_alive()
    assert controller.status()["state"] == VoiceState.IDLE
    assert controller.status()["session"] is None


def test_clean_stop_does_not_duplicate_session_ended() -> None:
    controller, sessions = _controller()
    subscription = controller.subscribe()
    controller.start()
    sessions[0].running.wait(timeout=2)
    controller.stop()
    # listening state, session_ended, idle state — and nothing more
    events = _drain(subscription, 3)
    types = [event.type for event in events]
    assert types.count(VoiceEventType.SESSION_ENDED) == 1
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        assert subscription.events.qsize() == 0
        time.sleep(0.05)


def test_session_crash_emits_terminal_error_and_session_ended() -> None:
    controller, _ = _controller(crash=RuntimeError("ws connect failed"))
    subscription = controller.subscribe()
    controller.start()

    events = _drain(subscription, 3)
    assert events[0].type == VoiceEventType.ERROR
    assert events[0].data["code"] == "voice_session_crashed"
    assert events[0].data["terminal"] is True
    assert "ws connect failed" in events[0].data["message"]
    assert events[1].type == VoiceEventType.SESSION_ENDED
    assert events[1].data["reason"] == "error"
    assert events[2].type == VoiceEventType.STATE
    assert events[2].data["state"] == VoiceState.IDLE

    # controller recovered to a startable state
    deadline = time.monotonic() + 2
    while controller.status()["state"] != VoiceState.IDLE:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert controller.status()["session"] is None


def test_unknown_mode_is_rejected_before_any_session_is_built() -> None:
    controller, sessions = _controller()
    with pytest.raises(UnknownModeError):
        controller.start(mode="karaoke")
    assert sessions == []
    assert controller.status()["state"] == VoiceState.IDLE


def test_factory_failure_maps_to_voice_unavailable() -> None:
    def factory(*, event_sink, mode):  # noqa: ANN001, ARG001
        raise RuntimeError("OPENAI_API_KEY is required")

    controller = VoiceController(session_factory=factory)
    with pytest.raises(VoiceUnavailableError) as excinfo:
        controller.start()
    assert "OPENAI_API_KEY" in str(excinfo.value)
    assert controller.status()["state"] == VoiceState.IDLE
    assert controller._session is None  # nothing half-attached


def test_interrupt_requires_running_session() -> None:
    controller, _ = _controller()
    with pytest.raises(VoiceNotRunningError):
        controller.interrupt()


def test_interrupt_delegates_to_session() -> None:
    controller, sessions = _controller(interrupt_result=False)
    controller.start()
    sessions[0].running.wait(timeout=2)
    try:
        assert controller.interrupt() is False
        assert sessions[0].interrupts == 1
    finally:
        controller.stop()


def test_status_reflects_running_session_and_subscribers() -> None:
    controller, sessions = _controller()
    subscription = controller.subscribe()
    controller.start()
    sessions[0].running.wait(timeout=2)
    try:
        status = controller.status()
        assert status["state"] == VoiceState.LISTENING
        assert status["session"]["mode"] == "conversation"
        assert status["subscribers"] == 1
        assert status["meeting_active"] is False
    finally:
        controller.stop()
        controller.unsubscribe(subscription)


def test_unsubscribed_queue_receives_nothing() -> None:
    controller, _ = _controller()
    subscription = controller.subscribe()
    controller.unsubscribe(subscription)
    controller.emit(VoiceEvent(VoiceEventType.LOG, {"text": "x"}))
    assert subscription.events.qsize() == 0
    assert controller.subscriber_count == 0


def test_slow_subscriber_drops_events_without_blocking() -> None:
    def factory(*, event_sink, mode):  # noqa: ANN001, ARG001
        raise AssertionError("not used")

    controller = VoiceController(session_factory=factory, queue_size=1)
    subscription = controller.subscribe()
    healthy = controller.subscribe()

    started = time.monotonic()
    for index in range(5):
        controller.emit(VoiceEvent(VoiceEventType.LOG, {"text": str(index)}))
    assert time.monotonic() - started < 0.5  # never blocked

    assert subscription.events.qsize() == 1
    assert subscription.dropped == 4
    # the slow subscriber must not affect healthy ones... bounded by the same
    # queue size here, so it also keeps only one — but its dropped counter
    # proves delivery was attempted independently.
    assert healthy.dropped == 4


# --- RealtimeSpeechSession.interrupt(): the session half of the contract ---


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[VoiceEvent] = []

    def emit(self, event: VoiceEvent) -> None:
        self.events.append(event)


def _bare_session_with_active_response() -> tuple[RealtimeSpeechSession, list[dict], _RecordingSink]:
    sink = _RecordingSink()
    session = object.__new__(RealtimeSpeechSession)
    session._sink = sink
    session.state = VoiceState.SPEAKING
    session._end_reason = "stopped"
    session._tool_lock = threading.Lock()
    session._pending_calls = [{"name": "app_open", "call_id": "c1"}]
    session._response_state_lock = threading.Lock()
    session._assistant_response_active = True
    session._last_response_started_at = time.monotonic()
    session._suppress_input_until = 0.0
    session._pending_response_text = "queued"
    session._last_spoken_text = ""
    session._last_printed_response_text = ""
    session._current_assistant_item_id = "item-1"
    session._current_audio_started_at = 0.0
    session._current_audio_received_ms = 900
    session._current_audio_played_ms = 500
    session._current_response_text_chunks = ["partial ", "answer"]
    session._interrupted_response_pending = False
    session._interrupted_response_at = 0.0
    session._meeting_active = False
    session._output = None
    session.config = SimpleNamespace()
    sent: list[dict] = []
    session._send_json = lambda payload: sent.append(payload)  # type: ignore[method-assign]
    return session, sent, sink


def test_session_interrupt_cancels_active_response() -> None:
    session, sent, sink = _bare_session_with_active_response()

    assert session.interrupt() is True

    sent_types = [payload["type"] for payload in sent]
    assert "response.cancel" in sent_types
    assert "conversation.item.truncate" in sent_types
    truncate = next(p for p in sent if p["type"] == "conversation.item.truncate")
    assert truncate["item_id"] == "item-1"
    assert truncate["audio_end_ms"] == 500

    assert session._assistant_response_active is False
    assert session._pending_response_text is None
    assert session._pending_calls == []
    assert session._interrupted_response_pending is True
    assert session._last_spoken_text == "partial answer"

    event_types = [event.type for event in sink.events]
    assert event_types == [VoiceEventType.INTERRUPTED, VoiceEventType.STATE]
    assert sink.events[0].data == {"at_ms": 500, "via": "request"}
    assert sink.events[1].data["state"] == VoiceState.LISTENING
    assert session.state == VoiceState.LISTENING


def test_session_interrupt_without_active_response_is_a_no_op() -> None:
    session, sent, sink = _bare_session_with_active_response()
    session._assistant_response_active = False

    assert session.interrupt() is False
    assert sent == []
    assert sink.events == []
