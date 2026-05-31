from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import time
from typing import Any, Callable
from urllib import request
from urllib.parse import parse_qs, quote_plus, unquote_plus, urlparse

from iris.accessibility_backend import AccessibilityBackend
from iris.actions import LocalAction, RiskLevel
from iris.browser_cdp import ChromeCDPBackend
from iris.computer import ComputerBackend
from iris.config import IrisConfig
from iris.connectors import connector_health, default_manifest_dirs, get_connector
from iris.integrations.google_vision import GoogleVisionClient
from iris.integrations.openai_client import OpenAIResponsesClient
from iris.knowledge import format_search_results, search_pages
from iris.mac_controller import ActionResult, MacController
from iris.perception import LiveScreenFrame, PerceptionService, ScreenAwarenessService
from iris.recipes import ActionRecipeRegistry
from iris.safety import CancellationToken, SafetyGate, classify_action
from iris.state import open_state
from iris import tasks as task_store
from iris.system import applescript_string, run_osascript


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    message: str
    payload: Any | None = None
    continue_planning: bool = False


@dataclass(frozen=True)
class ToolContext:
    controller: MacController
    perception: PerceptionService
    safety_gate: SafetyGate
    openai_client: OpenAIResponsesClient
    google_vision: GoogleVisionClient
    computer: ComputerBackend | None = None
    screen_awareness: ScreenAwarenessService | None = None
    computer_use_runner: Callable[[str], Any] | None = None
    session_state: dict[str, Any] | None = None
    recipes: ActionRecipeRegistry | None = None
    config: IrisConfig | None = None
    approved_tool_call: bool = False
    cancellation_token: CancellationToken | None = None

    def assert_not_cancelled(self) -> None:
        if self.cancellation_token is not None:
            self.cancellation_token.throw_if_cancelled()


ToolExecute = Callable[[dict[str, Any], ToolContext], ToolResult]


PLANNER_HIDDEN_TOOLS = {
    "open_app",
    "open_url",
    "new_tab",
    "navigate_url",
    "browser_open_url",
    "browser_javascript",
    "gmail_search",
    "gmail_search_and_summarize",
    "inspect_current_tab",
    "describe_screen",
    "ocr_screen",
    "find_visible_element",
    "press_hotkey",
    "type_text",
    "click",
    "scroll",
    "media_control",
    "media_search_or_play",
    "media_play_current",
    "find_file",
    "summarize_pdf",
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    risk: RiskLevel
    execute: ToolExecute

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "risk": str(self.risk),
        }


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec] | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            tool.schema()
            for name, tool in self._tools.items()
            if name not in PLANNER_HIDDEN_TOOLS
        ]

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        context.assert_not_cancelled()
        tool = self.get(name)
        if tool is None:
            return ToolResult(False, f"I do not have a tool named {name}.")
        action = LocalAction(
            tool.name,
            arguments,
            tool.description,
            risk=tool.risk,
        )
        decision = classify_action(action)
        if decision.risk == RiskLevel.BLOCKED:
            raise PermissionError(f"Blocked action: {tool.name} ({decision.reason})")
        if decision.risk == RiskLevel.SENSITIVE:
            raise ApprovalRequired(tool.name, arguments, decision.reason)
        context.safety_gate.allow(action)
        context.assert_not_cancelled()
        result = tool.execute(arguments, context)
        context.assert_not_cancelled()
        return result

    def execute_approved(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        context.assert_not_cancelled()
        tool = self.get(name)
        if tool is None:
            return ToolResult(False, f"I do not have a tool named {name}.")
        action = LocalAction(tool.name, arguments, tool.description, risk=tool.risk)
        decision = classify_action(action)
        if decision.risk == RiskLevel.BLOCKED:
            raise PermissionError(f"Blocked action: {tool.name} ({decision.reason})")
        context.safety_gate.assert_not_killed()
        context.assert_not_cancelled()
        result = tool.execute(arguments, context)
        context.assert_not_cancelled()
        return result

    @classmethod
    def default(cls) -> "ToolRegistry":
        registry = cls()
        for tool in _default_tools():
            registry.register(tool)
        return registry


class ApprovalRequired(PermissionError):
    def __init__(self, tool_name: str, arguments: dict[str, Any], reason: str) -> None:
        super().__init__(f"{tool_name} requires approval: {reason}")
        self.tool_name = tool_name
        self.arguments = arguments
        self.reason = reason


def _default_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="app_open",
            description="Open a macOS application by name.",
            parameters=_object_schema({"app_name": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_open_app,
        ),
        ToolSpec(
            name="app_activate",
            description="Bring an already-open app to the front, opening it if needed.",
            parameters=_object_schema({"app_name": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_app_activate,
        ),
        ToolSpec(
            name="app_inspect",
            description="Inspect the focused native macOS app's Accessibility tree.",
            parameters=_object_schema(
                {
                    "max_depth": {"type": "integer"},
                    "max_items": {"type": "integer"},
                },
                required=[],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_app_inspect,
        ),
        ToolSpec(
            name="app_windows",
            description="List running foreground apps and their visible windows using macOS Accessibility.",
            parameters=_object_schema({}),
            risk=RiskLevel.LOW_RISK,
            execute=_app_windows,
        ),
        ToolSpec(
            name="app_hotkey",
            description="Press a hotkey in the active app.",
            parameters=_object_schema(
                {
                    "key": {"type": "string"},
                    "modifiers": {"type": "array", "items": {"type": "string"}},
                },
                required=["key"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_press_hotkey,
        ),
        ToolSpec(
            name="app_click_text",
            description="Find visible text on screen and click it using screen/vision fallback.",
            parameters=_object_schema({"text": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_screen_click_element,
        ),
        ToolSpec(
            name="app_find_element",
            description="Find an element in the focused app using macOS Accessibility first.",
            parameters=_object_schema({"description": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_app_find_element,
        ),
        ToolSpec(
            name="app_click_element",
            description="Click an element in the focused app by role/name/value using macOS Accessibility.",
            parameters=_object_schema({"description": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_app_click_element,
        ),
        ToolSpec(
            name="app_menu_select",
            description="Select a native app menu item by app name and menu path.",
            parameters=_object_schema(
                {
                    "app_name": {"type": "string"},
                    "menu_path": {"type": "array", "items": {"type": "string"}},
                },
                required=["app_name", "menu_path"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_app_menu_select,
        ),
        ToolSpec(
            name="app_type_text",
            description="Type text into the focused native field. Requires approval.",
            parameters=_object_schema({"text": {"type": "string"}}),
            risk=RiskLevel.SENSITIVE,
            execute=_app_type_text,
        ),
        ToolSpec(
            name="browser_open",
            description="Navigate the current browser tab to a URL unless new_tab is true.",
            parameters=_object_schema(
                {
                    "url": {"type": "string"},
                    "browser": {"type": "string"},
                    "new_tab": {"type": "boolean"},
                },
                required=["url"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_open,
        ),
        ToolSpec(
            name="browser_ensure_runtime",
            description="Start or verify the managed Chrome CDP browser runtime.",
            parameters=_object_schema({}),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_ensure_runtime,
        ),
        ToolSpec(
            name="browser_current_page",
            description="Return the active browser tab title and URL.",
            parameters=_object_schema({"browser": {"type": "string"}}, required=[]),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_current_page,
        ),
        ToolSpec(
            name="browser_tabs",
            description="List Chrome tabs using the Chrome DevTools Protocol when available.",
            parameters=_object_schema({}),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_tabs,
        ),
        ToolSpec(
            name="browser_extract",
            description="Extract visible text from the current browser tab.",
            parameters=_object_schema({"browser": {"type": "string"}}, required=[]),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_extract,
        ),
        ToolSpec(
            name="browser_get_dom",
            description="Inspect the current browser DOM, visible text, and visible controls using CDP.",
            parameters=_object_schema({"max_chars": {"type": "integer"}}, required=[]),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_get_dom,
        ),
        ToolSpec(
            name="browser_click",
            description="Click a visible browser element by text or accessible label.",
            parameters=_object_schema(
                {
                    "text": {"type": "string"},
                    "browser": {"type": "string"},
                },
                required=["text"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_click,
        ),
        ToolSpec(
            name="browser_click_element",
            description="Click a browser element by visible text, role label, or aria label using CDP first.",
            parameters=_object_schema({"text": {"type": "string"}}, required=["text"]),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_click_element,
        ),
        ToolSpec(
            name="browser_type",
            description="Type into the active browser field. Requires approval.",
            parameters=_object_schema({"text": {"type": "string"}}),
            risk=RiskLevel.SENSITIVE,
            execute=_type_text,
        ),
        ToolSpec(
            name="browser_type_into",
            description="Type text into a browser field by selector, placeholder, aria-label, or focused field. Requires approval.",
            parameters=_object_schema(
                {
                    "field": {"type": "string"},
                    "text": {"type": "string"},
                },
                required=["text"],
            ),
            risk=RiskLevel.SENSITIVE,
            execute=_browser_type_into,
        ),
        ToolSpec(
            name="browser_verify_state",
            description="Verify the current browser tab contains expected text, title, or URL.",
            parameters=_object_schema(
                {
                    "text": {"type": "string"},
                    "url_contains": {"type": "string"},
                    "title_contains": {"type": "string"},
                },
                required=[],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_verify_state,
        ),
        ToolSpec(
            name="browser_media_state",
            description="Inspect audio/video and media controls in the current browser tab.",
            parameters=_object_schema({}),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_media_state,
        ),
        ToolSpec(
            name="browser_play_media",
            description="Play media in the active browser using automation, then screen fallback if needed.",
            parameters=_object_schema(
                {
                    "query": {"type": "string"},
                    "service": {"type": "string"},
                    "browser": {"type": "string"},
                },
                required=[],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_play_media,
        ),
        ToolSpec(
            name="screen_describe",
            description="Describe the current visible screen/tab/window from the latest live frame.",
            parameters=_object_schema({"prompt": {"type": "string"}}, required=[]),
            risk=RiskLevel.LOW_RISK,
            execute=_describe_screen,
        ),
        ToolSpec(
            name="screen_ocr",
            description="Extract visible text from the current screen using OCR.",
            parameters=_object_schema({}),
            risk=RiskLevel.LOW_RISK,
            execute=_ocr_screen,
        ),
        ToolSpec(
            name="screen_find_element",
            description="Find or describe a visible screen element before clicking.",
            parameters=_object_schema({"description": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_find_visible_element,
        ),
        ToolSpec(
            name="screen_click_element",
            description="Click a visible element by description using screen/computer-use fallback.",
            parameters=_object_schema({"description": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_screen_click_element,
        ),
        ToolSpec(
            name="audio_current_media",
            description="Detect currently playing media from native apps where possible.",
            parameters=_object_schema({}),
            risk=RiskLevel.LOW_RISK,
            execute=_audio_current_media,
        ),
        ToolSpec(
            name="audio_identify_playing",
            description="Identify currently playing background audio when metadata is unavailable.",
            parameters=_object_schema({}),
            risk=RiskLevel.LOW_RISK,
            execute=_audio_identify_playing,
        ),
        ToolSpec(
            name="audio_explain_current_song",
            description="Explain the currently playing song using media metadata first, then public web context.",
            parameters=_object_schema(
                {
                    "question": {"type": "string"},
                    "artist": {"type": "string"},
                    "title": {"type": "string"},
                },
                required=[],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_audio_explain_current_song,
        ),
        ToolSpec(
            name="integration_status",
            description="Check whether a named external integration/provider is configured for Iris.",
            parameters=_object_schema({"name": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_integration_status,
        ),
        ToolSpec(
            name="connector_list",
            description="List enabled/configured Iris connector manifests and their generic tools.",
            parameters=_object_schema({"category": {"type": "string"}}, required=[]),
            risk=RiskLevel.LOW_RISK,
            execute=_connector_list,
        ),
        ToolSpec(
            name="task_start_background",
            description="Create a durable background agent task for a larger goal, with approval gates handled by the task runtime.",
            parameters=_object_schema(
                {
                    "goal": {"type": "string"},
                    "kind": {"type": "string"},
                    "title": {"type": "string"},
                },
                required=["goal"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_task_start_background,
        ),
        ToolSpec(
            name="task_status",
            description="Report active, queued, or recent durable agent tasks and step logs.",
            parameters=_object_schema({"limit": {"type": "integer"}}, required=[]),
            risk=RiskLevel.LOW_RISK,
            execute=_task_status,
        ),
        ToolSpec(
            name="calendar_find_event",
            description="Search local macOS Calendar events by text and date window. Requires approval because calendar data is private.",
            parameters=_object_schema(
                {
                    "query": {"type": "string"},
                    "days_ahead": {"type": "integer"},
                },
                required=["query"],
            ),
            risk=RiskLevel.SENSITIVE,
            execute=_calendar_find_event,
        ),
        ToolSpec(
            name="reminder_create",
            description="Create a macOS Reminders item, optionally with a due date, then open Reminders for confirmation. Requires approval.",
            parameters=_object_schema(
                {
                    "title": {"type": "string"},
                    "due": {"type": "string"},
                    "open_app": {"type": "boolean"},
                },
                required=["title"],
            ),
            risk=RiskLevel.SENSITIVE,
            execute=_reminder_create,
        ),
        ToolSpec(
            name="media_search",
            description="Search for media using the best recipe for the requested service.",
            parameters=_object_schema(
                {
                    "service": {"type": "string"},
                    "query": {"type": "string"},
                    "surface": {"type": "string", "enum": ["auto", "app", "browser"]},
                    "browser": {"type": "string"},
                },
                required=["query"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_media_search,
        ),
        ToolSpec(
            name="media_play",
            description="Play media using native, browser, then screen fallback.",
            parameters=_object_schema(
                {
                    "service": {"type": "string"},
                    "query": {"type": "string"},
                    "surface": {"type": "string", "enum": ["auto", "app", "browser"]},
                    "browser": {"type": "string"},
                },
                required=[],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_media_play,
        ),
        ToolSpec(
            name="media_pause",
            description="Pause or toggle current media playback.",
            parameters=_object_schema({"service": {"type": "string"}}, required=[]),
            risk=RiskLevel.LOW_RISK,
            execute=_media_pause,
        ),
        ToolSpec(
            name="file_find",
            description="Find local files by name or content query.",
            parameters=_object_schema({"query": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_find_file,
        ),
        ToolSpec(
            name="knowledge_search",
            description="Search Iris local knowledge/wiki pages built from ingested notes, docs, logs, and project files.",
            parameters=_object_schema(
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                required=["query"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_knowledge_search,
        ),
        ToolSpec(
            name="machine_context",
            description="Inspect local machine context: installed apps, common project folders, browser profile, and connector health.",
            parameters=_object_schema({}),
            risk=RiskLevel.LOW_RISK,
            execute=_machine_context,
        ),
        ToolSpec(
            name="file_list_folder",
            description="List files in a local folder such as Downloads, Desktop, or Documents.",
            parameters=_object_schema(
                {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                required=["path"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_file_list_folder,
        ),
        ToolSpec(
            name="file_open",
            description="Open a local file path.",
            parameters=_object_schema({"path": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_file_open,
        ),
        ToolSpec(
            name="file_rename",
            description="Rename a local file or folder. Can resolve references from the last listed/found files. Requires approval.",
            parameters=_object_schema(
                {
                    "path": {"type": "string"},
                    "target": {"type": "string"},
                    "new_name": {"type": "string"},
                },
                required=["new_name"],
            ),
            risk=RiskLevel.SENSITIVE,
            execute=_file_rename,
        ),
        ToolSpec(
            name="file_summarize",
            description="Summarize a local file. PDF support is currently a placeholder.",
            parameters=_object_schema({"path": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_not_yet_connected,
        ),
        ToolSpec(
            name="workflow_run",
            description="Run or draft a named Iris workflow.",
            parameters=_object_schema({"name": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_workflow_run,
        ),
        ToolSpec(
            name="recipe_run",
            description="Run a manifest-backed verified action recipe with inputs and observations. Private recipes request approval from their manifest.",
            parameters=_object_schema(
                {
                    "recipe_name": {"type": "string"},
                    "inputs": {"type": "object"},
                    "max_steps": {"type": "integer"},
                },
                required=["recipe_name"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_recipe_run,
        ),
        ToolSpec(
            name="open_app",
            description="Open a macOS application by name.",
            parameters=_object_schema({"app_name": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_open_app,
        ),
        ToolSpec(
            name="open_url",
            description="Open a URL in the user's default browser or registered app.",
            parameters=_object_schema({"url": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_open_url,
        ),
        ToolSpec(
            name="new_tab",
            description="Open a new browser tab. If url is provided, navigate there.",
            parameters=_object_schema({"url": {"type": "string"}}, required=[]),
            risk=RiskLevel.LOW_RISK,
            execute=_new_tab,
        ),
        ToolSpec(
            name="navigate_url",
            description="Navigate the browser to a URL.",
            parameters=_object_schema({"url": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_open_url,
        ),
        ToolSpec(
            name="browser_open_url",
            description="Open a URL in a specific browser such as Google Chrome.",
            parameters=_object_schema(
                {
                    "url": {"type": "string"},
                    "browser": {"type": "string"},
                },
                required=["url"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_open_url,
        ),
        ToolSpec(
            name="browser_javascript",
            description="Run JavaScript in the active browser tab for browser automation.",
            parameters=_object_schema(
                {
                    "script": {"type": "string"},
                    "browser": {"type": "string"},
                },
                required=["script"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_javascript,
        ),
        ToolSpec(
            name="search_web",
            description="Search the web in the browser.",
            parameters=_object_schema({"query": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_search_web,
        ),
        ToolSpec(
            name="web_research",
            description="Search the public web, read top result snippets/pages, and summarize findings.",
            parameters=_object_schema(
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                required=["query"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_web_research,
        ),
        ToolSpec(
            name="identify_song",
            description="Identify a song from lyrics or a rough spoken lyric fragment using public web results.",
            parameters=_object_schema(
                {
                    "lyrics": {"type": "string"},
                    "artist_hint": {"type": "string"},
                },
                required=["lyrics"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_identify_song,
        ),
        ToolSpec(
            name="gmail_search",
            description="Open Gmail in the browser and search messages with a Gmail search query.",
            parameters=_object_schema(
                {
                    "query": {"type": "string"},
                    "browser": {"type": "string"},
                },
                required=["query"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_gmail_search,
        ),
        ToolSpec(
            name="gmail_search_and_summarize",
            description="Search Gmail, read visible matching message results/content, and summarize it. Requires approval because it reads email content.",
            parameters=_object_schema(
                {
                    "query": {"type": "string"},
                    "browser": {"type": "string"},
                },
                required=["query"],
            ),
            risk=RiskLevel.SENSITIVE,
            execute=_gmail_search_and_summarize,
        ),
        ToolSpec(
            name="gmail_create_draft",
            description="Create a Gmail compose draft with recipient, subject, and body in the browser. Requires approval because it writes to Gmail.",
            parameters=_object_schema(
                {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "browser": {"type": "string"},
                },
                required=[],
            ),
            risk=RiskLevel.SENSITIVE,
            execute=_gmail_create_draft,
        ),
        ToolSpec(
            name="inspect_current_tab",
            description="Inspect the current visible browser tab/window using live screen context.",
            parameters=_object_schema({}),
            risk=RiskLevel.LOW_RISK,
            execute=_describe_screen,
        ),
        ToolSpec(
            name="describe_screen",
            description="Describe the current visible Mac screen/tab/window from the latest live frame.",
            parameters=_object_schema({"prompt": {"type": "string"}}, required=[]),
            risk=RiskLevel.LOW_RISK,
            execute=_describe_screen,
        ),
        ToolSpec(
            name="ocr_screen",
            description="Extract readable text from the current screen using OCR.",
            parameters=_object_schema({}),
            risk=RiskLevel.LOW_RISK,
            execute=_ocr_screen,
        ),
        ToolSpec(
            name="find_visible_element",
            description="Find or describe a visible screen element before clicking or interacting.",
            parameters=_object_schema({"description": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_find_visible_element,
        ),
        ToolSpec(
            name="press_hotkey",
            description="Press a keyboard shortcut.",
            parameters=_object_schema(
                {
                    "key": {"type": "string"},
                    "modifiers": {"type": "array", "items": {"type": "string"}},
                },
                required=["key"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_press_hotkey,
        ),
        ToolSpec(
            name="type_text",
            description="Type text into the active app. Requires approval.",
            parameters=_object_schema({"text": {"type": "string"}}),
            risk=RiskLevel.SENSITIVE,
            execute=_type_text,
        ),
        ToolSpec(
            name="click",
            description="Click a screen coordinate.",
            parameters=_object_schema(
                {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "right"]},
                },
                required=["x", "y"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_click,
        ),
        ToolSpec(
            name="scroll",
            description="Scroll the active app or page.",
            parameters=_object_schema(
                {"dy": {"type": "integer"}, "dx": {"type": "integer"}},
                required=["dy"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_scroll,
        ),
        ToolSpec(
            name="set_volume",
            description="Set Mac output volume from 0 to 100.",
            parameters=_object_schema({"level": {"type": "integer", "minimum": 0, "maximum": 100}}),
            risk=RiskLevel.LOW_RISK,
            execute=_set_volume,
        ),
        ToolSpec(
            name="app_volume_set",
            description="Set volume for a supported app such as Spotify without changing full system volume.",
            parameters=_object_schema(
                {
                    "app_name": {"type": "string"},
                    "level": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                required=["app_name", "level"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_set_app_volume,
        ),
        ToolSpec(
            name="change_volume",
            description="Change Mac output volume by a signed delta.",
            parameters=_object_schema({"delta": {"type": "integer"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_change_volume,
        ),
        ToolSpec(
            name="media_control",
            description="Control media playback for a service such as Spotify.",
            parameters=_object_schema(
                {
                    "service": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["play_pause", "next", "previous"],
                    },
                },
                required=["action"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_media_control,
        ),
        ToolSpec(
            name="media_search_or_play",
            description="Search for or play media on a service. Supports Spotify app or browser.",
            parameters=_object_schema(
                {
                    "service": {"type": "string"},
                    "query": {"type": "string"},
                    "surface": {"type": "string", "enum": ["auto", "app", "browser"]},
                    "action": {"type": "string", "enum": ["search", "play"]},
                    "browser": {"type": "string"},
                },
                required=["query"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_media_search_or_play,
        ),
        ToolSpec(
            name="media_play_current",
            description="Play or resume the current media item, using recent media context when available.",
            parameters=_object_schema(
                {
                    "service": {"type": "string"},
                    "surface": {"type": "string", "enum": ["auto", "app", "browser"]},
                    "browser": {"type": "string"},
                },
                required=[],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_media_play_current,
        ),
        ToolSpec(
            name="find_file",
            description="Find local files by name or content query.",
            parameters=_object_schema({"query": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_find_file,
        ),
        ToolSpec(
            name="watch_change",
            description="Create or suggest a watcher for a URL, screen text, file, command, or repo state.",
            parameters=_object_schema(
                {
                    "kind": {"type": "string"},
                    "target": {"type": "string"},
                    "expected": {"type": "string"},
                },
                required=["kind", "target"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_watch_change,
        ),
        ToolSpec(
            name="summarize_pdf",
            description="Summarize a PDF file. This is a workflow placeholder until the PDF pipeline is connected.",
            parameters=_object_schema({"path": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_not_yet_connected,
        ),
        ToolSpec(
            name="draft_email",
            description="Draft an email without sending it.",
            parameters=_object_schema(
                {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                required=["subject", "body"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_draft_email,
        ),
        ToolSpec(
            name="message_send",
            description="Send an iMessage/SMS through macOS Messages after approval. Requires recipient and exact body.",
            parameters=_object_schema(
                {
                    "recipient": {"type": "string"},
                    "body": {"type": "string"},
                    "service": {"type": "string"},
                },
                required=["recipient", "body"],
            ),
            risk=RiskLevel.SENSITIVE,
            execute=_message_send,
        ),
        ToolSpec(
            name="create_task",
            description="Create a local task draft. Sending to external task systems requires a configured connector.",
            parameters=_object_schema(
                {
                    "title": {"type": "string"},
                    "due": {"type": "string"},
                    "owner": {"type": "string"},
                },
                required=["title"],
            ),
            risk=RiskLevel.LOW_RISK,
            execute=_create_task,
        ),
        ToolSpec(
            name="computer_use",
            description="Use visual UI navigation when accessibility/browser tools are insufficient.",
            parameters=_object_schema({"instruction": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_computer_use,
        ),
    ]


def _object_schema(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def _computer(context: ToolContext) -> ComputerBackend:
    if context.computer is not None:
        return context.computer
    return ComputerBackend(
        controller=context.controller,
        perception=context.perception,
        screen_awareness=context.screen_awareness,
    )


def _accessibility() -> AccessibilityBackend:
    return AccessibilityBackend()


def _chrome_cdp() -> ChromeCDPBackend:
    return ChromeCDPBackend(auto_start=True)


def _prefer_cdp(browser: str = "Google Chrome") -> bool:
    return browser.strip().lower() in {"google chrome", "chrome"}


def _media_service(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "")
    aliases = {
        "yt": "youtube",
        "youtube.com": "youtube",
        "youtu.be": "youtube",
        "spotify.com": "spotify",
        "": "spotify",
    }
    return aliases.get(normalized, normalized)


def _current_browser_is_youtube() -> bool:
    try:
        page = ChromeCDPBackend(auto_start=False).current_page()
    except Exception:
        return False
    if not page.ok or not isinstance(page.payload, dict):
        return False
    url = str(page.payload.get("url") or "").lower()
    return "youtube.com" in url or "youtu.be" in url


def _string_arg(arguments: dict[str, Any], name: str, default: str = "") -> str:
    value = arguments.get(name, default)
    return str(value).strip()


def _int_arg(arguments: dict[str, Any], name: str, default: int = 0) -> int:
    try:
        return int(arguments.get(name, default))
    except (TypeError, ValueError):
        return default


def _normalize_url(url: str) -> str:
    value = url.strip()
    if not value:
        return value
    if value.startswith(("http://", "https://", "spotify:")):
        return value
    return f"https://{value}"


def _from_action_result(result: ActionResult) -> ToolResult:
    retryable = False
    if not result.ok and isinstance(result.payload, dict):
        retryable = bool(result.payload.get("retry_with_screen"))
    return ToolResult(
        result.ok,
        _human_action_detail(result.detail),
        result.payload,
        continue_planning=retryable,
    )


def _human_action_detail(detail: str) -> str:
    lowered = detail.lower()
    if (
        "javascript through applescript is turned off" in lowered
        or "allow javascript from apple events" in lowered
    ):
        return "Chrome automation is off, so I’ll use screen clicks instead."
    if "execution error:" in lowered and "google chrome" in lowered:
        return "Chrome blocked that browser automation step."
    return detail


def _open_app(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    computer = _computer(context)
    return _from_action_result(computer.open_app(_string_arg(arguments, "app_name")))


def _app_activate(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    computer = _computer(context)
    return _from_action_result(computer.activate_app(_string_arg(arguments, "app_name")))


def _app_inspect(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    max_depth = max(1, min(_int_arg(arguments, "max_depth", 3), 6))
    max_items = max(10, min(_int_arg(arguments, "max_items", 120), 300))
    return _from_action_result(
        _accessibility().inspect_focused_app(max_depth=max_depth, max_items=max_items)
    )


def _app_windows(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(_accessibility().list_apps_windows())


def _app_find_element(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    description = _string_arg(arguments, "description")
    result = _accessibility().find_element(description)
    if result.ok:
        return _from_action_result(result)
    screen_result = _find_visible_element(arguments, context)
    if screen_result.ok:
        return screen_result
    return _from_action_result(result)


def _app_click_element(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    description = _string_arg(arguments, "description")
    result = _accessibility().click_element(description)
    if result.ok:
        return _from_action_result(result)
    screen_result = _screen_click_element(arguments, context)
    if screen_result.ok or screen_result.continue_planning:
        return screen_result
    return _from_action_result(result)


def _app_menu_select(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    raw_path = arguments.get("menu_path")
    if isinstance(raw_path, str):
        menu_path = [part.strip() for part in re.split(r"\s*(?:>|/|,)\s*", raw_path) if part.strip()]
    elif isinstance(raw_path, list):
        menu_path = [str(part).strip() for part in raw_path if str(part).strip()]
    else:
        menu_path = []
    return _from_action_result(
        _accessibility().menu_select(_string_arg(arguments, "app_name"), menu_path)
    )


def _app_type_text(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(_accessibility().type_text(_string_arg(arguments, "text")))


def _open_url(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    url = _normalize_url(_string_arg(arguments, "url"))
    result = context.controller.open_url(url)
    if result.ok:
        return ToolResult(True, _friendly_opened_url(url), {"url": url})
    return _from_action_result(result)


def _browser_open_url(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    url = _normalize_url(_string_arg(arguments, "url"))
    browser = _string_arg(arguments, "browser", "Google Chrome")
    result = context.controller.open_url_in_browser(url, browser)
    if result.ok:
        return ToolResult(True, _friendly_opened_url(url, browser), {"url": url, "browser": browser})
    return _from_action_result(result)


def _browser_open(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    url = _normalize_url(_string_arg(arguments, "url"))
    browser = _string_arg(arguments, "browser", "Google Chrome")
    new_tab = bool(arguments.get("new_tab"))
    if _prefer_cdp(browser):
        cdp = _chrome_cdp()
        if cdp.available():
            result = cdp.navigate(url, new_tab=new_tab)
            if result.ok:
                _remember_browser(context, url=url, browser=browser, current_task=_string_arg(arguments, "task"))
                return ToolResult(True, _friendly_opened_url(url, browser), result.payload)
    result = _computer(context).open_url(url, browser, new_tab=new_tab)
    if result.ok:
        _remember_browser(context, url=url, browser=browser, current_task=_string_arg(arguments, "task"))
        return ToolResult(True, _friendly_opened_url(url, browser), {"url": url, "browser": browser})
    return _from_action_result(result)


def _browser_ensure_runtime(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    result = _chrome_cdp().ensure_available()
    return _from_action_result(result)


def _browser_current_page(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    browser = _string_arg(arguments, "browser", "Google Chrome")
    if _prefer_cdp(browser):
        cdp = _chrome_cdp()
        if cdp.available():
            result = cdp.current_page()
            if result.ok and isinstance(result.payload, dict) and context.session_state is not None:
                context.session_state["last_opened_url"] = result.payload.get("url", "")
            return _from_action_result(result)
    result = _computer(context).browser_current_page(browser)
    if result.ok and isinstance(result.payload, dict) and context.session_state is not None:
        context.session_state["last_opened_url"] = result.payload.get("url", "")
    return _from_action_result(result)


def _browser_tabs(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(_chrome_cdp().list_tabs())


def _browser_extract(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    browser = _string_arg(arguments, "browser", "Google Chrome")
    if _prefer_cdp(browser):
        cdp = _chrome_cdp()
        if cdp.available():
            result = cdp.get_dom()
            if result.ok:
                payload = result.payload if isinstance(result.payload, dict) else {}
                text = str(payload.get("text") or "")
                return ToolResult(
                    True,
                    "I read the visible browser page.",
                    {"text": text, "browser": browser, "dom": payload, "backend": "cdp"},
                    continue_planning=True,
                )
    result = _from_action_result(_computer(context).browser_extract_text(browser))
    if result.ok:
        return ToolResult(result.ok, result.message, result.payload, continue_planning=True)
    return result


def _browser_get_dom(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    max_chars = max(1000, min(_int_arg(arguments, "max_chars", 12000), 30000))
    result = _from_action_result(_chrome_cdp().get_dom(max_chars=max_chars))
    if result.ok:
        return ToolResult(result.ok, result.message, result.payload, continue_planning=True)
    return result


def _browser_click(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    browser = _string_arg(arguments, "browser", "Google Chrome")
    if _prefer_cdp(browser):
        cdp = _chrome_cdp()
        if cdp.available():
            result = cdp.click_text(_string_arg(arguments, "text"))
            if result.ok:
                return _from_action_result(result)
    result = _computer(context).browser_click_text(
        _string_arg(arguments, "text"),
        browser,
    )
    if result.ok:
        return ToolResult(True, f"I clicked {_string_arg(arguments, 'text')}.", result.payload)
    return _from_action_result(result)


def _browser_click_element(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    result = _chrome_cdp().click_text(_string_arg(arguments, "text"))
    if result.ok:
        return _from_action_result(result)
    return _browser_click({"text": _string_arg(arguments, "text"), "browser": "Google Chrome"}, context)


def _browser_type_into(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    result = _chrome_cdp().type_into(
        field=_string_arg(arguments, "field"),
        text=_string_arg(arguments, "text"),
    )
    if result.ok:
        return _from_action_result(result)
    return _type_text({"text": _string_arg(arguments, "text")}, context)


def _browser_verify_state(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(
        _chrome_cdp().verify_state(
            text=_string_arg(arguments, "text"),
            url_contains=_string_arg(arguments, "url_contains"),
            title_contains=_string_arg(arguments, "title_contains"),
        )
    )


def _browser_media_state(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(_chrome_cdp().media_state())


def _browser_play_media(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    service = _media_service(_string_arg(arguments, "service", "spotify"))
    query = _string_arg(arguments, "query")
    browser = _string_arg(arguments, "browser", "Google Chrome")
    if service == "youtube":
        return _youtube_media_play(
            {"query": query, "browser": browser, "action": "play"},
            context,
        )
    if service != "spotify":
        return ToolResult(False, f"Browser media playback for {service} is not connected yet.")
    result = context.controller.spotify_web_play(query or None, browser)
    if result.ok:
        if query:
            _remember_media(context, service="spotify", query=query, surface="browser", browser=browser)
        return ToolResult(True, "I tried to start it in Spotify.", result.payload)
    tool_result = _from_action_result(result)
    if tool_result.continue_planning:
        return ToolResult(
            False,
            result.detail or "Chrome automation is off, so I’ll use screen clicks instead.",
            result.payload,
            continue_planning=True,
        )
    return tool_result


def _browser_javascript(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    result = context.controller.browser_javascript(
        _string_arg(arguments, "script"),
        _string_arg(arguments, "browser", "Google Chrome"),
    )
    if result.ok:
        return ToolResult(True, "I acted on the browser tab.", result.payload)
    return _from_action_result(result)


def _new_tab(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    result = context.controller.new_browser_tab()
    if not result.ok:
        return _from_action_result(result)
    url = _string_arg(arguments, "url")
    if not url:
        return _from_action_result(result)
    normalized = _normalize_url(url)
    opened = context.controller.open_url(normalized)
    if opened.ok:
        return ToolResult(True, _friendly_opened_url(normalized), {"url": normalized})
    return _from_action_result(opened)


def _search_web(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    query = _string_arg(arguments, "query")
    if not query:
        return ToolResult(False, "I need a search query.")
    url = f"https://www.google.com/search?q={quote_plus(query)}"
    result = context.controller.open_url(url)
    if result.ok:
        return ToolResult(True, f"I searched the web for {query}.", {"query": query, "url": url})
    return _from_action_result(result)


def _web_research(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    query = _string_arg(arguments, "query")
    max_results = max(1, min(_int_arg(arguments, "max_results", 5), 8))
    if not query:
        return ToolResult(False, "I need a search query.")
    results = _web_search(query, max_results=max_results)
    if not results:
        return ToolResult(False, f"I couldn't find public web results for {query}.")
    summaries: list[dict[str, str]] = []
    for item in results[: min(3, max_results)]:
        page_text = ""
        try:
            page_text = _extract_page_text(_fetch_text(item["url"], timeout=8))
        except Exception:
            page_text = ""
        summaries.append(
            {
                "title": item["title"],
                "url": item["url"],
                "snippet": item["snippet"],
                "page_text": _compact_text(page_text, 450),
            }
        )
    message = _format_research_summary(query, summaries or results)
    return ToolResult(True, message, {"query": query, "results": summaries or results})


def _identify_song(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    lyrics = _string_arg(arguments, "lyrics")
    artist_hint = _string_arg(arguments, "artist_hint")
    if not lyrics:
        return ToolResult(False, "I need the lyric fragment you heard.")
    query = f'"{lyrics}" song lyrics'
    if artist_hint:
        query += f" {artist_hint}"
    results = _web_search(query, max_results=5)
    if not results:
        return ToolResult(
            False,
            "I could not identify that lyric fragment confidently. I won’t open a browser search unless you ask me to.",
            {"query": query},
        )
    likely = results[0]
    title = likely.get("title") or _domain(likely.get("url", ""))
    snippet = likely.get("snippet") or ""
    message = f"The closest match I found is {title}."
    if snippet:
        message += f" {snippet}"
    return ToolResult(True, _compact_text(message, 500), {"query": query, "results": results})


def _gmail_search(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    query = _string_arg(arguments, "query")
    browser = _string_arg(arguments, "browser", "Google Chrome")
    if not query:
        return ToolResult(False, "I need a Gmail search query.")
    result = context.controller.gmail_search(query, browser)
    if result.ok:
        return ToolResult(True, f"I searched Gmail for {query}.", result.payload)
    return _from_action_result(result)


def _gmail_search_and_summarize(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    query = _string_arg(arguments, "query")
    browser = _string_arg(arguments, "browser", "Google Chrome")
    if not query:
        return ToolResult(False, "I need a Gmail search query.")
    opened = context.controller.gmail_search(query, browser)
    if not opened.ok:
        return _from_action_result(opened)
    time.sleep(2.0)
    visible = context.controller.gmail_visible_text(browser)
    if not visible.ok:
        return _from_action_result(visible)
    payload = visible.payload if isinstance(visible.payload, dict) else {}
    text = str(payload.get("text") or visible.detail or "").strip()
    if not text:
        return ToolResult(
            False,
            (
                "I opened the Gmail search, but I couldn't read visible message text. "
                "Make sure Gmail is loaded and Chrome allows JavaScript from Apple Events."
            ),
            opened.payload,
        )
    summary = _summarize_visible_email_text(query, text)
    return ToolResult(
        True,
        summary,
        {"query": query, "browser": browser, "text_preview": _compact_text(text, 1200)},
    )


def _gmail_create_draft(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    draft = _last_email_draft(context)
    to = _string_arg(arguments, "to") or str(draft.get("to") or "").strip()
    subject = _string_arg(arguments, "subject") or str(draft.get("subject") or "").strip()
    body = _string_arg(arguments, "body") or str(draft.get("body") or "").strip()
    browser = _string_arg(arguments, "browser", "Google Chrome")
    if not to:
        return ToolResult(False, "I need the recipient for the Gmail draft.")
    if not subject:
        return ToolResult(False, "I need the subject for the Gmail draft.")
    if not body:
        return ToolResult(False, "I need the body for the Gmail draft.")
    url = (
        "https://mail.google.com/mail/?view=cm&fs=1"
        f"&to={quote_plus(to)}"
        f"&su={quote_plus(subject)}"
        f"&body={quote_plus(body)}"
    )
    result = _browser_open(
        {"url": url, "browser": browser, "new_tab": False, "task": "gmail_draft"},
        context,
    )
    if not result.ok:
        return result
    if context.session_state is not None:
        context.session_state["last_email_draft"] = {
            "to": to,
            "subject": subject,
            "body": body,
            "surface": "gmail",
        }
    return ToolResult(
        True,
        "I opened Gmail compose with the draft filled in. Review it before sending.",
        {"to": to, "subject": subject, "body": body, "browser": browser, "url": url},
    )


def _latest_frame(context: ToolContext) -> LiveScreenFrame:
    if context.screen_awareness:
        frame = context.screen_awareness.latest()
        if frame is not None:
            return frame
        error = context.screen_awareness.latest_error()
        if error:
            raise RuntimeError(error)
        return context.screen_awareness.capture_now()
    return LiveScreenFrame(
        screenshot=context.perception.capture_screen(),
        context=context.perception.screen_context(),
    )


def _frame_age_seconds(frame: LiveScreenFrame) -> float:
    return max(0.0, (datetime.now(timezone.utc) - frame.screenshot.captured_at).total_seconds())


def _describe_screen(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    frame = _latest_frame(context)
    prompt = _string_arg(
        arguments,
        "prompt",
        "Describe the current visible screen, tab, and active window.",
    )
    fast_summary = _fast_live_screen_summary(frame, context)
    if fast_summary:
        return ToolResult(True, fast_summary)
    if context.google_vision.available:
        try:
            return ToolResult(True, _google_vision_screen_summary(frame, context))
        except Exception as exc:
            if not context.openai_client.available:
                return ToolResult(False, f"Google Vision screen read failed: {exc}")
    if not context.openai_client.available:
        return ToolResult(
            True,
            (
                "I can read the live screen metadata, but neither Google Vision nor "
                "OpenAI vision is configured for visual description.\n"
                f"Active app: {frame.context.active_app or 'unknown'}\n"
                f"Active window: {frame.context.active_window or 'unknown'}\n"
                f"Screenshot: {frame.screenshot.width}x{frame.screenshot.height}"
            ),
        )
    description = context.openai_client.describe_screen(
        frame.screenshot,
        frame.context,
        (
            f"{prompt}\n\n"
            "Use this latest Iris live screen-awareness frame. Do not reuse older "
            f"screen memory. Frame age: {_frame_age_seconds(frame):.1f}s."
        ),
    )
    return ToolResult(True, description)


def _google_vision_screen_summary(frame: LiveScreenFrame, context: ToolContext) -> str:
    ocr = context.google_vision.extract_text(frame.screenshot.png)
    labels = context.google_vision.label_image(frame.screenshot.png, max_results=8)
    text = _compact_text(ocr.text)
    label_text = ", ".join(
        f"{label.description} ({label.score:.0%})" for label in labels if label.description
    )
    parts = [
        "Google Vision live screen read:",
        f"Active app: {frame.context.active_app or 'unknown'}",
        f"Active window: {frame.context.active_window or 'unknown'}",
    ]
    if text:
        parts.append(f"Visible text: {text}")
    else:
        parts.append("Visible text: none detected")
    if label_text:
        parts.append(f"Visual labels: {label_text}")
    parts.append(f"Frame age: {_frame_age_seconds(frame):.1f}s")
    return "\n".join(parts)


def _compact_text(text: str, max_chars: int = 900) -> str:
    normalized = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 1].rstrip() + "..."


def _fast_live_screen_summary(frame: LiveScreenFrame, context: ToolContext) -> str:
    parts = [
        "Fast live screen read:",
        f"Active app: {frame.context.active_app or 'unknown'}",
        f"Active window: {frame.context.active_window or 'unknown'}",
        f"Frame age: {_frame_age_seconds(frame):.1f}s",
    ]
    app_name = (frame.context.active_app or "").lower()
    if app_name in {"google chrome", "chrome"}:
        cdp = ChromeCDPBackend(auto_start=False)
        try:
            page = cdp.current_page()
        except Exception:
            page = None
        if page and page.ok and isinstance(page.payload, dict):
            title = str(page.payload.get("title") or "").strip()
            url = str(page.payload.get("url") or "").strip()
            if title or url:
                parts.append(f"Browser page: {title or url}")
                if url:
                    parts.append(f"Browser URL: {_domain(url) or url}")
            try:
                dom = cdp.get_dom(max_chars=2500)
            except Exception:
                dom = None
            if dom and dom.ok and isinstance(dom.payload, dict):
                text = _compact_text(str(dom.payload.get("text") or ""), 700)
                controls = dom.payload.get("controls")
                labels: list[str] = []
                if isinstance(controls, list):
                    for item in controls:
                        if isinstance(item, dict):
                            label = str(item.get("label") or "").strip()
                            if label and label not in labels:
                                labels.append(label)
                        if len(labels) >= 8:
                            break
                if text:
                    parts.append(f"Visible page text: {text}")
                if labels:
                    parts.append(f"Visible controls: {', '.join(labels)}")
                return "\n".join(parts)
    if app_name and app_name not in {"google chrome", "chrome"}:
        try:
            inspected = _accessibility().inspect_focused_app(max_depth=2, max_items=80)
        except Exception:
            inspected = None
        if inspected and inspected.ok and isinstance(inspected.payload, dict):
            labels = _accessibility_labels(inspected.payload)
            if labels:
                parts.append(f"Visible UI text: {_compact_text(', '.join(labels), 700)}")
                return "\n".join(parts)
    if len(parts) > 4:
        return "\n".join(parts)
    if not context.google_vision.available and not context.openai_client.available:
        return "\n".join(parts) if frame.context.active_app or frame.context.active_window else ""
    return ""


def _accessibility_labels(payload: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    labels: list[str] = []
    elements = payload.get("elements")
    if not isinstance(elements, list):
        return labels
    for item in elements:
        if not isinstance(item, dict):
            continue
        for key in ("name", "value", "description"):
            label = str(item.get(key) or "").strip()
            if label and len(label) <= 160 and label not in seen:
                seen.add(label)
                labels.append(label)
                break
        if len(labels) >= 20:
            break
    return labels


def _ocr_screen(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    frame = _latest_frame(context)
    if not context.google_vision.available:
        return ToolResult(False, "GOOGLE_APPLICATION_CREDENTIALS is not configured for OCR.")
    ocr = context.google_vision.extract_text(frame.screenshot.png)
    return ToolResult(True, ocr.text or "No text detected.")


def _find_visible_element(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    description = _string_arg(arguments, "description")
    if not description:
        return ToolResult(False, "I need a visible element description.")
    return _describe_screen(
        {
            "prompt": (
                "Find this visible element if present and describe where it is. "
                f"Element: {description}"
            )
        },
        context,
    )


def _screen_click_element(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    description = _string_arg(arguments, "description") or _string_arg(arguments, "text")
    if not description:
        return ToolResult(False, "I need a visible element description.")
    if context.computer_use_runner is not None:
        result = context.computer_use_runner(f"Click the visible element: {description}")
        if bool(result.ok):
            return ToolResult(True, f"I clicked {description}.", getattr(result, "payload", None))
        message = str(result.message)
        if "can't use the computer-control model" not in message:
            return ToolResult(False, message)
    return ToolResult(
        False,
        f"I found the screen fallback path for {description}, but I need Accessibility or computer-use access to click it.",
    )


def _press_hotkey(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    modifiers = arguments.get("modifiers") or []
    if not isinstance(modifiers, list):
        modifiers = [str(modifiers)]
    return _from_action_result(
        _computer(context).press_hotkey(
            _string_arg(arguments, "key", "return"),
            [str(modifier) for modifier in modifiers],
        )
    )


def _type_text(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(_computer(context).type_text(_string_arg(arguments, "text")))


def _click(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(
        _computer(context).click(
            _int_arg(arguments, "x"),
            _int_arg(arguments, "y"),
            _string_arg(arguments, "button", "left"),
        )
    )


def _scroll(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(
        _computer(context).scroll(_int_arg(arguments, "dy"), _int_arg(arguments, "dx"))
    )


def _set_volume(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(context.controller.set_volume(_int_arg(arguments, "level", 50)))


def _set_app_volume(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(
        context.controller.set_app_volume(
            _string_arg(arguments, "app_name", "Spotify"),
            _int_arg(arguments, "level", 50),
        )
    )


def _change_volume(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(context.controller.change_volume(_int_arg(arguments, "delta")))


def _media_control(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    service = _string_arg(arguments, "service", "spotify").lower()
    action = _string_arg(arguments, "action").lower()
    if service and service != "spotify":
        return ToolResult(False, f"Media control for {service} is not connected yet.")
    if action == "play_pause":
        return _from_action_result(context.controller.spotify_play_pause())
    if action == "next":
        return _from_action_result(context.controller.spotify_next())
    if action == "previous":
        return _from_action_result(context.controller.spotify_previous())
    return ToolResult(False, f"Unsupported media action: {action}")


def _media_search(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    copied = dict(arguments)
    copied["action"] = "search"
    if _media_service(_string_arg(copied, "service", "spotify")) == "youtube":
        return _youtube_media_play(copied, context)
    return _media_search_or_play(copied, context)


def _media_play(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    copied = dict(arguments)
    if _media_service(_string_arg(copied, "service", "spotify")) == "youtube":
        copied["action"] = "play"
        return _youtube_media_play(copied, context)
    if _string_arg(copied, "query"):
        copied["action"] = "play"
        return _media_search_or_play(copied, context)
    return _media_play_current(copied, context)


def _media_pause(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(_computer(context).pause_current_media())


def _audio_current_media(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    result = context.controller.current_media()
    if result.ok:
        return _from_action_result(result)
    browser_state = ChromeCDPBackend(auto_start=False).media_state()
    if browser_state.ok and isinstance(browser_state.payload, dict):
        metadata = browser_state.payload.get("mediaSession")
        if isinstance(metadata, dict):
            title = str(metadata.get("title") or "").strip()
            artist = str(metadata.get("artist") or "").strip()
            album = str(metadata.get("album") or "").strip()
            if title or artist:
                label = " - ".join(part for part in [artist, title] if part)
                return ToolResult(
                    True,
                    f"{label} is playing in the browser.",
                    {
                        "service": "browser",
                        "state": "playing",
                        "artist": artist,
                        "title": title,
                        "album": album,
                        "source": browser_state.payload,
                    },
                )
        media_items = browser_state.payload.get("media")
        if isinstance(media_items, list) and any(
            isinstance(item, dict) and item.get("paused") is False for item in media_items
        ):
            title = str(browser_state.payload.get("title") or "browser media").strip()
            return ToolResult(
                True,
                f"Browser media is playing on {title}.",
                {"service": "browser", "state": "playing", "source": browser_state.payload},
            )
    state = context.session_state or {}
    media = state.get("last_media") if isinstance(state.get("last_media"), dict) else {}
    query = _string_arg(media, "query") if isinstance(media, dict) else ""
    if query:
        return ToolResult(True, f"The last media request was {query}.", media)
    return _from_action_result(result)


def _audio_identify_playing(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    metadata = _audio_current_media(arguments, context)
    if metadata.ok:
        return metadata
    return ToolResult(
        False,
        "I can’t identify background audio from the microphone yet without a dedicated audio fingerprint provider.",
    )


def _audio_explain_current_song(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    artist = _string_arg(arguments, "artist")
    title = _string_arg(arguments, "title")
    if not (artist and title):
        metadata = _audio_current_media(arguments, context)
        if not metadata.ok:
            return ToolResult(
                False,
                (
                    "I can’t tell what’s playing from system metadata yet, so I can’t explain the song reliably. "
                    "Tell me the artist and title, or enable a dedicated audio identification provider later."
                ),
            )
        payload = metadata.payload if isinstance(metadata.payload, dict) else {}
        artist = artist or str(payload.get("artist") or "").strip()
        title = title or str(payload.get("title") or "").strip()
    if not (artist and title):
        return ToolResult(False, "I need the song title and artist before I can explain it.")
    question = _string_arg(arguments, "question", "meaning themes background")
    query = f"{artist} {title} song meaning themes background"
    if question:
        query = f"{artist} {title} {question}"
    research = _web_research({"query": query, "max_results": 4}, context)
    if not research.ok:
        return ToolResult(False, f"I found {artist} - {title}, but I couldn't pull enough public context to explain it.")
    message = research.message.replace(
        f"Here’s what I found publicly for {query}:",
        f"For {title} by {artist}, here’s the public context I found:",
    )
    return ToolResult(
        True,
        _compact_text(message, 900),
        {"artist": artist, "title": title, "query": query, "research": research.payload},
    )


def _integration_status(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    name = _string_arg(arguments, "name")
    if not name:
        return ToolResult(False, "I need an integration name.")
    if context.config is None:
        return ToolResult(False, "The connector registry is not connected in this runtime.")
    with open_state(context.config) as db:
        connector = get_connector(
            db,
            name,
            manifest_dirs=default_manifest_dirs(context.config.project_root),
        )
    if connector is None:
        return ToolResult(
            True,
            f"I do not have a connector manifest for {name} yet.",
            {"name": name, "configured": False, "known": False},
        )
    configured = bool(connector.get("configured"))
    health = str(connector.get("health") or "unknown")
    message = (
        f"{connector['name']} is configured and {health}."
        if configured
        else f"{connector['name']} is not configured yet."
    )
    return ToolResult(True, message, connector)


def _connector_list(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    if context.config is None:
        return ToolResult(False, "The connector registry is not connected in this runtime.")
    category = _string_arg(arguments, "category")
    with open_state(context.config) as db:
        connectors = connector_health(
            db,
            manifest_dirs=default_manifest_dirs(context.config.project_root),
        )
    if category:
        connectors = [item for item in connectors if item.get("category") == category]
    ready = [item for item in connectors if item.get("enabled") and item.get("configured")]
    if not ready:
        return ToolResult(True, "No ready connectors matched that request.", {"connectors": connectors})
    names = ", ".join(str(item.get("name")) for item in ready[:12])
    return ToolResult(
        True,
        f"Ready connectors: {names}.",
        {"connectors": connectors, "ready_count": len(ready)},
    )


def _task_start_background(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    goal = _string_arg(arguments, "goal")
    if not goal:
        return ToolResult(False, "I need a background task goal.")
    if context.config is None:
        return ToolResult(False, "The durable task store is not connected in this runtime.")
    kind = _string_arg(arguments, "kind", "agent_background") or "agent_background"
    title = _string_arg(arguments, "title") or _compact_text(goal, 80)
    with open_state(context.config) as db:
        task_id = task_store.create_task(
            db,
            kind=kind,
            title=title,
            input_value={"goal": goal},
            metadata={"created_by": "agent_tool", "autonomy": context.config.autonomy_level},
            status="queued",
        )
    return ToolResult(
        True,
        f"I queued a background agent task for that. Task id: {task_id[:8]}.",
        {"task_id": task_id, "goal": goal, "status": "queued"},
    )


def _task_status(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    if context.config is None:
        return ToolResult(False, "The durable task store is not connected in this runtime.")
    limit = max(1, min(_int_arg(arguments, "limit", 5), 20))
    with open_state(context.config) as db:
        running = task_store.list_tasks(db, status="running", limit=limit)
        queued = task_store.list_tasks(db, status="queued", limit=limit)
        recent = task_store.list_tasks(db, limit=limit)
    if running:
        active = running[0]
        return ToolResult(
            True,
            f"I'm working on {active.get('title') or active.get('task_id')}.",
            {"running": running, "queued": queued, "recent": recent},
        )
    if queued:
        return ToolResult(
            True,
            f"I have {len(queued)} queued task{'s' if len(queued) != 1 else ''}.",
            {"running": running, "queued": queued, "recent": recent},
        )
    return ToolResult(True, "No Iris background task is running right now.", {"recent": recent})


def _machine_context(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    if context.config is None:
        return ToolResult(False, "Machine context needs an Iris config.")
    from iris.machine_context import collect_machine_context

    with open_state(context.config) as db:
        data = collect_machine_context(context.config, db)
    app_count = len(data.get("installed_apps", []))
    project_count = len(data.get("project_folders", []))
    return ToolResult(
        True,
        f"I can see {app_count} installed apps and {project_count} project folders in local context.",
        data,
    )


def _calendar_find_event(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    query = _string_arg(arguments, "query")
    if not query:
        return ToolResult(False, "I need an event search query.")
    days_ahead = max(1, min(_int_arg(arguments, "days_ahead", 21), 365))
    script = f"""
set queryText to {applescript_string(query)}
set startWindow to current date
set endWindow to startWindow + ({days_ahead} * days)
set outputLines to {{}}
tell application "Calendar"
  repeat with cal in calendars
    try
      set candidateEvents to every event of cal whose start date is greater than or equal to startWindow and start date is less than or equal to endWindow
      repeat with ev in candidateEvents
        set eventSummary to summary of ev as text
        set eventLocation to ""
        set eventDescription to ""
        try
          set eventLocation to location of ev as text
        end try
        try
          set eventDescription to description of ev as text
        end try
        set haystack to eventSummary & " " & eventLocation & " " & eventDescription
        ignoring case
          if haystack contains queryText then
            set end of outputLines to eventSummary & " | " & ((start date of ev) as text) & " | " & eventLocation
          end if
        end ignoring
      end repeat
    end try
  end repeat
end tell
set AppleScript's text item delimiters to linefeed
return outputLines as text
"""
    result = run_osascript(script, timeout=20)
    if not result.ok:
        return ToolResult(False, _human_action_detail(result.stderr or result.stdout))
    if not result.stdout.strip():
        return ToolResult(True, f"I did not find a calendar event matching {query}.", [])
    events = [line for line in result.stdout.splitlines() if line.strip()]
    return ToolResult(
        True,
        "I found this on your calendar: " + "; ".join(events[:5]),
        {"query": query, "events": events},
    )


def _reminder_create(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    title = _string_arg(arguments, "title")
    due = _string_arg(arguments, "due")
    open_app = bool(arguments.get("open_app", True))
    if not title:
        return ToolResult(False, "I need a reminder title.")
    if due:
        due_script = _reminder_due_applescript(due)
        script = f"""
set reminderTitle to {applescript_string(title)}
set dueText to {applescript_string(due)}
try
{due_script}
  tell application "Reminders"
    set targetList to default list
    make new reminder at end of reminders of targetList with properties {{name:reminderTitle, remind me date:dueDate}}
  end tell
  return "created reminder with date"
on error
  tell application "Reminders"
    set targetList to default list
    make new reminder at end of reminders of targetList with properties {{name:reminderTitle}}
  end tell
  return "created reminder without date"
end try
"""
    else:
        script = f"""
set reminderTitle to {applescript_string(title)}
tell application "Reminders"
  set targetList to default list
  make new reminder at end of reminders of targetList with properties {{name:reminderTitle}}
end tell
return "created reminder"
"""
    result = run_osascript(script, timeout=20)
    if not result.ok:
        return ToolResult(False, _human_action_detail(result.stderr or result.stdout))
    if open_app:
        context.controller.open_app("Reminders")
    message = f"I created the reminder: {title}."
    if due and "without date" in result.stdout.lower():
        message += " I could not parse the due date, so I saved it without a time."
    return ToolResult(True, message, {"title": title, "due": due, "opened": open_app})


def _reminder_due_applescript(due: str) -> str:
    parsed = _parse_due_datetime(due)
    if parsed is None:
        return "  set dueDate to date dueText"
    month_name = parsed.strftime("%B")
    return (
        "  set dueDate to current date\n"
        f"  set year of dueDate to {parsed.year}\n"
        f"  set month of dueDate to {month_name}\n"
        f"  set day of dueDate to {parsed.day}\n"
        f"  set time of dueDate to {parsed.hour * 3600 + parsed.minute * 60 + parsed.second}"
    )


def _parse_due_datetime(due: str) -> datetime | None:
    text = _clean_space(due).lower()
    if not text:
        return None
    now = datetime.now()
    target = now
    if "tomorrow" in text:
        target = now + timedelta(days=1)
    elif "today" in text:
        target = now
    else:
        weekdays = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        for name, weekday in weekdays.items():
            if name in text:
                days_ahead = (weekday - now.weekday()) % 7
                if days_ahead == 0 or f"next {name}" in text:
                    days_ahead += 7
                target = now + timedelta(days=days_ahead)
                break
        else:
            return None
    hour, minute = _parse_due_time(text)
    return target.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _parse_due_time(text: str) -> tuple[int, int]:
    if "noon" in text:
        return 12, 0
    if "midnight" in text:
        return 0, 0
    match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = match.group(3).replace(".", "")
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        return max(0, min(hour, 23)), max(0, min(minute, 59))
    match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\b", text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        return max(0, min(hour, 23)), max(0, min(minute, 59))
    return 9, 0


def _media_search_or_play(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    service = _media_service(_string_arg(arguments, "service", "spotify"))
    query = _string_arg(arguments, "query")
    surface = _string_arg(arguments, "surface", "auto").lower() or "auto"
    action = _string_arg(arguments, "action", "search").lower() or "search"
    browser = _string_arg(arguments, "browser", "Google Chrome")
    if service == "youtube":
        return _youtube_media_play(arguments, context)
    if not query:
        return ToolResult(False, "I need something to search or play.")
    if service and service != "spotify":
        return ToolResult(False, f"Media search for {service} is not connected yet.")
    if context.recipes is not None:
        recipe = context.recipes.find_for_service("spotify")
        if recipe and context.session_state is not None:
            context.session_state["current_recipe"] = recipe.name
    if action == "play" and surface == "auto":
        web_result = _spotify_web_cdp(query, action=action, browser=browser)
        if web_result is not None:
            if web_result.ok:
                _remember_media(context, service="spotify", query=query, surface="browser", browser=browser)
                payload = web_result.payload if isinstance(web_result.payload, dict) else {}
                verified = bool(payload.get("verified_playback"))
                return ToolResult(
                    True,
                    (
                        f"Spotify is playing {query}."
                        if verified
                        else f"I clicked play for {query} in Spotify, but I could not verify audio yet."
                    ),
                    web_result.payload,
                    continue_planning=not verified,
                )
            if "Chrome CDP is running without the Iris origin allowlist" in web_result.detail:
                return _from_action_result(web_result)
    if surface == "browser":
        cdp_result = _spotify_web_cdp(query, action=action, browser=browser)
        if cdp_result is not None:
            if cdp_result.ok:
                _remember_media(context, service="spotify", query=query, surface="browser", browser=browser)
                payload = cdp_result.payload if isinstance(cdp_result.payload, dict) else {}
                verified = bool(payload.get("verified_playback"))
                message = (
                    f"Spotify is playing {query}."
                    if verified
                    else f"I clicked play for {query} in Spotify, but I could not verify audio yet."
                    if action == "play"
                    else f"I opened Spotify in your browser and searched for {query}."
                )
                return ToolResult(True, message, cdp_result.payload, continue_planning=action == "play" and not verified)
            if "Chrome CDP is running without the Iris origin allowlist" in cdp_result.detail:
                return _from_action_result(cdp_result)
        result = (
            context.controller.spotify_web_play(query, browser)
            if action == "play"
            else context.controller.spotify_web_search(query, browser)
        )
        if result.ok:
            _remember_media(context, service="spotify", query=query, surface="browser", browser=browser)
            message = (
                f"I started Spotify in your browser and looked for {query}."
                if action == "play"
                else f"I opened Spotify in your browser and searched for {query}."
            )
            return ToolResult(True, message, result.payload)
        return _from_action_result(result)
    app_result = context.controller.spotify_search(query)
    if app_result.ok or surface == "app":
        if app_result.ok:
            _remember_media(context, service="spotify", query=query, surface="app", browser=browser)
            message = f"I opened Spotify and searched for {query}."
            if action == "play":
                play = context.controller.spotify_play_pause()
                if play.ok:
                    message = f"I tried to start {query} in Spotify."
            return ToolResult(True, message, app_result.payload)
        return _from_action_result(app_result)
    web_result = _spotify_web_cdp(query, action=action, browser=browser)
    if web_result is None or (
        not web_result.ok
        and "Chrome CDP is running without the Iris origin allowlist" not in web_result.detail
    ):
        web_result = (
            context.controller.spotify_web_play(query, browser)
            if action == "play"
            else context.controller.spotify_web_search(query, browser)
        )
    if web_result.ok:
        _remember_media(context, service="spotify", query=query, surface="browser", browser=browser)
        message = (
            f"I started Spotify and looked for {query}."
            if action == "play"
            else f"I opened Spotify in your browser and searched for {query}."
        )
        return ToolResult(True, message, web_result.payload)
    return _from_action_result(web_result)


def _spotify_web_cdp(query: str, *, action: str, browser: str) -> ActionResult | None:
    if not _prefer_cdp(browser):
        return None
    cdp = _chrome_cdp()
    if action == "play":
        return cdp.spotify_play_search(query)
    url = f"https://open.spotify.com/search/{quote_plus(query)}"
    return cdp.navigate(url, new_tab=False)


def _youtube_media_play(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    query = _string_arg(arguments, "query")
    action = _string_arg(arguments, "action", "play").lower() or "play"
    browser = _string_arg(arguments, "browser", "Google Chrome")
    if action == "search":
        if not query:
            return ToolResult(False, "I need something to search on YouTube.")
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
        opened = _browser_open(
            {"url": url, "browser": browser, "new_tab": False, "task": "youtube_search"},
            context,
        )
        if opened.ok:
            _remember_media(context, service="youtube", query=query, surface="browser", browser=browser)
        return opened
    result = _chrome_cdp().youtube_play(query or None)
    if result.ok:
        payload = result.payload if isinstance(result.payload, dict) else {}
        verified = bool(payload.get("verified_playback"))
        media_query = query or str(payload.get("title") or "current YouTube video")
        _remember_media(context, service="youtube", query=media_query, surface="browser", browser=browser)
        return ToolResult(
            True,
            (
                f"YouTube is playing {query}."
                if query and verified
                else "The YouTube video is playing."
                if verified
                else "I clicked play on YouTube, but I could not verify audio yet."
            ),
            result.payload,
            continue_planning=not verified,
        )
    if "Chrome CDP is running without the Iris origin allowlist" in result.detail:
        return _from_action_result(result)
    if not query:
        fallback = _press_hotkey({"key": "k"}, context)
        if fallback.ok:
            return ToolResult(
                True,
                "I pressed play on the current video.",
                fallback.payload,
                continue_planning=True,
            )
    return _from_action_result(result)


def _media_play_current(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    service = _media_service(_string_arg(arguments, "service", "spotify"))
    surface = _string_arg(arguments, "surface", "auto").lower() or "auto"
    browser = _string_arg(arguments, "browser", "Google Chrome")
    state = context.session_state or {}
    media = state.get("last_media") if isinstance(state.get("last_media"), dict) else {}
    query = _string_arg(media, "query") if isinstance(media, dict) else ""
    remembered_surface = _string_arg(media, "surface") if isinstance(media, dict) else ""
    remembered_service = _media_service(_string_arg(media, "service")) if isinstance(media, dict) else ""
    if service == "youtube" or remembered_service == "youtube" or _current_browser_is_youtube():
        return _youtube_media_play(
            {"service": "youtube", "query": query if remembered_service == "youtube" else "", "browser": browser},
            context,
        )
    if service and service != "spotify":
        return ToolResult(False, f"Media playback for {service} is not connected yet.")
    if surface == "browser" or remembered_surface == "browser":
        result = context.controller.spotify_web_play(query or None, browser)
        if result.ok:
            if query:
                _remember_media(context, service="spotify", query=query, surface="browser", browser=browser)
            return ToolResult(True, "I tried to start it in Spotify.", result.payload)
        return _from_action_result(result)
    result = context.controller.spotify_play_pause()
    if result.ok:
        return ToolResult(True, "I toggled playback.", result.payload)
    return _from_action_result(result)


def _find_file(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    query = _string_arg(arguments, "query")
    if _is_reference_to_previous_results(query) and context.session_state is not None:
        previous = context.session_state.get("last_file_results")
        if isinstance(previous, list):
            return _format_file_results(previous)
    result = context.controller.find_file(query)
    if result.ok and isinstance(result.payload, list):
        if context.session_state is not None:
            context.session_state["last_file_results"] = result.payload
        return _format_file_results([str(path) for path in result.payload])
    return _from_action_result(result)


def _knowledge_search(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    query = _string_arg(arguments, "query")
    if not query:
        return ToolResult(False, "I need a knowledge search query.")
    if context.config is None:
        return ToolResult(False, "The local knowledge store is not connected in this runtime.")
    limit = max(1, min(_int_arg(arguments, "limit", 8), 20))
    with open_state(context.config) as db:
        results = search_pages(db, query, limit=limit)
    if context.session_state is not None:
        context.session_state["last_knowledge_query"] = query
        context.session_state["last_knowledge_results"] = [
            {
                "page_id": item.get("page_id"),
                "title": item.get("title"),
                "uri": item.get("uri"),
                "score": item.get("score"),
            }
            for item in results
        ]
    return ToolResult(
        True,
        format_search_results(results),
        {
            "query": query,
            "results": [
                {
                    "page_id": item.get("page_id"),
                    "title": item.get("title"),
                    "summary": item.get("summary"),
                    "snippet": item.get("snippet"),
                    "uri": item.get("uri"),
                    "score": item.get("score"),
                }
                for item in results
            ],
        },
    )


def _file_list_folder(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    raw_path = _string_arg(arguments, "path")
    if not raw_path:
        return ToolResult(False, "I need a folder path.")
    folder = _resolve_common_folder(raw_path)
    limit = max(1, min(_int_arg(arguments, "limit", 20), 100))
    if not folder.exists():
        return ToolResult(False, f"I couldn't find that folder: {folder}")
    if not folder.is_dir():
        return ToolResult(False, f"That path is not a folder: {folder}")
    entries = sorted(
        [entry for entry in folder.iterdir() if not entry.name.startswith(".")],
        key=lambda entry: entry.stat().st_mtime if entry.exists() else 0,
        reverse=True,
    )[:limit]
    paths = [str(entry) for entry in entries]
    if context.session_state is not None:
        context.session_state["last_file_results"] = paths
        context.session_state["last_folder"] = str(folder)
    return _format_file_results(paths, heading=f"I found {len(paths)} item(s) in {folder.name}.")


def _file_open(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    raw_path = _string_arg(arguments, "path")
    if not raw_path:
        return ToolResult(False, "I need a file path.")
    path = str(_resolve_common_folder(raw_path))
    return _from_action_result(context.controller.open_file(path))


def _file_rename(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    new_name = _string_arg(arguments, "new_name")
    if not new_name:
        return ToolResult(False, "I need the new file name.")
    if "/" in new_name or "\\" in new_name:
        return ToolResult(False, "The new name must be a file name, not a path.")
    raw_path = _string_arg(arguments, "path")
    target = _string_arg(arguments, "target")
    path = _resolve_file_reference(raw_path or target, context)
    if path is None:
        return ToolResult(False, "I could not tell which file or folder to rename.")
    if not path.exists():
        return ToolResult(False, f"I couldn't find that file or folder: {path}")
    final_name = new_name
    if path.is_file() and path.suffix and not Path(new_name).suffix:
        final_name = f"{new_name}{path.suffix}"
    destination = path.with_name(final_name)
    if destination.exists():
        return ToolResult(False, f"{destination.name} already exists.")
    try:
        path.rename(destination)
    except Exception as exc:
        return ToolResult(False, f"I could not rename it: {exc}")
    if context.session_state is not None:
        previous = context.session_state.get("last_file_results")
        if isinstance(previous, list):
            context.session_state["last_file_results"] = [
                str(destination) if str(item) == str(path) else item for item in previous
            ]
    return ToolResult(
        True,
        f"Renamed {path.name} to {destination.name}.",
        {"old_path": str(path), "new_path": str(destination)},
    )


def _is_reference_to_previous_results(query: str) -> bool:
    normalized = query.strip().lower()
    return normalized in {"", "them", "it", "the results", "those files", "list them", "list results"}


def _resolve_common_folder(path: str) -> Path:
    cleaned = path.strip()
    lowered = cleaned.lower()
    home = Path.home()
    if lowered in {"download", "downloads", "download folder", "downloads folder", "~/downloads"}:
        return home / "Downloads"
    if lowered in {"desktop", "desktop folder", "~/desktop"}:
        return home / "Desktop"
    if lowered in {"document", "documents", "documents folder", "~/documents"}:
        return home / "Documents"
    return Path(cleaned).expanduser()


def _resolve_file_reference(reference: str, context: ToolContext) -> Path | None:
    cleaned = reference.strip()
    if cleaned:
        direct = _resolve_common_folder(cleaned)
        if direct.exists():
            return direct
    candidates: list[Path] = []
    state = context.session_state or {}
    previous = state.get("last_file_results")
    if isinstance(previous, list):
        candidates.extend(Path(str(item)) for item in previous)
    last_folder = str(state.get("last_folder") or "").strip()
    if last_folder:
        folder = Path(last_folder).expanduser()
        if folder.exists() and folder.is_dir():
            try:
                candidates.extend(entry for entry in folder.iterdir() if not entry.name.startswith("."))
            except OSError:
                pass
    if not candidates:
        return None
    if not cleaned:
        return candidates[0] if len(candidates) == 1 else None
    normalized = _name_terms(cleaned)
    scored = sorted(
        ((candidate, _path_match_score(candidate, normalized)) for candidate in candidates),
        key=lambda item: item[1],
        reverse=True,
    )
    if scored and scored[0][1] > 0:
        return scored[0][0]
    return None


def _name_terms(value: str) -> set[str]:
    ignored = {
        "the",
        "this",
        "that",
        "file",
        "folder",
        "document",
        "name",
        "rename",
        "called",
        "to",
    }
    return {
        term
        for term in re.split(r"[^a-z0-9]+", value.lower())
        if term and term not in ignored
    }


def _path_match_score(path: Path, terms: set[str]) -> int:
    if not terms:
        return 0
    name = path.stem.lower()
    full = path.name.lower()
    return sum(2 if term in name else 1 if term in full else 0 for term in terms)


def _format_file_results(paths: list[str], *, heading: str | None = None) -> ToolResult:
    if not paths:
        return ToolResult(True, "I found 0 results.", [])
    names = [Path(path).name or path for path in paths]
    lines = [heading or f"I found {len(paths)} result(s):"]
    lines.extend(f"{index}. {name}" for index, name in enumerate(names[:20], start=1))
    if len(paths) > 20:
        lines.append(f"...and {len(paths) - 20} more.")
    return ToolResult(True, "\n".join(lines), paths)


def _workflow_run(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    name = _string_arg(arguments, "name")
    if not name:
        return ToolResult(False, "I need a workflow name.")
    return ToolResult(True, f"Workflow {name} is ready to run through `./iris run {name}`.", {"name": name})


def _recipe_run(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    recipe_name = _string_arg(arguments, "recipe_name") or _string_arg(arguments, "name")
    if not recipe_name:
        return ToolResult(False, "I need a recipe name.")
    registry = context.recipes or ActionRecipeRegistry.default(
        context.config.project_root if context.config is not None else None
    )
    recipe = registry.get(recipe_name)
    if recipe is None:
        return ToolResult(False, f"I do not have a recipe named {recipe_name}.")
    if recipe.private and not context.approved_tool_call:
        raise ApprovalRequired(
            "recipe_run",
            arguments,
            f"{recipe.name} reads private {recipe.service or 'account'} data.",
        )
    raw_inputs = arguments.get("inputs")
    inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
    max_steps = max(1, min(_int_arg(arguments, "max_steps", 8), 20))
    observations: list[dict[str, Any]] = []
    tool_registry = ToolRegistry.default()

    def run_steps(steps: Any) -> bool:
        for step in list(steps)[:max_steps]:
            if not step.tool or step.tool == "recipe_run":
                continue
            step_args = _recipe_step_args(step.args, inputs)
            tool = tool_registry.get(step.tool)
            if tool is None:
                observations.append(
                    {"tool": step.tool, "ok": False, "message": "Tool is not available."}
                )
                if not step.continue_on_error:
                    return False
                continue
            try:
                result = tool_registry.execute_approved(step.tool, step_args, context)
            except Exception as exc:
                result = ToolResult(False, _human_tool_error(str(exc)))
            observations.append(
                {
                    "tool": step.tool,
                    "arguments": step_args,
                    "ok": result.ok,
                    "message": result.message,
                    "payload": result.payload,
                    "expected_observation": step.expected_observation,
                }
            )
            if not result.ok and not step.continue_on_error:
                return False
        return True

    primary_ok = run_steps(recipe.steps)
    fallback_ok = True
    if not primary_ok and recipe.fallback_steps:
        fallback_ok = run_steps(recipe.fallback_steps)
    ok = primary_ok or fallback_ok
    final_observation = str(observations[-1]["message"]) if observations else recipe.description
    if recipe.done_condition:
        final_observation = f"{final_observation} Done condition: {recipe.done_condition}"
    return ToolResult(
        ok,
        final_observation,
        {
            "recipe_name": recipe.name,
            "service": recipe.service,
            "done_condition": recipe.done_condition,
            "verification_tool": recipe.verification_tool,
            "observations": observations,
        },
        continue_planning=ok,
    )


def _watch_change(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    kind = _string_arg(arguments, "kind")
    target = _string_arg(arguments, "target")
    expected = _string_arg(arguments, "expected")
    if not kind or not target:
        return ToolResult(False, "I need a watch kind and target.")
    summary = f"watch_change request ready: kind={kind}, target={target}"
    if expected:
        summary += f", expected={expected}"
    return ToolResult(
        True,
        summary + ". Use `./iris watch add` to persist this watcher from the terminal.",
        {"kind": kind, "target": target, "expected": expected},
    )


def _not_yet_connected(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(False, "That workflow tool is registered but not connected yet.")


def _draft_email(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    subject = _string_arg(arguments, "subject")
    body = _string_arg(arguments, "body")
    draft = {"to": _string_arg(arguments, "to"), "subject": subject, "body": body}
    if context.session_state is not None:
        context.session_state["last_email_draft"] = draft
    return ToolResult(
        True,
        f"Draft ready: {subject}",
        draft,
    )


def _message_send(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    recipient = _string_arg(arguments, "recipient")
    body = _string_arg(arguments, "body")
    service = _string_arg(arguments, "service", "iMessage") or "iMessage"
    if not recipient:
        return ToolResult(False, "I need a message recipient.")
    if not body:
        return ToolResult(False, "I need the exact message to send.")
    return _from_action_result(
        context.controller.send_message(recipient=recipient, body=body, service=service)
    )


def _create_task(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    title = _string_arg(arguments, "title")
    if not title:
        return ToolResult(False, "I need a task title.")
    return ToolResult(
        True,
        f"Task draft ready: {title}",
        {
            "title": title,
            "due": _string_arg(arguments, "due"),
            "owner": _string_arg(arguments, "owner"),
        },
    )


def _computer_use(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    instruction = _string_arg(arguments, "instruction")
    if not instruction:
        return ToolResult(False, "I need a computer-use instruction.")
    if context.computer_use_runner is None:
        return ToolResult(False, "Computer-use is not connected in this runtime.")
    result = context.computer_use_runner(instruction)
    return ToolResult(bool(result.ok), str(result.message), getattr(result, "payload", None))


def _remember_media(
    context: ToolContext,
    *,
    service: str,
    query: str,
    surface: str,
    browser: str,
) -> None:
    if context.session_state is None:
        return
    context.session_state["last_media"] = {
        "service": service,
        "query": query,
        "surface": surface,
        "browser": browser,
        "url": f"https://open.spotify.com/search/{quote_plus(query)}",
    }


def _remember_browser(
    context: ToolContext,
    *,
    url: str,
    browser: str,
    current_task: str = "",
) -> None:
    if context.session_state is None:
        return
    context.session_state["last_opened_url"] = url
    context.session_state["current_browser_task"] = current_task or url
    context.session_state["last_browser"] = browser


def _friendly_opened_url(url: str, browser: str | None = None) -> str:
    target = url
    if "open.spotify.com/search/" in url:
        target = f"Spotify search for {unquote_plus(url.rsplit('/', 1)[-1])}"
    elif url.startswith("https://"):
        target = url.removeprefix("https://").split("/", 1)[0]
    elif url.startswith("http://"):
        target = url.removeprefix("http://").split("/", 1)[0]
    if browser:
        return f"I opened {target} in {browser}."
    return f"I opened {target}."


def _recipe_step_args(step_args: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    resolved = {key: _recipe_value(value, inputs) for key, value in step_args.items()}
    for key, value in inputs.items():
        resolved.setdefault(key, value)
    return resolved


def _recipe_value(value: Any, inputs: dict[str, Any]) -> Any:
    if isinstance(value, str):
        resolved = value
        for key, input_value in inputs.items():
            resolved = resolved.replace("{{" + str(key) + "}}", str(input_value))
        return resolved
    if isinstance(value, list):
        return [_recipe_value(item, inputs) for item in value]
    if isinstance(value, dict):
        return {key: _recipe_value(item, inputs) for key, item in value.items()}
    return value


def _last_email_draft(context: ToolContext) -> dict[str, Any]:
    if context.session_state is None:
        return {}
    draft = context.session_state.get("last_email_draft")
    return draft if isinstance(draft, dict) else {}


def _web_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    html = _fetch_text(
        f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
        timeout=12,
    )
    parser = _DuckDuckGoHTMLParser()
    parser.feed(html)
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parser.results:
        url = _clean_search_url(item.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(
            {
                "title": _clean_space(item.get("title", "")),
                "url": url,
                "snippet": _clean_space(item.get("snippet", "")),
            }
        )
        if len(unique) >= max_results:
            break
    return unique


def _fetch_text(url: str, timeout: float = 10) -> str:
    req = request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Iris/0.1"
            )
        },
        method="GET",
    )
    with request.urlopen(req, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read(1_500_000).decode(charset, errors="replace")


def _clean_search_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return target
    if url.startswith("//"):
        return f"https:{url}"
    return url


def _format_research_summary(query: str, results: list[dict[str, str]]) -> str:
    if not results:
        return f"I couldn't find much public information for {query}."
    lines = []
    for item in results[:2]:
        title = item.get("title") or _domain(item.get("url", ""))
        snippet = item.get("page_text") or item.get("snippet") or "No short description available."
        lines.append(f"{title}: {_compact_text(snippet, 150)}")
    joined = " ".join(lines)
    return _compact_text(f"Here’s what I found for {query}: {joined}", 520)


def _summarize_visible_email_text(query: str, text: str) -> str:
    cleaned = _compact_text(text, 1800)
    if not cleaned:
        return f"I searched Gmail for {query}, but I do not see readable message text yet."
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    useful = [
        line
        for line in lines
        if len(line) >= 12
        and line.lower()
        not in {
            "inbox",
            "starred",
            "snoozed",
            "sent",
            "drafts",
            "more",
            "compose",
        }
    ]
    excerpt = _compact_text(" ".join(useful[:12]) or cleaned, 700)
    return f"I searched Gmail for {query}. From the visible results, this is what stands out: {excerpt}"


def _domain(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.removeprefix("www.") or url


def _extract_page_text(html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(html)
    return _clean_space(" ".join(parser.parts))


def _clean_space(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture_title = False
        self._capture_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        classes = set(attrs_dict.get("class", "").split())
        if tag == "a" and "result__a" in classes:
            self._current = {"title": "", "url": attrs_dict.get("href", ""), "snippet": ""}
            self._capture_title = True
            return
        if self._current is not None and "result__snippet" in classes:
            self._capture_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title:
            self._capture_title = False
            if self._current is not None:
                self.results.append(self._current)
            return
        if self._capture_snippet and tag in {"a", "div"}:
            self._capture_snippet = False
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        if self._capture_title:
            self._current["title"] += data
        elif self._capture_snippet:
            self._current["snippet"] += data


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = _clean_space(data)
        if len(cleaned) >= 35:
            self.parts.append(cleaned)
