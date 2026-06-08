from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shlex


def default_project_root() -> Path:
    return (
        Path(os.environ.get("IRIS_PROJECT_ROOT") or Path.cwd()).expanduser().resolve()
    )


DEFAULT_PROJECT_ROOT = default_project_root()


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] in {"'", '"'}:
            try:
                value = shlex.split(value)[0]
            except ValueError:
                value = value.strip("'\"")
        values[key] = value
    return values


def load_env(project_root: Path | None = None) -> None:
    """Load .env values without overriding real environment variables."""

    root = project_root or default_project_root()
    env_values = _parse_env_file(root / ".env")
    for key, value in env_values.items():
        os.environ.setdefault(key, value)


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class IrisConfig:
    project_root: Path
    openai_api_key: str | None
    google_application_credentials: str | None
    agent_name: str
    voice: str
    toggle_hotkey: str
    kill_hotkey: str
    realtime_model: str
    chat_model: str
    vision_model: str
    computer_use_model: str
    computer_use_environment: str
    groq_api_key: str | None
    fast_intent_model: str
    stt_model: str
    realtime_transcription_model: str
    screenshot_interval_seconds: float
    gateway_token: str | None
    notify_provider: str
    ntfy_server: str
    ntfy_topic: str | None
    ntfy_token: str | None
    pushover_token: str | None
    pushover_user: str | None
    notify_timeout_seconds: float
    email_provider: str
    resend_api_key: str | None
    email_from: str | None
    email_to: str | None
    state_db_path: Path
    autonomy_level: str
    meeting_consent_required: bool
    meeting_retention_days: int
    listen_seconds: float
    wake_poll_seconds: float
    wake_words: tuple[str, ...]
    speak_responses: bool
    max_response_chars: int
    barge_in_enabled: bool = True
    barge_in_grace_ms: int = 250
    echo_suppression_ms: int = 800
    turn_buffer_enabled: bool = True
    turn_continuation_ms: int = 1200
    turn_max_wait_ms: int = 2500
    turn_min_words_for_immediate_response: int = 4
    live_vad_silence_ms: int = 900

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "IrisConfig":
        root = project_root or default_project_root()
        load_env(root)
        return cls(
            project_root=root,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            google_application_credentials=os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            or None,
            agent_name=os.getenv("IRIS_AGENT_NAME", "Iris"),
            voice=os.getenv("IRIS_VOICE", "marin"),
            toggle_hotkey=os.getenv("IRIS_TOGGLE_HOTKEY", "<ctrl>+<space>"),
            kill_hotkey=os.getenv("IRIS_KILL_HOTKEY", "<ctrl>+<alt>+k"),
            realtime_model=os.getenv("IRIS_REALTIME_MODEL", "gpt-realtime-2"),
            chat_model=os.getenv("IRIS_CHAT_MODEL", "gpt-5.5"),
            vision_model=os.getenv("IRIS_VISION_MODEL", "gpt-5.5"),
            computer_use_model=os.getenv(
                "IRIS_COMPUTER_USE_MODEL", "computer-use-preview"
            ),
            computer_use_environment=os.getenv(
                "IRIS_COMPUTER_USE_ENVIRONMENT", "browser"
            ),
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
            fast_intent_model=os.getenv("IRIS_FAST_INTENT_MODEL", "compound-mini"),
            stt_model=os.getenv("IRIS_STT_MODEL", "whisper-large-v3-turbo"),
            realtime_transcription_model=os.getenv(
                "IRIS_REALTIME_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"
            ),
            screenshot_interval_seconds=_float_env(
                "IRIS_SCREENSHOT_INTERVAL_SECONDS", 0.5
            ),
            gateway_token=os.getenv("IRIS_GATEWAY_TOKEN") or None,
            notify_provider=os.getenv("IRIS_NOTIFY_PROVIDER", "pushover"),
            ntfy_server=os.getenv("IRIS_NTFY_SERVER", "https://ntfy.sh"),
            ntfy_topic=os.getenv("IRIS_NTFY_TOPIC") or None,
            ntfy_token=os.getenv("IRIS_NTFY_TOKEN") or None,
            pushover_token=os.getenv("PUSHOVER_TOKEN")
            or os.getenv("IRIS_PUSHOVER_TOKEN")
            or None,
            pushover_user=os.getenv("PUSHOVER_USER")
            or os.getenv("IRIS_PUSHOVER_USER")
            or None,
            notify_timeout_seconds=_float_env("IRIS_NOTIFY_TIMEOUT_SECONDS", 30.0),
            email_provider=os.getenv("IRIS_EMAIL_PROVIDER", "resend"),
            resend_api_key=os.getenv("RESEND_API_KEY") or None,
            email_from=os.getenv("IRIS_EMAIL_FROM") or None,
            email_to=os.getenv("IRIS_EMAIL_TO") or None,
            state_db_path=Path(
                os.getenv("IRIS_STATE_DB", str(root / "build" / "iris.sqlite3"))
            ),
            autonomy_level=os.getenv("IRIS_AUTONOMY_LEVEL", "L1"),
            meeting_consent_required=_bool_env("IRIS_MEETING_CONSENT_REQUIRED", True),
            meeting_retention_days=_int_env("IRIS_MEETING_RETENTION_DAYS", 30),
            listen_seconds=_float_env("IRIS_LISTEN_SECONDS", 2.0),
            wake_poll_seconds=_float_env("IRIS_WAKE_POLL_SECONDS", 1.2),
            wake_words=_csv_env("IRIS_WAKE_WORDS", ("iris", "hey iris")),
            speak_responses=_bool_env("IRIS_SPEAK_RESPONSES", False),
            max_response_chars=_int_env("IRIS_MAX_RESPONSE_CHARS", 220),
            barge_in_enabled=_bool_env("IRIS_BARGE_IN_ENABLED", True),
            barge_in_grace_ms=_int_env("IRIS_BARGE_IN_GRACE_MS", 250),
            echo_suppression_ms=_int_env("IRIS_ECHO_SUPPRESSION_MS", 800),
            turn_buffer_enabled=_bool_env("IRIS_TURN_BUFFER_ENABLED", True),
            turn_continuation_ms=_int_env("IRIS_TURN_CONTINUATION_MS", 1200),
            turn_max_wait_ms=_int_env("IRIS_TURN_MAX_WAIT_MS", 2500),
            turn_min_words_for_immediate_response=_int_env(
                "IRIS_TURN_MIN_WORDS_FOR_IMMEDIATE_RESPONSE", 4
            ),
            live_vad_silence_ms=_int_env("IRIS_LIVE_VAD_SILENCE_MS", 900),
        )

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_google_vision(self) -> bool:
        return bool(self.google_application_credentials)

    @property
    def has_phone_notifications(self) -> bool:
        if self.notify_provider == "ntfy":
            return bool(self.ntfy_topic)
        if self.notify_provider == "pushover":
            return bool(self.pushover_token and self.pushover_user)
        return False

    @property
    def has_email(self) -> bool:
        return bool(self.resend_api_key and self.email_from)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if not value:
        return default
    items = tuple(item.strip().lower() for item in value.split(",") if item.strip())
    return items or default
