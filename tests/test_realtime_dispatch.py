from __future__ import annotations

import base64
from types import SimpleNamespace
import threading
import time

from iris.agent import AgentRunResult
from iris.voice import RealtimeSpeechSession


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.cancelled = False

    def run_tool(self, name: str, arguments: dict) -> AgentRunResult:
        self.calls.append((name, arguments))
        return AgentRunResult(True, f"ran {name}", None)

    def cancel_current(self, _reason: str) -> None:
        self.cancelled = True


class _FakeSafetyGate:
    def __init__(self) -> None:
        self.killed = False

    def kill(self) -> None:
        self.killed = True


class _FakeRouter:
    def __init__(self) -> None:
        self.agent_executor = _FakeExecutor()
        self.safety_gate = _FakeSafetyGate()


class _FakeOutput:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.aborted = False
        self.started = False

    def write(self, audio: bytes) -> None:
        self.writes.append(audio)

    def abort(self) -> None:
        self.aborted = True

    def start(self) -> None:
        self.started = True


def _bare_session() -> tuple[RealtimeSpeechSession, list[dict]]:
    from iris.voice_events import ConsoleSink, VoiceState

    session = object.__new__(RealtimeSpeechSession)
    session._sink = ConsoleSink()
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
        turn_buffer_enabled=True,
        turn_continuation_ms=1200,
        turn_max_wait_ms=2500,
        turn_min_words_for_immediate_response=4,
        live_vad_silence_ms=900,
        wake_words=("iris", "hey iris"),
    )
    session.router = _FakeRouter()
    sent: list[dict] = []
    session._send_json = lambda payload: sent.append(payload)  # type: ignore[method-assign]
    return session, sent


def test_parse_tool_arguments() -> None:
    parse = RealtimeSpeechSession._parse_tool_arguments
    assert parse({"a": 1}) == {"a": 1}
    assert parse('{"app_name": "Spotify"}') == {"app_name": "Spotify"}
    assert parse("not json") == {}
    assert parse("") == {}
    assert parse(None) == {}


def test_record_function_call_dedupes_by_call_id() -> None:
    session, _sent = _bare_session()
    session._record_function_call(
        {"name": "app_open", "call_id": "c1", "arguments": "{}"}
    )
    session._record_function_call(
        {"name": "app_open", "call_id": "c1", "arguments": "{}"}
    )
    session._record_function_call(
        {"name": "media_play", "call_id": "c2", "arguments": "{}"}
    )
    names = [c["name"] for c in session._pending_calls]
    assert names == ["app_open", "media_play"]


class _FakeShot:
    def data_url(self) -> str:
        return "data:image/png;base64,AAA"


class _FakeFrame:
    screenshot = _FakeShot()


class _FakeAwareness:
    def capture_now(self) -> _FakeFrame:
        return _FakeFrame()


def test_inject_screen_image_sends_image_message() -> None:
    session, sent = _bare_session()
    session._screen_awareness = _FakeAwareness()

    session._inject_screen_image("c9", {"question": "what's on screen?"})

    item_types = [
        p["item"]["type"] for p in sent if p.get("type") == "conversation.item.create"
    ]
    assert "function_call_output" in item_types

    messages = [p for p in sent if p["item"].get("type") == "message"]
    assert len(messages) == 1
    content = messages[0]["item"]["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["image_url"] == "data:image/png;base64,AAA"
    assert content[1] == {"type": "input_text", "text": "what's on screen?"}


def test_run_calls_executes_and_returns_output() -> None:
    session, sent = _bare_session()
    session._run_calls(
        [{"call_id": "c1", "name": "app_open", "arguments": '{"app_name": "Spotify"}'}]
    )

    assert session.router.agent_executor.calls == [
        ("app_open", {"app_name": "Spotify"})
    ]

    outputs = [p for p in sent if p.get("type") == "conversation.item.create"]
    assert len(outputs) == 1
    item = outputs[0]["item"]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "c1"
    assert item["output"] == "ran app_open"

    assert sent[-1]["type"] == "response.create"


def test_barge_in_truncates_active_audio_and_clears_pending_calls() -> None:
    session, sent = _bare_session()
    output = _FakeOutput()
    session._assistant_response_active = True
    session._last_response_started_at = time.monotonic() - 1.0
    session._current_assistant_item_id = "item_123"
    session._current_audio_started_at = time.monotonic() - 0.4
    session._current_audio_received_ms = 1000
    session._current_audio_played_ms = 300
    session._current_response_text_chunks = ["Once upon"]
    session._pending_calls = [{"call_id": "c1", "name": "app_open", "arguments": "{}"}]

    assert session._handle_barge_in_started(output) is True

    assert output.aborted is True
    assert output.started is True
    assert session._pending_calls == []
    assert session._has_interrupted_response_pending() is True
    truncate = sent[-1]
    assert truncate["type"] == "conversation.item.truncate"
    assert truncate["item_id"] == "item_123"
    assert truncate["content_index"] == 0
    assert 300 <= truncate["audio_end_ms"] <= 1000


def test_active_response_does_not_mute_input_when_barge_in_enabled() -> None:
    session, _sent = _bare_session()
    session._assistant_response_active = True

    assert session._input_should_be_muted() is False

    session.config.barge_in_enabled = False

    assert session._input_should_be_muted() is True


def test_echo_is_ignored_but_unrelated_barge_in_transcript_is_allowed() -> None:
    session, _sent = _bare_session()
    session._assistant_response_active = True
    session._current_response_text_chunks = ["Once upon a time there was a comet"]
    session._suppress_input_until = time.monotonic() + 1.0

    assert session._should_ignore_transcript("Once upon a time there was a comet")
    assert not session._should_ignore_transcript("Actually make it about Mars")


def test_revision_after_interruption_creates_response_with_revision_instructions() -> (
    None
):
    session, sent = _bare_session()
    session._interrupted_response_pending = True
    session._interrupted_response_at = time.monotonic()

    session._handle_transcript("Wait, make it about Mars instead")

    response = sent[-1]["response"]
    assert sent[-1]["type"] == "response.create"
    assert response["output_modalities"] == ["audio"]
    assert "revise the request" in response["instructions"]


def test_stop_after_interruption_still_cancels_automation() -> None:
    session, sent = _bare_session()
    session._interrupted_response_pending = True
    session._interrupted_response_at = time.monotonic()

    session._handle_transcript("stop")

    assert session.router.agent_executor.cancelled is True
    assert session.router.safety_gate.killed is True
    responses = [payload for payload in sent if payload["type"] == "response.create"]
    assert all(
        "revise the request" not in r["response"].get("instructions", "")
        for r in responses
    )


def test_audio_delta_tracks_played_duration_from_pcm16() -> None:
    session, _sent = _bare_session()
    output = _FakeOutput()
    one_second_pcm = b"\0" * (RealtimeSpeechSession.sample_rate * 2)

    session._handle_event(
        {
            "type": "response.audio.delta",
            "item_id": "item_456",
            "delta": base64.b64encode(one_second_pcm).decode("ascii"),
        },
        output,
    )

    assert output.writes == [one_second_pcm]
    assert session._current_assistant_item_id == "item_456"
    assert session._current_audio_received_ms == 1000
    assert session._current_audio_played_ms == 1000


def test_partial_voice_turn_buffers_without_response() -> None:
    session, sent = _bare_session()

    session._handle_transcript("Okay, so, um, I feel like there needs to be")

    assert sent == []
    assert session._turn_buffer_segments == [
        "Okay, so, um, I feel like there needs to be"
    ]
    session._clear_turn_buffer()


def test_voice_turn_continuation_flushes_as_one_response() -> None:
    session, sent = _bare_session()

    session._handle_transcript("There needs to be a")
    session._handle_transcript("better detection for Iris")

    assert sent[-1]["type"] == "response.create"
    assert "continuous turn" in sent[-1]["response"]["instructions"]
    assert session._turn_buffer_segments == []


def test_direct_voice_command_bypasses_turn_buffer() -> None:
    session, sent = _bare_session()

    session._handle_transcript("Could you open Apple Music")

    assert sent[-1]["type"] == "response.create"
    assert session._turn_buffer_segments == []


def test_voice_turn_flush_warns_model_when_still_partial() -> None:
    session, sent = _bare_session()

    session._handle_transcript("I feel like")
    session._flush_turn_buffer()

    assert sent[-1]["type"] == "response.create"
    assert "may be incomplete" in sent[-1]["response"]["instructions"]


def test_when_i_setup_clause_buffers_for_continuation() -> None:
    session, sent = _bare_session()

    session._handle_transcript("When I do the Iris start")

    assert sent == []
    assert session._turn_buffer_segments == ["When I do the Iris start"]
    session._clear_turn_buffer()


def test_meta_feedback_about_iris_gets_feedback_instructions() -> None:
    session, sent = _bare_session()

    session._handle_transcript("We still need better detection for Iris")

    assert sent[-1]["type"] == "response.create"
    instructions = sent[-1]["response"]["instructions"]
    assert "feedback about Iris" in instructions
    assert "do not rewrite their wording" in instructions


def test_voice_turn_buffer_emits_safe_observability_events() -> None:
    session, _sent = _bare_session()
    events: list[tuple[str, dict]] = []
    session._trace_voice_event = (  # type: ignore[method-assign]
        lambda event_name, *, status="ok", details=None: events.append(
            (event_name, details or {})
        )
    )

    session._handle_transcript("There needs to be a")
    session._handle_transcript("better detection for Iris")

    event_names = [name for name, _details in events]
    assert "voice.turn.segment_received" in event_names
    assert "voice.turn.endpoint_reason" in event_names
    assert "voice.turn.buffered" in event_names
    assert "voice.turn.flushed" in event_names
    for _name, details in events:
        assert "There needs" not in str(details)
        assert "better detection" not in str(details)


def test_voice_interruption_emits_safe_observability_event() -> None:
    session, _sent = _bare_session()
    output = _FakeOutput()
    events: list[tuple[str, dict]] = []
    session._trace_voice_event = (  # type: ignore[method-assign]
        lambda event_name, *, status="ok", details=None: events.append(
            (event_name, details or {})
        )
    )
    session._assistant_response_active = True
    session._last_response_started_at = time.monotonic() - 1.0
    session._current_assistant_item_id = "item_123"
    session._current_audio_started_at = time.monotonic() - 0.4
    session._current_audio_received_ms = 1000
    session._current_audio_played_ms = 300

    assert session._handle_barge_in_started(output) is True

    assert events[-1][0] == "voice.interrupt.detected"
    assert events[-1][1]["had_assistant_item"] is True
    assert "item_123" not in str(events[-1][1])
