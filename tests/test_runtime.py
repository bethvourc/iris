from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any

from iris.config import IrisConfig
from iris.gateway import GatewayService
from iris.approvals import create_approval
from iris.router import RouterResult
from iris.runtime import RunOrchestrator
from iris.safety import CancellationToken
from iris.state import open_state
from iris.tasks import get_task


def test_orchestrator_propagates_run_id_and_records_events(tmp_path: Path) -> None:
    config = _config(tmp_path)
    router = _Router()
    orchestrator = RunOrchestrator(config=config, router_factory=lambda: router)

    result = orchestrator.start_run("hello", channel="terminal")

    assert result.ok is True
    assert result.run_id
    assert router.calls[0]["run_id"] == result.run_id
    events = orchestrator.list_events(result.run_id)
    event_names = [event["event_name"] for event in events]
    assert "run.started" in event_names
    assert "run.finished" in event_names
    snapshot = orchestrator.get_run(result.run_id)
    assert snapshot is not None
    assert snapshot.status == "done"
    with open_state(config) as db:
        row = db.execute(
            "SELECT status, current_step, result_json FROM agent_runs WHERE run_id = ?",
            (result.run_id,),
        ).fetchone()
    assert row is not None
    assert row["status"] == "done"
    assert row["current_step"] == "run.finished"
    assert json.loads(row["result_json"])["message"] == "handled"


def test_orchestrator_persists_queued_run(tmp_path: Path) -> None:
    config = _config(tmp_path)
    orchestrator = RunOrchestrator(config=config, router_factory=lambda: _Router())

    result = orchestrator.start_run("queue this", channel="gateway", mode="queued")

    assert result.status == "queued"
    snapshot = orchestrator.get_run(result.run_id)
    assert snapshot is not None
    assert snapshot.status == "queued"
    assert snapshot.task_id == result.task_id
    events = orchestrator.list_events(result.run_id)
    assert [event["event_name"] for event in events] == ["run.queued"]


def test_gateway_message_response_shape_is_preserved(tmp_path: Path) -> None:
    config = _config(tmp_path)
    service = GatewayService(config=config, router_factory=lambda: _Router())

    status, payload = service.handle_post(
        "/messages",
        {"message": "do a thing", "channel": "gateway"},
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["message"] == "handled"
    assert payload["task_id"] == payload["run_id"]
    assert payload["task_status"] == "done"
    with open_state(config) as db:
        task = get_task(db, payload["task_id"])
    assert task is not None
    assert task["status"] == "done"


def test_orchestrator_cancels_active_run(tmp_path: Path) -> None:
    config = _config(tmp_path)
    router = _BlockingRouter()
    orchestrator = RunOrchestrator(config=config, router_factory=lambda: router)
    result_holder: list[Any] = []

    thread = threading.Thread(
        target=lambda: result_holder.append(
            orchestrator.start_run("long task", channel="voice")
        )
    )
    thread.start()
    assert router.started.wait(timeout=2.0)

    active = orchestrator.get_active_run(channel="voice")
    assert active is not None
    assert orchestrator.cancel_active_run(channel="voice", reason="stop now") is True

    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result_holder[0].status == "cancelled"
    assert result_holder[0].message == "stop now"


def test_orchestrator_recovers_stale_running_runs(tmp_path: Path) -> None:
    config = _config(tmp_path)
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with open_state(config) as db:
        db.execute(
            """
            INSERT INTO agent_runs (
              run_id, channel, status, current_step, message, created_at, started_at, updated_at
            ) VALUES ('run-stale', 'gateway', 'running', 'planning', 'old', ?, ?, ?)
            """,
            (stale, stale, stale),
        )
        db.commit()

    orchestrator = RunOrchestrator(config=config, router_factory=lambda: _Router())

    snapshot = orchestrator.get_run("run-stale")
    assert snapshot is not None
    assert snapshot.status == "failed"
    assert snapshot.current_step == "recovered"


def test_orchestrator_rejects_terminal_transition(tmp_path: Path) -> None:
    config = _config(tmp_path)
    orchestrator = RunOrchestrator(config=config, router_factory=lambda: _Router())
    result = orchestrator.start_run("hello", channel="terminal")

    try:
        orchestrator._transition_run(result.run_id, "running", "invalid")  # noqa: SLF001
    except ValueError as exc:
        assert "terminal" in str(exc)
    else:
        raise AssertionError("terminal run transition should fail")


def test_orchestrator_records_approval_decision_events(tmp_path: Path) -> None:
    config = _config(tmp_path)
    approval_holder: list[str] = []
    orchestrator = RunOrchestrator(
        config=config,
        router_factory=lambda: _ApprovalRouter(config, approval_holder),
    )
    result = orchestrator.start_run("approval needed", channel="gateway")
    approval_id = approval_holder[0]

    assert result.status == "waiting_approval"
    assert orchestrator.get_run(result.run_id).approval_id == approval_id  # type: ignore[union-attr]
    assert orchestrator.decide_approval(approval_id, "denied") is True

    snapshot = orchestrator.get_run(result.run_id)
    assert snapshot is not None
    assert snapshot.status == "blocked"
    events = [event["event_name"] for event in orchestrator.list_events(result.run_id)]
    assert "approval.denied" in events


class _ApprovalRouter:
    def __init__(self, config: IrisConfig, approval_holder: list[str]) -> None:
        self.config = config
        self.approval_holder = approval_holder

    def handle_text(
        self,
        _text: str,
        *,
        cancellation_token: CancellationToken | None = None,
        run_id: str | None = None,
    ) -> RouterResult:
        assert run_id is not None
        with open_state(self.config) as db:
            approval_id = create_approval(
                db,
                action_name="message_send",
                preview="message_send({})",
                run_id=run_id,
            )
        self.approval_holder.append(approval_id)
        return RouterResult(False, "That needs approval.", run_id=run_id)


def test_gateway_lists_runs(tmp_path: Path) -> None:
    config = _config(tmp_path)
    service = GatewayService(config=config, router_factory=lambda: _Router())
    service.handle_post("/messages", {"message": "do a thing", "channel": "gateway"})

    status, payload = service.handle_get("/runs", {"limit": ["10"]})

    assert status == 200
    assert len(payload["runs"]) == 1
    assert payload["runs"][0]["status"] == "done"


class _Router:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def handle_text(
        self,
        text: str,
        *,
        cancellation_token: CancellationToken | None = None,
        run_id: str | None = None,
    ) -> RouterResult:
        self.calls.append(
            {"text": text, "cancellation_token": cancellation_token, "run_id": run_id}
        )
        return RouterResult(True, "handled", {"text": text}, run_id or "")


class _BlockingRouter:
    def __init__(self) -> None:
        self.started = threading.Event()

    def handle_text(
        self,
        _text: str,
        *,
        cancellation_token: CancellationToken | None = None,
        run_id: str | None = None,
    ) -> RouterResult:
        self.started.set()
        assert cancellation_token is not None
        assert run_id
        while not cancellation_token.cancelled:
            time.sleep(0.01)
        return RouterResult(False, cancellation_token.reason, run_id=run_id)


def _config(tmp_path: Path) -> IrisConfig:
    return IrisConfig(
        project_root=tmp_path,
        openai_api_key=None,
        google_application_credentials=None,
        agent_name="Iris",
        voice="marin",
        toggle_hotkey="<ctrl>+<space>",
        kill_hotkey="<ctrl>+<alt>+k",
        realtime_model="gpt-realtime-2",
        chat_model="gpt-5.5",
        vision_model="gpt-5.5",
        computer_use_model="computer-use-preview",
        computer_use_environment="browser",
        groq_api_key=None,
        fast_intent_model="compound-mini",
        stt_model="whisper-large-v3-turbo",
        realtime_transcription_model="gpt-4o-mini-transcribe",
        screenshot_interval_seconds=0.5,
        gateway_token=None,
        notify_provider="pushover",
        ntfy_server="https://ntfy.sh",
        ntfy_topic=None,
        ntfy_token=None,
        pushover_token=None,
        pushover_user=None,
        notify_timeout_seconds=30.0,
        state_db_path=tmp_path / "iris.sqlite3",
        autonomy_level="L1",
        meeting_consent_required=True,
        meeting_retention_days=30,
        listen_seconds=2.0,
        wake_poll_seconds=1.2,
        wake_words=("iris", "hey iris"),
        speak_responses=False,
        max_response_chars=220,
    )
