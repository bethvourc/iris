from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import iris.gateway
from iris.gateway import GatewayService
from iris.voice_controller import VoiceController
from iris.voice_events import VoiceEvent, VoiceEventType, VoiceState

TOKEN = "voice-test-token"


class FakeSession:
    def __init__(self, event_sink, **_: object) -> None:
        self.sink = event_sink
        self.state = VoiceState.CONNECTING
        self._stop = threading.Event()
        self.running = threading.Event()
        self.interrupt_result = True

    def run(self) -> None:
        self.state = VoiceState.LISTENING
        self.running.set()
        self._stop.wait(timeout=10)
        self.sink.emit(
            VoiceEvent(VoiceEventType.SESSION_ENDED, {"reason": "stopped"})
        )

    def stop(self) -> None:
        self._stop.set()

    def interrupt(self) -> bool:
        return self.interrupt_result


def _service(
    *, factory=None, token: str | None = TOKEN
) -> tuple[GatewayService, list[FakeSession]]:
    sessions: list[FakeSession] = []

    def default_factory(*, event_sink, mode):  # noqa: ANN001, ARG001
        session = FakeSession(event_sink)
        sessions.append(session)
        return session

    service = GatewayService(
        config=SimpleNamespace(gateway_token=token, agent_name="Iris"),
        router_factory=lambda: None,
    )
    service._voice_controller = VoiceController(
        session_factory=factory or default_factory
    )
    return service, sessions


# ----------------------------------------------------------------- auth


@pytest.mark.parametrize(
    "path",
    ["/voice/start", "/voice/stop", "/voice/interrupt", "/voice/status", "/voice/events"],
)
def test_every_voice_route_requires_bearer_token(path: str) -> None:
    service, _ = _service()
    assert service.authorize(path, None) is False
    assert service.authorize(path, "Bearer wrong") is False
    assert service.authorize(path, f"Bearer {TOKEN}") is True


# ------------------------------------------------------------ start/stop


def test_voice_start_returns_202_with_session_descriptor() -> None:
    service, sessions = _service()
    status, payload = service.handle_post("/voice/start", {})
    try:
        assert status == 202
        assert payload["session"]["state"] == "connecting"
        assert payload["session"]["mode"] == "conversation"
        assert payload["session"]["id"].startswith("voice-")
        assert sessions[0].running.wait(timeout=2)
    finally:
        service.handle_post("/voice/stop", {})


def test_voice_double_start_returns_409_with_live_session() -> None:
    service, sessions = _service()
    _, first = service.handle_post("/voice/start", {})
    sessions[0].running.wait(timeout=2)
    try:
        status, payload = service.handle_post("/voice/start", {})
        assert status == 409
        assert payload["error"]["code"] == "voice_already_running"
        assert payload["session"]["id"] == first["session"]["id"]
    finally:
        service.handle_post("/voice/stop", {})


def test_voice_start_rejects_unknown_and_non_string_modes() -> None:
    service, _ = _service()
    status, payload = service.handle_post("/voice/start", {"mode": "karaoke"})
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"

    status, payload = service.handle_post("/voice/start", {"mode": 7})
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


def test_voice_start_maps_factory_failure_to_500() -> None:
    def broken_factory(*, event_sink, mode):  # noqa: ANN001, ARG001
        raise RuntimeError("OPENAI_API_KEY is required for live voice sessions")

    service, _ = _service(factory=broken_factory)
    status, payload = service.handle_post("/voice/start", {})
    assert status == 500
    assert payload["error"]["code"] == "voice_unavailable"
    assert "OPENAI_API_KEY" in payload["error"]["message"]


def test_voice_stop_is_idempotent() -> None:
    service, sessions = _service()
    service.handle_post("/voice/start", {})
    sessions[0].running.wait(timeout=2)

    status, payload = service.handle_post("/voice/stop", {})
    assert (status, payload) == (200, {"ok": True, "was_running": True})

    status, payload = service.handle_post("/voice/stop", {})
    assert (status, payload) == (200, {"ok": True, "was_running": False})


def test_voice_interrupt_semantics() -> None:
    service, sessions = _service()
    status, payload = service.handle_post("/voice/interrupt", {})
    assert status == 409
    assert payload["error"]["code"] == "voice_not_running"

    service.handle_post("/voice/start", {})
    sessions[0].running.wait(timeout=2)
    try:
        status, payload = service.handle_post("/voice/interrupt", {})
        assert (status, payload) == (200, {"ok": True})

        sessions[0].interrupt_result = False
        status, payload = service.handle_post("/voice/interrupt", {})
        assert (status, payload) == (200, {"ok": False})
    finally:
        service.handle_post("/voice/stop", {})


def test_voice_status_snapshot() -> None:
    service, sessions = _service()
    status, payload = service.handle_get("/voice/status", {})
    assert status == 200
    assert payload == {
        "state": "idle",
        "session": None,
        "subscribers": 0,
        "meeting_active": False,
        "wake_listening": False,
    }

    service.handle_post("/voice/start", {})
    sessions[0].running.wait(timeout=2)
    try:
        _, payload = service.handle_get("/voice/status", {})
        assert payload["state"] == "listening"
        assert payload["session"]["mode"] == "conversation"
    finally:
        service.handle_post("/voice/stop", {})


# ----------------------------------------------------------------- SSE


class _RunningGateway:
    def __init__(self, service: GatewayService) -> None:
        self.service = service
        self.thread = threading.Thread(
            target=service.serve, kwargs={"host": "127.0.0.1", "port": 0}
        )

    def __enter__(self) -> "_RunningGateway":
        self.thread.start()
        deadline = time.monotonic() + 5
        while self.service._server is None:
            if time.monotonic() > deadline:
                raise RuntimeError("gateway did not start")
            time.sleep(0.01)
        self.port = self.service._server.server_address[1]
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.service.request_shutdown()
        self.thread.join(timeout=5)

    def open_sse(self, path: str):
        request = Request(f"http://127.0.0.1:{self.port}{path}")
        request.add_header("Authorization", f"Bearer {TOKEN}")
        return urlopen(request, timeout=5)


def _read_frame(response) -> list[str]:
    lines: list[str] = []
    while True:
        line = response.readline().decode("utf-8").rstrip("\n")
        if line == "":
            return lines
        lines.append(line)


def test_voice_sse_sends_snapshot_then_events_and_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(iris.gateway, "VOICE_SSE_HEARTBEAT_SECONDS", 0.2)
    service, _ = _service()
    controller = service.voice_controller

    with _RunningGateway(service) as gateway:
        response = gateway.open_sse("/voice/events")
        try:
            # 1. snapshot always comes first
            frame = _read_frame(response)
            assert frame[0] == "event: state"
            snapshot = json.loads(frame[1].removeprefix("data: "))
            assert snapshot == {
                "state": "idle",
                "session": None,
                "meeting_active": False,
                "wake_listening": False,
            }

            # 2. idle stream produces heartbeat comments
            frame = _read_frame(response)
            assert frame == [": hb"]

            # 3. live events stream through with the contract frame format
            controller.emit(
                VoiceEvent(
                    VoiceEventType.USER_TRANSCRIPT,
                    {"text": "hello", "final": True},
                )
            )
            frame = _read_frame(response)
            while frame == [": hb"]:
                frame = _read_frame(response)
            assert frame[0] == "event: user_transcript"
            assert json.loads(frame[1].removeprefix("data: ")) == {
                "text": "hello",
                "final": True,
            }

            # 4. state events are enriched with the session descriptor
            controller.emit(
                VoiceEvent(
                    VoiceEventType.STATE,
                    {"state": "listening", "meeting_active": False},
                )
            )
            frame = _read_frame(response)
            while frame == [": hb"]:
                frame = _read_frame(response)
            assert frame[0] == "event: state"
            payload = json.loads(frame[1].removeprefix("data: "))
            assert payload["session"] is None  # no live session in this test
            assert payload["state"] == "listening"

            assert controller.subscriber_count == 1
        finally:
            response.close()

        deadline = time.monotonic() + 2
        while controller.subscriber_count and time.monotonic() < deadline:
            time.sleep(0.02)
        assert controller.subscriber_count == 0


def test_voice_sse_requires_auth_over_http() -> None:
    service, _ = _service()
    with _RunningGateway(service) as gateway:
        request = Request(f"http://127.0.0.1:{gateway.port}/voice/events")
        with pytest.raises(HTTPError) as excinfo:
            urlopen(request, timeout=5)
        assert excinfo.value.code == 401


def test_malformed_voice_body_uses_error_envelope() -> None:
    service, _ = _service()
    with _RunningGateway(service) as gateway:
        request = Request(
            f"http://127.0.0.1:{gateway.port}/voice/start",
            data=b"{not json",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "Content-Type": "application/json",
            },
        )
        with pytest.raises(HTTPError) as excinfo:
            urlopen(request, timeout=5)
        assert excinfo.value.code == 400
        payload = json.loads(excinfo.value.read())
        assert payload["error"]["code"] == "invalid_request"
