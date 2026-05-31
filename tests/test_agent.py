from __future__ import annotations

import unittest

from iris.actions import RiskLevel
from iris.agent import AgentExecutor, PlannerResult, _planner_result_from_json
from iris.integrations.google_vision import GoogleVisionClient
from iris.perception import ScreenContext
from iris.safety import SafetyGate
from iris.tools import ApprovalRequired, PLANNER_HIDDEN_TOOLS, ToolRegistry, ToolResult, ToolSpec


class StaticPlanner:
    def __init__(self, result: PlannerResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def plan(self, **kwargs) -> PlannerResult:  # noqa: ANN003
        self.calls.append(kwargs)
        return self.result


class SequencePlanner:
    def __init__(self, *results: PlannerResult) -> None:
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    def plan(self, **kwargs) -> PlannerResult:  # noqa: ANN003
        self.calls.append(kwargs)
        if self.results:
            return self.results.pop(0)
        return PlannerResult(type="final_answer", spoken_response="done")


class FakePerception:
    def screen_context(self) -> ScreenContext:
        return ScreenContext(active_app="Terminal", active_window="iris")


class FakeOpenAI:
    available = False


class FakeController:
    pass


class AgentExecutorTests(unittest.TestCase):
    def test_executes_tool_selected_by_planner(self) -> None:
        calls: list[dict[str, object]] = []

        def execute(arguments, context):  # noqa: ANN001
            calls.append(arguments)
            return ToolResult(True, "tool ran", {"ok": True})

        registry = ToolRegistry(
            [
                ToolSpec(
                    name="example_tool",
                    description="Example low-risk tool.",
                    parameters={"type": "object", "properties": {}, "required": []},
                    risk=RiskLevel.LOW_RISK,
                    execute=execute,
                )
            ]
        )
        executor = AgentExecutor(
            planner=StaticPlanner(
                PlannerResult(
                    type="tool_call",
                    tool_name="example_tool",
                    arguments={"value": 1},
                )
            ),  # type: ignore[arg-type]
            registry=registry,
            controller=FakeController(),  # type: ignore[arg-type]
            perception=FakePerception(),  # type: ignore[arg-type]
            safety_gate=SafetyGate(confirm=lambda _: True),
            openai_client=FakeOpenAI(),  # type: ignore[arg-type]
            google_vision=GoogleVisionClient(None),
        )
        result = executor.run("run the example")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "tool ran")
        self.assertEqual(calls, [{"value": 1}])

    def test_sensitive_tool_creates_approval_instead_of_blocking_for_terminal_input(self) -> None:
        def execute(arguments, context):  # noqa: ANN001
            raise AssertionError("sensitive tool should not execute before approval")

        registry = ToolRegistry(
            [
                ToolSpec(
                    name="send_email",
                    description="Send an email.",
                    parameters={"type": "object", "properties": {}, "required": []},
                    risk=RiskLevel.SENSITIVE,
                    execute=execute,
                )
            ]
        )
        executor = AgentExecutor(
            planner=StaticPlanner(
                PlannerResult(
                    type="tool_call",
                    tool_name="send_email",
                    arguments={"to": "test@example.com"},
                )
            ),  # type: ignore[arg-type]
            registry=registry,
            controller=FakeController(),  # type: ignore[arg-type]
            perception=FakePerception(),  # type: ignore[arg-type]
            safety_gate=SafetyGate(confirm=lambda _: False),
            openai_client=FakeOpenAI(),  # type: ignore[arg-type]
            google_vision=GoogleVisionClient(None),
        )
        result = executor.run("send an email")
        self.assertFalse(result.ok)
        self.assertIn("needs approval", result.message)

    def test_approve_pending_executes_stored_sensitive_tool(self) -> None:
        calls: list[dict[str, object]] = []

        def execute(arguments, context):  # noqa: ANN001
            calls.append(arguments)
            return ToolResult(True, "sent draft")

        registry = ToolRegistry(
            [
                ToolSpec(
                    name="send_email",
                    description="Send an email.",
                    parameters={"type": "object", "properties": {}, "required": []},
                    risk=RiskLevel.SENSITIVE,
                    execute=execute,
                )
            ]
        )
        executor = AgentExecutor(
            planner=StaticPlanner(
                PlannerResult(
                    type="tool_call",
                    tool_name="send_email",
                    arguments={"to": "test@example.com"},
                )
            ),  # type: ignore[arg-type]
            registry=registry,
            controller=FakeController(),  # type: ignore[arg-type]
            perception=FakePerception(),  # type: ignore[arg-type]
            safety_gate=SafetyGate(confirm=lambda _: False),
            openai_client=FakeOpenAI(),  # type: ignore[arg-type]
            google_vision=GoogleVisionClient(None),
        )
        pending = executor.run("send an email")
        self.assertFalse(pending.ok)
        approved = executor.approve_pending()
        self.assertTrue(approved.ok)
        self.assertEqual(approved.message, "sent draft")
        self.assertEqual(calls, [{"to": "test@example.com"}])

    def test_approve_pending_preserves_nested_followup_approval(self) -> None:
        def execute(arguments, context):  # noqa: ANN001
            raise ApprovalRequired("type_text", {"text": "private"}, "typing text")

        registry = ToolRegistry(
            [
                ToolSpec(
                    name="read_private_page",
                    description="Read private page.",
                    parameters={"type": "object", "properties": {}, "required": []},
                    risk=RiskLevel.SENSITIVE,
                    execute=execute,
                ),
                ToolSpec(
                    name="type_text",
                    description="Type text.",
                    parameters={"type": "object", "properties": {}, "required": []},
                    risk=RiskLevel.SENSITIVE,
                    execute=lambda _args, _context: ToolResult(True, "typed"),
                ),
            ]
        )
        executor = AgentExecutor(
            planner=StaticPlanner(
                PlannerResult(type="tool_call", tool_name="read_private_page", arguments={})
            ),  # type: ignore[arg-type]
            registry=registry,
            controller=FakeController(),  # type: ignore[arg-type]
            perception=FakePerception(),  # type: ignore[arg-type]
            safety_gate=SafetyGate(confirm=lambda _: False),
            openai_client=FakeOpenAI(),  # type: ignore[arg-type]
            google_vision=GoogleVisionClient(None),
        )
        executor.run("read it")
        approved = executor.approve_pending()
        self.assertFalse(approved.ok)
        self.assertIn("needs approval", approved.message)
        self.assertEqual(executor._session_state["pending_approval"]["tool_name"], "type_text")

    def test_blocked_tool_is_refused(self) -> None:
        registry = ToolRegistry(
            [
                ToolSpec(
                    name="purchase",
                    description="Purchase something.",
                    parameters={"type": "object", "properties": {}, "required": []},
                    risk=RiskLevel.BLOCKED,
                    execute=lambda _args, _context: ToolResult(True, "bought"),
                )
            ]
        )
        executor = AgentExecutor(
            planner=StaticPlanner(
                PlannerResult(type="tool_call", tool_name="purchase", arguments={})
            ),  # type: ignore[arg-type]
            registry=registry,
            controller=FakeController(),  # type: ignore[arg-type]
            perception=FakePerception(),  # type: ignore[arg-type]
            safety_gate=SafetyGate(confirm=lambda _: True),
            openai_client=FakeOpenAI(),  # type: ignore[arg-type]
            google_vision=GoogleVisionClient(None),
        )
        result = executor.run("buy it")
        self.assertFalse(result.ok)
        self.assertIn("Blocked action", result.message)

    def test_malformed_planner_json_becomes_human_retry_message(self) -> None:
        result = _planner_result_from_json(
            '{"type":"tool_call","tool_name":"gmail_search_and_summarize" '
            '"arguments":{"query":"from:Sleazy"}}'
        )
        self.assertEqual(result.type, "final_answer")
        self.assertIn("lost the thread", result.spoken_response)

    def test_planner_json_parser_ignores_trailing_text(self) -> None:
        result = _planner_result_from_json(
            '{"type":"final_answer","spoken_response":"Sure.","reason":"chat"} extra'
        )
        self.assertEqual(result.type, "final_answer")
        self.assertEqual(result.spoken_response, "Sure.")

    def test_agent_step_shape_maps_next_tool_and_args(self) -> None:
        result = _planner_result_from_json(
            '{"type":"tool_call","goal":"play music","next_tool":"media_play",'
            '"args":{"query":"Born to Win"},"expected_observation":"music starts",'
            '"done_condition":"media metadata changes","user_message":"I’ll try Spotify."}'
        )
        self.assertEqual(result.type, "tool_call")
        self.assertEqual(result.tool_name, "media_play")
        self.assertEqual(result.arguments, {"query": "Born to Win"})
        self.assertEqual(result.goal, "play music")
        self.assertEqual(result.done_condition, "media metadata changes")

    def test_retryable_tool_error_becomes_observation_for_fallback_plan(self) -> None:
        def retrying_tool(arguments, context):  # noqa: ANN001
            return ToolResult(
                False,
                "Chrome automation is off, so I’ll use screen clicks instead.",
                {"retry_with_screen": True},
                continue_planning=True,
            )

        registry = ToolRegistry(
            [
                ToolSpec(
                    name="browser_play_media",
                    description="Retryable browser media tool.",
                    parameters={"type": "object", "properties": {}, "required": []},
                    risk=RiskLevel.LOW_RISK,
                    execute=retrying_tool,
                )
            ]
        )
        planner = SequencePlanner(
            PlannerResult(type="tool_call", tool_name="browser_play_media", arguments={}),
            PlannerResult(type="final_answer", spoken_response="I’ll use the screen fallback."),
        )
        executor = AgentExecutor(
            planner=planner,  # type: ignore[arg-type]
            registry=registry,
            controller=FakeController(),  # type: ignore[arg-type]
            perception=FakePerception(),  # type: ignore[arg-type]
            safety_gate=SafetyGate(confirm=lambda _: True),
            openai_client=FakeOpenAI(),  # type: ignore[arg-type]
            google_vision=GoogleVisionClient(None),
        )
        result = executor.run("play music")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "I’ll use the screen fallback.")
        self.assertEqual(len(planner.calls), 2)
        self.assertEqual(
            planner.calls[1]["observations"],  # type: ignore[index]
            [
                {
                    "tool_name": "browser_play_media",
                    "arguments": {},
                    "ok": False,
                    "message": "Chrome automation is off, so I’ll use screen clicks instead.",
                    "payload": {"retry_with_screen": True},
                    "expected_observation": "",
                    "done_condition": "",
                }
            ],
        )

    def test_done_condition_keeps_agent_loop_running_for_verification(self) -> None:
        calls = 0

        def execute(arguments, context):  # noqa: ANN001
            nonlocal calls
            calls += 1
            return ToolResult(True, "clicked play", {"state": "unknown"})

        registry = ToolRegistry(
            [
                ToolSpec(
                    name="browser_click",
                    description="Click browser element.",
                    parameters={"type": "object", "properties": {}, "required": []},
                    risk=RiskLevel.LOW_RISK,
                    execute=execute,
                )
            ]
        )
        planner = SequencePlanner(
            PlannerResult(
                type="tool_call",
                tool_name="browser_click",
                arguments={"text": "Play"},
                expected_observation="playback should start",
                done_condition="media state is playing",
            ),
            PlannerResult(type="final_answer", spoken_response="It’s playing now."),
        )
        executor = AgentExecutor(
            planner=planner,  # type: ignore[arg-type]
            registry=registry,
            controller=FakeController(),  # type: ignore[arg-type]
            perception=FakePerception(),  # type: ignore[arg-type]
            safety_gate=SafetyGate(confirm=lambda _: True),
            openai_client=FakeOpenAI(),  # type: ignore[arg-type]
            google_vision=GoogleVisionClient(None),
        )
        result = executor.run("play it")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "It’s playing now.")
        self.assertEqual(calls, 1)
        self.assertEqual(len(planner.calls), 2)
        self.assertEqual(
            planner.calls[1]["observations"][0]["done_condition"],  # type: ignore[index]
            "media state is playing",
        )

    def test_planner_schema_hides_legacy_app_specific_tools(self) -> None:
        schemas = ToolRegistry.default().schemas()
        names = {schema["name"] for schema in schemas}
        self.assertNotIn("gmail_search", names)
        self.assertNotIn("gmail_search_and_summarize", names)
        self.assertNotIn("media_search_or_play", names)
        self.assertNotIn("media_play_current", names)
        self.assertNotIn("open_app", names)
        self.assertIn("app_open", names)
        self.assertIn("browser_open", names)
        self.assertIn("media_play", names)
        self.assertIn("browser_extract", names)
        self.assertIn("audio_explain_current_song", names)
        self.assertTrue({"gmail_search", "media_search_or_play"} <= PLANNER_HIDDEN_TOOLS)


if __name__ == "__main__":
    unittest.main()
