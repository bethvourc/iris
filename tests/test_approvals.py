from __future__ import annotations

from pathlib import Path
from typing import Any

from iris.actions import RiskLevel
from iris.agent import AgentExecutor
from iris.approvals import create_approval, decide_approval, get_approval
from iris.config import IrisConfig
from iris.safety import SafetyGate
from iris.state import connect, migrate, open_state
from iris.tools import ToolContext, ToolRegistry, ToolResult, ToolSpec


def test_expired_approval_cannot_be_approved(tmp_path: Path) -> None:
    db_path = tmp_path / "iris.sqlite3"
    with connect(db_path) as db:
        migrate(db)
        approval_id = create_approval(
            db,
            action_name="message_send",
            preview="message_send({})",
            ttl_seconds=-1,
        )

        assert decide_approval(db, approval_id, "approved") is False
        row = get_approval(db, approval_id)

    assert row is not None
    assert row["status"] == "pending"


def test_agent_does_not_execute_expired_pending_approval(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls: list[dict[str, Any]] = []

    def execute(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        calls.append(arguments)
        return ToolResult(True, "sent")

    registry = ToolRegistry(
        [
            ToolSpec(
                name="message_send",
                description="Send a message.",
                parameters={"type": "object", "properties": {}},
                risk=RiskLevel.SENSITIVE,
                execute=execute,
            )
        ]
    )
    executor = AgentExecutor(
        planner=object(),  # type: ignore[arg-type]
        registry=registry,
        controller=object(),  # type: ignore[arg-type]
        perception=object(),  # type: ignore[arg-type]
        safety_gate=SafetyGate(confirm=lambda _question: False),
        openai_client=object(),  # type: ignore[arg-type]
        google_vision=object(),  # type: ignore[arg-type]
        computer_backend=object(),  # type: ignore[arg-type]
        config=config,
    )
    arguments = {"recipient": "Beth", "body": "hello"}
    with open_state(config) as db:
        approval_id = create_approval(
            db,
            action_name="message_send",
            preview="message_send",
            ttl_seconds=-1,
            details={"arguments": arguments},
        )
    executor._session_state["pending_approval"] = {
        "approval_id": approval_id,
        "run_id": "run-1",
        "tool_name": "message_send",
        "arguments": arguments,
        "reason": "private message",
        "user_request": "send a message",
    }

    result = executor.approve_pending()

    assert result.ok is False
    assert "expired" in result.message
    assert calls == []


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
