from __future__ import annotations

from datetime import datetime, timezone
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import queue
import re
import signal
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from iris import __version__

from iris.activity import ActivityRequestError, activity_detail, activity_feed
from iris.approvals import list_approvals
from iris.audit import record_audit
from iris.config import IrisConfig
from iris.actions import RiskLevel
from iris.gateway_ui import DASHBOARD_HTML
from iris.memory_graph import (
    explain_fact,
    memory_context_packet,
    related_facts,
    search_facts,
)
from iris.memory_lifecycle import maintain_lifecycle, set_memory_pinned
from iris.memory_review import decide_review_item, list_review_items
from iris.runtime import RunOrchestrator
from iris.sessions import get_session, list_sessions
from iris.settings_store import (
    EnvManagedSettingError,
    SettingsError,
    settings_payload,
    update_stored_settings,
)
from iris.state import open_state
from iris.supervisor import TaskSupervisor
from iris.tasks import (
    get_task,
    list_tasks,
    resume_task,
)
from iris.voice_controller import (
    UnknownModeError,
    VoiceAlreadyRunningError,
    VoiceController,
    VoiceNotRunningError,
    VoiceUnavailableError,
)


RouterFactory = Callable[[], Any]
AUTH_EXEMPT_PATHS = {"/health", "/dashboard", "/memory-browser"}
MAX_BODY_BYTES = 1_000_000
CONTRACT_VERSION = 1
# Paths that use the structured error envelope from the desktop contract
# (docs/desktop/api-contract.md §1); legacy routes keep flat error strings.
CONTRACT_PATH_PREFIXES = ("/voice", "/settings", "/activity")
VOICE_SSE_HEARTBEAT_SECONDS = 15.0

_LOG = logging.getLogger("iris.gateway")


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


class GatewayService:
    def __init__(self, *, config: IrisConfig, router_factory: RouterFactory) -> None:
        self.config = config
        self.router_factory = router_factory
        self._orchestrator: RunOrchestrator | None = None
        self._voice_controller: VoiceController | None = None
        self._server: ThreadingHTTPServer | None = None
        self.started_at = datetime.now(timezone.utc)

    @property
    def orchestrator(self) -> RunOrchestrator:
        if self._orchestrator is None:
            self._orchestrator = RunOrchestrator(
                config=self.config,
                router_factory=self.router_factory,
            )
        return self._orchestrator

    @property
    def voice_controller(self) -> VoiceController:
        if self._voice_controller is None:
            self._voice_controller = VoiceController(
                session_factory=self._build_voice_session
            )
        return self._voice_controller

    def _build_voice_session(self, *, event_sink: Any, mode: str) -> Any:
        # `mode` is validated by the controller; "conversation" is the only
        # mode today and maps to an un-gated session (the caller's hotkey or
        # wake word already established intent).
        del mode
        if not self.config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for live voice sessions")
        from iris.profile import load_user_profile
        from iris.voice import RealtimeSpeechSession

        with open_state(self.config) as db:
            user_profile = load_user_profile(self.config, db)
        return RealtimeSpeechSession(
            config=self.config,
            router=self.router_factory(),
            user_profile=user_profile,
            orchestrator=self.orchestrator,
            wake_gated=False,
            event_sink=event_sink,
        )

    def serve(self, *, host: str = "127.0.0.1", port: int = 8765) -> None:
        service = self

        class Handler(_GatewayHandler):
            gateway = service

        server = ThreadingHTTPServer((host, port), Handler)
        self._server = server
        bound_host, bound_port = server.server_address[:2]
        base_url = f"http://{bound_host}:{bound_port}"
        print(f"Iris gateway listening on {base_url}")
        print(f"Open {base_url}/dashboard to use the UI")
        if not self.config.gateway_token:
            print(
                "WARNING: IRIS_GATEWAY_TOKEN is not set; all API endpoints will "
                "return 401. Set it before serving to enable access."
            )
        self._install_signal_handlers()
        _LOG.info(
            "gateway.start",
            extra={"host": bound_host, "port": bound_port, "pid": os.getpid()},
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()
            self._server = None
            _LOG.info("gateway.stop", extra={"pid": os.getpid()})

    def request_shutdown(self) -> None:
        """Stop the server. Safe to call from any thread or a signal handler."""
        server = self._server
        if server is None:
            return
        # shutdown() blocks until serve_forever() exits, so it must never run
        # on the thread that is inside serve_forever() (e.g. a SIGTERM handler
        # interrupting the main thread).
        threading.Thread(target=server.shutdown, daemon=True).start()

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        def handle_sigterm(_signum: int, _frame: Any) -> None:
            _LOG.info("gateway.sigterm")
            self.request_shutdown()

        signal.signal(signal.SIGTERM, handle_sigterm)

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
        if path == "/health":
            return 200, {
                "ok": True,
                "agent": self.config.agent_name,
                "version": __version__,
                "contract_version": CONTRACT_VERSION,
                "pid": os.getpid(),
                "started_at": self.started_at.isoformat(
                    timespec="seconds"
                ).replace("+00:00", "Z"),
            }
        if path == "/voice/status":
            return 200, self.voice_controller.status()
        if path == "/settings":
            return 200, settings_payload(self.config)
        with open_state(self.config) as db:
            if path == "/activity":
                try:
                    return 200, activity_feed(
                        db,
                        days=_int_query(query, "days", 7),
                        limit=_int_query(query, "limit", 50),
                        cursor=_str_query(query, "cursor"),
                        tz=_str_query(query, "tz") or "UTC",
                    )
                except ActivityRequestError as exc:
                    return 400, _error_payload("invalid_request", str(exc))
            activity_match = re.fullmatch(r"/activity/([A-Za-z0-9._:-]+)", path)
            if activity_match:
                detail = activity_detail(db, activity_match.group(1))
                if detail is None:
                    return 404, _error_payload(
                        "not_found", "activity item not found"
                    )
                return 200, detail
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
            if path == "/memory/facts":
                query_text = _str_query(query, "query") or ""
                limit = _int_query(query, "limit", 25)
                include_inactive = _bool_query(query, "include_inactive", False)
                facts = (
                    search_facts(
                        db,
                        query_text,
                        limit=limit,
                        include_inactive=include_inactive,
                        config=self.config,
                    )
                    if query_text
                    else memory_context_packet(db, limit=limit, config=self.config)
                )
                return 200, {"facts": facts}
            if path == "/memory/related":
                entity = _str_query(query, "entity") or ""
                if not entity:
                    return 400, {"error": "entity is required"}
                return 200, {
                    "facts": related_facts(
                        db,
                        entity,
                        limit=_int_query(query, "limit", 25),
                        include_inactive=_bool_query(query, "include_inactive", False),
                    )
                }
            if path == "/memory/explain":
                query_text = _str_query(query, "query") or ""
                if not query_text:
                    return 400, {"error": "query is required"}
                result = explain_fact(db, query_text)
                return (
                    (200, {"fact": result}) if result else (404, {"error": "not found"})
                )
            if path == "/memory/reviews":
                return 200, {
                    "items": list_review_items(
                        db,
                        status=_str_query(query, "status") or "pending",
                        limit=_int_query(query, "limit", 50),
                    )
                }
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

    def handle_put(
        self, path: str, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        if path == "/settings":
            return self._handle_settings_put(body)
        return 404, {"error": "not found"}

    def _handle_settings_put(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            update_stored_settings(self.config.state_db_path, body)
        except EnvManagedSettingError as exc:
            return 409, _error_payload(exc.code, str(exc))
        except SettingsError as exc:
            return 400, _error_payload(exc.code, str(exc))
        with open_state(self.config) as db:
            record_audit(
                db,
                actor="gateway",
                tool="settings.update",
                risk=RiskLevel.LOW_RISK,
                details={"keys": sorted(body)},
                result="ok",
            )
        # Future reads (and the next voice session) see the new values; a
        # live session keeps its config until it ends, per the contract.
        self.config = IrisConfig.from_env(self.config.project_root)
        return 200, settings_payload(self.config)

    def handle_post(
        self, path: str, body: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        if path == "/voice/start":
            return self._handle_voice_start(body)
        if path == "/voice/stop":
            return 200, {"ok": True, "was_running": self.voice_controller.stop()}
        if path == "/voice/interrupt":
            try:
                return 200, {"ok": self.voice_controller.interrupt()}
            except VoiceNotRunningError as exc:
                return 409, _error_payload(exc.code, str(exc))
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
        review = re.fullmatch(
            r"/memory/reviews/([a-fA-F0-9]+)/(approve|reject|supersede)", path
        )
        if review:
            with open_state(self.config) as db:
                result = decide_review_item(
                    db,
                    review_id=review.group(1),
                    decision=review.group(2),
                    actor="gateway",
                    notes=str(body.get("notes") or ""),
                )
                record_audit(
                    db,
                    actor="gateway",
                    tool=f"memory.review.{review.group(2)}",
                    risk=RiskLevel.LOW_RISK,
                    result="ok" if result.ok else "error",
                    input_value={
                        "review_id": review.group(1),
                        "notes": body.get("notes"),
                    },
                    output_value=result.__dict__,
                )
            return (200 if result.ok else 400), result.__dict__
        pin = re.fullmatch(r"/memory/facts/([a-fA-F0-9]+)/(pin|unpin)", path)
        if pin:
            pinned = pin.group(2) == "pin"
            with open_state(self.config) as db:
                ok = set_memory_pinned(db, pin.group(1), pinned)
                record_audit(
                    db,
                    actor="gateway",
                    tool="memory.pin" if pinned else "memory.unpin",
                    risk=RiskLevel.LOW_RISK,
                    result="ok" if ok else "error",
                    input_value={"relation_id": pin.group(1), "pinned": pinned},
                )
            return (200 if ok else 404), {
                "ok": ok,
                "relation_id": pin.group(1),
                "pinned": pinned,
            }
        if path == "/memory/maintain":
            with open_state(self.config) as db:
                result = maintain_lifecycle(db)
                record_audit(
                    db,
                    actor="gateway",
                    tool="memory.maintain",
                    risk=RiskLevel.LOW_RISK,
                    result="ok",
                    output_value=result.__dict__,
                )
            return 200, result.__dict__
        return 404, {"error": "not found"}

    def _handle_voice_start(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        mode = body.get("mode", "conversation")
        if not isinstance(mode, str):
            return 400, _error_payload("invalid_request", "mode must be a string")
        try:
            session = self.voice_controller.start(mode=mode)
        except UnknownModeError as exc:
            return 400, _error_payload(exc.code, str(exc))
        except VoiceAlreadyRunningError as exc:
            payload = _error_payload(exc.code, str(exc))
            payload["session"] = exc.session
            return 409, payload
        except VoiceUnavailableError as exc:
            return 500, _error_payload(exc.code, str(exc))
        return 202, {"session": session}

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
    _status_code = 0

    def do_GET(self) -> None:  # noqa: N802
        self._handle_request("GET", self._dispatch_get)

    def do_POST(self) -> None:  # noqa: N802
        self._handle_request("POST", self._dispatch_post)

    def do_PUT(self) -> None:  # noqa: N802
        self._handle_request("PUT", self._dispatch_put)

    def _dispatch_put(self, parsed: Any) -> None:
        if not self._authorize(parsed.path):
            return
        try:
            body = self._read_body()
        except ValueError as exc:
            self._write_json(400, _error_payload("invalid_request", str(exc)))
            return
        status, payload = self.gateway.handle_put(parsed.path, body)
        self._write_json(status, payload)

    def _dispatch_get(self, parsed: Any) -> None:
        if parsed.path in ("", "/"):
            self._redirect("/dashboard")
            return
        if not self._authorize(parsed.path):
            return
        if parsed.path in ("/dashboard", "/memory-browser"):
            self._write_html(200, DASHBOARD_HTML)
            return
        stream_match = re.fullmatch(r"/runs/([a-fA-F0-9]+)/stream", parsed.path)
        if stream_match:
            self._write_sse(stream_match.group(1))
            return
        if parsed.path == "/voice/events":
            self._write_voice_sse()
            return
        status, payload = self.gateway.handle_get(parsed.path, parse_qs(parsed.query))
        self._write_json(status, payload)

    def _dispatch_post(self, parsed: Any) -> None:
        if not self._authorize(parsed.path):
            return
        try:
            body = self._read_body()
        except ValueError as exc:
            if parsed.path.startswith(CONTRACT_PATH_PREFIXES):
                self._write_json(400, _error_payload("invalid_request", str(exc)))
            else:
                self._write_json(400, {"error": str(exc)})
            return
        status, payload = self.gateway.handle_post(parsed.path, body)
        self._write_json(status, payload)

    def _handle_request(self, method: str, dispatch: Callable[[Any], None]) -> None:
        started = time.monotonic()
        parsed = urlparse(self.path)
        self._status_code = 0
        try:
            dispatch(parsed)
        except Exception:
            # Log path/status only: request bodies and headers may carry
            # secrets and must never reach the log.
            _LOG.exception(
                "gateway.request_failed",
                extra={"method": method, "path": parsed.path},
            )
            if self._status_code == 0:
                try:
                    self._write_json(500, {"error": "internal error"})
                except Exception:
                    pass
        finally:
            _LOG.info(
                "gateway.request",
                extra={
                    "method": method,
                    "path": parsed.path,
                    "status": self._status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                },
            )

    def send_response(self, code: int, message: str | None = None) -> None:
        self._status_code = code
        super().send_response(code, message)

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

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _write_html(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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

    def _write_voice_sse(self) -> None:
        """Voice event stream per docs/desktop/api-contract.md §4.

        Always opens with a `state` snapshot; heartbeat comments let clients
        detect stale connections. The stream is not session-scoped — it stays
        open across sessions until the client disconnects.
        """
        controller = self.gateway.voice_controller
        subscription = controller.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self._write_sse_frame("state", self._voice_state_snapshot(controller))
            while True:
                try:
                    event = subscription.events.get(
                        timeout=VOICE_SSE_HEARTBEAT_SECONDS
                    )
                except queue.Empty:
                    self.wfile.write(b": hb\n\n")
                    self.wfile.flush()
                    continue
                data = event.data
                if event.type == "state":
                    # Contract state payloads carry the session descriptor;
                    # session-emitted events only know their own state.
                    data = {**data, "session": controller.status()["session"]}
                self._write_sse_frame(event.type, data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            controller.unsubscribe(subscription)

    @staticmethod
    def _voice_state_snapshot(controller: VoiceController) -> dict[str, Any]:
        status = controller.status()
        return {
            "state": status["state"],
            "session": status["session"],
            "meeting_active": status["meeting_active"],
        }

    def _write_sse_frame(self, event_type: str, data: dict[str, Any]) -> None:
        body = json.dumps(data, sort_keys=True, default=str)
        self.wfile.write(f"event: {event_type}\ndata: {body}\n\n".encode("utf-8"))
        self.wfile.flush()


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


def _bool_query(query: dict[str, list[str]], key: str, default: bool) -> bool:
    value = str((query.get(key) or [str(default)])[0]).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _str_query(query: dict[str, list[str]], key: str) -> str | None:
    value = (query.get(key) or [""])[0]
    return str(value) if value else None

