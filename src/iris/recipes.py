from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ActionRecipe:
    name: str
    description: str
    service: str
    tools: tuple[str, ...]
    private: bool = False


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
        return [
            {
                "name": recipe.name,
                "description": recipe.description,
                "service": recipe.service,
                "tools": list(recipe.tools),
                "private": recipe.private,
            }
            for recipe in self._recipes.values()
        ]

    @classmethod
    def default(cls) -> "ActionRecipeRegistry":
        return cls(
            [
                ActionRecipe(
                    name="spotify_media",
                    description=(
                        "Play, search, pause, and inspect Spotify through native app "
                        "controls first, then browser automation, then screen fallback."
                    ),
                    service="spotify",
                    tools=(
                        "audio_current_media",
                        "media_search",
                        "browser_play_media",
                        "screen_find_element",
                        "screen_click_element",
                    ),
                ),
                ActionRecipe(
                    name="gmail_browser",
                    description=(
                        "Search Gmail and extract visible messages through browser "
                        "automation after approval for private content."
                    ),
                    service="gmail",
                    tools=("browser_open", "browser_extract"),
                    private=True,
                ),
            ]
        )
