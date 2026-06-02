from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import re
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from iris.approvals import list_approvals
from iris.audit import record_audit
from iris.config import IrisConfig
from iris.actions import RiskLevel
from iris.runtime import RunOrchestrator
from iris.sessions import get_session, list_sessions
from iris.state import open_state
from iris.supervisor import TaskSupervisor
from iris.tasks import (
    get_task,
    list_tasks,
    resume_task,
)


RouterFactory = Callable[[], Any]
AUTH_EXEMPT_PATHS = {"/health"}
MAX_BODY_BYTES = 1_000_000


class GatewayService:
    def __init__(self, *, config: IrisConfig, router_factory: RouterFactory) -> None:
        self.config = config
        self.router_factory = router_factory
        self._orchestrator: RunOrchestrator | None = None

    @property
    def orchestrator(self) -> RunOrchestrator:
        if self._orchestrator is None:
            self._orchestrator = RunOrchestrator(
                config=self.config,
                router_factory=self.router_factory,
            )
        return self._orchestrator

    def serve(self, *, host: str = "127.0.0.1", port: int = 8765) -> None:
        service = self

        class Handler(_GatewayHandler):
            gateway = service

        server = ThreadingHTTPServer((host, port), Handler)
        print(f"Iris gateway listening on http://{host}:{port}")
        server.serve_forever()

    def authorize(self, path: str, authorization_header: str | None) -> bool:
        if path in AUTH_EXEMPT_PATHS:
            return True
        token = self.config.gateway_token
        if not token:
            return False
        scheme, _, value = (authorization_header or "").partition(" ")
        if scheme.lower() != "bearer" or not value:
            return False
        return hmac.compare_digest(value.strip(), token)

    def handle_get(
        self, path: str, query: dict[str, list[str]]
    ) -> tuple[int, dict[str, Any]]:
        with open_state(self.config) as db:
            if path == "/health":
                return 200, {"ok": True, "agent": self.config.agent_name}
            if path == "/sessions":
                return 200, {
                    "sessions": list_sessions(db, limit=_int_query(query, "limit", 25))
                }
            session_match = re.fullmatch(r"/sessions/([a-fA-F0-9]+)", path)
            if session_match:
                session = get_session(db, session_match.group(1))
                return (
                    (200, session) if session else (404, {"error": "session not found"})
                )
            if path == "/tasks":
                return 200, {
                    "tasks": list_tasks(
                        db,
                        status=_str_query(query, "status"),
                        limit=_int_query(query, "limit", 25),
                    )
                }
            if path == "/runs":
                return 200, {
                    "runs": [
                        run.to_dict()
                        for run in self.orchestrator.list_runs(
                            status=_str_query(query, "status"),
                            limit=_int_query(query, "limit", 25),
                        )
                    ]
                }
            task_match = re.fullmatch(r"/tasks/([a-fA-F0-9]+)", path)
            if task_match:
                task = get_task(db, task_match.group(1))
                return (200, task) if task else (404, {"error": "task not found"})
            run_match = re.fullmatch(r"/runs/([a-fA-F0-9]+)", path)
            if run_match:
                run = self.orchestrator.get_run(run_match.group(1))
                return (
                    (200, {"run": run.to_dict()})
                    if run
                    else (404, {"error": "run not found"})
                )
            run_events = re.fullmatch(r"/runs/([a-fA-F0-9]+)/events", path)
            if run_events:
                return 200, {
                    "events": self.orchestrator.list_events(
                        run_events.group(1),
                        limit=_int_query(query, "limit", 200),
                    )
                }
            run_approvals = re.fullmatch(r"/runs/([a-fA-F0-9]+)/approvals", path)
            if run_approvals:
                return 200, {
                    "approvals": self.orchestrator.approvals_for_run(
                        run_approvals.group(1)
                    )
                }
            if path == "/approvals":
                return 200, {"approvals": list_approvals(db)}
        return 404, {"error": "not found"}

    def stream_run_events(
        self, run_id: str, write: Callable[[dict[str, Any]], None]
    ) -> None:
        for event in self.orchestrator.list_events(run_id):
            write(event)
        snapshot = self.orchestrator.get_run(run_id)
        if snapshot is None or snapshot.status in {
            "blocked",
            "cancelled",
            "failed",
            "done",
        }:
            return
        events: queue.Queue[dict[str, Any]] = queue.Queue()

        def on_event(event: Any) -> None:
            if event.run_id == run_id:
                events.put(event.to_dict())

        unsubscribe = self.orchestrator.subscribe(on_event)
        try:
            while True:
                try:
                    event = events.get(timeout=30.0)
                except queue.Empty:
                    return
                write(event)
                if event.get("status") in {"blocked", "cancelled", "failed", "done"}:
                    return
        finally:
            unsubscribe()

    def handle_post(
        self, path: str, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        if path == "/messages":
            return self._handle_message(body)
        task_cancel = re.fullmatch(r"/tasks/([a-fA-F0-9]+)/cancel", path)
        if task_cancel:
            ok = self.orchestrator.cancel_run(task_cancel.group(1))
            return (200 if ok else 404), {"ok": ok}
        run_cancel = re.fullmatch(r"/runs/([a-fA-F0-9]+)/cancel", path)
        if run_cancel:
            reason = str(body.get("reason") or "Operation cancelled.")
            ok = self.orchestrator.cancel_run(run_cancel.group(1), reason=reason)
            return (200 if ok else 404), {"ok": ok}
        task_resume = re.fullmatch(r"/tasks/([a-fA-F0-9]+)/resume", path)
        if task_resume:
            with open_state(self.config) as db:
                ok = resume_task(db, task_resume.group(1))
            return (200 if ok else 404), {"ok": ok}
        if path == "/tasks/run-next":
            result = TaskSupervisor(
                config=self.config,
                router_factory=self.router_factory,
            ).run_next()
            return (200 if result.ok else 500), result.__dict__
        approval = re.fullmatch(r"/approvals/([a-fA-F0-9]+)/(approve|deny)", path)
        if approval:
            status = "approved" if approval.group(2) == "approve" else "denied"
            with open_state(self.config) as db:
                record_audit(
                    db,
                    actor="gateway",
                    tool=f"approval.{status}",
                    risk=RiskLevel.SENSITIVE,
                    input_value={"approval_id": approval.group(1)},
                    result="requested",
                )
            ok = self.orchestrator.decide_approval(approval.group(1), status)
            return (200 if ok else 404), {"ok": ok, "status": status}
        return 404, {"error": "not found"}

    def _handle_message(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        text = str(body.get("message") or body.get("text") or "").strip()
        if not text:
            return 400, {"error": "message is required"}
        channel = str(body.get("channel") or "gateway")
        session_id = str(body.get("session_id") or "") or None
        try:
            result = self.orchestrator.start_run(
                text,
                channel=channel,
                session_id=session_id,
                persist_task=True,
            )
            return 200, {
                "ok": result.ok,
                "session_id": result.session_id,
                "task_id": result.task_id,
                "run_id": result.run_id,
                "message": result.message,
                "payload": result.payload,
                "task_status": result.status,
            }
        except Exception as exc:
            return 500, {
                "ok": False,
                "session_id": session_id,
                "error": str(exc),
            }


class _GatewayHandler(BaseHTTPRequestHandler):
    gateway: GatewayService

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._authorize(parsed.path):
            return
        stream_match = re.fullmatch(r"/runs/([a-fA-F0-9]+)/stream", parsed.path)
        if stream_match:
            self._write_sse(stream_match.group(1))
            return
        status, payload = self.gateway.handle_get(parsed.path, parse_qs(parsed.query))
        self._write_json(status, payload)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not self._authorize(parsed.path):
            return
        try:
            body = self._read_body()
        except ValueError as exc:
            self._write_json(400, {"error": str(exc)})
            return
        status, payload = self.gateway.handle_post(parsed.path, body)
        self._write_json(status, payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _authorize(self, path: str) -> bool:
        if self.gateway.authorize(path, self.headers.get("Authorization")):
            return True
        self._write_json(401, {"error": "gateway authorization required"})
        return False

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("JSON body is too large")
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

    def _write_sse(self, run_id: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def write(event: dict[str, Any]) -> None:
            body = json.dumps(event, sort_keys=True, default=str)
            self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
            self.wfile.flush()

        self.gateway.stream_run_events(run_id, write)


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
