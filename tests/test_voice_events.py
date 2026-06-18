from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from iris.voice import RealtimeSpeechSession
from iris.voice_events import (
    ConsoleSink,
    VoiceEvent,
    VoiceEventType,
    VoiceState,
    error_event,
    log_event,
)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[VoiceEvent] = []

    def emit(self, event: VoiceEvent) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [event.type for event in self.events]


def _session(sink: RecordingSink) -> RealtimeSpeechSession:
    """Minimal session, mirroring tests/test_realtime_dispatch.py."""
    session = object.__new__(RealtimeSpeechSession)
    session._sink = sink
    session.state = VoiceState.IDLE
    session._end_reason = "stopped"
    session._tool_lock = threading.Lock()
    session._pending_calls = []
    session._response_state_lock = threading.Lock()
    session._assistant_response_active = False
    session._last_response_started_at = 0.0
    session._suppress_input_until = 0.0
    session._pending_response_text = None
    session._last_spoken_text = ""
    session._last_printed_response_text = ""
    session._current_assistant_item_id = ""
    session._current_audio_started_at = 0.0
    session._current_audio_received_ms = 0
    session._current_audio_played_ms = 0
    session._current_response_text_chunks = []
    session._interrupted_response_pending = False
    session._interrupted_response_at = 0.0
    session._turn_buffer_lock = threading.Lock()
    session._turn_buffer_segments = []
    session._turn_buffer_timer = None
    session._turn_buffer_started_at = 0.0
    session._turn_buffer_last_at = 0.0
    session._turn_buffer_interrupted_response = False
    session.awake = True
    session.wake_gated = True
    session._meeting_active = False
    session.config = SimpleNamespace(
        barge_in_enabled=True,
        barge_in_grace_ms=0,
        echo_suppression_ms=800,
        turn_buffer_enabled=False,
        turn_continuation_ms=1200,
        turn_max_wait_ms=2500,
        turn_min_words_for_immediate_response=4,
        live_vad_silence_ms=900,
        wake_words=("iris", "hey iris"),
    )
    session._send_json = lambda payload: None  # type: ignore[method-assign]
    return session


def test_voice_event_to_dict_shape() -> None:
    event = VoiceEvent(VoiceEventType.USER_TRANSCRIPT, {"text": "hi", "final": True})
    payload = event.to_dict()

    assert payload["type"] == "user_transcript"
    assert payload["data"] == {"text": "hi", "final": True}
    assert payload["ts"].endswith("Z")


def test_session_ready_emits_listening_state(capsys: pytest.CaptureFixture) -> None:
    sink = RecordingSink()
    session = _session(sink)

    session._handle_event({"type": "session.updated"}, output=None)

    assert sink.events[-1].type == VoiceEventType.STATE
    assert sink.events[-1].data["state"] == VoiceState.LISTENING
    assert sink.events[-1].data["via"] == "session_ready"
    assert session.state == VoiceState.LISTENING


def test_response_created_emits_speaking_state() -> None:
    sink = RecordingSink()
    session = _session(sink)

    session._handle_event({"type": "response.created"}, output=None)

    assert sink.events[-1].data["state"] == VoiceState.SPEAKING
    assert session._assistant_response_active is True


def test_assistant_delta_and_done_emit_text() -> None:
    sink = RecordingSink()
    session = _session(sink)

    session._handle_event(
        {"type": "response.output_text.delta", "delta": "Hello "}, output=None
    )
    session._handle_event(
        {"type": "response.output_text.done", "text": "Hello there."}, output=None
    )

    assert sink.events[0].type == VoiceEventType.ASSISTANT_DELTA
    assert sink.events[0].data["text"] == "Hello "
    assert sink.events[1].type == VoiceEventType.ASSISTANT_DONE
    assert sink.events[1].data["text"] == "Hello there."


def test_barge_in_emits_interrupted_then_user_speaking() -> None:
    sink = RecordingSink()
    session = _session(sink)
    session._current_audio_played_ms = 1234
    session._handle_barge_in_started = lambda output: True  # type: ignore[method-assign]

    session._handle_event(
        {"type": "input_audio_buffer.speech_started"}, output=None
    )

    assert sink.types() == [VoiceEventType.INTERRUPTED, VoiceEventType.STATE]
    assert sink.events[0].data["at_ms"] == 1234
    assert sink.events[1].data == {
        "state": VoiceState.USER_SPEAKING,
        "meeting_active": False,
        "via": "barge_in",
    }


def test_plain_speech_emits_user_speaking_without_interrupted() -> None:
    sink = RecordingSink()
    session = _session(sink)

    session._handle_event(
        {"type": "input_audio_buffer.speech_started"}, output=None
    )
    session._handle_event(
        {"type": "input_audio_buffer.speech_stopped"}, output=None
    )

    assert sink.types() == [VoiceEventType.STATE, VoiceEventType.STATE]
    assert sink.events[0].data["via"] == "speech"
    assert sink.events[1].data["state"] == VoiceState.TRANSCRIBING


def test_response_done_returns_to_listening() -> None:
    sink = RecordingSink()
    session = _session(sink)
    session._handle_event({"type": "response.created"}, output=None)

    session._handle_event({"type": "response.done"}, output=None)

    assert sink.events[-1].data["state"] == VoiceState.LISTENING
    assert sink.events[-1].data["via"] == "response_done"
    assert session._assistant_response_active is False


def test_realtime_error_event_is_emitted_with_code() -> None:
    sink = RecordingSink()
    session = _session(sink)

    session._handle_event(
        {"type": "error", "error": {"code": "rate_limited", "message": "slow down"}},
        output=None,
    )

    assert sink.events[-1].type == VoiceEventType.ERROR
    assert sink.events[-1].data["code"] == "rate_limited"
    assert sink.events[-1].data["terminal"] is False


def test_transcript_emits_user_transcript_and_wake_detected() -> None:
    sink = RecordingSink()
    session = _session(sink)
    session.awake = False
    # _trace_voice_event opens state; the bare config makes it raise, which
    # the production code swallows by design.

    session._handle_transcript("Iris what time is it")

    types = sink.types()
    assert VoiceEventType.USER_TRANSCRIPT in types
    assert VoiceEventType.WAKE_DETECTED in types
    transcript = next(
        e for e in sink.events if e.type == VoiceEventType.USER_TRANSCRIPT
    )
    assert transcript.data == {"text": "Iris what time is it", "final": True}
    wake = next(e for e in sink.events if e.type == VoiceEventType.WAKE_DETECTED)
    assert wake.data["source"] == "in_session"
    assert session.awake is True


def test_sink_failure_does_not_break_the_session() -> None:
    class ExplodingSink:
        def emit(self, event: VoiceEvent) -> None:
            raise RuntimeError("sink broken")

    session = _session(RecordingSink())
    session._sink = ExplodingSink()

    session._handle_event({"type": "session.updated"}, output=None)

    assert session.state == VoiceState.LISTENING


# --- ConsoleSink: output must be byte-identical to the historical prints ---


def _console_output(capsys: pytest.CaptureFixture, event: VoiceEvent) -> str:
    ConsoleSink().emit(event)
    return capsys.readouterr().out


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            VoiceEvent(
                VoiceEventType.STATE,
                {"state": VoiceState.CONNECTING, "model": "gpt-realtime"},
            ),
            "iris> connecting realtime model gpt-realtime...\n",
        ),
        (
            VoiceEvent(
                VoiceEventType.STATE,
                {"state": VoiceState.LISTENING, "via": "session_ready"},
            ),
            "iris> session ready\n",
        ),
        (
            VoiceEvent(
                VoiceEventType.STATE,
                {"state": VoiceState.USER_SPEAKING, "via": "speech"},
            ),
            "iris> heard speech...\n",
        ),
        (
            VoiceEvent(VoiceEventType.STATE, {"state": VoiceState.TRANSCRIBING}),
            "iris> speech stopped, waiting for transcript...\n",
        ),
        (
            VoiceEvent(
                VoiceEventType.STATE,
                {"state": VoiceState.THINKING, "via": "agent"},
            ),
            "iris> working...\n",
        ),
        (
            VoiceEvent(VoiceEventType.INTERRUPTED, {"at_ms": 10}),
            "iris> interrupted, listening...\n",
        ),
        (
            VoiceEvent(VoiceEventType.USER_TRANSCRIPT, {"text": "hello"}),
            "you> hello\n",
        ),
        (
            VoiceEvent(VoiceEventType.ASSISTANT_DONE, {"text": "Hi."}),
            "iris> Hi.\n",
        ),
        (
            VoiceEvent(
                VoiceEventType.TOOL_CALL, {"name": "app_open", "status": "started"}
            ),
            "iris> · app_open\n",
        ),
        (
            VoiceEvent(
                VoiceEventType.TOOL_CALL,
                {"name": "app_open", "status": "failed", "detail": "boom"},
            ),
            "iris error> boom\n",
        ),
        (
            VoiceEvent(VoiceEventType.MEETING, {"active": True, "meeting_id": "m1"}),
            "iris> meeting mode on (silent; say 'stop the meeting' when done)\n",
        ),
        (
            error_event("audio_warning", "input overflow"),
            "audio warning: input overflow\n",
        ),
        (
            error_event("meeting_email_failed", "smtp down"),
            "iris> meeting email send failed: smtp down\n",
        ),
        (
            error_event("realtime_receive_failed", "realtime receive failed: eof"),
            "iris error> realtime receive failed: eof\n",
        ),
        (
            VoiceEvent(
                VoiceEventType.AGENT_RUN,
                {"status": "failed", "summary": "no", "detail": "agent exploded"},
            ),
            "iris error> agent exploded\n",
        ),
        (log_event("iris> websocket connected"), "iris> websocket connected\n"),
    ],
)
def test_console_sink_matches_historical_output(
    capsys: pytest.CaptureFixture, event: VoiceEvent, expected: str
) -> None:
    assert _console_output(capsys, event) == expected


@pytest.mark.parametrize(
    "event",
    [
        VoiceEvent(VoiceEventType.STATE, {"state": VoiceState.SPEAKING}),
        VoiceEvent(
            VoiceEventType.STATE,
            {"state": VoiceState.USER_SPEAKING, "via": "barge_in"},
        ),
        VoiceEvent(
            VoiceEventType.STATE,
            {"state": VoiceState.LISTENING, "via": "response_done"},
        ),
        VoiceEvent(VoiceEventType.STATE, {"state": VoiceState.IDLE}),
        VoiceEvent(VoiceEventType.ASSISTANT_DELTA, {"text": "chunk"}),
        VoiceEvent(VoiceEventType.WAKE_DETECTED, {"source": "in_session"}),
        VoiceEvent(VoiceEventType.SESSION_ENDED, {"reason": "stopped"}),
        VoiceEvent(VoiceEventType.MEETING, {"active": False}),
        VoiceEvent(VoiceEventType.TOOL_CALL, {"name": "x", "status": "done"}),
        VoiceEvent(VoiceEventType.AGENT_RUN, {"status": "done", "summary": "ok"}),
    ],
)
def test_console_sink_is_silent_for_wire_only_events(
    capsys: pytest.CaptureFixture, event: VoiceEvent
) -> None:
    assert _console_output(capsys, event) == ""
