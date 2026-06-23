from __future__ import annotations

from pathlib import Path
from typing import Any

from iris.actions import RiskLevel
from iris.agent import AgentExecutor, PlannerResult
from iris.agent_loop import (
    SubGoal,
    coerce_subgoals,
    detect_stuck,
    normalize_call,
    observation_signature,
)
from iris.config import IrisConfig
from iris.safety import SafetyGate
from iris.tools import ToolContext, ToolRegistry, ToolResult, ToolSpec


# --- Pure logic --------------------------------------------------------------


def test_coerce_subgoals_reads_ordered_subgoals() -> None:
    subgoals = coerce_subgoals(
        [
            {"goal": "Open doc", "done_condition": "doc visible"},
            {"goal": "Email it"},
        ]
    )
    assert [s.goal for s in subgoals] == ["Open doc", "Email it"]
    assert subgoals[0].done_condition == "doc visible"


def test_coerce_subgoals_ignores_malformed_entries() -> None:
    assert coerce_subgoals("not a list") == []
    assert coerce_subgoals([{"no_goal": "x"}, {"goal": "  "}]) == []
    assert coerce_subgoals(["bare string"]) == [SubGoal(goal="bare string")]


def test_coerce_subgoals_caps_length() -> None:
    assert len(coerce_subgoals([{"goal": f"step {i}"} for i in range(20)])) == 6


def test_normalize_call_is_argument_order_independent() -> None:
    assert normalize_call("t", {"a": 1, "b": 2}) == normalize_call(
        "t", {"b": 2, "a": 1}
    )
    assert normalize_call("t", {"a": 1}) != normalize_call("t", {"a": 2})


def test_observation_signature_ignores_volatile_payload_values() -> None:
    base = {"tool_name": "read", "arguments": {}, "ok": True, "message": "done"}
    sig_a = observation_signature({**base, "payload": {"value": 1}})
    sig_b = observation_signature({**base, "payload": {"value": 999}})
    sig_c = observation_signature({**base, "payload": {"value": 1, "extra": "x"}})
    assert sig_a == sig_b  # same keys -> same signature
    assert sig_a != sig_c  # different payload shape -> different signature


def test_detect_stuck_flags_repeated_calls() -> None:
    calls = ["t:{}", "t:{}", "t:{}"]
    assert (
        detect_stuck(["a", "b", "c"], calls, repeat_threshold=3) == "repeating_action"
    )


def test_detect_stuck_flags_no_progress() -> None:
    sigs = ["same", "same", "same"]
    calls = ["a:{}", "b:{}", "c:{}"]
    assert detect_stuck(sigs, calls, repeat_threshold=3, window=3) == "no_progress"


def test_detect_stuck_returns_none_when_progressing() -> None:
    assert detect_stuck(["a", "b", "c"], ["x:{}", "y:{}", "z:{}"]) is None


# --- Executor integration ----------------------------------------------------


def test_simple_request_uses_one_planner_call(tmp_path: Path) -> None:
    # A simple request: the first (triage) planner turn answers directly, so
    # there is exactly one planner call and no separate decomposition round-trip.
    planner = _ScriptedPlanner(
        plans=[PlannerResult(type="final_answer", spoken_response="Hi there.")]
    )
    executor = _build_executor(tmp_path, planner)

    result = executor.run("say hi")

    assert result.ok is True
    assert result.message == "Hi there."
    assert planner.plan_calls == 1  # triage turn doubles as the answer


def test_simple_tool_request_seeds_triage_turn(tmp_path: Path) -> None:
    # The triage turn returns a concrete action; it must be executed, not thrown
    # away and re-planned. One planner call total for a one-shot tool request.
    planner = _ScriptedPlanner(
        plans=[
            PlannerResult(type="tool_call", tool_name="noop", arguments={"x": 1}),
            PlannerResult(type="final_answer", spoken_response="All set."),
        ]
    )
    executor = _build_executor(tmp_path, planner)

    result = executor.run("do the noop")

    assert result.ok is True
    assert result.message == "ok"  # the tool's terminal result
    assert planner.plan_calls == 1  # seed used; no re-plan of the same step


def test_repeating_planner_gets_unstuck_and_asks(tmp_path: Path) -> None:
    # A planner that loops the same retryable tool forever.
    looping = PlannerResult(
        type="tool_call",
        tool_name="flaky",
        arguments={"x": 1},
        expected_observation="something changes",
        done_condition="it changed",
    )
    planner = _ScriptedPlanner(plans=[looping], repeat_last=True)
    executor = _build_executor(tmp_path, planner, registry=_flaky_registry())

    result = executor.run("keep trying the flaky thing")

    assert result.ok is False
    assert "blocked" in result.message.lower()  # background maps this to 'blocked'
    # It escalated (injected stuck signals) rather than spinning silently...
    assert planner.saw_stuck_signal is True
    # ...and it stopped well within the global budget rather than looping forever.
    assert planner.plan_calls <= executor.max_total_steps


def test_plan_shape_decomposes_and_runs_each_subgoal(tmp_path: Path) -> None:
    # The triage turn returns a "plan" shape (the model judged it multi-step);
    # each sub-goal then gets its own planning turn.
    planner = _ScriptedPlanner(
        plans=[
            PlannerResult(
                type="plan",
                subgoals=[
                    SubGoal(goal="Open the doc"),
                    SubGoal(goal="Email it to Sam"),
                ],
            ),
            PlannerResult(type="final_answer", spoken_response="Opened the doc."),
            PlannerResult(type="final_answer", spoken_response="Emailed it to Sam."),
        ],
    )
    executor = _build_executor(tmp_path, planner)

    result = executor.run("open the doc and then email it to Sam")

    assert result.ok is True
    assert result.message == "Emailed it to Sam."  # final sub-goal's answer
    assert planner.plan_calls == 3  # triage + one turn per sub-goal
    assert isinstance(result.payload, dict)
    assert [s["status"] for s in result.payload["subgoals"]] == ["done", "done"]


# --- Scripted doubles --------------------------------------------------------


class _ScriptedPlanner:
    def __init__(
        self,
        *,
        plans: list[PlannerResult],
        repeat_last: bool = False,
    ) -> None:
        self._plans = plans
        self._repeat_last = repeat_last
        self._index = 0
        self.plan_calls = 0
        self.saw_stuck_signal = False

    def plan(
        self, *, observations: list[dict[str, Any]], **_kwargs: Any
    ) -> PlannerResult:
        self.plan_calls += 1
        if any(o.get("tool_name") == "_stuck_signal" for o in observations):
            self.saw_stuck_signal = True
        if self._index < len(self._plans):
            result = self._plans[self._index]
            self._index += 1
            return result
        if self._repeat_last:
            return self._plans[-1]
        return PlannerResult(type="final_answer", spoken_response="Done.")


def _flaky_registry() -> ToolRegistry:
    def execute(_arguments: dict[str, Any], _context: ToolContext) -> ToolResult:
        # Retryable error keeps the loop going until stuck-detection intervenes.
        return ToolResult(False, "retry_with_screen: could not act")

    return ToolRegistry(
        [
            ToolSpec(
                name="flaky",
                description="Always-failing retryable test tool.",
                parameters={"type": "object", "properties": {}},
                risk=RiskLevel.LOW_RISK,
                execute=execute,
            )
        ]
    )


def _noop_registry() -> ToolRegistry:
    def execute(_arguments: dict[str, Any], _context: ToolContext) -> ToolResult:
        return ToolResult(True, "ok")

    return ToolRegistry(
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


def _build_executor(
    tmp_path: Path,
    planner: _ScriptedPlanner,
    *,
    registry: ToolRegistry | None = None,
) -> AgentExecutor:
    return AgentExecutor(
        planner=planner,  # type: ignore[arg-type]
        registry=registry or _noop_registry(),
        controller=object(),  # type: ignore[arg-type]
        perception=object(),  # type: ignore[arg-type]
        safety_gate=SafetyGate(confirm=lambda _question: True),
        openai_client=object(),  # type: ignore[arg-type]
        google_vision=object(),  # type: ignore[arg-type]
        computer_backend=_ComputerBackend(),  # type: ignore[arg-type]
        recipes=_Recipes(),  # type: ignore[arg-type]
        config=_config(tmp_path),
    )


class _ComputerBackend:
    def observe(self, *, include_browser: bool = False) -> "_Observation":
        return _Observation(include_browser=include_browser)

    def backend_health(self, _config: IrisConfig | None) -> dict[str, Any]:
        return {"native": {"ok": True}}

    def invalidate_observation_cache(self) -> None:
        return None


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
