from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import re
import time
from typing import Any, Callable
from urllib import request
from urllib.parse import parse_qs, quote_plus, unquote_plus, urlparse

from iris.actions import LocalAction, RiskLevel
from iris.computer import ComputerBackend
from iris.integrations.google_vision import GoogleVisionClient
from iris.integrations.openai_client import OpenAIResponsesClient
from iris.mac_controller import ActionResult, MacController
from iris.perception import LiveScreenFrame, PerceptionService, ScreenAwarenessService
from iris.recipes import ActionRecipeRegistry
from iris.safety import SafetyGate, classify_action


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
        return tool.execute(arguments, context)

    def execute_approved(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult(False, f"I do not have a tool named {name}.")
        action = LocalAction(tool.name, arguments, tool.description, risk=tool.risk)
        decision = classify_action(action)
        if decision.risk == RiskLevel.BLOCKED:
            raise PermissionError(f"Blocked action: {tool.name} ({decision.reason})")
        context.safety_gate.assert_not_killed()
        return tool.execute(arguments, context)

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
            description="Find an element in the active app using screen/vision context.",
            parameters=_object_schema({"description": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_find_visible_element,
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
            name="browser_current_page",
            description="Return the active browser tab title and URL.",
            parameters=_object_schema({"browser": {"type": "string"}}, required=[]),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_current_page,
        ),
        ToolSpec(
            name="browser_extract",
            description="Extract visible text from the current browser tab.",
            parameters=_object_schema({"browser": {"type": "string"}}, required=[]),
            risk=RiskLevel.LOW_RISK,
            execute=_browser_extract,
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
            name="browser_type",
            description="Type into the active browser field. Requires approval.",
            parameters=_object_schema({"text": {"type": "string"}}),
            risk=RiskLevel.SENSITIVE,
            execute=_type_text,
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
            risk=RiskLevel.SENSITIVE,
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
            name="file_open",
            description="Open a local file path.",
            parameters=_object_schema({"path": {"type": "string"}}),
            risk=RiskLevel.LOW_RISK,
            execute=_file_open,
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
    result = _computer(context).open_url(url, browser, new_tab=new_tab)
    if result.ok:
        _remember_browser(context, url=url, browser=browser, current_task=_string_arg(arguments, "task"))
        return ToolResult(True, _friendly_opened_url(url, browser), {"url": url, "browser": browser})
    return _from_action_result(result)


def _browser_current_page(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    browser = _string_arg(arguments, "browser", "Google Chrome")
    result = _computer(context).browser_current_page(browser)
    if result.ok and isinstance(result.payload, dict) and context.session_state is not None:
        context.session_state["last_opened_url"] = result.payload.get("url", "")
    return _from_action_result(result)


def _browser_extract(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    return _from_action_result(
        _computer(context).browser_extract_text(_string_arg(arguments, "browser", "Google Chrome"))
    )


def _browser_click(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    result = _computer(context).browser_click_text(
        _string_arg(arguments, "text"),
        _string_arg(arguments, "browser", "Google Chrome"),
    )
    if result.ok:
        return ToolResult(True, f"I clicked {_string_arg(arguments, 'text')}.", result.payload)
    return _from_action_result(result)


def _browser_play_media(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    service = _string_arg(arguments, "service", "spotify").lower() or "spotify"
    query = _string_arg(arguments, "query")
    browser = _string_arg(arguments, "browser", "Google Chrome")
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
        fallback = context.controller.open_url(f"https://www.google.com/search?q={quote_plus(query)}")
        if fallback.ok:
            return ToolResult(
                True,
                "I could not identify it confidently yet, so I opened a web search for those lyrics.",
                {"query": query},
            )
        return ToolResult(False, f"I couldn't find a song match for those lyrics.")
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
    return _media_search_or_play(copied, context)


def _media_play(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    copied = dict(arguments)
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


def _media_search_or_play(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    service = _string_arg(arguments, "service", "spotify").lower()
    query = _string_arg(arguments, "query")
    surface = _string_arg(arguments, "surface", "auto").lower() or "auto"
    action = _string_arg(arguments, "action", "search").lower() or "search"
    browser = _string_arg(arguments, "browser", "Google Chrome")
    if not query:
        return ToolResult(False, "I need something to search or play.")
    if service and service != "spotify":
        return ToolResult(False, f"Media search for {service} is not connected yet.")
    if context.recipes is not None:
        recipe = context.recipes.find_for_service("spotify")
        if recipe and context.session_state is not None:
            context.session_state["current_recipe"] = recipe.name
    if surface == "browser":
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
    return _from_action_result(app_result)


def _media_play_current(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    service = _string_arg(arguments, "service", "spotify").lower()
    surface = _string_arg(arguments, "surface", "auto").lower() or "auto"
    browser = _string_arg(arguments, "browser", "Google Chrome")
    state = context.session_state or {}
    media = state.get("last_media") if isinstance(state.get("last_media"), dict) else {}
    query = _string_arg(media, "query") if isinstance(media, dict) else ""
    remembered_surface = _string_arg(media, "surface") if isinstance(media, dict) else ""
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
    return _from_action_result(context.controller.find_file(_string_arg(arguments, "query")))


def _file_open(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    path = _string_arg(arguments, "path")
    if not path:
        return ToolResult(False, "I need a file path.")
    return _from_action_result(context.controller.open_file(path))


def _workflow_run(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    name = _string_arg(arguments, "name")
    if not name:
        return ToolResult(False, "I need a workflow name.")
    return ToolResult(True, f"Workflow {name} is ready to run through `./iris run {name}`.", {"name": name})


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
    return ToolResult(
        True,
        f"Draft ready: {subject}",
        {"to": _string_arg(arguments, "to"), "subject": subject, "body": body},
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
    for item in results[:3]:
        title = item.get("title") or _domain(item.get("url", ""))
        snippet = item.get("page_text") or item.get("snippet") or "No short description available."
        lines.append(f"{title}: {_compact_text(snippet, 220)}")
    joined = " ".join(lines)
    return f"Here’s what I found publicly for {query}: {joined}"


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
