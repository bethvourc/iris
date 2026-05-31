from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RecipeStep:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    expected_observation: str = ""
    continue_on_error: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "expected_observation": self.expected_observation,
            "continue_on_error": self.continue_on_error,
        }


@dataclass(frozen=True)
class ActionRecipe:
    name: str
    description: str
    service: str
    tools: tuple[str, ...]
    private: bool = False
    required_connectors: tuple[str, ...] = ()
    steps: tuple[RecipeStep, ...] = ()
    fallback_steps: tuple[RecipeStep, ...] = ()
    done_condition: str = ""
    verification_tool: str = ""
    approval_gates: tuple[str, ...] = ()
    rollback: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "service": self.service,
            "tools": list(self.tools),
            "private": self.private,
            "required_connectors": list(self.required_connectors),
            "steps": [step.schema() for step in self.steps],
            "fallback_steps": [step.schema() for step in self.fallback_steps],
            "done_condition": self.done_condition,
            "verification_tool": self.verification_tool,
            "approval_gates": list(self.approval_gates),
            "rollback": self.rollback,
            "metadata": self.metadata,
        }


class ActionRecipeRegistry:
    def __init__(self, recipes: list[ActionRecipe] | None = None) -> None:
        self._recipes: dict[str, ActionRecipe] = {}
        for recipe in recipes or []:
            self.register(recipe)

    def register(self, recipe: ActionRecipe) -> None:
        self._recipes[recipe.name] = recipe

    def get(self, name: str) -> ActionRecipe | None:
        return self._recipes.get(name)

    def find_for_service(self, service: str) -> ActionRecipe | None:
        normalized = service.strip().lower()
        for recipe in self._recipes.values():
            if recipe.service == normalized:
                return recipe
        return None

    def schemas(self) -> list[dict[str, Any]]:
        return [recipe.schema() for recipe in self._recipes.values()]

    @classmethod
    def default(cls, project_root: Path | None = None) -> "ActionRecipeRegistry":
        registry = cls()
        for directory in default_recipe_dirs(project_root):
            for recipe in load_recipe_directory(directory):
                registry.register(recipe)
        if not registry._recipes:
            for recipe in _fallback_recipes():
                registry.register(recipe)
        return registry


def default_recipe_dirs(project_root: Path | None = None) -> list[Path]:
    repo_root = Path(__file__).resolve().parents[2]
    dirs = [repo_root / "recipes"]
    if project_root is not None:
        dirs.append(project_root / "recipes")
    custom = os.getenv("IRIS_RECIPE_DIR")
    if custom:
        dirs.append(Path(custom).expanduser())
    dirs.append(Path.home() / ".iris" / "recipes")
    return dirs


def load_recipe_directory(directory: Path) -> list[ActionRecipe]:
    if not directory.exists():
        return []
    recipes: list[ActionRecipe] = []
    for path in sorted(directory.glob("*.json")):
        recipes.extend(load_recipe_file(path))
    return recipes


def load_recipe_file(path: Path) -> list[ActionRecipe]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    raw_recipes = data.get("recipes") if isinstance(data, dict) else data
    if not isinstance(raw_recipes, list):
        return []
    recipes: list[ActionRecipe] = []
    for item in raw_recipes:
        if not isinstance(item, dict):
            continue
        recipe = _recipe_from_dict(item)
        if recipe:
            recipes.append(recipe)
    return recipes


def _recipe_from_dict(item: dict[str, Any]) -> ActionRecipe | None:
    name = str(item.get("name") or "").strip()
    if not name:
        return None
    steps = tuple(_step_from_dict(step) for step in item.get("steps", []) if isinstance(step, dict))
    fallback_steps = tuple(
        _step_from_dict(step) for step in item.get("fallback_steps", []) if isinstance(step, dict)
    )
    tools = item.get("tools")
    if isinstance(tools, list):
        tool_names = tuple(str(tool).strip() for tool in tools if str(tool).strip())
    else:
        ordered = [step.tool for step in steps + fallback_steps if step.tool]
        tool_names = tuple(dict.fromkeys(ordered))
    return ActionRecipe(
        name=name,
        description=str(item.get("description") or ""),
        service=str(item.get("service") or name).strip().lower(),
        tools=tool_names,
        private=bool(item.get("private")),
        required_connectors=tuple(
            str(connector).strip()
            for connector in item.get("required_connectors", [])
            if str(connector).strip()
        ),
        steps=steps,
        fallback_steps=fallback_steps,
        done_condition=str(item.get("done_condition") or ""),
        verification_tool=str(item.get("verification_tool") or ""),
        approval_gates=tuple(
            str(gate).strip()
            for gate in item.get("approval_gates", [])
            if str(gate).strip()
        ),
        rollback=str(item.get("rollback") or ""),
        metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
    )


def _step_from_dict(item: dict[str, Any]) -> RecipeStep:
    return RecipeStep(
        tool=str(item.get("tool") or ""),
        args=item.get("args") if isinstance(item.get("args"), dict) else {},
        expected_observation=str(item.get("expected_observation") or ""),
        continue_on_error=bool(item.get("continue_on_error")),
    )


def _fallback_recipes() -> list[ActionRecipe]:
    return [
        ActionRecipe(
            name="spotify_play_song",
            description="Search and play a song using native Spotify, browser CDP, and screen fallback.",
            service="spotify",
            tools=("media_play", "browser_media_state", "screen_click_element"),
            done_condition="A Spotify app or browser page shows playback has started.",
            verification_tool="audio_current_media",
        ),
        ActionRecipe(
            name="stripe_revenue_check",
            description="Open Stripe dashboard, read today's sales/revenue, and report only verified visible values.",
            service="stripe",
            tools=("browser_open", "browser_get_dom", "browser_extract", "screen_describe"),
            private=True,
            required_connectors=("stripe",),
            approval_gates=("read_account_dashboard",),
            done_condition="Today's Stripe revenue, sales, or gross volume is visible and extracted.",
            verification_tool="browser_verify_state",
        ),
    ]
