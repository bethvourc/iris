from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from iris.approvals import decide_approval, list_approvals
from iris.audit import record_audit
from iris.config import IrisConfig
from iris.actions import RiskLevel
from iris.sessions import add_message, ensure_session, get_session, list_sessions
from iris.state import open_state
from iris.tasks import create_task, finish_task, get_task, heartbeat_task, list_tasks, request_cancel, resume_task, start_task


RouterFactory = Callable[[], Any]


class GatewayService:
    def __init__(self, *, config: IrisConfig, router_factory: RouterFactory) -> None:
        self.config = config
        self.router_factory = router_factory

    def serve(self, *, host: str = "127.0.0.1", port: int = 8765) -> None:
        service = self

        class Handler(_GatewayHandler):
            gateway = service

        server = ThreadingHTTPServer((host, port), Handler)
        print(f"Iris gateway listening on http://{host}:{port}")
        server.serve_forever()

    def handle_get(self, path: str, query: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
        with open_state(self.config) as db:
            if path == "/health":
                return 200, {"ok": True, "agent": self.config.agent_name}
            if path == "/sessions":
                return 200, {"sessions": list_sessions(db, limit=_int_query(query, "limit", 25))}
            session_match = re.fullmatch(r"/sessions/([a-fA-F0-9]+)", path)
            if session_match:
                session = get_session(db, session_match.group(1))
                return (200, session) if session else (404, {"error": "session not found"})
            if path == "/tasks":
                return 200, {
                    "tasks": list_tasks(
                        db,
                        status=_str_query(query, "status"),
                        limit=_int_query(query, "limit", 25),
                    )
                }
            task_match = re.fullmatch(r"/tasks/([a-fA-F0-9]+)", path)
            if task_match:
                task = get_task(db, task_match.group(1))
                return (200, task) if task else (404, {"error": "task not found"})
            if path == "/approvals":
                return 200, {"approvals": list_approvals(db)}
        return 404, {"error": "not found"}

    def handle_post(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path == "/messages":
            return self._handle_message(body)
        task_cancel = re.fullmatch(r"/tasks/([a-fA-F0-9]+)/cancel", path)
        if task_cancel:
            with open_state(self.config) as db:
                ok = request_cancel(db, task_cancel.group(1))
            return (200 if ok else 404), {"ok": ok}
        task_resume = re.fullmatch(r"/tasks/([a-fA-F0-9]+)/resume", path)
        if task_resume:
            with open_state(self.config) as db:
                ok = resume_task(db, task_resume.group(1))
            return (200 if ok else 404), {"ok": ok}
        approval = re.fullmatch(r"/approvals/([a-fA-F0-9]+)/(approve|deny)", path)
        if approval:
            status = "approved" if approval.group(2) == "approve" else "denied"
            with open_state(self.config) as db:
                ok = decide_approval(db, approval.group(1), status)
                record_audit(
                    db,
                    actor="gateway",
                    tool=f"approval.{status}",
                    risk=RiskLevel.SENSITIVE,
                    result="ok" if ok else "error",
                    input_value={"approval_id": approval.group(1)},
                )
            return (200 if ok else 404), {"ok": ok, "status": status}
        return 404, {"error": "not found"}

    def _handle_message(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        text = str(body.get("message") or body.get("text") or "").strip()
        if not text:
            return 400, {"error": "message is required"}
        channel = str(body.get("channel") or "gateway")
        session_id = str(body.get("session_id") or "") or None
        with open_state(self.config) as db:
            session_id = ensure_session(
                db,
                session_id=session_id,
                channel=channel,
                title=_title_from_message(text),
            )
            add_message(db, session_id=session_id, role="user", content=text, metadata={"channel": channel})
            task_id = create_task(
                db,
                session_id=session_id,
                kind="message",
                title=_title_from_message(text),
                input_value={"message": text, "channel": channel},
                status="queued",
            )
            start_task(db, task_id)
            heartbeat_task(db, task_id)
        router = self.router_factory()
        try:
            result = router.handle_text(text)
            status = "done" if result.ok else _task_status_for_message(result.message)
            with open_state(self.config) as db:
                add_message(
                    db,
                    session_id=session_id,
                    role="assistant",
                    content=result.message,
                    metadata={"task_id": task_id, "ok": result.ok},
                )
                finish_task(
                    db,
                    task_id,
                    status=status,
                    result_value={"message": result.message, "payload": result.payload, "ok": result.ok},
                    error=None if result.ok else result.message,
                )
            return 200, {
                "ok": result.ok,
                "session_id": session_id,
                "task_id": task_id,
                "message": result.message,
                "payload": result.payload,
                "task_status": status,
            }
        except Exception as exc:
            with open_state(self.config) as db:
                finish_task(db, task_id, status="failed", error=str(exc))
            return 500, {"ok": False, "session_id": session_id, "task_id": task_id, "error": str(exc)}


class _GatewayHandler(BaseHTTPRequestHandler):
    gateway: GatewayService

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        status, payload = self.gateway.handle_get(parsed.path, parse_qs(parsed.query))
        self._write_json(status, payload)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            body = self._read_body()
        except ValueError as exc:
            self._write_json(400, {"error": str(exc)})
            return
        status, payload = self.gateway.handle_post(parsed.path, body)
        self._write_json(status, payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _write_json(self, status: int, payload: dict[str, Any] | None) -> None:
        body = json.dumps(payload or {}, sort_keys=True, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _task_status_for_message(message: str) -> str:
    lowered = message.lower()
    if "needs approval" in lowered or "approval" in lowered:
        return "waiting_approval"
    if "blocked" in lowered:
        return "blocked"
    return "failed"


def _title_from_message(message: str) -> str:
    return message[:80].strip() or "Iris task"


def _int_query(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int((query.get(key) or [default])[0])
    except (TypeError, ValueError):
        return default


def _str_query(query: dict[str, list[str]], key: str) -> str | None:
    value = (query.get(key) or [""])[0]
    return str(value) if value else None
