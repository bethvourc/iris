from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import uuid
from urllib.request import Request, urlopen
import sqlite3

from iris.audit import stable_hash
from iris.perception import PerceptionService


@dataclass(frozen=True)
class WatchResult:
    changed: bool
    snapshot: str
    summary: str
    confidence: float
    details: dict[str, object]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_watch(
    db: sqlite3.Connection,
    *,
    kind: str,
    name: str,
    target: str,
    expected: str | None = None,
    selector: str | None = None,
    interval_seconds: float = 60,
    timeout_seconds: float = 120,
) -> str:
    watch_id = uuid.uuid4().hex
    now = _now()
    db.execute(
        """
        INSERT INTO watch_rules (
          watch_id, kind, name, target, expected, selector, interval_seconds,
          timeout_seconds, enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            watch_id,
            kind,
            name,
            target,
            expected,
            selector,
            interval_seconds,
            timeout_seconds,
            now,
            now,
        ),
    )
    db.commit()
    return watch_id


def list_watches(db: sqlite3.Connection) -> list[dict[str, object]]:
    rows = db.execute(
        """
        SELECT watch_id, kind, name, target, expected, selector, interval_seconds,
               timeout_seconds, enabled, last_snapshot, created_at, updated_at
        FROM watch_rules
        ORDER BY created_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def run_watch_once(
    db: sqlite3.Connection,
    watch_id: str,
    *,
    perception: PerceptionService | None = None,
) -> WatchResult:
    row = db.execute(
        "SELECT * FROM watch_rules WHERE watch_id = ?", (watch_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"watch not found: {watch_id}")
    run_id = uuid.uuid4().hex
    started_at = _now()
    db.execute(
        "INSERT INTO watch_runs (run_id, watch_id, status, started_at) VALUES (?, ?, 'running', ?)",
        (run_id, watch_id, started_at),
    )
    db.commit()
    try:
        result = _execute_watch(dict(row), perception=perception)
        changed = bool(row["last_snapshot"]) and row["last_snapshot"] != result.snapshot
        result = WatchResult(
            changed, result.snapshot, result.summary, result.confidence, result.details
        )
        db.execute(
            "UPDATE watch_rules SET last_snapshot = ?, updated_at = ? WHERE watch_id = ?",
            (result.snapshot, _now(), watch_id),
        )
        db.execute(
            "UPDATE watch_runs SET status = 'completed', finished_at = ?, result_json = ? WHERE run_id = ?",
            (_now(), json.dumps(result.__dict__, sort_keys=True, default=str), run_id),
        )
        db.commit()
        return result
    except Exception as exc:
        db.execute(
            "UPDATE watch_runs SET status = 'failed', finished_at = ?, result_json = ? WHERE run_id = ?",
            (_now(), json.dumps({"error": str(exc)}), run_id),
        )
        db.commit()
        raise


def _execute_watch(
    row: dict[str, object],
    *,
    perception: PerceptionService | None,
) -> WatchResult:
    kind = str(row["kind"])
    target = str(row["target"])
    expected = row["expected"] if row["expected"] is None else str(row["expected"])
    timeout = float(row["timeout_seconds"])
    if kind == "web":
        return _web_watch(target, expected, timeout)
    if kind == "file":
        return _file_watch(target, expected)
    if kind == "command":
        return _command_watch(target, expected, timeout)
    if kind == "screen":
        return _screen_watch(expected, perception or PerceptionService())
    if kind == "app":
        return _app_watch(expected, perception or PerceptionService())
    if kind == "repo":
        return _command_watch("git status --short", expected, timeout)
    return WatchResult(
        False,
        stable_hash({"kind": kind, "target": target, "implemented": False}),
        f"{kind} watcher is registered but not implemented yet.",
        0.2,
        {"kind": kind, "target": target},
    )


def _web_watch(target: str, expected: str | None, timeout: float) -> WatchResult:
    request = Request(target, headers={"User-Agent": "Iris/0.1"})
    with urlopen(request, timeout=timeout) as response:
        body = response.read(250_000).decode("utf-8", errors="replace")
        status = int(getattr(response, "status", 200))
    found = expected in body if expected else status < 400
    return WatchResult(
        False,
        stable_hash({"status": status, "body": body[:50_000]}),
        f"HTTP {status}; expected text {'found' if found else 'not found'}."
        if expected
        else f"HTTP {status}.",
        0.9 if status < 400 else 0.55,
        {"status": status, "expected": expected, "found": found},
    )


def _file_watch(target: str, expected: str | None) -> WatchResult:
    path = Path(target).expanduser()
    if not path.exists():
        return WatchResult(
            False,
            stable_hash({"exists": False, "target": str(path)}),
            f"Missing file: {path}",
            0.95,
            {"exists": False},
        )
    stat = path.stat()
    content = (
        path.read_text(encoding="utf-8", errors="replace")[:50_000]
        if path.is_file()
        else ""
    )
    found = expected in content if expected else None
    return WatchResult(
        False,
        stable_hash({"mtime": stat.st_mtime, "size": stat.st_size, "content": content}),
        f"File expected text {'found' if found else 'not found'}."
        if expected
        else "File snapshot captured.",
        0.95,
        {"exists": True, "size": stat.st_size, "expected": expected, "found": found},
    )


def _command_watch(target: str, expected: str | None, timeout: float) -> WatchResult:
    completed = subprocess.run(
        target,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = (completed.stdout + completed.stderr)[:50_000]
    found = expected in output if expected else None
    return WatchResult(
        False,
        stable_hash(output),
        f"Command expected text {'found' if found else 'not found'}."
        if expected
        else f"Command exited {completed.returncode}.",
        0.75 if completed.returncode else 0.9,
        {
            "returncode": completed.returncode,
            "expected": expected,
            "found": found,
            "output": output[:2000],
        },
    )


def _screen_watch(expected: str | None, perception: PerceptionService) -> WatchResult:
    screenshot = perception.capture_screen()
    context = perception.screen_context()
    return WatchResult(
        False,
        stable_hash(
            {
                "active_app": context.active_app,
                "active_window": context.active_window,
                "png": screenshot.png[:4096].hex(),
            }
        ),
        "Screen snapshot captured. OCR phrase matching needs Google Vision wiring for watch jobs.",
        0.6,
        {
            "active_app": context.active_app,
            "active_window": context.active_window,
            "expected": expected,
        },
    )


def _app_watch(expected: str | None, perception: PerceptionService) -> WatchResult:
    context = perception.screen_context()
    found = (
        expected in (context.active_app or "") if expected else bool(context.active_app)
    )
    return WatchResult(
        False,
        stable_hash(
            {"active_app": context.active_app, "active_window": context.active_window}
        ),
        f"Active app: {context.active_app or 'unknown'}",
        0.8,
        {
            "active_app": context.active_app,
            "active_window": context.active_window,
            "expected": expected,
            "found": found,
        },
    )
