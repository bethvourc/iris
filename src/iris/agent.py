from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import uuid
from typing import Any, Literal

from iris.actions import RiskLevel
from iris.approvals import create_approval, decide_approval
from iris.audit import record_audit
from iris.computer import ComputerBackend
from iris.config import IrisConfig
from iris.connectors import connector_health, default_manifest_dirs
from iris.integrations.google_vision import GoogleVisionClient
from iris.integrations.openai_client import OpenAIResponsesClient
from iris.mac_controller import MacController
from iris.perception import PerceptionService, ScreenAwarenessService
from iris.recipes import ActionRecipeRegistry
from iris.safety import SafetyGate
from iris.state import open_state
from iris.tools import ApprovalRequired, ToolContext, ToolRegistry, ToolResult


PlannerType = Literal["tool_call", "final_answer", "approval_request"]


@dataclass(frozen=True)
class PlannerResult:
    type: PlannerType
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    spoken_response: str = ""
    reason: str = ""
    goal: str = ""
    expected_observation: str = ""
    done_condition: str = ""
    user_message: str = ""


@dataclass(frozen=True)
class AgentRunResult:
    ok: bool
    message: str
    payload: Any | None = None
    run_id: str = ""


class AgentPlanner:
    def __init__(self, openai_client: OpenAIResponsesClient) -> None:
        self.openai_client = openai_client

    def plan(
        self,
        *,
        user_request: str,
        tool_schemas: list[dict[str, Any]],
        screen_context: dict[str, Any],
        conversation_history: list[dict[str, str]],
        observations: list[dict[str, Any]],
        action_recipes: list[dict[str, Any]] | None = None,
        session_state: dict[str, Any] | None = None,
        connector_context: list[dict[str, Any]] | None = None,
    ) -> PlannerResult:
        if not self.openai_client.available:
            return PlannerResult(
                type="final_answer",
                spoken_response=(
                    "OPENAI_API_KEY is not configured, so I cannot plan agent actions yet."
                ),
                reason="missing_openai_api_key",
            )
        try:
            raw = self._call_model(
                user_request=user_request,
                tool_schemas=tool_schemas,
                action_recipes=action_recipes or [],
                screen_context=screen_context,
                conversation_history=conversation_history,
                observations=observations,
                session_state=session_state or {},
                connector_context=connector_context or [],
            )
            return _planner_result_from_json(raw)
        except Exception as exc:
            return PlannerResult(
                type="final_answer",
                spoken_response="I lost the thread for a second. Say that again and I’ll handle it.",
                reason="planner_error",
            )

    def _call_model(
        self,
        *,
        user_request: str,
        tool_schemas: list[dict[str, Any]],
        action_recipes: list[dict[str, Any]],
        screen_context: dict[str, Any],
        conversation_history: list[dict[str, str]],
        observations: list[dict[str, Any]],
        session_state: dict[str, Any],
        connector_context: list[dict[str, Any]],
    ) -> str:
        client = self.openai_client._get_client()
        response = client.responses.create(
            model=self.openai_client.config.chat_model,
            instructions=_planner_instructions(),
            input=[
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                {
                                    "user_profile": self.openai_client.user_profile.prompt_context(),
                                    "available_tools": tool_schemas,
                                    "available_connectors": connector_context,
                                    "action_recipes": action_recipes,
                                    "live_screen_context": screen_context,
                                    "recent_conversation": conversation_history[-8:],
                                    "session_context": {
                                        "state": session_state,
                                        "last_observation": observations[-1]
                                        if observations
                                        else None
                                    },
                                    "observations": observations,
                                },
                                sort_keys=True,
                                default=str,
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_request}],
                },
            ],
            max_output_tokens=450,
        )
        return self.openai_client.output_text(response)


class AgentExecutor:
    def __init__(
        self,
        *,
        planner: AgentPlanner,
        registry: ToolRegistry,
        controller: MacController,
        perception: PerceptionService,
        safety_gate: SafetyGate,
        openai_client: OpenAIResponsesClient,
        google_vision: GoogleVisionClient,
        config: IrisConfig | None = None,
        screen_awareness: ScreenAwarenessService | None = None,
        computer_use_runner: Any | None = None,
        computer_backend: ComputerBackend | None = None,
        recipes: ActionRecipeRegistry | None = None,
        max_steps: int = 6,
    ) -> None:
        self.planner = planner
        self.registry = registry
        self.controller = controller
        self.perception = perception
        self.safety_gate = safety_gate
        self.openai_client = openai_client
        self.google_vision = google_vision
        self.config = config
        self.screen_awareness = screen_awareness
        self.computer_use_runner = computer_use_runner
        self.computer_backend = computer_backend or ComputerBackend(
            controller=controller,
            perception=perception,
            screen_awareness=screen_awareness,
        )
        self.recipes = recipes or ActionRecipeRegistry.default()
        self.max_steps = max(1, max_steps)
        self._conversation_history: list[dict[str, str]] = []
        self._session_state: dict[str, Any] = {}

    def set_screen_awareness(self, screen_awareness: ScreenAwarenessService | None) -> None:
        self.screen_awareness = screen_awareness
        self.computer_backend.set_screen_awareness(screen_awareness)

    def reset(self) -> None:
        self._conversation_history.clear()
        self._session_state.pop("pending_approval", None)

    def approve_pending(self) -> AgentRunResult:
        pending = self._session_state.get("pending_approval")
        if not isinstance(pending, dict):
            return AgentRunResult(False, "I don't have a pending action to approve.")
        approval_id = str(pending.get("approval_id") or "")
        tool_name = str(pending.get("tool_name") or "")
        arguments = pending.get("arguments") if isinstance(pending.get("arguments"), dict) else {}
        run_id = str(pending.get("run_id") or uuid.uuid4().hex)
        if not tool_name:
            self._session_state.pop("pending_approval", None)
            return AgentRunResult(False, "That pending approval is missing its action.")
        if approval_id and self.config is not None:
            with open_state(self.config) as db:
                decide_approval(db, approval_id, "approved")
        original_pending = pending
        result = self._execute_tool(run_id, tool_name, arguments, approved=True)
        current_pending = self._session_state.get("pending_approval")
        if current_pending is original_pending or (
            isinstance(current_pending, dict)
            and str(current_pending.get("approval_id") or "") == approval_id
            and str(current_pending.get("tool_name") or "") == tool_name
        ):
            self._session_state.pop("pending_approval", None)
        self._remember_turn("approved", result.message)
        return AgentRunResult(result.ok, result.message, result.payload, run_id=run_id)

    def deny_pending(self) -> AgentRunResult:
        pending = self._session_state.pop("pending_approval", None)
        if not isinstance(pending, dict):
            return AgentRunResult(False, "I don't have a pending action to deny.")
        approval_id = str(pending.get("approval_id") or "")
        if approval_id and self.config is not None:
            with open_state(self.config) as db:
                decide_approval(db, approval_id, "denied")
        return AgentRunResult(True, "Okay, I won't do that.")

    def run(self, user_request: str) -> AgentRunResult:
        run_id = uuid.uuid4().hex
        observations: list[dict[str, Any]] = []
        for _step in range(self.max_steps):
            plan = self.planner.plan(
                user_request=user_request,
                tool_schemas=self.registry.schemas(),
                action_recipes=self.recipes.schemas(),
                screen_context=self._screen_context_summary(),
                conversation_history=self._conversation_history,
                observations=observations,
                session_state=self._session_state,
                connector_context=self._connector_context(),
            )
            if plan.type == "final_answer":
                message = plan.spoken_response or plan.user_message or "I am here."
                self._audit_agent_event(
                    run_id=run_id,
                    result="ok",
                    input_value={"request": user_request, "observations": observations},
                    output_value={"message": message},
                )
                self._remember_turn(user_request, message)
                return AgentRunResult(True, message, run_id=run_id)
            if plan.type == "approval_request":
                message = self._create_approval(
                    run_id=run_id,
                    action_name=plan.tool_name or "agent_action",
                    arguments=plan.arguments,
                    reason=plan.reason or plan.spoken_response,
                )
                self._audit_agent_event(
                    run_id=run_id,
                    result="approval_required",
                    input_value={"request": user_request, "plan": plan},
                    output_value={"message": message},
                )
                self._remember_turn(user_request, message)
                return AgentRunResult(False, message, run_id=run_id)
            if plan.type != "tool_call" or not plan.tool_name:
                message = plan.spoken_response or "I could not decide the next agent action."
                self._remember_turn(user_request, message)
                return AgentRunResult(False, message, run_id=run_id)
            result = self._execute_tool(run_id, plan.tool_name, plan.arguments)
            observations.append(
                {
                    "tool_name": plan.tool_name,
                    "arguments": plan.arguments,
                    "ok": result.ok,
                    "message": result.message,
                    "payload": result.payload,
                    "expected_observation": plan.expected_observation,
                    "done_condition": plan.done_condition,
                    "post_action_screen": self._screen_context_summary()
                    if result.ok or result.continue_planning
                    else None,
                }
            )
            if result.continue_planning:
                continue
            if (
                result.ok
                and _needs_verification(plan)
                and _step + 1 < self.max_steps
            ):
                self._session_state["last_step_needs_verification"] = {
                    "tool_name": plan.tool_name,
                    "expected_observation": plan.expected_observation,
                    "done_condition": plan.done_condition,
                    "message": result.message,
                }
                continue
            if not result.ok and _is_retryable_tool_error(result.message) and _step + 1 < self.max_steps:
                continue
            if not result.continue_planning:
                self._remember_turn(user_request, result.message)
                self._audit_agent_event(
                    run_id=run_id,
                    result="ok" if result.ok else "error",
                    input_value={"request": user_request, "observations": observations},
                    output_value={"message": result.message, "payload": result.payload},
                    error=None if result.ok else result.message,
                )
                return AgentRunResult(result.ok, result.message, result.payload, run_id=run_id)
        message = observations[-1]["message"] if observations else "I could not complete that."
        self._audit_agent_event(
            run_id=run_id,
            result="error",
            input_value={"request": user_request, "observations": observations},
            output_value={"message": message},
            error=str(message),
        )
        self._remember_turn(user_request, str(message))
        return AgentRunResult(False, str(message), run_id=run_id)

    def _execute_tool(
        self,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        approved: bool = False,
    ) -> ToolResult:
        tool = self.registry.get(tool_name)
        risk = tool.risk if tool else RiskLevel.BLOCKED
        context = ToolContext(
            controller=self.controller,
            perception=self.perception,
            safety_gate=self.safety_gate,
            openai_client=self.openai_client,
            google_vision=self.google_vision,
            computer=self.computer_backend,
            screen_awareness=self.screen_awareness,
            computer_use_runner=self.computer_use_runner,
            session_state=self._session_state,
            recipes=self.recipes,
            config=self.config,
        )
        try:
            result = (
                self.registry.execute_approved(tool_name, arguments, context)
                if approved
                else self.registry.execute(tool_name, arguments, context)
            )
            self._audit_tool(
                run_id=run_id,
                tool=tool_name,
                risk=risk,
                result="ok" if result.ok else "error",
                input_value=arguments,
                output_value={"message": result.message, "payload": result.payload},
                error=None if result.ok else result.message,
            )
            return result
        except ApprovalRequired as exc:
            message = self._create_approval(
                run_id=run_id,
                action_name=exc.tool_name,
                arguments=exc.arguments,
                reason=exc.reason,
            )
            self._audit_tool(
                run_id=run_id,
                tool=tool_name,
                risk=RiskLevel.SENSITIVE,
                result="approval_required",
                input_value=arguments,
                output_value={"message": message},
            )
            return ToolResult(False, message)
        except Exception as exc:
            message = _human_tool_error(str(exc))
            self._audit_tool(
                run_id=run_id,
                tool=tool_name,
                risk=risk,
                result="error",
                input_value=arguments,
                error=message,
            )
            return ToolResult(False, message)

    def _screen_context_summary(self) -> dict[str, Any]:
        summary = self.computer_backend.observe(include_browser=True).summary()
        summary["control_backends"] = self.computer_backend.backend_health(self.config)
        return summary

    def _connector_context(self) -> list[dict[str, Any]]:
        if self.config is None:
            return []
        try:
            with open_state(self.config) as db:
                connectors = connector_health(
                    db,
                    manifest_dirs=default_manifest_dirs(self.config.project_root),
                )
        except Exception:
            return []
        return [
            {
                "connector_id": item.get("connector_id"),
                "name": item.get("name"),
                "category": item.get("category"),
                "enabled": item.get("enabled"),
                "configured": item.get("configured"),
                "health": item.get("health"),
                "tools": item.get("tools"),
            }
            for item in connectors
            if item.get("enabled")
        ]

    def _create_approval(
        self,
        *,
        run_id: str,
        action_name: str,
        arguments: dict[str, Any],
        reason: str,
    ) -> str:
        preview = f"{action_name}({json.dumps(arguments, sort_keys=True, default=str)})"
        approval_id = ""
        if self.config is None:
            self._session_state["pending_approval"] = {
                "approval_id": "",
                "run_id": run_id,
                "tool_name": action_name,
                "arguments": arguments,
                "reason": reason,
            }
            return f"That needs approval before I can do it: {preview}"
        with open_state(self.config) as db:
            approval_id = create_approval(
                db,
                action_name=action_name,
                preview=preview,
                risk=RiskLevel.SENSITIVE,
                run_id=run_id,
                details={"reason": reason, "arguments": arguments},
            )
        self._session_state["pending_approval"] = {
            "approval_id": approval_id,
            "run_id": run_id,
            "tool_name": action_name,
            "arguments": arguments,
            "reason": reason,
        }
        return (
            "That needs approval before I can do it. "
            "Say approve to continue, or run `./iris approvals`."
        )

    def _audit_tool(
        self,
        *,
        run_id: str,
        tool: str,
        risk: RiskLevel,
        result: str,
        input_value: Any | None = None,
        output_value: Any | None = None,
        error: str | None = None,
    ) -> None:
        if self.config is None:
            return
        try:
            with open_state(self.config) as db:
                record_audit(
                    db,
                    actor="agent",
                    tool=tool,
                    risk=risk,
                    result=result,
                    run_id=run_id,
                    input_value=input_value,
                    output_value=output_value,
                    error=error,
                )
        except Exception:
            return

    def _audit_agent_event(
        self,
        *,
        run_id: str,
        result: str,
        input_value: Any | None = None,
        output_value: Any | None = None,
        error: str | None = None,
    ) -> None:
        if self.config is None:
            return
        try:
            with open_state(self.config) as db:
                record_audit(
                    db,
                    actor="agent",
                    tool="agent_run",
                    risk=RiskLevel.LOW_RISK,
                    result=result,
                    run_id=run_id,
                    input_value=input_value,
                    output_value=output_value,
                    error=error,
                )
        except Exception:
            return

    def _remember_turn(self, user_request: str, response: str) -> None:
        self._conversation_history.append({"role": "user", "text": user_request})
        self._conversation_history.append({"role": "assistant", "text": response})
        if len(self._conversation_history) > 12:
            del self._conversation_history[:-12]


def _planner_instructions() -> str:
    return """You are the Iris agent planner.

Return exactly one JSON object and no markdown.

Valid shapes:
{"type":"tool_call","tool_name":"name","arguments":{},"spoken_response":"","reason":"why"}
{"type":"tool_call","goal":"what the user wants","next_tool":"name","args":{},"expected_observation":"what should change","done_condition":"how to verify","user_message":"short progress phrase"}
{"type":"final_answer","spoken_response":"short natural answer","reason":"why no tool is needed"}
{"type":"approval_request","tool_name":"name","arguments":{},"spoken_response":"what needs approval","reason":"risk"}

Rules:
- Hardcoded app-command parsing is not available. Choose tools from available_tools.
- Choose generic tools only. action_recipes are context for how to combine tools, not tools to call directly.
- available_connectors tells you which app/service manifests exist, whether they are enabled/configured, and which generic tools they expose.
- If a connector is disabled or missing credentials, use integration_status or explain the setup instead of pretending it works.
- Prefer: browser_open/browser_click/browser_extract, app_open/app_activate/app_hotkey, screen_describe/screen_find_element/screen_click_element, audio_current_media/audio_explain_current_song, media_search/media_play/media_pause, app_volume_set, connector_list, integration_status, task_start_background, calendar_find_event, reminder_create, knowledge_search/file_find/file_open/workflow_run.
- Use live_screen_context as current state, but call describe_screen for current-screen/current-tab questions.
- Use knowledge_search when the user asks what Iris knows/remembers from local notes, docs, project context, prior logs, personal wiki, or ingested files.
- For media requests such as playing music, prefer media_play or media_search over open_app.
- For "turn it down in Spotify" or similar per-app volume requests, use app_volume_set before set_volume.
- For larger goals that should keep working after the conversation, use task_start_background and include a concrete goal.
- For "is X connected/configured", use integration_status.
- For calendar/reminder actions, use calendar_find_event or reminder_create; these may require approval because they touch private data or create records.
- If the user says "play it", "click it", "do it", or otherwise refers to a previous media/search action, use media_play or the relevant browser/screen tool instead of repeating the search.
- For browser navigation, use browser_open with new_tab=false unless the user explicitly asks for a new tab.
- If the user asks to look something up online, browse public results, research a person/company/topic, or summarize what is online, use web_research instead of only opening a search page.
- If the user asks what song something is, use audio_current_media first. If they ask what the current/background song means or what it is about, use audio_explain_current_song. If they give lyric fragments, use identify_song instead of search_web.
- For private app/site content such as messages, inboxes, calendars, or account pages, ask approval before extraction or summarization.
- For clicking or visual UI work, prefer browser_click, screen_find_element, or screen_click_element; use computer_use when safer layers cannot act.
- If a tool fails with a retryable observation, choose a fallback tool instead of giving up.
- Verify actions when possible before claiming completion.
- Do not claim an action was done unless you call a tool.
- Do not request destructive, payment, credential, security, or sending actions without approval.
- Keep final spoken_response conversational and human: usually one natural sentence, two when it helps. Do not say raw encoded URLs out loud."""


def _planner_result_from_json(text: str) -> PlannerResult:
    try:
        data = _extract_json_object(text)
    except Exception:
        return PlannerResult(
            type="final_answer",
            spoken_response="I lost the thread for a second. Say that again and I’ll handle it.",
            reason="planner_parse_error",
        )
    result_type = str(data.get("type") or ("tool_call" if data.get("next_tool") else "final_answer"))
    if result_type not in {"tool_call", "final_answer", "approval_request"}:
        result_type = "final_answer"
    arguments = data.get("arguments") or data.get("args") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    return PlannerResult(
        type=result_type,  # type: ignore[arg-type]
        tool_name=str(data.get("tool_name") or data.get("next_tool") or "") or None,
        arguments=arguments,
        spoken_response=str(data.get("spoken_response") or ""),
        reason=str(data.get("reason") or ""),
        goal=str(data.get("goal") or ""),
        expected_observation=str(data.get("expected_observation") or ""),
        done_condition=str(data.get("done_condition") or ""),
        user_message=str(data.get("user_message") or ""),
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("empty planner response")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        candidate = _balanced_json_candidate(stripped)
        if not candidate:
            match = re.search(r"\{.*\}", stripped, flags=re.S)
            candidate = match.group(0) if match else ""
        if not candidate:
            raise
        data = json.loads(candidate)
    if not isinstance(data, dict):
        raise ValueError("planner response was not a JSON object")
    return data


def _balanced_json_candidate(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _is_retryable_tool_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in {
            "use screen clicks instead",
            "could not find a visible play button",
            "need accessibility or computer-use access",
            "retry_with_screen",
        }
    )


def _needs_verification(plan: PlannerResult) -> bool:
    return bool(plan.expected_observation.strip() or plan.done_condition.strip())


def _human_tool_error(message: str) -> str:
    if "javascript through applescript is turned off" in message.lower() or "allow javascript from apple events" in message.lower():
        return "Chrome automation is off, so I’ll use screen clicks instead."
    if "OpenAI Responses API failed" in message:
        return "The model call failed before I could finish that step."
    return message
