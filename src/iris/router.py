from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from iris.agent import AgentExecutor, AgentPlanner
from iris.actions import LocalAction, RiskLevel
from iris.config import IrisConfig
from iris.integrations.google_vision import GoogleVisionClient
from iris.integrations.openai_client import OpenAIResponsesClient
from iris.mac_controller import ActionResult, MacController
from iris.perception import ScreenAwarenessService, PerceptionService
from iris.safety import CancellationToken, SafetyGate
from iris.tools import ToolRegistry


@dataclass(frozen=True)
class RouterResult:
    ok: bool
    message: str
    payload: Any | None = None


class ActionRouter:
    def __init__(
        self,
        *,
        perception: PerceptionService,
        controller: MacController,
        safety_gate: SafetyGate,
        openai_client: OpenAIResponsesClient,
        google_vision: GoogleVisionClient,
        screen_awareness: ScreenAwarenessService | None = None,
        config: IrisConfig | None = None,
        agent_executor: AgentExecutor | None = None,
        tool_registry: ToolRegistry | None = None,
        agent_planner: AgentPlanner | None = None,
    ) -> None:
        self.perception = perception
        self.controller = controller
        self.safety_gate = safety_gate
        self.openai_client = openai_client
        self.google_vision = google_vision
        self.screen_awareness = screen_awareness
        self._previous_chat_response_id: str | None = None
        registry = tool_registry or ToolRegistry.default()
        planner = agent_planner or AgentPlanner(openai_client)
        self.agent_executor = agent_executor or AgentExecutor(
            planner=planner,
            registry=registry,
            controller=controller,
            perception=perception,
            safety_gate=safety_gate,
            openai_client=openai_client,
            google_vision=google_vision,
            config=config,
            screen_awareness=screen_awareness,
            computer_use_runner=self.run_computer_use,
        )

    def set_screen_awareness(self, screen_awareness: ScreenAwarenessService | None) -> None:
        self.screen_awareness = screen_awareness
        self.agent_executor.set_screen_awareness(screen_awareness)

    def handle_text(
        self,
        text: str,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> RouterResult:
        command = text.strip()
        if not command:
            return RouterResult(False, "No command provided.")
        lowered = command.lower()
        normalized_command = lowered.strip(" \t\r\n.,!?")
        if lowered in {"stop", "pause", "kill"}:
            self.agent_executor.cancel_current("Operation cancelled by user.")
            self.safety_gate.kill()
            return RouterResult(True, "Automation paused.")
        if lowered in {"resume", "unpause"}:
            self.safety_gate.resume()
            return RouterResult(True, "Automation resumed.")
        if lowered in {"reset", "/reset", "reset conversation"}:
            self.reset_conversation()
            return RouterResult(True, "Conversation reset.")
        if normalized_command in {"approve", "approved", "yes approve", "go ahead", "yes, go ahead"}:
            result = self.agent_executor.approve_pending()
            return RouterResult(result.ok, result.message, result.payload)
        if normalized_command in {"deny", "denied", "cancel that", "never mind", "no"}:
            result = self.agent_executor.deny_pending()
            return RouterResult(result.ok, result.message, result.payload)
        result = self.agent_executor.run(command, cancellation_token=cancellation_token)
        return RouterResult(result.ok, result.message, result.payload)

    def reset_conversation(self) -> None:
        self._previous_chat_response_id = None
        self.agent_executor.reset()

    def chat(self, message: str) -> RouterResult:
        if not self.openai_client.available:
            return RouterResult(False, "OPENAI_API_KEY is not configured.")
        context = self.perception.screen_context()
        response = self.openai_client.chat(
            message=message,
            context=context,
            previous_response_id=self._previous_chat_response_id,
        )
        response_id = self.openai_client.response_id(response)
        if response_id:
            self._previous_chat_response_id = response_id
        answer = self.openai_client.output_text(response)
        return RouterResult(True, answer or "I am here.")

    def describe_current_screen(self, prompt: str) -> RouterResult:
        if self.screen_awareness:
            frame = self.screen_awareness.latest()
            if frame is None:
                frame = self.screen_awareness.capture_now()
            screenshot = frame.screenshot
            context = frame.context
        else:
            screenshot = self.perception.capture_screen()
            context = self.perception.screen_context()
        if not self.openai_client.available:
            return RouterResult(
                True,
                (
                    "Screen captured, but OPENAI_API_KEY is not configured for "
                    "visual description.\n"
                    f"Active app: {context.active_app or 'unknown'}\n"
                    f"Active window: {context.active_window or 'unknown'}\n"
                    f"Screenshot: {screenshot.width}x{screenshot.height}"
                ),
            )
        description = self.openai_client.describe_screen(
            screenshot,
            context,
            (
                f"{prompt}\n\n"
                "This frame comes from Iris live screen awareness. Describe the "
                "current visible screen/tab/window, not any previous context."
            ),
        )
        return RouterResult(True, description)

    def ocr_current_screen(self) -> RouterResult:
        screenshot = self.perception.capture_screen()
        if not self.google_vision.available:
            return RouterResult(
                False,
                "GOOGLE_APPLICATION_CREDENTIALS is not configured for Vision OCR.",
            )
        ocr = self.google_vision.extract_text(screenshot.png)
        return RouterResult(True, ocr.text or "No text detected.")

    def run_computer_use(self, instruction: str, max_steps: int = 8) -> RouterResult:
        if not self.openai_client.available:
            return RouterResult(False, "OPENAI_API_KEY is not configured.")
        screenshot = self.perception.capture_screen()
        try:
            response = self.openai_client.create_computer_use_response(instruction, screenshot)
        except Exception as exc:
            return self._computer_use_unavailable(exc)
        last_text = self.openai_client.output_text(response)
        for _ in range(max_steps):
            calls = self.openai_client.computer_calls(response)
            if not calls:
                return RouterResult(True, last_text or "Computer-use finished.")
            call = calls[0]
            acknowledged = self._approve_pending_safety_checks(call.pending_safety_checks)
            action_result = self._execute_computer_action(call.action)
            if not action_result.ok:
                return RouterResult(False, action_result.detail, action_result)
            self.controller.wait(0.6)
            screenshot = self.perception.capture_screen()
            try:
                response = self.openai_client.continue_computer_use_response(
                    previous_response_id=self.openai_client.response_id(response),
                    call_id=call.call_id,
                    screenshot=screenshot,
                    acknowledged_safety_checks=acknowledged,
                )
            except Exception as exc:
                return self._computer_use_unavailable(exc)
            last_text = self.openai_client.output_text(response) or last_text
        return RouterResult(
            False,
            f"Stopped after {max_steps} computer-use steps. Last response: {last_text}",
        )

    def _execute_computer_action(self, action: dict[str, Any]) -> ActionResult:
        action_type = str(action.get("type", "")).lower()
        if action_type == "click":
            return self.controller.click(
                int(action.get("x", 0)),
                int(action.get("y", 0)),
                str(action.get("button", "left")),
            )
        if action_type == "double_click":
            first = self.controller.click(int(action.get("x", 0)), int(action.get("y", 0)))
            if not first.ok:
                return first
            return self.controller.click(int(action.get("x", 0)), int(action.get("y", 0)))
        if action_type == "type":
            return self.controller.type_text(str(action.get("text", "")))
        if action_type in {"keypress", "key"}:
            keys = action.get("keys") or action.get("key") or []
            if isinstance(keys, str):
                keys = [keys]
            return self._press_computer_keys([str(key) for key in keys])
        if action_type == "scroll":
            dy = int(action.get("scroll_y", action.get("dy", action.get("y", 0))))
            dx = int(action.get("scroll_x", action.get("dx", action.get("x", 0))))
            return self.controller.scroll(dy=dy, dx=dx)
        if action_type == "wait":
            return self.controller.wait(float(action.get("seconds", 1.0)))
        return ActionResult(
            "computer_use",
            False,
            f"Unsupported computer-use action: {action}",
        )

    def _press_computer_keys(self, keys: list[str]) -> ActionResult:
        modifiers: list[str] = []
        key = ""
        for raw in keys:
            normalized = raw.lower().replace("meta", "command")
            if normalized in {"cmd", "command", "shift", "ctrl", "control", "alt", "option"}:
                modifiers.append(normalized)
            else:
                key = normalized
        if not key and modifiers:
            key = modifiers.pop()
        return self.controller.press_hotkey(key or "return", modifiers)

    def _approve_pending_safety_checks(
        self, checks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not checks:
            return []
        self.safety_gate.allow(
            LocalAction(
                "computer_safety_check",
                {"checks": checks},
                "acknowledge OpenAI computer-use safety checks",
                risk=RiskLevel.SENSITIVE,
            )
        )
        return checks

    def _computer_use_unavailable(self, exc: Exception) -> RouterResult:
        detail = str(exc)
        if "computer-use-preview" in detail or "model_not_found" in detail or "403" in detail:
            return RouterResult(
                False,
                (
                    "I can't use the computer-control model from this OpenAI project yet. "
                    "I can still open the right settings pages and walk you through the clicks."
                ),
            )
        return RouterResult(False, f"Computer control failed: {detail}")

    @staticmethod
    def _from_action_result(result: ActionResult) -> RouterResult:
        return RouterResult(result.ok, result.detail, result.payload)
