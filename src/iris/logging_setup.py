"""Structured logging for the Iris daemon.

The desktop app supervises the daemon and reads its log file
(`~/Library/Logs/Iris/daemon.log`); every line must be one JSON object so the
diagnostics tooling can parse it. See docs/desktop/architecture.md §9.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "iris"
LOG_FILE_NAME = "daemon.log"
_MAX_BYTES = 5_000_000
_BACKUP_COUNT = 3

# Attributes present on every LogRecord; anything else was passed via extra=
# and belongs in the payload.
_RESERVED_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "component": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    *,
    json_logs: bool = False,
    log_dir: str | Path | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure the `iris` logger tree. Safe to call more than once."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    handler: logging.Handler
    if log_dir is not None:
        directory = Path(log_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            directory / LOG_FILE_NAME,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
    else:
        handler = logging.StreamHandler()

    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    logger.addHandler(handler)
    return logger
