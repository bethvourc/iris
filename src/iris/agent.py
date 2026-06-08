from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import time
import uuid
from typing import Any, Callable, Literal, TypeVar

from iris.actions import RiskLevel
from iris.approvals import (
    approval_details,
    create_approval,
    decide_approval,
    get_approval,
)
from iris.audit import record_audit
from iris.computer import ComputerBackend
from iris.config import IrisConfig
from iris.connectors import connector_health, default_manifest_dirs
from iris.integrations.google_vision import GoogleVisionClient
from iris.integrations.openai_client import OpenAIResponsesClient
from iris.memory import add_memory, list_memories
from iris.memory_graph import memory_context_packet
from iris.memory_llm_extract import enrich_memory_with_llm
from iris.mac_controller import MacController
from iris.perception import PerceptionService, ScreenAwarenessService
from iris.recipes import ActionRecipeRegistry
from iris.safety import AutomationPaused, CancellationToken, SafetyGate
from iris.state import open_state
from iris.tools import ApprovalRequired, ToolContext, ToolRegistry, ToolResult
from iris.tracing import now_iso, record_trace_event


T = TypeVar("T")


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
        known_memories: list[dict[str, Any]] | None = None,
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
                known_memories=known_memories or [],
            )
            return _planner_result_from_json(raw)
        except Exception:
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
        known_memories: list[dict[str, Any]] | None = None,
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
                                    "known_memories": known_memories or [],
                                    "available_tools": tool_schemas,
                                    "available_connectors": connector_context,
                                    "action_recipes": action_recipes,
                                    "live_screen_context": screen_context,
                                    "recent_conversation": conversation_history[-8:],
                                    "session_context": {
                                        "state": session_state,
                                        "last_observation": observations[-1]
                                        if observations
                                        else None,
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
            max_output_tokens=1200,
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
        max_steps: int = 12,
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
        self.recipes = recipes or ActionRecipeRegistry.default(
            config.project_root if config is not None else None
        )
        self.max_steps = max(1, max_steps)
        self._conversation_history: list[dict[str, str]] = []
        self._session_state: dict[str, Any] = _initial_session_state()
        self._current_cancellation_token: CancellationToken | None = None
        self._current_run_id: str | None = None
        self._current_status: dict[str, Any] = {}

    def set_screen_awareness(
        self, screen_awareness: ScreenAwarenessService | None
    ) -> None:
        self.screen_awareness = screen_awareness
        self.computer_backend.set_screen_awareness(screen_awareness)

    def reset(self) -> None:
        self._conversation_history.clear()
        self._session_state = _initial_session_state()

    def cancel_current(self, reason: str = "Operation cancelled.") -> None:
        if self._current_cancellation_token is not None:
            self._current_cancellation_token.cancel(reason)
        self._set_current_status(stage="cancelling", reason=reason)
        if self._current_run_id is not None:
            self._trace_event(
                self._current_run_id,
                "cancellation_requested",
                status="cancelled",
                details={"reason": reason},
            )

    def current_status(self) -> dict[str, Any]:
        return dict(self._current_status)

    def approve_pending(self) -> AgentRunResult:
        pending = self._session_state.get("pending_approval")
        if not isinstance(pending, dict):
            return AgentRunResult(False, "I don't have a pending action to approve.")
        approval_id = str(pending.get("approval_id") or "")
        tool_name = str(pending.get("tool_name") or "")
        arguments = (
            pending.get("arguments")
            if isinstance(pending.get("arguments"), dict)
            else {}
        )
        run_id = str(pending.get("run_id") or uuid.uuid4().hex)
        if not tool_name:
            self._session_state.pop("pending_approval", None)
            return AgentRunResult(False, "That pending approval is missing its action.")
        if approval_id and self.config is not None:
            with open_state(self.config) as db:
                row = get_approval(db, approval_id)
                if row is None or not _approval_matches_pending(
                    row, tool_name, arguments
                ):
                    self._session_state.pop("pending_approval", None)
                    return AgentRunResult(
                        False, "That approval no longer matches the pending action."
                    )
                if not decide_approval(db, approval_id, "approved"):
                    self._session_state.pop("pending_approval", None)
                    return AgentRunResult(
                        False,
                        "That approval is no longer pending or has expired. Please ask me to try again.",
                    )
        original_pending = pending
        result = self._execute_tool(run_id, tool_name, arguments, approved=True)
        user_request = str(pending.get("user_request") or "")
        current_pending = self._session_state.get("pending_approval")
        if current_pending is original_pending or (
            isinstance(current_pending, dict)
            and str(current_pending.get("approval_id") or "") == approval_id
            and str(current_pending.get("tool_name") or "") == tool_name
        ):
            self._session_state.pop("pending_approval", None)
        if result.ok and result.continue_planning and user_request:
            self._session_state["last_approved_tool_observation"] = {
                "tool_name": tool_name,
                "arguments": arguments,
                "ok": result.ok,
                "message": result.message,
                "payload": result.payload,
            }
            continued = self.run(user_request)
            self._session_state.pop("last_approved_tool_observation", None)
            return continued
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

    def run(
        self,
        user_request: str,
        *,
        cancellation_token: CancellationToken | None = None,
        run_id: str | None = None,
    ) -> AgentRunResult:
        run_id = run_id or uuid.uuid4().hex
        run_started_at = time.monotonic()
        observations: list[dict[str, Any]] = []
        seeded = self._session_state.get("last_approved_tool_observation")
        if isinstance(seeded, dict):
            observations.append(dict(seeded))
        token = cancellation_token or CancellationToken()
        self._current_cancellation_token = token
        self._current_run_id = run_id
        self._set_current_status(
            run_id=run_id,
            stage="starting",
            request=user_request,
        )
        self._session_state["current_user_request"] = user_request
        self._session_state["last_goal"] = user_request
        self._trace_event(
            run_id,
            "run_started",
            details={"request_preview": user_request[:220]},
        )
        try:
            for _step in range(self.max_steps):
                token.throw_if_cancelled()
                screen_context = self._trace_call(
                    run_id,
                    "screen_context",
                    "perception",
                    self._screen_context_summary,
                )
                connector_context = self._trace_call(
                    run_id,
                    "connector_context",
                    "connectors",
                    self._connector_context,
                )
                plan = self._trace_call(
                    run_id,
                    "planner",
                    "agent",
                    lambda: self.planner.plan(
                        user_request=user_request,
                        tool_schemas=self.registry.schemas(),
                        action_recipes=self.recipes.schemas(),
                        screen_context=screen_context,
                        conversation_history=self._conversation_history,
                        observations=observations,
                        session_state=self._session_state,
                        connector_context=connector_context,
                        known_memories=self._known_memories(),
                    ),
                    details={"step": _step + 1, "observations": len(observations)},
                )
                self._set_current_status(
                    run_id=run_id,
                    stage="executing_plan",
                    request=user_request,
                    tool_name=plan.tool_name,
                    plan_type=plan.type,
                )
                token.throw_if_cancelled()
                if plan.type == "final_answer":
                    message = plan.spoken_response or plan.user_message or "I am here."
                    self._audit_agent_event(
                        run_id=run_id,
                        result="ok",
                        input_value={
                            "request": user_request,
                            "observations": observations,
                        },
                        output_value={"message": message},
                    )
                    self._remember_turn(user_request, message)
                    return self._complete_run(
                        run_id,
                        run_started_at,
                        AgentRunResult(True, message, run_id=run_id),
                    )
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
                    return self._complete_run(
                        run_id,
                        run_started_at,
                        AgentRunResult(False, message, run_id=run_id),
                        status="approval_required",
                    )
                if plan.type != "tool_call" or not plan.tool_name:
                    message = (
                        plan.spoken_response
                        or "I could not decide the next agent action."
                    )
                    self._remember_turn(user_request, message)
                    return self._complete_run(
                        run_id,
                        run_started_at,
                        AgentRunResult(False, message, run_id=run_id),
                        status="error",
                    )
                result = self._execute_tool(
                    run_id,
                    plan.tool_name,
                    plan.arguments,
                    cancellation_token=token,
                )
                token.throw_if_cancelled()
                observation = {
                    "tool_name": plan.tool_name,
                    "arguments": plan.arguments,
                    "ok": result.ok,
                    "message": result.message,
                    "payload": result.payload,
                    "expected_observation": plan.expected_observation,
                    "done_condition": plan.done_condition,
                    "post_action_screen": self._trace_call(
                        run_id,
                        "post_action_screen_context",
                        "perception",
                        self._screen_context_summary,
                        details={"tool_name": plan.tool_name},
                    )
                    if result.ok or result.continue_planning
                    else None,
                }
                observations.append(observation)
                _update_session_from_observation(self._session_state, observation)
                token.throw_if_cancelled()
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
                if (
                    not result.ok
                    and _is_retryable_tool_error(result.message)
                    and _step + 1 < self.max_steps
                ):
                    continue
                if not result.continue_planning:
                    self._remember_turn(user_request, result.message)
                    self._audit_agent_event(
                        run_id=run_id,
                        result="ok" if result.ok else "error",
                        input_value={
                            "request": user_request,
                            "observations": observations,
                        },
                        output_value={
                            "message": result.message,
                            "payload": result.payload,
                        },
                        error=None if result.ok else result.message,
                    )
                    return self._complete_run(
                        run_id,
                        run_started_at,
                        AgentRunResult(
                            result.ok, result.message, result.payload, run_id=run_id
                        ),
                        status="ok" if result.ok else "error",
                    )
            message = (
                observations[-1]["message"]
                if observations
                else "I could not complete that."
            )
            self._audit_agent_event(
                run_id=run_id,
                result="error",
                input_value={"request": user_request, "observations": observations},
                output_value={"message": message},
                error=str(message),
            )
            self._remember_turn(user_request, str(message))
            return self._complete_run(
                run_id,
                run_started_at,
                AgentRunResult(False, str(message), run_id=run_id),
                status="error",
            )
        except AutomationPaused as exc:
            message = str(exc) or "Operation cancelled."
            self._trace_event(
                run_id,
                "cancellation_observed",
                status="cancelled",
                details={"message": message},
            )
            self._audit_agent_event(
                run_id=run_id,
                result="cancelled",
                input_value={"request": user_request, "observations": observations},
                output_value={"message": message},
                error=message,
            )
            self._remember_turn(user_request, message)
            return self._complete_run(
                run_id,
                run_started_at,
                AgentRunResult(False, message, run_id=run_id),
                status="cancelled",
                error=message,
            )
        except Exception as exc:
            message = str(exc)
            self._complete_run(
                run_id,
                run_started_at,
                AgentRunResult(False, message, run_id=run_id),
                status="error",
                error=message,
            )
            raise
        finally:
            if self._current_cancellation_token is token:
                self._current_cancellation_token = None
            if self._current_run_id == run_id:
                self._current_run_id = None

    def run_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> AgentRunResult:
        """Execute one tool the Realtime model chose, reusing safety/approval/audit.

        This is the execution shim for the unified voice brain: the Realtime model
        decides which tool to call, this runs it through the same gate as the deep
        planner (classification, approval creation, cancellation, session state).
        """
        run_id = uuid.uuid4().hex
        token = cancellation_token or CancellationToken()
        self._current_cancellation_token = token
        try:
            token.throw_if_cancelled()
            result = self._execute_tool(
                run_id, tool_name, arguments, cancellation_token=token
            )
            observation = {
                "tool_name": tool_name,
                "arguments": arguments,
                "ok": result.ok,
                "message": result.message,
                "payload": result.payload,
            }
            _update_session_from_observation(self._session_state, observation)
            return AgentRunResult(
                result.ok, result.message, result.payload, run_id=run_id
            )
        except AutomationPaused as exc:
            return AgentRunResult(
                False, str(exc) or "Operation cancelled.", run_id=run_id
            )
        finally:
            if self._current_cancellation_token is token:
                self._current_cancellation_token = None

    def summarize_session_to_memory(self) -> int:
        """Distill the conversation into durable memories at session end.

        This is how Iris gets to know the user over time: stable facts and
        preferences are extracted and stored, then surfaced again next session.
        """
        if self.config is None or not self._conversation_history:
            return 0
        if not getattr(self.openai_client, "available", False):
            return 0
        transcript = "\n".join(
            f"{turn['role']}: {turn['text']}"
            for turn in self._conversation_history[-20:]
        )
        try:
            client = self.openai_client._get_client()
            response = client.responses.create(
                model=self.openai_client.config.chat_model,
                instructions=(
                    "Extract durable facts or preferences about the user worth "
                    "remembering long-term from this conversation. Return a JSON array "
                    "of short strings (max 8). Only stable facts, preferences, or "
                    "standing instructions — no one-off task details or chit-chat. "
                    "Return [] if nothing is worth keeping."
                ),
                input=[
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": transcript}],
                    }
                ],
                max_output_tokens=300,
            )
            facts = _parse_json_list(self.openai_client.output_text(response))
        except Exception:
            return 0
        if not facts:
            return 0
        saved = 0
        with open_state(self.config) as db:
            existing = {
                str(row.get("content") or "").strip().lower()
                for row in list_memories(db)
            }
            for fact in facts:
                text = str(fact).strip()
                if not text or text.lower() in existing:
                    continue
                memory_id = add_memory(
                    db,
                    category="session_summary",
                    content=text,
                    provenance="session_summary",
                    confidence=0.6,
                    user_confirmed=False,
                    sensitive=False,
                )
                enrich_memory_with_llm(
                    db,
                    memory_id=memory_id,
                    category="session_summary",
                    content=text,
                    provenance="session_summary",
                    confidence=0.6,
                    openai_client=self.openai_client,
                    config=self.config,
                )
                existing.add(text.lower())
                saved += 1
        return saved

    def _execute_tool(
        self,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        approved: bool = False,
        cancellation_token: CancellationToken | None = None,
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
            deep_task_runner=self.run,
            session_state=self._session_state,
            recipes=self.recipes,
            config=self.config,
            approved_tool_call=approved,
            cancellation_token=cancellation_token,
            run_id=run_id,
        )
        started_at = time.monotonic()
        self._set_current_status(
            run_id=run_id,
            stage="executing_tool",
            tool_name=tool_name,
            approved=approved,
        )
        self._trace_event(
            run_id,
            "tool_started",
            component="tool",
            details={"tool_name": tool_name, "approved": approved},
        )
        try:
            result = (
                self.registry.execute_approved(tool_name, arguments, context)
                if approved
                else self.registry.execute(tool_name, arguments, context)
            )
            invalidate_cache = getattr(
                self.computer_backend,
                "invalidate_observation_cache",
                None,
            )
            if callable(invalidate_cache):
                invalidate_cache()
            self._trace_event(
                run_id,
                "tool_finished",
                component="tool",
                status="ok" if result.ok else "error",
                duration_ms=_elapsed_ms(started_at),
                error=None if result.ok else result.message,
                details={"tool_name": tool_name, "approved": approved},
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
            self._trace_event(
                run_id,
                "tool_finished",
                component="tool",
                status="approval_required",
                duration_ms=_elapsed_ms(started_at),
                details={"tool_name": tool_name, "approved": approved},
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
        except AutomationPaused as exc:
            message = str(exc) or "Operation cancelled."
            self._trace_event(
                run_id,
                "tool_finished",
                component="tool",
                status="cancelled",
                duration_ms=_elapsed_ms(started_at),
                error=message,
                details={"tool_name": tool_name, "approved": approved},
            )
            raise
        except Exception as exc:
            message = _human_tool_error(str(exc))
            self._trace_event(
                run_id,
                "tool_finished",
                component="tool",
                status="error",
                duration_ms=_elapsed_ms(started_at),
                error=message,
                details={"tool_name": tool_name, "approved": approved},
            )
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

    def _known_memories(self, limit: int = 40) -> list[dict[str, Any]]:
        if self.config is None:
            return []
        try:
            with open_state(self.config) as db:
                graph_rows = memory_context_packet(db, limit=limit, config=self.config)
                if graph_rows:
                    return [
                        {
                            "category": "graph",
                            "content": str(row.get("content") or ""),
                            "confidence": row.get("confidence"),
                            "active": row.get("active"),
                        }
                        for row in graph_rows
                        if row.get("content")
                    ][:limit]
                rows = list_memories(db)
        except Exception:
            return []
        memories: list[dict[str, Any]] = []
        for row in rows:
            category = str(row.get("category") or "")
            if category == "profile" or category.startswith("machine_"):
                continue
            content = str(row.get("content") or "").strip()
            if content:
                memories.append({"category": category, "content": content})
            if len(memories) >= limit:
                break
        return memories

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
                "user_request": str(
                    self._session_state.get("current_user_request") or ""
                ),
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
            "user_request": str(self._session_state.get("current_user_request") or ""),
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

    def _trace_event(
        self,
        run_id: str,
        event_name: str,
        *,
        component: str = "agent",
        status: str = "ok",
        duration_ms: float | None = None,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.config is None:
            return
        try:
            with open_state(self.config) as db:
                record_trace_event(
                    db,
                    run_id=run_id,
                    event_name=event_name,
                    component=component,
                    status=status,
                    finished_at=now_iso() if duration_ms is not None else None,
                    duration_ms=duration_ms,
                    error=error,
                    details=details,
                )
        except Exception:
            return

    def _trace_call(
        self,
        run_id: str,
        event_name: str,
        component: str,
        call: Callable[[], T],
        *,
        details: dict[str, Any] | None = None,
    ) -> T:
        started_at = time.monotonic()
        self._set_current_status(
            run_id=run_id,
            stage=event_name,
            component=component,
        )
        try:
            value = call()
        except Exception as exc:
            self._trace_event(
                run_id,
                event_name,
                component=component,
                status="error",
                duration_ms=_elapsed_ms(started_at),
                error=str(exc),
                details=details,
            )
            raise
        self._trace_event(
            run_id,
            event_name,
            component=component,
            duration_ms=_elapsed_ms(started_at),
            details=details,
        )
        return value

    def _complete_run(
        self,
        run_id: str,
        started_at: float,
        result: AgentRunResult,
        *,
        status: str = "ok",
        error: str | None = None,
    ) -> AgentRunResult:
        self._trace_event(
            run_id,
            "run_finished",
            status=status,
            duration_ms=_elapsed_ms(started_at),
            error=error,
            details={"ok": result.ok, "message_preview": result.message[:220]},
        )
        self._set_current_status()
        return result

    def _set_current_status(self, **status: Any) -> None:
        self._current_status = {key: value for key, value in status.items() if value}

    def _remember_turn(self, user_request: str, response: str) -> None:
        self._conversation_history.append({"role": "user", "text": user_request})
        self._conversation_history.append({"role": "assistant", "text": response})
        if len(self._conversation_history) > 12:
            del self._conversation_history[:-12]


def _parse_json_list(raw: str) -> list[Any]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            parsed = json.loads(text[start : end + 1])
        except Exception:
            return []
    return parsed if isinstance(parsed, list) else []


def _planner_instructions() -> str:
    return """You are the Iris agent planner.

Return exactly one JSON object and no markdown.

Valid shapes:
{"type":"tool_call","tool_name":"name","arguments":{},"spoken_response":"","reason":"why"}
{"type":"tool_call","goal":"what the user wants","next_tool":"name","args":{},"expected_observation":"what should change","done_condition":"how to verify","user_message":"short progress phrase"}
{"type":"final_answer","spoken_response":"short natural answer","reason":"why no tool is needed"}
{"type":"approval_request","tool_name":"name","arguments":{},"spoken_response":"what needs approval","reason":"risk"}

Rules:
- Voice style matters. Final answers should sound like Iris is in the room with the user: calm, warm, grounded, and lightly playful only when the user is casual.
- Avoid canned assistant phrases such as "How can I help you today?", "I'm here to assist", "Sure thing!", or repeated generic check-ins. Vary wording naturally.
- Do not over-compress every answer into one sentence. Use one sentence for simple confirmations, two or three for conversation, guidance, or a result that needs context.
- Use the user's preferred first name occasionally, not every turn.
- For casual chat, respond like a person before steering back to the task.
- For tool results, translate the result into plain speech. Do not read raw URLs, JSON, stack traces, command syntax, or approval IDs aloud unless needed.
- Hardcoded app-command parsing is not available. Choose tools from available_tools.
- Choose generic tools. Use recipe_run when a listed action_recipe directly matches a multi-step goal that needs verification; private recipes will request approval from their manifest.
- available_connectors tells you which app/service manifests exist, whether they are enabled/configured, and which generic tools they expose.
- If session_context.state.last_approved_tool_observation exists, treat it as the latest observation from the approved action. Use it to answer or choose a non-duplicate next verification step; do not repeat the same approved read/open tool unless the observation is clearly insufficient.
- If a connector is disabled or missing credentials, use integration_status or explain the setup instead of pretending it works.
- Prefer generic tools: browser_ensure_runtime/browser_open/browser_tabs/browser_current_page/browser_get_dom/browser_focus_element/browser_click_element/browser_type_into/browser_submit/browser_extract, app_windows/app_inspect/app_find_element/app_click_element/app_menu_select/app_type_text/app_hotkey, screen_describe/screen_find_element/screen_click_element, audio_current_media/audio_explain_current_song, media_search/media_play/media_pause, app_volume_set, connector_list, integration_status, task_start_background/task_status, calendar_find_event, reminder_create, message_send, gmail_create_draft, memory_search/memory_related/memory_explain/memory_review_list/memory_review_decide/memory_confirm/knowledge_search/machine_context/file_find/file_open/file_rename/workflow_run/recipe_run.
- Use live_screen_context as current state, but call describe_screen for current-screen/current-tab questions.
- Use memory_search for durable facts/preferences Iris has learned, memory_related for relationship questions, and memory_explain when the user asks why Iris believes a memory.
- Use memory_review_list for pending memory approvals and memory_confirm/memory_review_decide when the user explicitly approves, rejects, or supersedes a pending memory.
- Use knowledge_search when the user asks what Iris knows/remembers from local notes, docs, project context, prior logs, personal wiki, or ingested files.
- For media requests such as playing music or playing the current browser video, use media_play/media_search or a matching recipe. Do not use open_app for media playback.
- For "turn it down in Spotify" or similar per-app volume requests, use app_volume_set before set_volume.
- For larger goals that should keep working after the conversation, use task_start_background and include a concrete goal.
- For "is X connected/configured", use integration_status.
- For calendar/reminder actions, use calendar_find_event or reminder_create; these may require approval because they touch private data or create records.
- For private dashboards, messages, inboxes, calendars, and account pages, use a matching recipe or generic private tool and request approval before reading or writing private data. Never handle credentials; ask the user to sign in manually if needed.
- For email draft requests, use gmail_create_draft when the user wants the draft created in the browser. Use draft_email only when they ask for a local draft or when the recipient/body is incomplete. If the user says "create the draft" after a local draft, use gmail_create_draft with the previous draft context.
- For renaming files or folders, use file_rename. If the user refers to a recently listed/found item, pass that phrase in target and the requested new name in new_name; the tool can resolve it from session context and will ask approval.
- If the user says "play it", "click it", "do it", or otherwise refers to a previous media/search action, use media_play or the relevant browser/screen tool instead of repeating the search.
- For browser navigation, use browser_open with new_tab=false unless the user explicitly asks for a new tab.
- If the user asks to look something up online, browse public results, research a person/company/topic, or summarize what is online, use web_research instead of only opening a search page.
- If the user asks for current/latest/trending/best right now recommendations, use web_research before choosing or playing anything. Do not invent current chart/music answers from memory.
- If the user asks to play a current/trending song, first use web_research to identify a likely song, then use media_play with that specific title/artist.
- If the user asks what song is currently playing or asks you to listen to background audio, use audio_current_media first, then audio_identify_playing. Do not use identify_song for ambient audio, and do not open a browser lyrics search unless the user explicitly asks to search the web.
- If the user gives lyric fragments as text and asks for identification, use identify_song instead of search_web.
- For private app/site content such as messages, inboxes, calendars, or account pages, ask approval before extraction or summarization.
- For clicking or visual UI work, prefer browser_click_element/browser_click and app_find_element/app_click_element, then screen_find_element/screen_click_element; use computer_use only when safer layers cannot act.
- You always control this Mac through the local tools (app/browser/screen primitives). These never require any external model and are always available. Never tell the user that a capability, model, or backend is unavailable, off, or not enabled. If one approach is blocked, silently pick another local tool and keep going. Never mention which backend or model you used.
- You accomplish novel requests by chaining these general primitives (open/navigate, read the screen, find an element, click, type, verify). Do not refuse a reasonable request just because there is no single dedicated tool for it.
- For system tasks with no dedicated tool (installing an app, running a script, file system work), use run_shell, e.g. "brew install --cask spotify". It asks for approval once; pick the simplest correct command.
- available_tools may include external tools named mcp__<server>__<tool> (with a [server] prefix in the description). These come from connected MCP services and are real, usable tools — prefer them when one matches the request.
- If app_open reports an app is not installed, do not stop: open the web version with browser_open, look for an installer with file_find, or ask the owner — choose the most useful next step.
- If a tool fails with a retryable observation, choose a fallback tool instead of giving up.
- Memory: when the user states a durable preference, fact about themselves, or a standing instruction ("always...", "from now on...", "I prefer...", "remember that...", "my X is..."), silently call remember with a concise content string and a category (preferences, facts, contacts, etc.) as one step of the turn. Do not ask permission and do not announce that you saved it unless asked.
- Apply remembered context: known_memories in the input holds what you have learned about the user. Honor stored preferences (e.g. "use the Spotify app, not the browser") when choosing tools.
- For "what do you know about me" or recall questions, use recall or memory_search (and knowledge_search for ingested notes); answer from known_memories, graph memory, and the user_profile, not from guesses.
- Verify actions when possible before claiming completion.
- Honesty: only state that an action happened or describe a result if it is supported by an observation in this turn. Never invent outcomes (e.g. a song that started, a message that sent). If you have not verified, say what you attempted, not what you assume happened.
- Do not request destructive, payment, credential, security, or sending actions without approval.
- Keep final spoken_response conversational and human. It should feel spoken, specific, and alive, not like a generic support bot."""


def _initial_session_state() -> dict[str, Any]:
    return {
        "active_app": "",
        "active_tab": {},
        "last_goal": "",
        "last_selected_element": {},
        "last_media_target": {},
        "last_file_target": "",
        "last_verification": {},
    }


def _update_session_from_observation(
    session_state: dict[str, Any], observation: dict[str, Any]
) -> None:
    payload = observation.get("payload")
    if not isinstance(payload, dict):
        return
    tool_name = str(observation.get("tool_name") or "")
    if tool_name.startswith("browser_"):
        title = str(payload.get("title") or "")
        url = str(payload.get("url") or payload.get("last_opened_url") or "")
        if title or url:
            session_state["active_tab"] = {"title": title, "url": url}
    if tool_name in {"browser_click_element", "browser_focus_element"}:
        session_state["last_selected_element"] = {
            "label": payload.get("label") or "",
            "role": payload.get("role") or "",
            "tag": payload.get("tag") or "",
            "url": payload.get("url") or "",
        }
    if tool_name.startswith("app_"):
        app = str(payload.get("app") or "")
        if app:
            session_state["active_app"] = app
    if tool_name.startswith("media_") or tool_name.startswith("audio_"):
        session_state["last_media_target"] = payload
    if tool_name.startswith("file_"):
        if payload.get("new_path"):
            session_state["last_file_target"] = str(payload["new_path"])
        elif payload.get("path"):
            session_state["last_file_target"] = str(payload["path"])
    if observation.get("done_condition") or "verified" in payload:
        session_state["last_verification"] = {
            "tool_name": tool_name,
            "ok": observation.get("ok"),
            "message": observation.get("message"),
            "done_condition": observation.get("done_condition"),
            "payload": payload,
        }


def _planner_result_from_json(text: str) -> PlannerResult:
    try:
        data = _extract_json_object(text)
    except Exception:
        return PlannerResult(
            type="final_answer",
            spoken_response="I lost the thread for a second. Say that again and I’ll handle it.",
            reason="planner_parse_error",
        )
    result_type = str(
        data.get("type") or ("tool_call" if data.get("next_tool") else "final_answer")
    )
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


def _approval_matches_pending(
    approval_row: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
) -> bool:
    if str(approval_row.get("action_name") or "") != tool_name:
        return False
    details = approval_details(approval_row)
    stored_arguments = details.get("arguments")
    return not isinstance(stored_arguments, dict) or stored_arguments == arguments


def _elapsed_ms(started_at: float) -> float:
    return round((time.monotonic() - started_at) * 1000.0, 2)


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
    if (
        "javascript through applescript is turned off" in message.lower()
        or "allow javascript from apple events" in message.lower()
    ):
        return "Chrome automation is off, so I’ll use screen clicks instead."
    if "OpenAI Responses API failed" in message:
        return "The model call failed before I could finish that step."
    return message
