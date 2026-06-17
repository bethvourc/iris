from __future__ import annotations

import threading
import time

import pytest

from iris.settings_store import _SPECS_BY_KEY
from iris.voice_controller import VoiceController
from iris.voice_events import VoiceEventType, VoiceState
from iris.wake_word import Detection, WakeWordListener


# --------------------------------------------------------------- helpers


class ScriptPredictor:
    """Returns scripted per-frame scores; records reset() calls."""

    def __init__(self, scores: list[dict[str, float]]) -> None:
        self.scores = scores
        self.calls = 0
        self.resets = 0

    def predict(self, frame: bytes) -> dict[str, float]:
        index = min(self.calls, len(self.scores) - 1)
        self.calls += 1
        return self.scores[index]

    def reset(self) -> None:
        self.resets += 1


def _endless(frame: bytes = b"\x00\x00", delay: float = 0.002):
    while True:
        yield frame
        time.sleep(delay)


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _collect(detections: list[Detection]):
    def on_detected(detection: Detection) -> None:
        detections.append(detection)

    return on_detected


# --------------------------------------------------------------- listener


def test_detection_above_threshold_fires_once_and_resets() -> None:
    detections: list[Detection] = []
    predictor = ScriptPredictor([{"hey_iris": 0.91}])
    listener = WakeWordListener(
        on_detected=_collect(detections),
        threshold=0.5,
        cooldown_seconds=10.0,
        predictor_factory=lambda: predictor,
        audio_source_factory=lambda: iter([b"a", b"b", b"c"]),
    )

    listener.start()
    assert _wait_until(lambda: not listener.is_running)

    # One utterance over three frames fires exactly once; reset() is called so
    # the same utterance can't re-trigger.
    assert len(detections) == 1
    assert detections[0].model == "hey_iris"
    assert detections[0].score == pytest.approx(0.91)
    assert predictor.resets == 1


def test_below_threshold_never_fires() -> None:
    detections: list[Detection] = []
    listener = WakeWordListener(
        on_detected=_collect(detections),
        threshold=0.5,
        predictor_factory=lambda: ScriptPredictor([{"hey_iris": 0.2}]),
        audio_source_factory=lambda: iter([b"a"] * 5),
    )

    listener.start()
    assert _wait_until(lambda: not listener.is_running)
    assert detections == []


def test_cooldown_allows_refire_when_zero() -> None:
    detections: list[Detection] = []
    listener = WakeWordListener(
        on_detected=_collect(detections),
        threshold=0.5,
        cooldown_seconds=0.0,
        predictor_factory=lambda: ScriptPredictor([{"hey_iris": 0.8}]),
        audio_source_factory=lambda: iter([b"a", b"b"]),
    )

    listener.start()
    assert _wait_until(lambda: not listener.is_running)
    # With no cooldown, every above-threshold frame fires.
    assert len(detections) == 2


def test_start_stop_lifecycle() -> None:
    listener = WakeWordListener(
        on_detected=_collect([]),
        threshold=0.99,
        predictor_factory=lambda: ScriptPredictor([{"hey_iris": 0.0}]),
        audio_source_factory=_endless,
    )

    listener.start()
    assert _wait_until(lambda: listener.is_running)
    listener.start()  # idempotent
    listener.stop()
    assert _wait_until(lambda: not listener.is_running)


def test_start_failure_reports_and_stays_stopped() -> None:
    errors: list[str] = []

    def boom():
        raise RuntimeError("no onnxruntime")

    listener = WakeWordListener(
        on_detected=_collect([]),
        predictor_factory=boom,
        audio_source_factory=lambda: iter([]),
        on_error=errors.append,
    )

    listener.start()
    assert _wait_until(lambda: bool(errors))
    assert "onnxruntime" in errors[0]
    assert listener.is_running is False


# -------------------------------------------------- controller integration


def _wake_factory(scores: list[dict[str, float]], **kwargs):
    def factory(on_detected):
        return WakeWordListener(
            on_detected=on_detected,
            threshold=0.5,
            predictor_factory=lambda: ScriptPredictor(scores),
            **kwargs,
        )

    return factory


def test_disabled_by_default() -> None:
    # The setting itself defaults off...
    assert _SPECS_BY_KEY["wake_word_enabled"].default is False
    # ...and a controller never told to enable it does not listen.
    controller = VoiceController(
        session_factory=lambda **_: None,
        wake_listener_factory=_wake_factory(
            [{"hey_iris": 0.9}], audio_source_factory=_endless
        ),
    )
    assert controller.wake_listening is False


def test_enable_without_factory_is_safe() -> None:
    controller = VoiceController(session_factory=lambda **_: None)
    controller.set_wake_word_enabled(True)
    assert controller.wake_listening is False


def test_enable_emits_wake_detected_event() -> None:
    controller = VoiceController(
        session_factory=lambda **_: None,
        wake_listener_factory=_wake_factory(
            [{"hey_iris": 0.95}],
            cooldown_seconds=10.0,
            audio_source_factory=lambda: iter([b"a", b"b"]),
        ),
    )
    subscription = controller.subscribe()
    controller.set_wake_word_enabled(True)

    event = subscription.events.get(timeout=2.0)
    assert event.type == VoiceEventType.WAKE_DETECTED
    assert event.data["model"] == "hey_iris"
    assert event.data["score"] == pytest.approx(0.95, abs=0.01)

    controller.set_wake_word_enabled(False)
    assert _wait_until(lambda: not controller.wake_listening)


class _BlockingSession:
    """A session that stays in run() until stop(), like the real one."""

    def __init__(self, event_sink) -> None:
        self.sink = event_sink
        self.state = VoiceState.LISTENING
        self._stop = threading.Event()

    def run(self) -> None:
        self._stop.wait()

    def stop(self) -> None:
        self._stop.set()

    def interrupt(self) -> bool:
        return True


def test_listener_paused_during_session_and_resumes_after() -> None:
    controller = VoiceController(
        session_factory=lambda **kwargs: _BlockingSession(kwargs["event_sink"]),
        wake_listener_factory=_wake_factory(
            [{"hey_iris": 0.0}], audio_source_factory=_endless
        ),
    )
    controller.set_wake_word_enabled(True)
    assert _wait_until(lambda: controller.wake_listening)

    # Starting a session releases the microphone (listener stops)...
    controller.start()
    assert _wait_until(lambda: not controller.wake_listening)

    # ...and ending it hands the mic back to the still-armed listener.
    controller.stop()
    assert _wait_until(lambda: controller.wake_listening)
    controller.set_wake_word_enabled(False)
