from __future__ import annotations

from pathlib import Path

from iris.actions import RiskLevel
from iris.agent import _planner_instructions
from iris.config import IrisConfig
from iris.recipes import ActionRecipe, ActionRecipeRegistry, RecipeStep
from iris.safety import SafetyGate
from iris.tools import ToolContext, ToolRegistry, ToolResult, ToolSpec


def test_planner_instructions_use_generic_tools_not_app_phrase_branches() -> None:
    instructions = _planner_instructions()

    assert "Prefer generic tools" in instructions
    assert 'recipe_name="stripe_revenue_check"' not in instructions
    assert 'recipe_name="revenuecat_revenue_check"' not in instructions
    assert "For YouTube or" not in instructions


def test_browser_control_tools_are_registered() -> None:
    registry = ToolRegistry.default()

    assert registry.get("browser_focus_element") is not None
    assert registry.get("browser_submit") is not None
    assert registry.get("browser_click_element") is not None


def test_recipe_run_requires_verification_success(tmp_path: Path) -> None:
    calls: list[str] = []

    def ok_tool(arguments: dict, context: ToolContext) -> ToolResult:
        calls.append("step")
        return ToolResult(True, "step ok")

    def failed_verify(arguments: dict, context: ToolContext) -> ToolResult:
        calls.append("verify")
        return ToolResult(False, "not verified")

    registry = ToolRegistry(
        [
            ToolSpec(
                name="recipe_run",
                description="Run recipe.",
                parameters={"type": "object", "properties": {}},
                risk=RiskLevel.LOW_RISK,
                execute=ToolRegistry.default().get("recipe_run").execute,  # type: ignore[union-attr]
            ),
            ToolSpec(
                name="step_ok",
                description="Step ok.",
                parameters={"type": "object", "properties": {}},
                risk=RiskLevel.LOW_RISK,
                execute=ok_tool,
            ),
            ToolSpec(
                name="verify_fail",
                description="Verify fail.",
                parameters={"type": "object", "properties": {}},
                risk=RiskLevel.LOW_RISK,
                execute=failed_verify,
            ),
        ]
    )
    recipes = ActionRecipeRegistry(
        [
            ActionRecipe(
                name="needs_verify",
                description="Needs verification.",
                service="test",
                tools=("step_ok", "verify_fail"),
                steps=(RecipeStep(tool="step_ok"),),
                verification_tool="verify_fail",
                metadata={"failure_message": "Verification failed."},
            )
        ]
    )
    context = _context(tmp_path, recipes=recipes, tool_registry=registry)

    result = registry.execute_approved(
        "recipe_run", {"recipe_name": "needs_verify"}, context
    )

    assert result.ok is False
    assert result.message == "Verification failed."
    assert calls == ["step", "verify"]


def _context(
    tmp_path: Path, *, recipes: ActionRecipeRegistry, tool_registry: ToolRegistry
) -> ToolContext:
    return ToolContext(
        controller=object(),  # type: ignore[arg-type]
        perception=object(),  # type: ignore[arg-type]
        safety_gate=SafetyGate(confirm=lambda _question: True),
        openai_client=object(),  # type: ignore[arg-type]
        google_vision=object(),  # type: ignore[arg-type]
        session_state={"_tool_registry": tool_registry},
        recipes=recipes,
        config=_config(tmp_path),
        approved_tool_call=True,
    )


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
