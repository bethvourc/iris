from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import re
import shutil
import sys

from iris.actions import RiskLevel
from iris.approvals import decide_approval, list_approvals
from iris.audit import list_audit, record_audit
from iris.config import IrisConfig, default_project_root
from iris.connectors import (
    connector_categories,
    connector_health,
    default_manifest_dirs,
    get_connector,
    list_connectors,
    set_connector_enabled,
)
from iris.knowledge import (
    format_search_results,
    get_page,
    graph_all_pages,
    graph_page,
    ingest_folder,
    list_pages,
    search_pages,
)
from iris.meetings import list_meetings, start_silent_meeting, stop_meeting
from iris.memory import add_memory, edit_memory, forget_memory, list_memories
from iris.memory_graph import (
    explain_fact,
    format_fact_results,
    related_facts,
    search_facts,
)
from iris.memory_lifecycle import maintain_lifecycle, set_memory_pinned
from iris.memory_review import decide_review_item, list_review_items
from iris.plugins import list_plugins, plugin_health, set_plugin_enabled
from iris.profile import has_user_profile, load_user_profile, save_user_profile
from iris.providers import ProviderRegistry
from iris.safety import AutomationPaused, SafetyGate
from iris.sessions import get_session, list_sessions
from iris.state import ensure_state, open_state
from iris.tasks import get_task, list_tasks, request_cancel, resume_task
from iris.tracing import latest_run_id, summarize_run_trace
from iris.watchers import add_watch, list_watches, run_watch_once
from iris.workflows import list_workflows, run_workflow


COMMON_COMMANDS = (
    ("iris start --live", "Start live voice mode"),
    ("iris start", "Start an interactive Iris session"),
    ("iris status", "Show local configuration status"),
    ("iris doctor", "Check permissions, connectors, and control backends"),
    ("iris tasks list", "List durable agent tasks"),
    ("iris runs list", "Inspect recent Iris runs"),
)

COMMAND_HINTS = {
    "live": ("iris start --live",),
    "listen": ("iris start --live",),
    "health": ("iris doctor", "iris control health"),
    "task": ("iris tasks list",),
    "run-next": ("iris tasks run-next",),
    "session": ("iris sessions list",),
    "plugin": ("iris plugins list",),
    "connector": ("iris connectors list",),
}


class IrisArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self._print_human_error(message)
        raise SystemExit(2)

    def _print_human_error(self, message: str) -> None:
        print(_format_parse_error(self, message), file=sys.stderr)


def _format_parse_error(parser: argparse.ArgumentParser, message: str) -> str:
    invalid = _parse_invalid_choice(message)
    required = _parse_required_argument(message)

    lines = ["Iris couldn't understand that command.", ""]
    if invalid:
        argument, value = invalid
        choices = _choices_for_argument(parser, argument)
        lines.extend(_invalid_choice_lines(parser, argument, value, choices))
    elif required:
        choices = _choices_for_argument(parser, required)
        lines.extend(_missing_argument_lines(parser, required, choices))
    elif message.startswith("unrecognized arguments:"):
        lines.extend(_unrecognized_argument_lines(parser, message))
    else:
        lines.append(f"Problem: {_sentence(message)}")
        lines.append("")
        lines.append(f"Run `{parser.prog} --help` to see valid usage.")

    lines.append("")
    lines.append("Developer detail:")
    lines.append(f"  {_developer_detail(invalid, required, message)}")
    return "\n".join(lines)


def _parse_invalid_choice(message: str) -> tuple[str, str] | None:
    match = re.match(
        r"argument (?P<argument>[^:]+): invalid choice: (?P<value>.+?) "
        r"\(choose from .+\)",
        message,
    )
    if not match:
        return None
    return match.group("argument"), match.group("value").strip("'\"")


def _parse_required_argument(message: str) -> str | None:
    match = re.match(r"the following arguments are required: (?P<argument>.+)", message)
    if not match:
        return None
    return match.group("argument").split(", ", maxsplit=1)[0]


def _choices_for_argument(
    parser: argparse.ArgumentParser, argument: str
) -> tuple[str, ...]:
    for action in parser._actions:
        if _action_matches_argument(action, argument):
            choices = action.choices or ()
            if isinstance(choices, dict):
                return tuple(str(choice) for choice in choices)
            return tuple(str(choice) for choice in choices)
    return ()


def _action_matches_argument(action: argparse.Action, argument: str) -> bool:
    return (
        action.dest == argument
        or argument in action.option_strings
        or action.dest == argument.replace("-", "_").lstrip("-")
    )


def _invalid_choice_lines(
    parser: argparse.ArgumentParser,
    argument: str,
    value: str,
    choices: tuple[str, ...],
) -> list[str]:
    lines: list[str] = []
    if argument == "command":
        lines.append(f"Problem: `{value}` is not an Iris command.")
    elif argument.endswith("_command"):
        command_name = argument.removesuffix("_command").replace("_", " ")
        lines.append(f"Problem: `{value}` is not a valid `{command_name}` command.")
    else:
        lines.append(f"Problem: `{value}` is not valid for `{argument}`.")

    suggestions = _command_suggestions(parser, value, choices)
    if suggestions:
        lines.append("")
        lines.append("Try:")
        lines.extend(f"  {suggestion}" for suggestion in suggestions[:4])
    elif choices:
        lines.append("")
        lines.append("Valid values:")
        lines.extend(f"  {choice}" for choice in choices[:8])
        if len(choices) > 8:
            lines.append(f"  ...and {len(choices) - 8} more")

    lines.extend(_help_lines(parser))
    return lines


def _missing_argument_lines(
    parser: argparse.ArgumentParser,
    argument: str,
    choices: tuple[str, ...],
) -> list[str]:
    lines: list[str] = []
    if argument.endswith("_command"):
        lines.append(f"Problem: `{parser.prog}` needs another command.")
    else:
        lines.append(f"Problem: `{argument}` is required.")

    suggestions = _command_suggestions(parser, "", choices)
    if suggestions:
        lines.append("")
        lines.append("Try one of:")
        lines.extend(f"  {suggestion}" for suggestion in suggestions[:5])

    lines.extend(_help_lines(parser))
    return lines


def _unrecognized_argument_lines(
    parser: argparse.ArgumentParser, message: str
) -> list[str]:
    value = message.removeprefix("unrecognized arguments:").strip()
    return [
        f"Problem: Iris does not recognize `{value}` here.",
        "",
        f"Run `{parser.prog} --help` to see the options accepted in this context.",
    ]


def _command_suggestions(
    parser: argparse.ArgumentParser, value: str, choices: tuple[str, ...]
) -> list[str]:
    suggestions: list[str] = []
    if parser.prog == "iris" and value in COMMAND_HINTS:
        suggestions.extend(COMMAND_HINTS[value])

    close_matches = difflib.get_close_matches(value, choices, n=4, cutoff=0.55)
    suggestions.extend(f"{parser.prog} {match}" for match in close_matches)

    if not suggestions and parser.prog == "iris":
        suggestions.extend(command for command, _description in COMMON_COMMANDS[:4])
    elif not suggestions and choices:
        suggestions.extend(f"{parser.prog} {choice}" for choice in choices[:5])

    return list(dict.fromkeys(suggestions))


def _help_lines(parser: argparse.ArgumentParser) -> list[str]:
    if parser.prog == "iris":
        lines = ["", "Common commands:"]
        width = max(len(command) for command, _description in COMMON_COMMANDS)
        for command, description in COMMON_COMMANDS:
            lines.append(f"  {command:<{width}}  {description}")
        lines.append("")
        lines.append("Run `iris --help` to see every command.")
        return lines
    return ["", f"Run `{parser.prog} --help` to see valid usage."]


def _sentence(message: str) -> str:
    return message[:1].upper() + message[1:]


def _developer_detail(
    invalid: tuple[str, str] | None, required: str | None, message: str
) -> str:
    if invalid:
        argument, value = invalid
        return f"argparse: invalid choice for {argument}: {value}"
    if required:
        return f"argparse: missing required argument: {required}"
    return f"argparse: {message}"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        return args.func(args)
    except AutomationPaused as exc:
        print(f"Automation paused: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        return 130
    except Exception as exc:
        print(f"iris error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = IrisArgumentParser(prog="iris", description="Local Mac agent CLI")
    parser.add_argument(
        "--project-root",
        default=str(default_project_root()),
        help="Project root containing .env",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize Iris local state").set_defaults(
        func=cmd_init
    )
    setup = subparsers.add_parser("setup", help="Run first-time local Iris setup")
    setup.add_argument("--name", default=None, help="Preferred name Iris should use")
    setup.add_argument("--full-name", default=None, help="Full display name")
    setup.add_argument("--pronouns", default=None, help="Pronouns, e.g. he/him")
    setup.add_argument(
        "--skip-profile",
        action="store_true",
        help="Skip profile setup and keep inferred defaults",
    )
    setup.set_defaults(func=cmd_setup)
    subparsers.add_parser(
        "status", help="Show local configuration status"
    ).set_defaults(func=cmd_status)
    subparsers.add_parser(
        "providers", help="Show provider registry and model routes"
    ).set_defaults(func=cmd_providers)
    profile = subparsers.add_parser(
        "profile", help="Show or update the local user profile"
    )
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("show", help="Show current user profile").set_defaults(
        func=cmd_profile_show
    )
    profile_set = profile_sub.add_parser("set", help="Save preferred name/pronouns")
    profile_set.add_argument("--name", default=None, help="Preferred first name")
    profile_set.add_argument("--full-name", default=None, help="Full display name")
    profile_set.add_argument("--pronouns", default=None, help="Pronouns, e.g. he/him")
    profile_set.set_defaults(func=cmd_profile_set)
    configure = subparsers.add_parser(
        "configure-provider", help="Print provider configuration guidance"
    )
    configure.add_argument("provider", choices=["openai", "groq", "pushover", "ntfy"])
    configure.set_defaults(func=cmd_configure_provider)
    subparsers.add_parser(
        "permissions", help="Check macOS permissions needed by Iris"
    ).set_defaults(func=cmd_permissions)
    doctor = subparsers.add_parser(
        "doctor", help="Check permissions, connectors, and control backends"
    )
    doctor.add_argument(
        "--json", action="store_true", help="Print machine-readable status"
    )
    doctor.add_argument(
        "--open",
        choices=["microphone", "screen", "accessibility", "automation", "music"],
        help="Open the matching macOS privacy settings pane",
    )
    doctor.set_defaults(func=cmd_doctor)

    notify = subparsers.add_parser(
        "test-notify", help="Send a phone push notification through configured provider"
    )
    notify.add_argument(
        "--message",
        default="Iris phone notification test.",
        help="Message body to send to your phone",
    )
    notify.add_argument("--title", default=None, help="Notification title")
    notify.set_defaults(func=cmd_test_notify)
    notify2 = subparsers.add_parser("notify", help="Notification commands")
    notify2_sub = notify2.add_subparsers(dest="notify_command", required=True)
    notify_test = notify2_sub.add_parser("test", help="Send a phone push notification")
    notify_test.add_argument("--message", default="Iris phone notification test.")
    notify_test.add_argument("--title", default=None)
    notify_test.set_defaults(func=cmd_test_notify)

    screen = subparsers.add_parser(
        "test-screen", help="Capture and describe the screen"
    )
    screen.add_argument(
        "--local",
        action="store_true",
        help="Skip OpenAI description and only verify local capture",
    )
    screen.set_defaults(func=cmd_test_screen)

    voice = subparsers.add_parser("test-voice", help="Test voice output/realtime text")
    voice.add_argument(
        "--remote",
        action="store_true",
        help="Also send a one-shot text prompt through OpenAI Realtime",
    )
    voice.add_argument(
        "--listen",
        action="store_true",
        help="Record microphone audio once and transcribe it through OpenAI Realtime",
    )
    voice.add_argument(
        "--seconds",
        type=float,
        default=4.0,
        help="Seconds to record when --listen is used",
    )
    voice.set_defaults(func=cmd_test_voice)

    subparsers.add_parser(
        "test-vision", help="Run Google Vision OCR on the screen"
    ).set_defaults(func=cmd_test_vision)

    control = subparsers.add_parser(
        "test-control", help="Run a safe live Mac control test"
    )
    control.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Do not prompt before opening TextEdit and typing test text",
    )
    control.set_defaults(func=cmd_test_control)

    control_backends = subparsers.add_parser(
        "control", help="Inspect Mac/browser control backends"
    )
    control_backends_sub = control_backends.add_subparsers(
        dest="control_command", required=True
    )
    control_backends_sub.add_parser(
        "health", help="Show control backend health"
    ).set_defaults(func=cmd_control_health)

    browser = subparsers.add_parser(
        "browser", help="Manage the Iris controlled browser runtime"
    )
    browser_sub = browser.add_subparsers(dest="browser_command", required=True)
    browser_sub.add_parser(
        "status", help="Show managed Chrome/CDP status"
    ).set_defaults(func=cmd_browser_status)
    browser_sub.add_parser(
        "start-cdp", help="Start managed Chrome with CDP enabled"
    ).set_defaults(func=cmd_browser_start_cdp)
    browser_sub.add_parser(
        "restart-cdp", help="Restart the Iris managed Chrome/CDP runtime"
    ).set_defaults(func=cmd_browser_restart_cdp)
    browser_sub.add_parser("tabs", help="List CDP Chrome tabs").set_defaults(
        func=cmd_browser_tabs
    )
    browser_sub.add_parser(
        "current", help="Show the current CDP Chrome tab"
    ).set_defaults(func=cmd_browser_current)

    controls = subparsers.add_parser(
        "controls", help="Inspect and operate native macOS UI through Accessibility"
    )
    controls_sub = controls.add_subparsers(dest="controls_command", required=True)
    controls_sub.add_parser("apps", help="List visible apps and windows").set_defaults(
        func=cmd_controls_apps
    )
    controls_inspect = controls_sub.add_parser(
        "inspect", help="Inspect the focused app Accessibility tree"
    )
    controls_inspect.add_argument("--max-depth", type=int, default=3)
    controls_inspect.add_argument("--max-items", type=int, default=120)
    controls_inspect.set_defaults(func=cmd_controls_inspect)
    controls_find = controls_sub.add_parser("find", help="Find a UI element")
    controls_find.add_argument("description")
    controls_find.set_defaults(func=cmd_controls_find)
    controls_click = controls_sub.add_parser("click", help="Click a UI element")
    controls_click.add_argument("description")
    controls_click.set_defaults(func=cmd_controls_click)

    start = subparsers.add_parser("start", help="Start the interactive Iris session")
    start.add_argument(
        "--wake",
        action="store_true",
        help="Start live speech-to-speech with wake-word gating",
    )
    start.add_argument(
        "--live",
        action="store_true",
        help="Alias for --wake",
    )
    start.set_defaults(func=cmd_start)

    serve = subparsers.add_parser("serve", help="Run the local Iris agent gateway")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=cmd_serve)

    tasks = subparsers.add_parser(
        "tasks", help="Inspect and control durable agent tasks"
    )
    tasks_sub = tasks.add_subparsers(dest="tasks_command", required=True)
    tasks_list = tasks_sub.add_parser("list", help="List tasks")
    tasks_list.add_argument("--status", default=None)
    tasks_list.add_argument("--limit", type=int, default=25)
    tasks_list.set_defaults(func=cmd_tasks_list)
    tasks_show = tasks_sub.add_parser("show", help="Show a task")
    tasks_show.add_argument("task_id")
    tasks_show.set_defaults(func=cmd_tasks_show)
    tasks_cancel = tasks_sub.add_parser("cancel", help="Request task cancellation")
    tasks_cancel.add_argument("task_id")
    tasks_cancel.set_defaults(func=cmd_tasks_cancel)
    tasks_resume = tasks_sub.add_parser(
        "resume", help="Requeue a failed, blocked, cancelled, or approval-waiting task"
    )
    tasks_resume.add_argument("task_id")
    tasks_resume.set_defaults(func=cmd_tasks_resume)
    tasks_sub.add_parser("run-next", help="Run one queued durable task").set_defaults(
        func=cmd_tasks_run_next
    )
    tasks_worker = tasks_sub.add_parser(
        "worker", help="Run the durable task worker loop"
    )
    tasks_worker.add_argument("--poll-interval", type=float, default=2.0)
    tasks_worker.set_defaults(func=cmd_tasks_worker)

    runs = subparsers.add_parser("runs", help="Inspect orchestrated Iris runs")
    runs_sub = runs.add_subparsers(dest="runs_command", required=True)
    runs_list = runs_sub.add_parser("list", help="List runs")
    runs_list.add_argument("--status", default=None)
    runs_list.add_argument("--limit", type=int, default=25)
    runs_list.set_defaults(func=cmd_runs_list)
    runs_show = runs_sub.add_parser("show", help="Show a run snapshot")
    runs_show.add_argument("run_id")
    runs_show.set_defaults(func=cmd_runs_show)
    runs_events = runs_sub.add_parser("events", help="Show run lifecycle events")
    runs_events.add_argument("run_id")
    runs_events.add_argument("--limit", type=int, default=200)
    runs_events.set_defaults(func=cmd_runs_events)
    runs_cancel = runs_sub.add_parser("cancel", help="Cancel a run")
    runs_cancel.add_argument("run_id")
    runs_cancel.add_argument("--reason", default="Operation cancelled.")
    runs_cancel.set_defaults(func=cmd_runs_cancel)
    runs_approvals = runs_sub.add_parser("approvals", help="List approvals for a run")
    runs_approvals.add_argument("run_id")
    runs_approvals.set_defaults(func=cmd_runs_approvals)

    evals = subparsers.add_parser("evals", help="Run Iris agent capability evaluations")
    evals_sub = evals.add_subparsers(dest="evals_command", required=True)
    evals_list = evals_sub.add_parser("list", help="List eval cases")
    evals_list.add_argument("--file", default=None)
    evals_list.set_defaults(func=cmd_evals_list)
    evals_run = evals_sub.add_parser("run", help="Run static or live evals")
    evals_run.add_argument("--file", default=None)
    evals_run.add_argument("--case", default=None, help="Run one case id")
    evals_run.add_argument(
        "--live-agent",
        action="store_true",
        help="Run cases through the live local agent",
    )
    evals_run.add_argument(
        "--include-unsafe",
        action="store_true",
        help="Allow live eval cases not marked live_safe",
    )
    evals_run.add_argument("--output", default=None)
    evals_run.set_defaults(func=cmd_evals_run)

    recipes = subparsers.add_parser("recipes", help="Inspect and run action recipes")
    recipes_sub = recipes.add_subparsers(dest="recipes_command", required=True)
    recipes_sub.add_parser("list", help="List available recipes").set_defaults(
        func=cmd_recipes_list
    )
    recipes_show = recipes_sub.add_parser("show", help="Show one recipe")
    recipes_show.add_argument("recipe_name")
    recipes_show.set_defaults(func=cmd_recipes_show)
    recipes_run = recipes_sub.add_parser("run", help="Run a recipe through Iris tools")
    recipes_run.add_argument("recipe_name")
    recipes_run.add_argument(
        "--input",
        action="append",
        default=[],
        help="Recipe input as key=value. Can be passed multiple times.",
    )
    recipes_run.set_defaults(func=cmd_recipes_run)

    plugins = subparsers.add_parser(
        "plugins", help="Inspect and configure Iris plugins"
    )
    plugins_sub = plugins.add_subparsers(dest="plugins_command", required=True)
    plugins_sub.add_parser("list", help="List plugins").set_defaults(
        func=cmd_plugins_list
    )
    plugins_enable = plugins_sub.add_parser("enable", help="Enable a plugin")
    plugins_enable.add_argument("plugin_id")
    plugins_enable.set_defaults(func=cmd_plugins_enable)
    plugins_disable = plugins_sub.add_parser("disable", help="Disable a plugin")
    plugins_disable.add_argument("plugin_id")
    plugins_disable.set_defaults(func=cmd_plugins_disable)
    plugins_sub.add_parser("health", help="Show plugin health").set_defaults(
        func=cmd_plugins_health
    )

    connectors = subparsers.add_parser(
        "connectors", help="Inspect and configure Iris connector manifests"
    )
    connectors_sub = connectors.add_subparsers(dest="connectors_command", required=True)
    connectors_list = connectors_sub.add_parser("list", help="List connector manifests")
    connectors_list.add_argument("--category", default=None)
    connectors_list.add_argument("--enabled-only", action="store_true")
    connectors_list.set_defaults(func=cmd_connectors_list)
    connectors_show = connectors_sub.add_parser("show", help="Show one connector")
    connectors_show.add_argument("connector_id")
    connectors_show.set_defaults(func=cmd_connectors_show)
    connectors_enable = connectors_sub.add_parser("enable", help="Enable a connector")
    connectors_enable.add_argument("connector_id")
    connectors_enable.set_defaults(func=cmd_connectors_enable)
    connectors_disable = connectors_sub.add_parser(
        "disable", help="Disable a connector"
    )
    connectors_disable.add_argument("connector_id")
    connectors_disable.set_defaults(func=cmd_connectors_disable)
    connectors_sub.add_parser("health", help="Show connector health").set_defaults(
        func=cmd_connectors_health
    )
    connectors_sub.add_parser(
        "categories", help="List connector categories"
    ).set_defaults(func=cmd_connectors_categories)

    sessions = subparsers.add_parser("sessions", help="Inspect Iris agent sessions")
    sessions_sub = sessions.add_subparsers(dest="sessions_command", required=True)
    sessions_list = sessions_sub.add_parser("list", help="List sessions")
    sessions_list.add_argument("--limit", type=int, default=25)
    sessions_list.set_defaults(func=cmd_sessions_list)
    sessions_show = sessions_sub.add_parser("show", help="Show a session and messages")
    sessions_show.add_argument("session_id")
    sessions_show.set_defaults(func=cmd_sessions_show)

    watch = subparsers.add_parser("watch", help="Manage watch rules")
    watch_sub = watch.add_subparsers(dest="watch_command", required=True)
    watch_add = watch_sub.add_parser("add", help="Add a watch rule")
    watch_add.add_argument(
        "--kind",
        required=True,
        choices=["web", "file", "command", "screen", "app", "repo"],
    )
    watch_add.add_argument("--name", required=True)
    watch_add.add_argument("--target", required=True)
    watch_add.add_argument("--expected", default=None)
    watch_add.add_argument("--selector", default=None)
    watch_add.add_argument("--interval", type=float, default=60.0)
    watch_add.add_argument("--timeout", type=float, default=120.0)
    watch_add.set_defaults(func=cmd_watch_add)
    watch_sub.add_parser("list", help="List watch rules").set_defaults(
        func=cmd_watch_list
    )
    watch_run = watch_sub.add_parser("run", help="Run a watch once")
    watch_run.add_argument("watch_id")
    watch_run.set_defaults(func=cmd_watch_run)

    memory = subparsers.add_parser("memory", help="Manage local memory")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    memory_add = memory_sub.add_parser("add", help="Add memory")
    memory_add.add_argument("--category", required=True)
    memory_add.add_argument("--content", required=True)
    memory_add.add_argument("--provenance", default="manual")
    memory_add.add_argument("--confidence", type=float, default=1.0)
    memory_add.add_argument("--sensitive", action="store_true")
    memory_add.set_defaults(func=cmd_memory_add)
    memory_list = memory_sub.add_parser("list", help="List memory")
    memory_list.add_argument("--category", default=None)
    memory_list.set_defaults(func=cmd_memory_list)
    memory_search = memory_sub.add_parser("search", help="Search graph-backed memory")
    memory_search.add_argument("query")
    memory_search.add_argument("--limit", type=int, default=10)
    memory_search.add_argument("--include-inactive", action="store_true")
    memory_search.add_argument("--json", action="store_true")
    memory_search.set_defaults(func=cmd_memory_search)
    memory_related = memory_sub.add_parser(
        "related", help="Show graph memory related to an entity"
    )
    memory_related.add_argument("entity")
    memory_related.add_argument("--limit", type=int, default=10)
    memory_related.add_argument("--include-inactive", action="store_true")
    memory_related.add_argument("--json", action="store_true")
    memory_related.set_defaults(func=cmd_memory_related)
    memory_explain = memory_sub.add_parser(
        "explain", help="Explain graph memory evidence"
    )
    memory_explain.add_argument("query")
    memory_explain.set_defaults(func=cmd_memory_explain)
    memory_sub.add_parser(
        "maintain", help="Recompute memory decay and lifecycle state"
    ).set_defaults(func=cmd_memory_maintain)
    memory_pin = memory_sub.add_parser(
        "pin", help="Prevent a graph memory from decaying"
    )
    memory_pin.add_argument("relation_id")
    memory_pin.set_defaults(func=cmd_memory_pin)
    memory_unpin = memory_sub.add_parser("unpin", help="Allow a graph memory to decay")
    memory_unpin.add_argument("relation_id")
    memory_unpin.set_defaults(func=cmd_memory_unpin)
    memory_review = memory_sub.add_parser(
        "review", help="Review pending memory candidates"
    )
    memory_review_sub = memory_review.add_subparsers(
        dest="memory_review_command", required=True
    )
    memory_review_list = memory_review_sub.add_parser(
        "list", help="List pending memory review items"
    )
    memory_review_list.add_argument("--status", default="pending")
    memory_review_list.add_argument("--limit", type=int, default=50)
    memory_review_list.set_defaults(func=cmd_memory_review_list)
    for decision in ("approve", "reject", "supersede"):
        review_decision = memory_review_sub.add_parser(
            decision, help=f"{decision.title()} a memory review item"
        )
        review_decision.add_argument("review_id")
        review_decision.add_argument("--notes", default="")
        review_decision.set_defaults(func=cmd_memory_review_decide, decision=decision)
    memory_edit = memory_sub.add_parser("edit", help="Edit memory")
    memory_edit.add_argument("memory_id")
    memory_edit.add_argument("--content", required=True)
    memory_edit.set_defaults(func=cmd_memory_edit)
    memory_forget = memory_sub.add_parser("forget", help="Forget memory")
    memory_forget.add_argument("memory_id")
    memory_forget.set_defaults(func=cmd_memory_forget)
    memory_scan = memory_sub.add_parser(
        "scan-machine",
        help="Store installed apps, project folders, browser profile, and connector health as local memory",
    )
    memory_scan.set_defaults(func=cmd_memory_scan_machine)

    knowledge = subparsers.add_parser(
        "knowledge", help="Manage local Iris knowledge/wiki"
    )
    knowledge_sub = knowledge.add_subparsers(dest="knowledge_command", required=True)
    knowledge_ingest = knowledge_sub.add_parser(
        "ingest-folder", help="Ingest text notes/files into local knowledge"
    )
    knowledge_ingest.add_argument("path")
    knowledge_ingest.add_argument("--limit", type=int, default=200)
    knowledge_ingest.add_argument("--no-recursive", action="store_true")
    knowledge_ingest.set_defaults(func=cmd_knowledge_ingest_folder)
    knowledge_search = knowledge_sub.add_parser("search", help="Search local knowledge")
    knowledge_search.add_argument("query")
    knowledge_search.add_argument("--limit", type=int, default=10)
    knowledge_search.add_argument("--json", action="store_true")
    knowledge_search.set_defaults(func=cmd_knowledge_search)
    knowledge_graph_page = knowledge_sub.add_parser(
        "graph-page", help="Extract graph relations from a local knowledge page"
    )
    knowledge_graph_page.add_argument("page_id")
    knowledge_graph_page.set_defaults(func=cmd_knowledge_graph_page)
    knowledge_graph_all = knowledge_sub.add_parser(
        "graph-all", help="Extract graph relations from local knowledge pages"
    )
    knowledge_graph_all.add_argument("--limit", type=int, default=50)
    knowledge_graph_all.set_defaults(func=cmd_knowledge_graph_all)
    knowledge_related = knowledge_sub.add_parser(
        "related", help="Show graph memory related to a knowledge entity"
    )
    knowledge_related.add_argument("entity")
    knowledge_related.add_argument("--limit", type=int, default=10)
    knowledge_related.add_argument("--include-inactive", action="store_true")
    knowledge_related.add_argument("--json", action="store_true")
    knowledge_related.set_defaults(func=cmd_knowledge_related)
    knowledge_list = knowledge_sub.add_parser("list", help="List local knowledge pages")
    knowledge_list.add_argument("--limit", type=int, default=50)
    knowledge_list.set_defaults(func=cmd_knowledge_list)
    knowledge_show = knowledge_sub.add_parser(
        "show", help="Show a local knowledge page"
    )
    knowledge_show.add_argument("page_id")
    knowledge_show.set_defaults(func=cmd_knowledge_show)

    meeting = subparsers.add_parser("meeting", help="Silent meeting mode records")
    meeting_sub = meeting.add_subparsers(dest="meeting_command", required=True)
    meeting_start = meeting_sub.add_parser("start", help="Start silent meeting mode")
    meeting_start.add_argument("--silent", action="store_true", required=True)
    meeting_start.add_argument("--title", default=None)
    meeting_start.add_argument("--disclosure-spoken", action="store_true")
    meeting_start.set_defaults(func=cmd_meeting_start)
    meeting_stop = meeting_sub.add_parser("stop", help="Stop a running meeting")
    meeting_stop.add_argument("--meeting-id", default=None)
    meeting_stop.set_defaults(func=cmd_meeting_stop)
    meeting_sub.add_parser("list", help="List meetings").set_defaults(
        func=cmd_meeting_list
    )

    subparsers.add_parser("workflows", help="List built-in workflows").set_defaults(
        func=cmd_workflows
    )
    run = subparsers.add_parser("run", help="Run or queue a workflow")
    run.add_argument("workflow")
    run.set_defaults(func=cmd_run_workflow)
    subparsers.add_parser("approvals", help="List pending approvals").set_defaults(
        func=cmd_approvals
    )
    approve = subparsers.add_parser("approve", help="Approve a pending action")
    approve.add_argument("approval_id")
    approve.set_defaults(func=cmd_approve)
    deny = subparsers.add_parser("deny", help="Deny a pending action")
    deny.add_argument("approval_id")
    deny.set_defaults(func=cmd_deny)
    audit = subparsers.add_parser("audit", help="Show audit trail")
    audit.add_argument("--limit", type=int, default=25)
    audit.set_defaults(func=cmd_audit)
    diagnostics = subparsers.add_parser(
        "diagnostics", help="Inspect run timing and response-loop diagnostics"
    )
    diagnostics_sub = diagnostics.add_subparsers(
        dest="diagnostics_command", required=True
    )
    diagnostics_last = diagnostics_sub.add_parser(
        "last-run", help="Show timing details for the latest traced run"
    )
    diagnostics_last.add_argument(
        "--run-id", default=None, help="Inspect a specific run instead of the latest"
    )
    diagnostics_last.set_defaults(func=cmd_diagnostics_last_run)
    rollback = subparsers.add_parser("rollback", help="Show rollback records for a run")
    rollback.add_argument("run_id")
    rollback.set_defaults(func=cmd_rollback)
    return parser


def _config(args: argparse.Namespace) -> IrisConfig:
    return IrisConfig.from_env(Path(args.project_root))


def _runtime(args: argparse.Namespace):
    from iris.integrations.google_vision import GoogleVisionClient
    from iris.integrations.openai_client import OpenAIResponsesClient
    from iris.mac_controller import MacController
    from iris.perception import PerceptionService
    from iris.router import ActionRouter

    config = _config(args)
    safety_gate = SafetyGate()
    perception = PerceptionService()
    controller = MacController(safety_gate)
    with open_state(config) as db:
        user_profile = load_user_profile(config, db)
    openai_client = OpenAIResponsesClient(config, user_profile=user_profile)
    google_vision = GoogleVisionClient(config.google_application_credentials)
    router = ActionRouter(
        perception=perception,
        controller=controller,
        safety_gate=safety_gate,
        openai_client=openai_client,
        google_vision=google_vision,
        config=config,
    )
    return (
        config,
        safety_gate,
        perception,
        controller,
        openai_client,
        google_vision,
        router,
    )


def cmd_init(args: argparse.Namespace) -> int:
    config = _config(args)
    path = ensure_state(config)
    profile_exists = False
    with open_state(config) as db:
        profile_exists = has_user_profile(db)
        record_audit(
            db,
            actor="user",
            tool="init",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            output_value={"state_db": str(path)},
        )
    print(f"Initialized Iris state: {path}")
    if not profile_exists:
        if sys.stdin.isatty():
            _run_profile_setup(config, interactive=True)
        else:
            print("Run `./iris setup` to personalize Iris.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        user_profile = load_user_profile(config, db)
    print(f"Project root: {config.project_root}")
    print(f"Agent name: {config.agent_name}")
    print(f"User name: {user_profile.preferred_name}")
    print(f"User pronouns: {user_profile.pronouns or 'not specified'}")
    print(f"OpenAI API key: {'configured' if config.has_openai else 'missing'}")
    print(
        "Google Vision credentials: "
        f"{'configured' if config.has_google_vision else 'missing'}"
    )
    print(f"Realtime model: {config.realtime_model}")
    print(f"Chat model: {config.chat_model}")
    print(f"Vision model: {config.vision_model}")
    print(f"Computer-use model: {config.computer_use_model}")
    print(f"Computer-use environment: {config.computer_use_environment}")
    print(f"Fast intent model: {config.fast_intent_model}")
    print(f"STT model: {config.stt_model}")
    print(f"Realtime transcription model: {config.realtime_transcription_model}")
    print(f"Groq API key: {'configured' if config.groq_api_key else 'missing'}")
    print(f"Notification provider: {config.notify_provider}")
    print(
        "Phone notifications: "
        f"{'configured' if config.has_phone_notifications else 'missing credentials'}"
    )
    print(f"State DB: {config.state_db_path}")
    print(f"Autonomy level: {config.autonomy_level}")
    print(f"Meeting consent required: {config.meeting_consent_required}")
    print(f"Listen seconds: {config.listen_seconds}")
    print(f"Wake poll seconds: {config.wake_poll_seconds}")
    print(f"Wake words: {', '.join(config.wake_words)}")
    print(f"Terminal macOS say responses: {config.speak_responses}")
    print(f"Max response chars: {config.max_response_chars}")
    for binary in ("screencapture", "osascript", "open", "mdfind", "say", "ffmpeg"):
        print(f"{binary}: {shutil.which(binary) or 'missing'}")
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    config = _config(args)
    registry = ProviderRegistry(config)
    _print_json(
        {
            "health": [item.__dict__ for item in registry.health()],
            "routes": {
                purpose: registry.route(purpose).__dict__
                for purpose in (
                    "realtime",
                    "reasoning",
                    "stt",
                    "fast-intent",
                    "computer-use",
                    "notification",
                )
            },
        }
    )
    return 0


def cmd_configure_provider(args: argparse.Namespace) -> int:
    guidance = {
        "openai": "Set OPENAI_API_KEY in .env.",
        "groq": "Set GROQ_API_KEY in .env.",
        "pushover": "Set IRIS_NOTIFY_PROVIDER=pushover, PUSHOVER_TOKEN, and PUSHOVER_USER in .env.",
        "ntfy": "Set IRIS_NOTIFY_PROVIDER=ntfy and IRIS_NTFY_TOPIC in .env.",
    }
    print(guidance[args.provider])
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    config = _config(args)
    if args.skip_profile:
        inferred = load_user_profile(config)
        result = _save_profile_setup(
            config,
            preferred_name=inferred.preferred_name,
            full_name=inferred.full_name,
            pronouns=inferred.pronouns,
        )
        _print_json({**result, "skipped": True})
        return 0
    if any((args.name, args.full_name, args.pronouns)):
        result = _save_profile_setup(
            config,
            preferred_name=args.name,
            full_name=args.full_name,
            pronouns=args.pronouns,
        )
        _print_json(result)
        return 0
    if not sys.stdin.isatty():
        print(
            "No setup values provided. Run `./iris setup --name <name>` or use an interactive terminal.",
            file=sys.stderr,
        )
        return 1
    _run_profile_setup(config, interactive=True)
    return 0


def cmd_profile_show(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        profile = load_user_profile(config, db)
    _print_json(profile.__dict__)
    return 0


def cmd_profile_set(args: argparse.Namespace) -> int:
    if not any((args.name, args.full_name, args.pronouns)):
        print(
            "Provide at least one of --name, --full-name, or --pronouns.",
            file=sys.stderr,
        )
        return 1
    config = _config(args)
    with open_state(config) as db:
        memory_id = save_user_profile(
            db,
            full_name=args.full_name,
            preferred_name=args.name,
            pronouns=args.pronouns,
        )
        profile = load_user_profile(config, db)
        record_audit(
            db,
            actor="user",
            tool="profile.set",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            output_value={"memory_id": memory_id, "profile": profile.__dict__},
        )
    _print_json({"memory_id": memory_id, "profile": profile.__dict__})
    return 0


def _run_profile_setup(config: IrisConfig, *, interactive: bool) -> None:
    inferred = load_user_profile(config)
    if not interactive:
        print("Run `./iris setup` to personalize Iris.")
        return
    print("Let's personalize Iris.")
    preferred_name = _prompt_name(
        "What should I call you? Type a name, or press Enter to keep",
        inferred.preferred_name,
    )
    full_name = _prompt_name(
        "What is your full name? Type it, or press Enter to keep",
        inferred.full_name,
    )
    pronouns = _prompt_optional("Pronouns (e.g. she/her)? Press Enter to skip.")
    result = _save_profile_setup(
        config,
        preferred_name=preferred_name,
        full_name=full_name,
        pronouns=pronouns,
    )
    profile = result["profile"]
    print(f"Saved profile. Iris will call you {profile['preferred_name']}.")


def _save_profile_setup(
    config: IrisConfig,
    *,
    preferred_name: str | None,
    full_name: str | None,
    pronouns: str | None,
) -> dict[str, object]:
    with open_state(config) as db:
        memory_id = save_user_profile(
            db,
            full_name=_clean_optional(full_name),
            preferred_name=_clean_optional(preferred_name),
            pronouns=_clean_optional(pronouns),
        )
        profile = load_user_profile(config, db)
        record_audit(
            db,
            actor="user",
            tool="setup.profile",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            output_value={"memory_id": memory_id, "profile": profile.__dict__},
        )
    return {"memory_id": memory_id, "profile": profile.__dict__}


_AFFIRMATIONS = {"yes", "y", "yeah", "yep", "ok", "okay", "sure", "correct", "right"}
_NEGATIONS = {"no", "n", "nope", "nah", "wrong", "incorrect"}


def _prompt_name(question: str, default: str) -> str:
    while True:
        value = input(f"{question} [{default}]: ").strip()
        if not value or value.lower() in _AFFIRMATIONS:
            return default
        if value.lower() in _NEGATIONS:
            print("No problem — type the name you'd like me to use.")
            continue
        return value


def _prompt_optional(question: str) -> str | None:
    value = input(f"{question} ").strip()
    return value or None


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def cmd_permissions(args: argparse.Namespace) -> int:
    from iris.permissions import PermissionChecker

    checker = PermissionChecker()
    statuses = checker.check_all()
    for status in statuses:
        print(f"{status.name}: {status.status}")
        print(f"  Capability: {status.capability}")
        print(f"  Detail: {status.detail}")
        print(f"  Settings: {status.settings_hint}")
    return 0 if all(item.status == "available" for item in statuses) else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    from iris.control_backends import control_backend_status
    from iris.machine_context import collect_machine_context
    from iris.permissions import PermissionChecker
    from iris.system import run_command

    settings_urls = {
        "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
        "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
        "music": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
    }
    if args.open:
        run_command(["open", settings_urls[args.open]], timeout=5)
    config = _config(args)
    with open_state(config) as db:
        permissions = [status.__dict__ for status in PermissionChecker().check_all()]
        backends = control_backend_status(config)
        connectors = connector_health(
            db,
            manifest_dirs=default_manifest_dirs(config.project_root),
        )
        machine = collect_machine_context(config, db)
    report = {
        "permissions": permissions,
        "control_backends": backends,
        "connectors": connectors,
        "machine": {
            "installed_apps_count": len(machine.get("installed_apps", [])),
            "project_folders_count": len(machine.get("project_folders", [])),
            "browser_profiles": machine.get("browser_profiles", {}),
        },
        "settings_urls": settings_urls,
        "cdp_setup": (
            'open -na "Google Chrome" --args --remote-debugging-port=9222 '
            "--remote-allow-origins=http://127.0.0.1:9222 "
            '--user-data-dir="$HOME/.iris/chrome-cdp"'
        ),
    }
    if args.json:
        _print_json(report)
        return 0 if _doctor_ok(report) else 1
    print("Iris Doctor")
    print("Permissions:")
    for item in permissions:
        print(f"  {item['name']}: {item['status']} - {item['detail']}")
        if item["status"] != "available":
            print(f"    Settings: {item['settings_hint']}")
    print("Control backends:")
    for item in backends:
        status = "ready" if item["available"] else "missing"
        print(f"  {item['name']}: {status} - {item['detail']}")
    if not any(
        item["backend_id"] == "browser_cdp" and item["available"] for item in backends
    ):
        print("  CDP setup:")
        print(f"    {report['cdp_setup']}")
    if not any(
        item["backend_id"] == "apple_music" and item["available"] for item in backends
    ):
        print("  Apple Music setup:")
        print(
            "    Allow the terminal app running Iris to control Music in "
            "System Settings > Privacy & Security > Automation."
        )
    ready = [item for item in connectors if item.get("health") == "ready"]
    needs_setup = [
        item
        for item in connectors
        if item.get("enabled") and item.get("health") != "ready"
    ]
    print(f"Connectors: {len(ready)} ready, {len(needs_setup)} enabled needing setup")
    print(
        "Machine context: "
        f"{report['machine']['installed_apps_count']} apps, "
        f"{report['machine']['project_folders_count']} project folders"
    )
    return 0 if _doctor_ok(report) else 1


def _doctor_ok(report: dict[str, object]) -> bool:
    permissions = report.get("permissions")
    backends = report.get("control_backends")
    if not isinstance(permissions, list) or not isinstance(backends, list):
        return False
    required_permissions = {
        "Microphone",
        "Screen Recording",
        "Accessibility",
        "Music Automation",
    }
    permission_ok = all(
        not isinstance(item, dict)
        or item.get("name") not in required_permissions
        or item.get("status") == "available"
        for item in permissions
    )
    accessibility_ok = any(
        isinstance(item, dict)
        and item.get("backend_id") == "accessibility"
        and item.get("available")
        for item in backends
    )
    apple_music_ok = any(
        isinstance(item, dict)
        and item.get("backend_id") == "apple_music"
        and item.get("available")
        for item in backends
    )
    return permission_ok and accessibility_ok and apple_music_ok


def cmd_test_notify(args: argparse.Namespace) -> int:
    from iris.notifications import NotificationService

    config = _config(args)
    result = NotificationService(config).send_phone_push(
        args.message,
        title=args.title,
        tags="iris",
    )
    print(result.detail)
    if result.status_code is not None:
        print(f"HTTP status: {result.status_code}")
    with open_state(config) as db:
        record_audit(
            db,
            actor="user",
            tool="notify.test",
            risk=RiskLevel.LOW_RISK,
            result="ok" if result.ok else "error",
            input_value={"message": args.message, "title": args.title},
            output_value=result.__dict__,
        )
    return 0 if result.ok else 1


def cmd_test_screen(args: argparse.Namespace) -> int:
    config, _, perception, _, openai_client, _, router = _runtime(args)
    screenshot = perception.capture_screen()
    context = perception.screen_context()
    print(
        f"Captured screen: {screenshot.width or '?'}x{screenshot.height or '?'} "
        f"at {screenshot.captured_at.isoformat()}"
    )
    print(f"Active app: {context.active_app or 'unknown'}")
    print(f"Active window: {context.active_window or 'unknown'}")
    if args.local or not config.has_openai:
        if not config.has_openai and not args.local:
            print("OPENAI_API_KEY missing; skipped visual description.")
        return 0
    result = router.describe_current_screen("Describe what you can see on my screen.")
    print(result.message)
    return 0 if result.ok else 1


def cmd_test_voice(args: argparse.Namespace) -> int:
    from iris.voice import RealtimeAudioClient, RealtimeTextClient

    config = _config(args)
    if (args.remote or args.listen) and not config.has_openai:
        print("OPENAI_API_KEY missing; cannot test OpenAI Realtime voice features.")
        return 1
    phrase = f"{config.agent_name} voice output test."
    print(f"Speaking locally with macOS say: {phrase}")
    result = shutil.which("say")
    if result:
        from iris.system import run_command

        say_result = run_command(["say", phrase], timeout=20)
        if not say_result.ok:
            print(say_result.stderr or "macOS say failed")
            return 1
    else:
        print("say binary is missing; voice output unavailable.")
        return 1
    if args.remote:
        client = RealtimeTextClient(config)
        response = client.send_text_once(
            "Say one short sentence confirming Iris is online."
        )
        print(f"Realtime response: {response}")
    if args.listen:
        print(f"Listening for {args.seconds:.1f} seconds...")
        transcript = RealtimeAudioClient(config).transcribe_once(seconds=args.seconds)
        print(f"Transcript: {transcript}")
    return 0


def cmd_test_vision(args: argparse.Namespace) -> int:
    config, _, perception, _, _, google_vision, _ = _runtime(args)
    if not config.has_google_vision:
        print("GOOGLE_APPLICATION_CREDENTIALS missing; cannot run Google Vision OCR.")
        return 1
    screenshot = perception.capture_screen()
    ocr = google_vision.extract_text(screenshot.png)
    labels = google_vision.label_image(screenshot.png, max_results=5)
    print("OCR text:")
    print(ocr.text.strip() or "(no text detected)")
    print("Labels:")
    for label in labels:
        print(f"- {label.description}: {label.score:.2f}")
    return 0


def cmd_test_control(args: argparse.Namespace) -> int:
    _, safety_gate, _, controller, _, _, _ = _runtime(args)
    if not args.yes:
        print("This will open TextEdit and type a short Iris control test line.")
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Control test cancelled.")
            return 1
    steps = [
        controller.open_app("TextEdit"),
        controller.wait(0.8),
        controller.type_text("Iris control test. The kill switch is next."),
    ]
    for step in steps:
        print(f"{step.action}: {'ok' if step.ok else 'failed'} - {step.detail}")
        if not step.ok:
            return 1
    safety_gate.kill()
    try:
        controller.open_app("TextEdit")
    except AutomationPaused:
        print("kill_switch: ok - automation blocked after kill switch")
        safety_gate.resume()
        return 0
    print("kill_switch: failed - automation was not blocked")
    return 1


def cmd_control_health(args: argparse.Namespace) -> int:
    from iris.control_backends import control_backend_status

    config = _config(args)
    _print_json(control_backend_status(config))
    return 0


def cmd_browser_status(args: argparse.Namespace) -> int:
    from iris.managed_browser import ManagedChrome

    result = ManagedChrome().status()
    _print_json({"ok": result.ok, "message": result.detail, "payload": result.payload})
    return 0 if result.ok else 1


def cmd_browser_start_cdp(args: argparse.Namespace) -> int:
    from iris.managed_browser import ManagedChrome

    result = ManagedChrome().ensure_running()
    _print_json({"ok": result.ok, "message": result.detail, "payload": result.payload})
    return 0 if result.ok else 1


def cmd_browser_restart_cdp(args: argparse.Namespace) -> int:
    from iris.managed_browser import ManagedChrome

    result = ManagedChrome().restart()
    _print_json({"ok": result.ok, "message": result.detail, "payload": result.payload})
    return 0 if result.ok else 1


def cmd_browser_tabs(args: argparse.Namespace) -> int:
    from iris.browser_cdp import ChromeCDPBackend

    result = ChromeCDPBackend(auto_start=True).list_tabs()
    _print_json({"ok": result.ok, "message": result.detail, "payload": result.payload})
    return 0 if result.ok else 1


def cmd_browser_current(args: argparse.Namespace) -> int:
    from iris.browser_cdp import ChromeCDPBackend

    result = ChromeCDPBackend(auto_start=True).current_page()
    _print_json({"ok": result.ok, "message": result.detail, "payload": result.payload})
    return 0 if result.ok else 1


def cmd_controls_apps(args: argparse.Namespace) -> int:
    from iris.accessibility_backend import AccessibilityBackend

    result = AccessibilityBackend().list_apps_windows()
    _print_json({"ok": result.ok, "message": result.detail, "payload": result.payload})
    return 0 if result.ok else 1


def cmd_controls_inspect(args: argparse.Namespace) -> int:
    from iris.accessibility_backend import AccessibilityBackend

    result = AccessibilityBackend().inspect_focused_app(
        max_depth=args.max_depth,
        max_items=args.max_items,
    )
    _print_json({"ok": result.ok, "message": result.detail, "payload": result.payload})
    return 0 if result.ok else 1


def cmd_controls_find(args: argparse.Namespace) -> int:
    from iris.accessibility_backend import AccessibilityBackend

    result = AccessibilityBackend().find_element(args.description)
    _print_json({"ok": result.ok, "message": result.detail, "payload": result.payload})
    return 0 if result.ok else 1


def cmd_controls_click(args: argparse.Namespace) -> int:
    from iris.accessibility_backend import AccessibilityBackend

    result = AccessibilityBackend().click_element(args.description)
    _print_json({"ok": result.ok, "message": result.detail, "payload": result.payload})
    return 0 if result.ok else 1


def cmd_start(args: argparse.Namespace) -> int:
    from iris.runtime import RunOrchestrator
    from iris.voice import VoiceSession

    config, safety_gate, _, _, _, _, router = _runtime(args)

    def router_factory():
        return router

    orchestrator = RunOrchestrator(config=config, router_factory=router_factory)
    with open_state(config) as db:
        user_profile = load_user_profile(config, db)
    VoiceSession(
        config=config,
        router=router,
        safety_gate=safety_gate,
        user_profile=user_profile,
        orchestrator=orchestrator,
    ).run_terminal_loop(wake_mode=args.wake or args.live)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from iris.gateway import GatewayService

    config = _config(args)

    def router_factory():
        return _runtime(args)[-1]

    GatewayService(config=config, router_factory=router_factory).serve(
        host=args.host,
        port=args.port,
    )
    return 0


def cmd_tasks_list(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(list_tasks(db, status=args.status, limit=args.limit))
    return 0


def cmd_tasks_show(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        task = get_task(db, args.task_id)
    if task is None:
        print("Task not found.", file=sys.stderr)
        return 1
    _print_json(task)
    return 0


def cmd_tasks_cancel(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        ok = request_cancel(db, args.task_id)
        record_audit(
            db,
            actor="user",
            tool="task.cancel",
            risk=RiskLevel.LOW_RISK,
            result="ok" if ok else "error",
            input_value={"task_id": args.task_id},
        )
    _print_json({"ok": ok})
    return 0 if ok else 1


def cmd_tasks_resume(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        ok = resume_task(db, args.task_id)
        record_audit(
            db,
            actor="user",
            tool="task.resume",
            risk=RiskLevel.LOW_RISK,
            result="ok" if ok else "error",
            input_value={"task_id": args.task_id},
        )
    _print_json({"ok": ok})
    return 0 if ok else 1


def cmd_tasks_run_next(args: argparse.Namespace) -> int:
    from iris.supervisor import TaskSupervisor

    config = _config(args)

    def router_factory():
        return _runtime(args)[-1]

    result = TaskSupervisor(config=config, router_factory=router_factory).run_next()
    _print_json(result.__dict__)
    return 0 if result.ok else 1


def cmd_tasks_worker(args: argparse.Namespace) -> int:
    from iris.supervisor import TaskSupervisor

    config = _config(args)

    def router_factory():
        return _runtime(args)[-1]

    TaskSupervisor(config=config, router_factory=router_factory).run_forever(
        poll_interval=args.poll_interval
    )
    return 0


def _run_orchestrator(args: argparse.Namespace):
    from iris.runtime import RunOrchestrator

    config = _config(args)
    return RunOrchestrator(config=config, router_factory=lambda: _runtime(args)[-1])


def cmd_runs_list(args: argparse.Namespace) -> int:
    orchestrator = _run_orchestrator(args)
    _print_json(
        {
            "runs": [
                run.to_dict()
                for run in orchestrator.list_runs(
                    status=args.status,
                    limit=args.limit,
                )
            ]
        }
    )
    return 0


def cmd_runs_show(args: argparse.Namespace) -> int:
    orchestrator = _run_orchestrator(args)
    snapshot = orchestrator.get_run(args.run_id)
    if snapshot is None:
        print("Run not found.", file=sys.stderr)
        return 1
    _print_json(snapshot.to_dict())
    return 0


def cmd_runs_events(args: argparse.Namespace) -> int:
    orchestrator = _run_orchestrator(args)
    _print_json({"events": orchestrator.list_events(args.run_id, limit=args.limit)})
    return 0


def cmd_runs_cancel(args: argparse.Namespace) -> int:
    orchestrator = _run_orchestrator(args)
    ok = orchestrator.cancel_run(args.run_id, reason=args.reason)
    _print_json({"ok": ok})
    return 0 if ok else 1


def cmd_runs_approvals(args: argparse.Namespace) -> int:
    orchestrator = _run_orchestrator(args)
    _print_json({"approvals": orchestrator.approvals_for_run(args.run_id)})
    return 0


def cmd_evals_list(args: argparse.Namespace) -> int:
    from iris.evals import default_eval_file, list_eval_cases

    config = _config(args)
    path = Path(args.file) if args.file else default_eval_file(config.project_root)
    _print_json(list_eval_cases(path))
    return 0


def cmd_evals_run(args: argparse.Namespace) -> int:
    from iris.evals import (
        default_eval_file,
        load_eval_cases,
        run_live_evals,
        run_static_evals,
        summarize_results,
        write_eval_report,
    )

    config = _config(args)
    path = Path(args.file) if args.file else default_eval_file(config.project_root)
    cases = load_eval_cases(path)
    if args.case:
        cases = [case for case in cases if case.case_id == args.case]
    if not cases:
        print("No eval cases matched.", file=sys.stderr)
        return 1
    if args.live_agent:
        results = run_live_evals(
            cases,
            router_factory=lambda: _runtime(args)[-1],
            include_unsafe=args.include_unsafe,
        )
        mode = "live"
    else:
        results = run_static_evals(cases, project_root=config.project_root)
        mode = "static"
    output_path = Path(args.output) if args.output else None
    report_path = write_eval_report(
        project_root=config.project_root,
        results=results,
        mode=mode,
        output_path=output_path,
    )
    _print_json(
        {
            "mode": mode,
            "summary": summarize_results(results),
            "report_path": str(report_path),
        }
    )
    return 0 if all(result.passed for result in results) else 1


def cmd_recipes_list(args: argparse.Namespace) -> int:
    from iris.recipes import ActionRecipeRegistry

    config = _config(args)
    recipes = ActionRecipeRegistry.default(config.project_root)
    _print_json(recipes.schemas())
    return 0


def cmd_recipes_show(args: argparse.Namespace) -> int:
    from iris.recipes import ActionRecipeRegistry

    config = _config(args)
    recipe = ActionRecipeRegistry.default(config.project_root).get(args.recipe_name)
    if recipe is None:
        print("Recipe not found.", file=sys.stderr)
        return 1
    _print_json(recipe.schema())
    return 0


def cmd_recipes_run(args: argparse.Namespace) -> int:
    from iris.tools import ToolContext, ToolRegistry

    (
        config,
        safety_gate,
        perception,
        controller,
        openai_client,
        google_vision,
        router,
    ) = _runtime(args)
    inputs = _parse_key_value_args(args.input)
    registry = ToolRegistry.default()
    tool_context = ToolContext(
        controller=controller,
        perception=perception,
        safety_gate=safety_gate,
        openai_client=openai_client,
        google_vision=google_vision,
        computer=router.agent_executor.computer_backend,
        screen_awareness=router.screen_awareness,
        computer_use_runner=router.agent_executor.computer_use_runner,
        session_state={},
        recipes=router.agent_executor.recipes,
        config=config,
        approved_tool_call=True,
    )
    result = registry.execute_approved(
        "recipe_run",
        {"recipe_name": args.recipe_name, "inputs": inputs},
        tool_context,
    )
    _print_json({"ok": result.ok, "message": result.message, "payload": result.payload})
    return 0 if result.ok else 1


def cmd_plugins_list(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(list_plugins(db))
    return 0


def cmd_plugins_enable(args: argparse.Namespace) -> int:
    return _set_plugin(args, True)


def cmd_plugins_disable(args: argparse.Namespace) -> int:
    return _set_plugin(args, False)


def cmd_plugins_health(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(plugin_health(db))
    return 0


def cmd_connectors_list(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(
            list_connectors(
                db,
                manifest_dirs=default_manifest_dirs(config.project_root),
                category=args.category,
                enabled_only=args.enabled_only,
            )
        )
    return 0


def cmd_connectors_show(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        connector = get_connector(
            db,
            args.connector_id,
            manifest_dirs=default_manifest_dirs(config.project_root),
        )
    if connector is None:
        print("Connector not found.", file=sys.stderr)
        return 1
    _print_json(connector)
    return 0


def cmd_connectors_enable(args: argparse.Namespace) -> int:
    return _set_connector(args, True)


def cmd_connectors_disable(args: argparse.Namespace) -> int:
    return _set_connector(args, False)


def cmd_connectors_health(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(
            connector_health(
                db,
                manifest_dirs=default_manifest_dirs(config.project_root),
            )
        )
    return 0


def cmd_connectors_categories(args: argparse.Namespace) -> int:
    config = _config(args)
    _print_json(connector_categories(default_manifest_dirs(config.project_root)))
    return 0


def _set_connector(args: argparse.Namespace, enabled: bool) -> int:
    config = _config(args)
    with open_state(config) as db:
        ok = set_connector_enabled(
            db,
            args.connector_id,
            enabled,
            manifest_dirs=default_manifest_dirs(config.project_root),
        )
        record_audit(
            db,
            actor="user",
            tool="connector.enable" if enabled else "connector.disable",
            risk=RiskLevel.LOW_RISK,
            result="ok" if ok else "error",
            input_value={"connector_id": args.connector_id},
        )
    _print_json({"ok": ok, "connector_id": args.connector_id, "enabled": enabled})
    return 0 if ok else 1


def _set_plugin(args: argparse.Namespace, enabled: bool) -> int:
    config = _config(args)
    with open_state(config) as db:
        ok = set_plugin_enabled(db, args.plugin_id, enabled)
        record_audit(
            db,
            actor="user",
            tool="plugin.enable" if enabled else "plugin.disable",
            risk=RiskLevel.LOW_RISK,
            result="ok" if ok else "error",
            input_value={"plugin_id": args.plugin_id},
        )
    _print_json({"ok": ok, "plugin_id": args.plugin_id, "enabled": enabled})
    return 0 if ok else 1


def _parse_key_value_args(items: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items:
        key, sep, value = str(item).partition("=")
        if sep and key.strip():
            values[key.strip()] = value
    return values


def cmd_sessions_list(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(list_sessions(db, limit=args.limit))
    return 0


def cmd_sessions_show(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        session = get_session(db, args.session_id)
    if session is None:
        print("Session not found.", file=sys.stderr)
        return 1
    _print_json(session)
    return 0


def cmd_watch_add(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        watch_id = add_watch(
            db,
            kind=args.kind,
            name=args.name,
            target=args.target,
            expected=args.expected,
            selector=args.selector,
            interval_seconds=args.interval,
            timeout_seconds=args.timeout,
        )
        record_audit(
            db,
            actor="user",
            tool="watch.add",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            output_value={"watch_id": watch_id},
        )
    _print_json({"watch_id": watch_id})
    return 0


def cmd_watch_list(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(list_watches(db))
    return 0


def cmd_watch_run(args: argparse.Namespace) -> int:
    from iris.perception import PerceptionService

    config = _config(args)
    perception = PerceptionService()
    with open_state(config) as db:
        result = run_watch_once(db, args.watch_id, perception=perception)
        record_audit(
            db,
            actor="user",
            tool="watch.run",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            input_value={"watch_id": args.watch_id},
            output_value=result.__dict__,
        )
    _print_json(result.__dict__)
    return 0


def cmd_memory_add(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        memory_id = add_memory(
            db,
            category=args.category,
            content=args.content,
            provenance=args.provenance,
            confidence=args.confidence,
            sensitive=args.sensitive,
        )
        record_audit(
            db,
            actor="user",
            tool="memory.add",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            output_value={"memory_id": memory_id},
        )
    _print_json({"memory_id": memory_id})
    return 0


def cmd_memory_list(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(list_memories(db, args.category))
    return 0


def cmd_memory_search(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        results = search_facts(
            db,
            args.query,
            limit=args.limit,
            include_inactive=args.include_inactive,
            config=config,
        )
        record_audit(
            db,
            actor="user",
            tool="memory.search",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            input_value={
                "query": args.query,
                "limit": args.limit,
                "include_inactive": args.include_inactive,
            },
            output_value={"count": len(results)},
        )
    if args.json:
        _print_json(results)
    else:
        print(format_fact_results(results))
    return 0


def cmd_memory_related(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        results = related_facts(
            db,
            args.entity,
            limit=args.limit,
            include_inactive=args.include_inactive,
        )
        record_audit(
            db,
            actor="user",
            tool="memory.related",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            input_value={
                "entity": args.entity,
                "limit": args.limit,
                "include_inactive": args.include_inactive,
            },
            output_value={"count": len(results)},
        )
    if args.json:
        _print_json(results)
    else:
        print(format_fact_results(results))
    return 0


def cmd_memory_explain(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        result = explain_fact(db, args.query)
        record_audit(
            db,
            actor="user",
            tool="memory.explain",
            risk=RiskLevel.LOW_RISK,
            result="ok" if result else "error",
            input_value={"query": args.query},
            output_value={"found": result is not None},
        )
    if result is None:
        print("I did not find evidence for that graph memory.", file=sys.stderr)
        return 1
    _print_json(result)
    return 0


def cmd_memory_maintain(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        result = maintain_lifecycle(db)
        record_audit(
            db,
            actor="user",
            tool="memory.maintain",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            output_value=result.__dict__,
        )
    _print_json(result.__dict__)
    return 0


def cmd_memory_pin(args: argparse.Namespace) -> int:
    return _cmd_memory_set_pinned(args, pinned=True)


def cmd_memory_unpin(args: argparse.Namespace) -> int:
    return _cmd_memory_set_pinned(args, pinned=False)


def _cmd_memory_set_pinned(args: argparse.Namespace, *, pinned: bool) -> int:
    config = _config(args)
    with open_state(config) as db:
        ok = set_memory_pinned(db, args.relation_id, pinned)
        record_audit(
            db,
            actor="user",
            tool="memory.pin" if pinned else "memory.unpin",
            risk=RiskLevel.LOW_RISK,
            result="ok" if ok else "error",
            input_value={"relation_id": args.relation_id, "pinned": pinned},
        )
    _print_json({"ok": ok, "relation_id": args.relation_id, "pinned": pinned})
    return 0 if ok else 1


def cmd_memory_review_list(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        items = list_review_items(db, status=args.status, limit=args.limit)
        record_audit(
            db,
            actor="user",
            tool="memory.review.list",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            input_value={"status": args.status, "limit": args.limit},
            output_value={"count": len(items)},
        )
    _print_json(items)
    return 0


def cmd_memory_review_decide(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        result = decide_review_item(
            db,
            review_id=args.review_id,
            decision=args.decision,
            actor="user",
            notes=args.notes,
        )
        record_audit(
            db,
            actor="user",
            tool=f"memory.review.{args.decision}",
            risk=RiskLevel.LOW_RISK,
            result="ok" if result.ok else "error",
            input_value={
                "review_id": args.review_id,
                "decision": args.decision,
                "notes": args.notes,
            },
            output_value=result.__dict__,
        )
    _print_json(result.__dict__)
    return 0 if result.ok else 1


def cmd_memory_edit(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        ok = edit_memory(db, args.memory_id, args.content)
        record_audit(
            db,
            actor="user",
            tool="memory.edit",
            risk=RiskLevel.SENSITIVE,
            result="ok" if ok else "error",
        )
    _print_json({"ok": ok})
    return 0 if ok else 1


def cmd_memory_forget(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        ok = forget_memory(db, args.memory_id)
        record_audit(
            db,
            actor="user",
            tool="memory.forget",
            risk=RiskLevel.SENSITIVE,
            result="ok" if ok else "error",
        )
    _print_json({"ok": ok})
    return 0 if ok else 1


def cmd_memory_scan_machine(args: argparse.Namespace) -> int:
    from iris.machine_context import collect_machine_context, remember_machine_context

    config = _config(args)
    with open_state(config) as db:
        context = collect_machine_context(config, db)
        memory_ids = remember_machine_context(config, db)
        record_audit(
            db,
            actor="user",
            tool="memory.scan_machine",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            output_value={"memory_ids": memory_ids},
        )
    _print_json(
        {
            "memory_ids": memory_ids,
            "installed_apps_count": len(context.get("installed_apps", [])),
            "project_folders_count": len(context.get("project_folders", [])),
        }
    )
    return 0


def cmd_knowledge_ingest_folder(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        result = ingest_folder(
            db,
            Path(args.path),
            limit=args.limit,
            recursive=not args.no_recursive,
        )
        record_audit(
            db,
            actor="user",
            tool="knowledge.ingest_folder",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            input_value={
                "path": args.path,
                "limit": args.limit,
                "recursive": not args.no_recursive,
            },
            output_value=result.__dict__,
        )
    _print_json(result.__dict__)
    return 0


def cmd_knowledge_search(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        results = search_pages(db, args.query, limit=args.limit)
        graph_results = search_facts(db, args.query, limit=5, config=config)
        record_audit(
            db,
            actor="user",
            tool="knowledge.search",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            input_value={"query": args.query, "limit": args.limit},
            output_value={"count": len(results), "graph_count": len(graph_results)},
        )
    if args.json:
        _print_json({"results": results, "graph_results": graph_results})
    else:
        print(format_search_results(results))
        if graph_results:
            print(format_fact_results(graph_results))
    return 0


def cmd_knowledge_graph_page(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        result = graph_page(db, args.page_id)
        record_audit(
            db,
            actor="user",
            tool="knowledge.graph_page",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            input_value={"page_id": args.page_id},
            output_value=result.__dict__,
        )
    _print_json(result.__dict__)
    return 0


def cmd_knowledge_graph_all(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        result = graph_all_pages(db, limit=args.limit)
        record_audit(
            db,
            actor="user",
            tool="knowledge.graph_all",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            input_value={"limit": args.limit},
            output_value=result.__dict__,
        )
    _print_json(result.__dict__)
    return 0


def cmd_knowledge_related(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        results = related_facts(
            db,
            args.entity,
            limit=args.limit,
            include_inactive=args.include_inactive,
        )
        record_audit(
            db,
            actor="user",
            tool="knowledge.related",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            input_value={
                "entity": args.entity,
                "limit": args.limit,
                "include_inactive": args.include_inactive,
            },
            output_value={"count": len(results)},
        )
    if args.json:
        _print_json(results)
    else:
        print(format_fact_results(results))
    return 0


def cmd_knowledge_list(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(list_pages(db, limit=args.limit))
    return 0


def cmd_knowledge_show(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        page = get_page(db, args.page_id)
    if page is None:
        print("Knowledge page not found.", file=sys.stderr)
        return 1
    _print_json(page)
    return 0


def cmd_meeting_start(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        meeting_id = start_silent_meeting(
            db,
            config,
            title=args.title,
            disclosure_spoken=args.disclosure_spoken,
        )
        record_audit(
            db,
            actor="user",
            tool="meeting.start",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            output_value={"meeting_id": meeting_id},
        )
    _print_json(
        {
            "meeting_id": meeting_id,
            "silent": True,
            "consent_required": config.meeting_consent_required,
        }
    )
    return 0


def cmd_meeting_stop(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        result = stop_meeting(db, config, args.meeting_id)
        record_audit(
            db,
            actor="user",
            tool="meeting.stop",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            output_value=result,
        )
    _print_json(result)
    return 0


def cmd_meeting_list(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(list_meetings(db))
    return 0


def cmd_workflows(args: argparse.Namespace) -> int:
    _print_json(list_workflows())
    return 0


def cmd_run_workflow(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        result = run_workflow(db, args.workflow)
        record_audit(
            db,
            actor="user",
            tool="workflow.run",
            risk=RiskLevel.SENSITIVE
            if result.get("approval_id")
            else RiskLevel.LOW_RISK,
            result=str(result["status"]),
            output_value=result,
        )
    _print_json(result)
    return 0


def cmd_approvals(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(list_approvals(db))
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        ok = decide_approval(db, args.approval_id, "approved")
        record_audit(
            db,
            actor="user",
            tool="approve",
            risk=RiskLevel.SENSITIVE,
            result="ok" if ok else "error",
        )
    _print_json({"ok": ok})
    return 0 if ok else 1


def cmd_deny(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        ok = decide_approval(db, args.approval_id, "denied")
        record_audit(
            db,
            actor="user",
            tool="deny",
            risk=RiskLevel.SENSITIVE,
            result="ok" if ok else "error",
        )
    _print_json({"ok": ok})
    return 0 if ok else 1


def cmd_audit(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        _print_json(list_audit(db, args.limit))
    return 0


def cmd_diagnostics_last_run(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        run_id = args.run_id or latest_run_id(db)
        if run_id is None:
            _print_json({"ok": False, "message": "No traced runs found."})
            return 1
        _print_json(summarize_run_trace(db, run_id))
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    config = _config(args)
    with open_state(config) as db:
        rows = db.execute(
            "SELECT * FROM rollback_entries WHERE run_id = ? ORDER BY created_at DESC",
            (args.run_id,),
        ).fetchall()
    _print_json([dict(row) for row in rows])
    return 0


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
