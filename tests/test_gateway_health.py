from __future__ import annotations

from datetime import datetime
import json
import logging
import os
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from iris import __version__
from iris.gateway import CONTRACT_VERSION, GatewayService


def _service(token: str | None = None) -> GatewayService:
    return GatewayService(
        config=SimpleNamespace(gateway_token=token, agent_name="Iris"),
        router_factory=lambda: None,
    )


def test_health_payload_matches_contract() -> None:
    status, payload = _service().handle_get("/health", {})

    assert status == 200
    assert payload["ok"] is True
    assert payload["agent"] == "Iris"
    assert payload["version"] == __version__
    assert payload["contract_version"] == CONTRACT_VERSION
    assert payload["pid"] == os.getpid()
    # started_at must be ISO 8601 UTC with Z suffix
    assert payload["started_at"].endswith("Z")
    datetime.fromisoformat(payload["started_at"].replace("Z", "+00:00"))


def test_health_does_not_open_state_db() -> None:
    # The config deliberately lacks everything open_state would need; health
    # must answer anyway so liveness probes never depend on DB access.
    status, payload = _service().handle_get("/health", {})
    assert status == 200 and payload["ok"] is True


class _RunningGateway:
    def __init__(self, token: str = "test-secret-token") -> None:
        self.token = token
        self.service = _service(token)
        self.thread = threading.Thread(
            target=self.service.serve, kwargs={"host": "127.0.0.1", "port": 0}
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

    def get(self, path: str, token: str | None = None) -> tuple[int, dict]:
        request = Request(f"http://127.0.0.1:{self.port}{path}")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())


def test_serve_answers_health_and_shuts_down_cleanly() -> None:
    with _RunningGateway() as gateway:
        status, payload = gateway.get("/health")
        assert status == 200
        assert payload["version"] == __version__

    assert not gateway.thread.is_alive()
    assert gateway.service._server is None


def test_request_shutdown_without_server_is_a_no_op() -> None:
    _service().request_shutdown()


def test_requests_are_logged_without_leaking_tokens(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="iris.gateway"):
        with _RunningGateway() as gateway:
            gateway.get("/health")
            status, _ = gateway.get("/sessions", token=gateway.token + "-wrong")
            assert status == 401

    request_logs = [r for r in caplog.records if r.message == "gateway.request"]
    health_log = next(r for r in request_logs if r.path == "/health")
    assert health_log.status == 200
    assert health_log.method == "GET"
    assert health_log.duration_ms >= 0

    sessions_log = next(r for r in request_logs if r.path == "/sessions")
    assert sessions_log.status == 401

    # No token material may appear anywhere in the log output.
    all_text = "\n".join(
        f"{r.message} {r.__dict__}" for r in caplog.records
    )
    assert gateway.token not in all_text
