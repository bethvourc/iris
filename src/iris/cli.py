from __future__ import annotations

import argparse
import json
from pathlib import Path
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
    ingest_folder,
    list_pages,
    search_pages,
)
from iris.meetings import list_meetings, start_silent_meeting, stop_meeting
from iris.memory import add_memory, edit_memory, forget_memory, list_memories
from iris.plugins import list_plugins, plugin_health, set_plugin_enabled
from iris.profile import has_user_profile, load_user_profile, save_user_profile
from iris.providers import ProviderRegistry
from iris.safety import AutomationPaused, SafetyGate
from iris.sessions import get_session, list_sessions
from iris.state import ensure_state, open_state
from iris.tasks import get_task, list_tasks, request_cancel, resume_task
from iris.watchers import add_watch, list_watches, run_watch_once
from iris.workflows import list_workflows, run_workflow


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
    parser = argparse.ArgumentParser(prog="iris", description="Local Mac agent CLI")
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
        choices=["microphone", "screen", "accessibility", "automation"],
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
    preferred_name = _prompt_default("What should I call you?", inferred.preferred_name)
    full_name = _prompt_default(
        "Full name? Press Enter to use the detected value.", inferred.full_name
    )
    pronouns = _prompt_optional("Pronouns? Press Enter to skip.")
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


def _prompt_default(question: str, default: str) -> str:
    value = input(f"{question} [{default}] ").strip()
    return value or default


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
    required_permissions = {"Microphone", "Screen Recording", "Accessibility"}
    permission_ok = all(
        not isinstance(item, dict)
        or item.get("name") not in required_permissions
        or item.get("status") == "available"
        for item in permissions
    )
    backend_ok = any(
        isinstance(item, dict)
        and item.get("backend_id") == "accessibility"
        and item.get("available")
        for item in backends
    )
    return permission_ok and backend_ok


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


def cmd_start(args: argparse.Namespace) -> int:
    from iris.voice import VoiceSession

    config, safety_gate, _, _, _, _, router = _runtime(args)
    with open_state(config) as db:
        user_profile = load_user_profile(config, db)
    VoiceSession(
        config=config,
        router=router,
        safety_gate=safety_gate,
        user_profile=user_profile,
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
        record_audit(
            db,
            actor="user",
            tool="knowledge.search",
            risk=RiskLevel.LOW_RISK,
            result="ok",
            input_value={"query": args.query, "limit": args.limit},
            output_value={"count": len(results)},
        )
    if args.json:
        _print_json(results)
    else:
        print(format_search_results(results))
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
