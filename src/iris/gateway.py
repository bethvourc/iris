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
from iris.state import open_state
from iris.supervisor import TaskSupervisor
from iris.tasks import (
    get_task,
    list_tasks,
    resume_task,
)


RouterFactory = Callable[[], Any]
AUTH_EXEMPT_PATHS = {"/health", "/memory-browser"}
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
        if parsed.path == "/memory-browser":
            self._write_html(200, MEMORY_BROWSER_HTML)
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


MEMORY_BROWSER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Iris Memory Browser</title>
  <style>
    :root {
      --ink: #1a1813;
      --paper: #f5ecd8;
      --paper-2: #efe0bf;
      --oxide: #a34b2f;
      --moss: #3f5d45;
      --blueprint: #23384f;
      --gold: #c29136;
      --muted: #776b5d;
      --line: rgba(26, 24, 19, .16);
      --shadow: 0 24px 70px rgba(35, 31, 20, .18);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font-family: "Avenir Next", "Gill Sans", Verdana, sans-serif;
      background:
        radial-gradient(circle at 12% 10%, rgba(194,145,54,.32), transparent 27rem),
        radial-gradient(circle at 90% 0%, rgba(63,93,69,.20), transparent 24rem),
        linear-gradient(135deg, var(--paper), var(--paper-2));
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background-image:
        linear-gradient(rgba(26,24,19,.035) 1px, transparent 1px),
        linear-gradient(90deg, rgba(26,24,19,.035) 1px, transparent 1px);
      background-size: 34px 34px;
      mask-image: linear-gradient(to bottom, rgba(0,0,0,.9), rgba(0,0,0,.28));
    }
    header {
      padding: 34px clamp(18px, 4vw, 56px) 18px;
      display: grid;
      grid-template-columns: 1.2fr .8fr;
      gap: 24px;
      align-items: end;
    }
    h1 {
      margin: 0;
      font-family: Georgia, "Iowan Old Style", serif;
      font-size: clamp(2.6rem, 7vw, 6.8rem);
      letter-spacing: -.07em;
      line-height: .82;
      max-width: 900px;
    }
    .deck {
      color: var(--blueprint);
      font-size: 1rem;
      line-height: 1.55;
      border-left: 3px solid var(--oxide);
      padding-left: 18px;
    }
    main {
      padding: 12px clamp(18px, 4vw, 56px) 56px;
      display: grid;
      grid-template-columns: minmax(260px, 340px) minmax(0, 1fr);
      gap: 22px;
    }
    .panel, .card {
      background: rgba(255, 250, 238, .72);
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
    }
    .panel { padding: 18px; position: sticky; top: 18px; align-self: start; }
    label { display: block; font-size: .78rem; text-transform: uppercase; letter-spacing: .14em; color: var(--muted); margin: 14px 0 7px; }
    input, select {
      width: 100%;
      border: 1px solid rgba(26,24,19,.18);
      background: rgba(255,255,255,.62);
      color: var(--ink);
      border-radius: 16px;
      padding: 12px 13px;
      font: inherit;
      outline: none;
    }
    input:focus, select:focus { border-color: var(--oxide); box-shadow: 0 0 0 4px rgba(163,75,47,.14); }
    button {
      border: 0;
      border-radius: 999px;
      padding: 11px 15px;
      background: var(--ink);
      color: var(--paper);
      font-weight: 700;
      cursor: pointer;
      transition: transform .18s ease, background .18s ease;
    }
    button:hover { transform: translateY(-1px); background: var(--oxide); }
    button.secondary { background: rgba(26,24,19,.08); color: var(--ink); }
    button.good { background: var(--moss); }
    button.warn { background: var(--oxide); }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
    .tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
    .tab.active { background: var(--oxide); color: white; }
    .grid { display: grid; gap: 14px; }
    .card {
      padding: 18px;
      display: grid;
      gap: 9px;
      animation: rise .45s ease both;
    }
    @keyframes rise { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: none; } }
    .fact-title {
      font-family: Georgia, "Iowan Old Style", serif;
      font-size: 1.35rem;
      line-height: 1.15;
    }
    .meta { display: flex; flex-wrap: wrap; gap: 7px; color: var(--muted); font-size: .84rem; }
    .pill { border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; background: rgba(255,255,255,.45); }
    .status { min-height: 24px; color: var(--blueprint); font-size: .92rem; margin-top: 12px; }
    .canvas {
      min-height: 540px;
      border: 1px dashed rgba(26,24,19,.18);
      border-radius: 32px;
      padding: 18px;
      background: linear-gradient(155deg, rgba(255,255,255,.28), rgba(255,255,255,.08));
    }
    .empty {
      padding: 56px 20px;
      text-align: center;
      color: var(--muted);
      font-family: Georgia, "Iowan Old Style", serif;
      font-size: 1.5rem;
    }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: rgba(35,56,79,.08);
      border-radius: 18px;
      padding: 12px;
      margin: 0;
      font-size: .85rem;
    }
    @media (max-width: 860px) {
      header, main { grid-template-columns: 1fr; }
      .panel { position: static; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Memory<br>Cartograph</h1>
    <p class="deck">A local instrument panel for Iris memory: inspect active facts, trace evidence, review pending sensitive candidates, and tune lifecycle state without leaving the gateway.</p>
  </header>
  <main>
    <aside class="panel">
      <label for="token">Gateway token</label>
      <input id="token" type="password" placeholder="Bearer token">
      <label for="query">Search or entity</label>
      <input id="query" placeholder="Spotify, Iris, SQLite">
      <label for="mode">View</label>
      <select id="mode">
        <option value="facts">Facts</option>
        <option value="related">Related graph</option>
        <option value="reviews">Review queue</option>
        <option value="explain">Evidence timeline</option>
      </select>
      <div class="actions">
        <button id="run">Inspect</button>
        <button class="secondary" id="maintain">Maintain</button>
      </div>
      <div class="status" id="status">Ready.</div>
    </aside>
    <section>
      <div class="tabs">
        <button class="tab active" data-mode="facts">Facts</button>
        <button class="tab" data-mode="related">Related</button>
        <button class="tab" data-mode="reviews">Review</button>
        <button class="tab" data-mode="explain">Evidence</button>
      </div>
      <div class="canvas"><div id="results" class="grid"></div></div>
    </section>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const state = {
      token: localStorage.getItem("irisGatewayToken") || "",
      mode: "facts",
    };
    $("token").value = state.token;
    $("token").addEventListener("input", () => {
      state.token = $("token").value.trim();
      localStorage.setItem("irisGatewayToken", state.token);
    });
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        state.mode = tab.dataset.mode;
        $("mode").value = state.mode;
        document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === tab));
        load();
      });
    });
    $("mode").addEventListener("change", () => {
      state.mode = $("mode").value;
      document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item.dataset.mode === state.mode));
    });
    $("run").addEventListener("click", load);
    $("maintain").addEventListener("click", async () => {
      const data = await api("/memory/maintain", { method: "POST", body: "{}" });
      setStatus(`Maintained ${data.relations || 0} relation(s); ${data.stale || 0} stale.`);
      load();
    });
    async function api(path, options = {}) {
      if (!state.token) throw new Error("Enter IRIS_GATEWAY_TOKEN first.");
      const response = await fetch(path, {
        ...options,
        headers: {
          "Authorization": `Bearer ${state.token}`,
          "Content-Type": "application/json",
          ...(options.headers || {})
        }
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || data.message || `HTTP ${response.status}`);
      return data;
    }
    async function load() {
      try {
        setStatus("Loading memory map...");
        const q = encodeURIComponent($("query").value.trim());
        if (state.mode === "facts") {
          const data = await api(`/memory/facts?query=${q}&limit=30`);
          renderFacts(data.facts || []);
        } else if (state.mode === "related") {
          const data = await api(`/memory/related?entity=${q}&limit=30`);
          renderFacts(data.facts || []);
        } else if (state.mode === "reviews") {
          const data = await api("/memory/reviews?status=pending&limit=50");
          renderReviews(data.items || []);
        } else {
          const data = await api(`/memory/explain?query=${q}`);
          renderExplain(data.fact);
        }
        setStatus("Loaded.");
      } catch (error) {
        $("results").innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
        setStatus("Blocked.");
      }
    }
    function renderFacts(facts) {
      if (!facts.length) return empty("No matching memory facts.");
      $("results").innerHTML = facts.map((fact, index) => `
        <article class="card" style="animation-delay:${index * 35}ms">
          <div class="fact-title">${escapeHtml(fact.subject)} <span style="color:var(--oxide)">${escapeHtml((fact.predicate || "").replaceAll("_", " "))}</span> ${escapeHtml(fact.object_value)}</div>
          <div class="meta">
            <span class="pill">${fact.active ? "active" : "inactive"}</span>
            <span class="pill">confidence ${num(fact.confidence)}</span>
            <span class="pill">decay ${num(fact.decay_score)}</span>
            <span class="pill">${fact.pinned ? "pinned" : "unpinned"}</span>
            <span class="pill">access ${fact.access_count || 0}</span>
          </div>
          <div>${escapeHtml(fact.content || fact.relation_content || "")}</div>
          <div class="actions">
            <button class="secondary" data-action="pin" data-id="${escapeAttr(fact.relation_id)}" data-pinned="${fact.pinned ? "false" : "true"}">${fact.pinned ? "Unpin" : "Pin"}</button>
            <button class="secondary" data-action="explain" data-value="${escapeAttr(fact.object_value)}">Evidence</button>
          </div>
        </article>`).join("");
    }
    function renderReviews(items) {
      if (!items.length) return empty("No pending review items.");
      $("results").innerHTML = items.map((item, index) => {
        const c = item.candidate || {};
        return `<article class="card" style="animation-delay:${index * 35}ms">
          <div class="fact-title">${escapeHtml(c.subject || "candidate")} <span style="color:var(--oxide)">${escapeHtml(c.predicate || "")}</span> ${escapeHtml(c.object_value || "")}</div>
          <div class="meta"><span class="pill">${escapeHtml(item.reason)}</span><span class="pill">${escapeHtml(item.review_id)}</span></div>
          <pre>${escapeHtml(JSON.stringify(c, null, 2))}</pre>
          <div class="actions">
            <button class="good" data-action="decide" data-id="${escapeAttr(item.review_id)}" data-decision="approve">Approve</button>
            <button class="secondary" data-action="decide" data-id="${escapeAttr(item.review_id)}" data-decision="supersede">Supersede</button>
            <button class="warn" data-action="decide" data-id="${escapeAttr(item.review_id)}" data-decision="reject">Reject</button>
          </div>
        </article>`;
      }).join("");
    }
    function renderExplain(fact) {
      if (!fact) return empty("No evidence found.");
      $("results").innerHTML = `<article class="card">
        <div class="fact-title">${escapeHtml(fact.relation_content || fact.content)}</div>
        <div class="meta"><span class="pill">${fact.active ? "active" : "inactive"}</span><span class="pill">${escapeHtml(fact.provenance || "")}</span></div>
        ${(fact.evidence || []).map((ev) => `<pre>${escapeHtml(JSON.stringify(ev, null, 2))}</pre>`).join("")}
      </article>`;
    }
    $("results").addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      if (button.dataset.action === "pin") {
        await pin(button.dataset.id, button.dataset.pinned === "true");
      } else if (button.dataset.action === "decide") {
        await decide(button.dataset.id, button.dataset.decision);
      } else if (button.dataset.action === "explain") {
        explain(button.dataset.value);
      }
    });
    async function pin(id, pinned) {
      await api(`/memory/facts/${id}/${pinned ? "pin" : "unpin"}`, { method: "POST", body: "{}" });
      load();
    }
    async function decide(id, decision) {
      await api(`/memory/reviews/${id}/${decision}`, { method: "POST", body: "{}" });
      load();
    }
    function explain(value) {
      state.mode = "explain";
      $("mode").value = "explain";
      $("query").value = value;
      load();
    }
    function empty(message) { $("results").innerHTML = `<div class="empty">${escapeHtml(message)}</div>`; }
    function setStatus(message) { $("status").textContent = message; }
    function num(value) { return Number(value || 0).toFixed(2); }
    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
    }
    function escapeAttr(value) { return escapeHtml(value).replace(/`/g, "&#96;"); }
    load();
  </script>
</body>
</html>
"""
