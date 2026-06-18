from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from iris.logging_setup import JsonFormatter, configure_logging


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="iris.gateway",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_parseable_json_with_core_fields() -> None:
    line = JsonFormatter().format(_record("gateway.request"))
    payload = json.loads(line)

    assert payload["event"] == "gateway.request"
    assert payload["level"] == "INFO"
    assert payload["component"] == "iris.gateway"
    assert payload["ts"].endswith("Z")


def test_json_formatter_includes_extra_fields() -> None:
    line = JsonFormatter().format(
        _record("gateway.request", path="/health", status=200, duration_ms=1.2)
    )
    payload = json.loads(line)

    assert payload["path"] == "/health"
    assert payload["status"] == 200
    assert payload["duration_ms"] == 1.2


def test_json_formatter_includes_exception_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record("gateway.request_failed")
        record.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in payload["exc"]


def test_configure_logging_writes_rotating_file(tmp_path: Path) -> None:
    logger = configure_logging(json_logs=True, log_dir=tmp_path)
    logger.info("test.event", extra={"detail": "value"})

    log_file = tmp_path / "daemon.log"
    assert log_file.exists()
    payload = json.loads(log_file.read_text().strip().splitlines()[-1])
    assert payload["event"] == "test.event"
    assert payload["detail"] == "value"
    assert isinstance(logger.handlers[0], RotatingFileHandler)


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    configure_logging(json_logs=True, log_dir=tmp_path)
    logger = configure_logging(json_logs=True, log_dir=tmp_path)

    assert len(logger.handlers) == 1
