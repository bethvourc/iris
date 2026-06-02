from __future__ import annotations

from pathlib import Path
from typing import Any

from iris.actions import RiskLevel
from iris.agent import AgentExecutor, PlannerResult
from iris.config import IrisConfig
from iris.safety import SafetyGate
from iris.state import open_state
from iris.tools import ToolContext, ToolRegistry, ToolResult, ToolSpec
from iris.tracing import latest_run_id, summarize_run_trace


def test_agent_records_run_trace_events(tmp_path: Path) -> None:
    config = _config(tmp_path)
    calls: list[dict[str, Any]] = []
    planner = _Planner()

    def execute(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        calls.append(arguments)
        return ToolResult(True, "done")

    executor = AgentExecutor(
        planner=planner,  # type: ignore[arg-type]
        registry=ToolRegistry(
            [
                ToolSpec(
                    name="noop",
                    description="No-op test tool.",
                    parameters={"type": "object", "properties": {}},
                    risk=RiskLevel.LOW_RISK,
                    execute=execute,
                )
            ]
        ),
        controller=object(),  # type: ignore[arg-type]
        perception=object(),  # type: ignore[arg-type]
        safety_gate=SafetyGate(confirm=lambda _question: True),
        openai_client=object(),  # type: ignore[arg-type]
        google_vision=object(),  # type: ignore[arg-type]
        computer_backend=_ComputerBackend(),  # type: ignore[arg-type]
        recipes=_Recipes(),  # type: ignore[arg-type]
        config=config,
    )

    result = executor.run("run the no-op")

    assert result.ok is True
    assert calls == [{"ok": True}]
    with open_state(config) as db:
        assert latest_run_id(db) == result.run_id
        summary = summarize_run_trace(db, result.run_id)

    event_names = [event["event_name"] for event in summary["events"]]
    assert "run_started" in event_names
    assert "screen_context" in event_names
    assert "planner" in event_names
    assert "tool_started" in event_names
    assert "tool_finished" in event_names
    assert "run_finished" in event_names
    assert summary["slowest_events"]


class _Planner:
    def plan(self, **_kwargs: Any) -> PlannerResult:
        return PlannerResult(type="tool_call", tool_name="noop", arguments={"ok": True})


class _ComputerBackend:
    def observe(self, *, include_browser: bool = False) -> "_Observation":
        return _Observation(include_browser=include_browser)

    def backend_health(self, _config: IrisConfig | None) -> dict[str, Any]:
        return {"native": {"ok": True}}


class _Observation:
    def __init__(self, *, include_browser: bool) -> None:
        self.include_browser = include_browser

    def summary(self) -> dict[str, Any]:
        return {"include_browser": self.include_browser}


class _Recipes:
    def schemas(self) -> list[dict[str, Any]]:
        return []


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
        email_provider="resend",
        resend_api_key=None,
        email_from=None,
        email_to=None,
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
