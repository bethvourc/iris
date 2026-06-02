from __future__ import annotations

import threading

from iris.agent import AgentRunResult
from iris.voice import RealtimeSpeechSession


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def run_tool(self, name: str, arguments: dict) -> AgentRunResult:
        self.calls.append((name, arguments))
        return AgentRunResult(True, f"ran {name}", None)


class _FakeRouter:
    def __init__(self) -> None:
        self.agent_executor = _FakeExecutor()


def _bare_session() -> tuple[RealtimeSpeechSession, list[dict]]:
    session = object.__new__(RealtimeSpeechSession)
    session._tool_lock = threading.Lock()
    session._pending_calls = []
    session._response_state_lock = threading.Lock()
    session._assistant_response_active = False
    session._last_response_started_at = 0.0
    session._suppress_input_until = 0.0
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
    session._record_function_call({"name": "app_open", "call_id": "c1", "arguments": "{}"})
    session._record_function_call({"name": "app_open", "call_id": "c1", "arguments": "{}"})
    session._record_function_call({"name": "media_play", "call_id": "c2", "arguments": "{}"})
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

    assert session.router.agent_executor.calls == [("app_open", {"app_name": "Spotify"})]

    outputs = [p for p in sent if p.get("type") == "conversation.item.create"]
    assert len(outputs) == 1
    item = outputs[0]["item"]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "c1"
    assert item["output"] == "ran app_open"

    assert sent[-1]["type"] == "response.create"
