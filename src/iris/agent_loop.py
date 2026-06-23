"""Deliberate-loop primitives for the agent executor.

This module holds the *pure* logic behind two capabilities layered onto the
agent's ReAct loop (see ``agent.py``):

1. **Planning / decomposition** — the sub-goal model the executor works through
   one at a time (``Plan`` / ``SubGoal`` / ``coerce_subgoals``). The decision of
   *whether* to decompose lives in the planner itself (it returns a ``plan``
   shape), so there is no heuristic here.
2. **Stuck detection** — recognising when the loop is spinning (repeating the
   same action, or making no visible progress) so the executor can escalate to a
   different approach and, failing that, stop and ask the user
   (``normalize_call`` / ``observation_signature`` / ``detect_stuck``).

Everything here is deliberately side-effect free so it can be unit-tested in
isolation and reasoned about without the rest of the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any


@dataclass
class SubGoal:
    """One step of a plan. ``status`` is updated as the executor works."""

    goal: str
    done_condition: str = ""
    status: str = "pending"  # pending | done | failed


@dataclass
class Plan:
    """An ordered list of sub-goals derived from a user request."""

    subgoals: list[SubGoal] = field(default_factory=list)

    @property
    def is_multi(self) -> bool:
        return len(self.subgoals) > 1

    @classmethod
    def single(cls, goal: str) -> "Plan":
        return cls(subgoals=[SubGoal(goal=goal)])


# --- Decomposition -----------------------------------------------------------

# Cap so a runaway planner can't enqueue an unbounded amount of work.
MAX_SUBGOALS = 6


def coerce_subgoals(raw: Any) -> list[SubGoal]:
    """Build a capped list of sub-goals from a planner's ``subgoals`` field.

    Takes the already-parsed value (a list of ``{"goal", "done_condition"}``
    dicts, or bare strings) and ignores anything malformed, so a stray planner
    response can never crash planning or enqueue unbounded work. The complexity
    judgement now lives in the planner itself — it returns a ``plan`` shape only
    when a request genuinely needs several sub-goals — so there is no separate
    heuristic or extra model call here.
    """

    if not isinstance(raw, list):
        return []
    subgoals: list[SubGoal] = []
    for item in raw:
        if isinstance(item, dict):
            goal = str(item.get("goal") or item.get("step") or "").strip()
            done = str(item.get("done_condition") or "").strip()
        else:
            goal, done = str(item or "").strip(), ""
        if goal:
            subgoals.append(SubGoal(goal=goal, done_condition=done))
        if len(subgoals) >= MAX_SUBGOALS:
            break
    return subgoals


# --- Stuck detection ---------------------------------------------------------


def normalize_call(tool_name: str, arguments: dict[str, Any] | None) -> str:
    """Stable signature of a tool call, for spotting exact repeats."""

    payload = json.dumps(arguments or {}, sort_keys=True, default=str)
    return f"{tool_name}:{payload}"


def observation_signature(observation: dict[str, Any]) -> str:
    """Stable signature of an observation's *outcome*.

    Captures the tool, arguments, ok-flag, message and the shape of the payload
    (its keys) — enough to tell "the world changed" from "nothing changed"
    without being so precise that timestamps or ids defeat it.
    """

    payload = observation.get("payload")
    payload_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
    basis = {
        "tool_name": observation.get("tool_name"),
        "arguments": observation.get("arguments") or {},
        "ok": observation.get("ok"),
        "message": observation.get("message"),
        "payload_keys": payload_keys,
    }
    encoded = json.dumps(basis, sort_keys=True, default=str)
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()


def detect_stuck(
    signatures: list[str],
    calls: list[str],
    *,
    repeat_threshold: int = 3,
    window: int = 3,
) -> str | None:
    """Decide whether the loop is stuck.

    Returns a short reason code (``"repeating_action"`` or ``"no_progress"``) or
    ``None``. ``repeat_threshold`` is how many identical *calls* in a row count
    as a repeat; ``window`` is how many identical *outcomes* in a row count as no
    progress.
    """

    if repeat_threshold >= 1 and len(calls) >= repeat_threshold:
        tail = calls[-repeat_threshold:]
        if len(set(tail)) == 1:
            return "repeating_action"
    if window >= 2 and len(signatures) >= window:
        tail = signatures[-window:]
        if len(set(tail)) == 1:
            return "no_progress"
    return None
