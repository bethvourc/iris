"""On-device wake-word listener (opt-in, default off).

When enabled, the daemon runs a low-cost local microphone loop that detects
*only* the wake phrase using openWakeWord (ONNX, fully on-device). On a
detection it invokes a callback; the gateway turns that into a `wake_detected`
event on `/voice/events`, and the app opens the overlay and starts the realtime
session (the 4.3 path). The OpenAI realtime socket is therefore opened only
*after* a local detection — idle audio never leaves the machine.

Two seams keep this testable without audio hardware or the ONNX runtime:
`predictor_factory` (frame -> per-model scores) and `audio_source_factory`
(an iterator of int16 PCM frames). The defaults build openWakeWord + a
`sounddevice` capture stream lazily, so importing this module never requires the
optional `iris[wakeword]` dependencies.

Privacy contract: frames are scored and discarded — never stored, never sent
anywhere. The mic loop runs only while the listener is started, so the macOS
microphone indicator honestly reflects when Iris is listening.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import queue
import threading
import time
from typing import Callable, Iterator, Mapping, Protocol

_LOG = logging.getLogger("iris.wake_word")

# openWakeWord operates on 16 kHz mono int16 audio in 80 ms (1280-sample)
# frames; these are the model's native expectations, not tunables.
SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1280

DEFAULT_THRESHOLD = 0.5
# After a detection, ignore further hits for this long so one "Hey Iris" fires
# exactly once (and never while the resulting session is being set up).
DEFAULT_COOLDOWN_SECONDS = 3.0


@dataclass(frozen=True)
class Detection:
    """A single wake-phrase hit above threshold."""

    model: str
    score: float


class Predictor(Protocol):
    """Scores one audio frame against each loaded wake-word model."""

    def predict(self, frame: bytes) -> Mapping[str, float]: ...

    def reset(self) -> None: ...


PredictorFactory = Callable[[], Predictor]
AudioSourceFactory = Callable[[], Iterator[bytes]]
DetectionCallback = Callable[[Detection], None]


class WakeWordListener:
    """A start/stop microphone loop that fires a callback on the wake phrase.

    Thread-safe and idempotent: `start()` while already running is a no-op, and
    `stop()` while stopped returns immediately. The listener owns no session
    state — the controller stops it for the duration of a voice session so the
    session has sole use of the microphone.
    """

    def __init__(
        self,
        *,
        on_detected: DetectionCallback,
        threshold: float = DEFAULT_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        predictor_factory: PredictorFactory | None = None,
        audio_source_factory: AudioSourceFactory | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._on_detected = on_detected
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._predictor_factory = predictor_factory or _default_predictor_factory()
        self._audio_source_factory = (
            audio_source_factory or _default_audio_source_factory()
        )
        self._on_error = on_error
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._running = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="iris-wake-word", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout)
        if thread.is_alive():
            _LOG.warning("wake_word.stop_timeout", extra={"timeout": timeout})

    # -------------------------------------------------------- internals

    def _run(self) -> None:
        try:
            predictor = self._predictor_factory()
            source = self._audio_source_factory()
        except Exception as exc:  # missing deps, no mic, model load failure
            _LOG.warning("wake_word.start_failed", extra={"detail": str(exc)})
            if self._on_error is not None:
                self._on_error(str(exc))
            return

        self._running.set()
        _LOG.info("wake_word.listening", extra={"threshold": self._threshold})
        last_fire = 0.0
        try:
            for frame in source:
                if self._stop.is_set():
                    break
                try:
                    scores = predictor.predict(frame)
                except Exception as exc:
                    _LOG.warning("wake_word.predict_failed", extra={"detail": str(exc)})
                    continue
                if not scores:
                    continue
                model, score = max(scores.items(), key=lambda item: item[1])
                now = time.monotonic()
                if score >= self._threshold and (now - last_fire) >= self._cooldown:
                    last_fire = now
                    # Clear model state so the same utterance can't re-trigger
                    # on the next frame once the cooldown elapses.
                    predictor.reset()
                    _LOG.info(
                        "wake_word.detected",
                        extra={"model": model, "score": round(float(score), 3)},
                    )
                    try:
                        self._on_detected(Detection(model=model, score=float(score)))
                    except Exception:
                        _LOG.exception("wake_word.callback_failed")
        finally:
            self._running.clear()
            _close(source)
            _LOG.info("wake_word.stopped")


# ---------------------------------------------------------------- defaults


def _default_predictor_factory() -> PredictorFactory:
    """Build the real openWakeWord-backed predictor, lazily and per-thread."""

    def factory() -> Predictor:
        return _OpenWakeWordPredictor()

    return factory


def _default_audio_source_factory() -> AudioSourceFactory:
    """Capture 16 kHz mono int16 frames from the default input device."""

    def factory() -> Iterator[bytes]:
        return _microphone_frames()

    return factory


class _OpenWakeWordPredictor:
    """Adapter over `openwakeword.Model`; converts frames to int16 arrays."""

    def __init__(self) -> None:
        import numpy  # noqa: F401  (validate availability early)
        from openwakeword.model import Model  # type: ignore

        self._np = numpy
        self._model = Model()

    def predict(self, frame: bytes) -> Mapping[str, float]:
        array = self._np.frombuffer(frame, dtype=self._np.int16)
        return self._model.predict(array)

    def reset(self) -> None:
        self._model.reset()


def _microphone_frames() -> Iterator[bytes]:
    """Yield fixed-size int16 frames from the microphone until the stream ends.

    Uses a bounded queue so a slow consumer drops audio rather than growing
    memory — for wake detection, dropping stale audio is correct.
    """
    import sounddevice  # type: ignore

    from iris.voice import _select_input_device

    frames: "queue.Queue[bytes]" = queue.Queue(maxsize=32)
    device = _select_input_device(sounddevice)

    def callback(indata, _frames, _time, status) -> None:  # noqa: ANN001
        if status:
            _LOG.debug("wake_word.audio_status", extra={"status": str(status)})
        try:
            frames.put_nowait(bytes(indata))
        except queue.Full:
            pass

    stream = sounddevice.RawInputStream(
        samplerate=SAMPLE_RATE,
        device=device.index,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        callback=callback,
    )
    stream.start()
    try:
        while True:
            try:
                yield frames.get(timeout=1.0)
            except queue.Empty:
                continue
    finally:
        stream.stop()
        stream.close()


def _close(source: Iterator[bytes]) -> None:
    close = getattr(source, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            _LOG.debug("wake_word.source_close_failed", exc_info=True)
