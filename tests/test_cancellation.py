from __future__ import annotations

from typing import Any

import pytest

from iris.actions import LocalAction, RiskLevel
from iris.safety import AutomationPaused, CancellationToken, SafetyGate, classify_action
from iris.tools import ToolContext, ToolRegistry, ToolResult, ToolSpec


def test_cancelled_token_prevents_tool_execution() -> None:
    calls: list[dict[str, Any]] = []

    def execute(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        calls.append(arguments)
        return ToolResult(True, "ran")

    token = CancellationToken()
    token.cancel("user stopped the run")
    registry = ToolRegistry(
        [
            ToolSpec(
                name="noop",
                description="No-op test tool.",
                parameters={"type": "object", "properties": {}},
                risk=RiskLevel.LOW_RISK,
                execute=execute,
            )
        ]
    )
    context = ToolContext(
        controller=object(),  # type: ignore[arg-type]
        perception=object(),  # type: ignore[arg-type]
        safety_gate=SafetyGate(confirm=lambda _question: True),
        openai_client=object(),  # type: ignore[arg-type]
        google_vision=object(),  # type: ignore[arg-type]
        cancellation_token=token,
    )

    with pytest.raises(AutomationPaused, match="user stopped the run"):
        registry.execute("noop", {}, context)

    assert calls == []


def test_safety_gate_kill_blocks_low_risk_actions() -> None:
    gate = SafetyGate(confirm=lambda _question: True)
    gate.kill()

    with pytest.raises(AutomationPaused):
        gate.allow(LocalAction("open_app", {"app_name": "TextEdit"}))


def test_delete_action_is_blocked() -> None:
    decision = classify_action(LocalAction("delete_file", {"path": "/tmp/example"}))

    assert decision.risk == RiskLevel.BLOCKED
