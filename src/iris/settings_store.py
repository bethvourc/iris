"""Layered, file-backed settings beneath environment overrides.

Precedence: environment variable > settings.json > built-in default
(docs/desktop/api-contract.md §6). Only whitelisted keys are mutable through
the API, and a key pinned by its environment variable rejects writes with
`setting_env_managed` so the UI can render it as managed-by-environment.

This module must not import iris.config (config imports us to layer values).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

_LOG = logging.getLogger("iris.settings")

SETTINGS_FILE_NAME = "settings.json"

# Keys whose values must never serialize into API responses, logs, or
# diagnostics; the API reports presence only.
SECRET_FIELDS = (
    ("openai_api_key", "OPENAI_API_KEY"),
    ("groq_api_key", "GROQ_API_KEY"),
    ("google_application_credentials", "GOOGLE_APPLICATION_CREDENTIALS"),
    ("pushover_token", "PUSHOVER_TOKEN"),
    ("ntfy_token", "IRIS_NTFY_TOKEN"),
    ("resend_api_key", "RESEND_API_KEY"),
)


class SettingsError(Exception):
    code = "internal"


class UnknownSettingError(SettingsError):
    code = "invalid_request"


class InvalidSettingValueError(SettingsError):
    code = "invalid_request"


class EnvManagedSettingError(SettingsError):
    code = "setting_env_managed"


def _validate_wake_words(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise InvalidSettingValueError(
            "wake_words must be a non-empty list of strings"
        )
    normalized = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise InvalidSettingValueError(
                "wake_words must be a non-empty list of strings"
            )
        normalized.append(item.strip().lower())
    return normalized


def _validate_non_empty_str(key: str) -> Callable[[Any], str]:
    def validate(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise InvalidSettingValueError(f"{key} must be a non-empty string")
        return value.strip()

    return validate


def _validate_notify_provider(value: Any) -> str:
    allowed = {"pushover", "ntfy", "none"}
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        raise InvalidSettingValueError(
            f"notify_provider must be one of: {', '.join(sorted(allowed))}"
        )
    return value.strip().lower()


def _validate_bool(key: str) -> Callable[[Any], bool]:
    def validate(value: Any) -> bool:
        if not isinstance(value, bool):
            raise InvalidSettingValueError(f"{key} must be a boolean")
        return value

    return validate


@dataclass(frozen=True)
class SettingSpec:
    key: str
    env_var: str
    default: Any
    validate: Callable[[Any], Any]


SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="wake_words",
        env_var="IRIS_WAKE_WORDS",
        default=["iris", "hey iris"],
        validate=_validate_wake_words,
    ),
    SettingSpec(
        key="voice",
        env_var="IRIS_VOICE",
        default="marin",
        validate=_validate_non_empty_str("voice"),
    ),
    SettingSpec(
        key="realtime_model",
        env_var="IRIS_REALTIME_MODEL",
        default="gpt-realtime-2",
        validate=_validate_non_empty_str("realtime_model"),
    ),
    SettingSpec(
        key="notify_provider",
        env_var="IRIS_NOTIFY_PROVIDER",
        default="pushover",
        validate=_validate_notify_provider,
    ),
    SettingSpec(
        key="wake_word_enabled",
        env_var="IRIS_WAKE_WORD_ENABLED",
        default=False,
        validate=_validate_bool("wake_word_enabled"),
    ),
)

_SPECS_BY_KEY = {spec.key: spec for spec in SETTING_SPECS}


def settings_path(state_db_path: Path) -> Path:
    """The settings file lives next to the state DB."""
    return Path(state_db_path).parent / SETTINGS_FILE_NAME


def is_env_managed(spec: SettingSpec) -> bool:
    return bool(os.getenv(spec.env_var))


def load_stored_settings(state_db_path: Path) -> dict[str, Any]:
    """Read settings.json, filtered to known keys with valid values.

    A corrupt or invalid file degrades to defaults (logged) — it must never
    prevent the daemon from starting.
    """
    path = settings_path(state_db_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        _LOG.warning("settings.read_failed", extra={"detail": str(exc)})
        return {}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("settings.json must contain a JSON object")
    except ValueError as exc:
        _LOG.warning(
            "settings.corrupt_file_ignored",
            extra={"path": str(path), "detail": str(exc)},
        )
        return {}

    values: dict[str, Any] = {}
    for key, value in data.items():
        spec = _SPECS_BY_KEY.get(key)
        if spec is None:
            continue
        try:
            values[key] = spec.validate(value)
        except InvalidSettingValueError as exc:
            _LOG.warning(
                "settings.invalid_value_ignored",
                extra={"key": key, "detail": str(exc)},
            )
    return values


def update_stored_settings(
    state_db_path: Path, changes: dict[str, Any]
) -> dict[str, Any]:
    """Validate and persist a partial update. All-or-nothing.

    Raises UnknownSettingError / InvalidSettingValueError /
    EnvManagedSettingError before anything is written.
    """
    if not changes:
        raise InvalidSettingValueError("no settings provided")
    validated: dict[str, Any] = {}
    for key, value in changes.items():
        spec = _SPECS_BY_KEY.get(key)
        if spec is None:
            raise UnknownSettingError(f"unknown setting: {key!r}")
        if is_env_managed(spec):
            raise EnvManagedSettingError(
                f"{key} is managed by the {spec.env_var} environment variable"
            )
        validated[key] = spec.validate(value)

    merged = {**load_stored_settings(state_db_path), **validated}
    path = settings_path(state_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(
        json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(tmp_path, path)
    _LOG.info("settings.updated", extra={"keys": sorted(validated)})
    return merged


def settings_payload(config: Any) -> dict[str, Any]:
    """Build the GET /settings response from an IrisConfig.

    Secrets are reported as presence booleans only — values must never
    appear in any API payload.
    """
    file_values = load_stored_settings(config.state_db_path)
    settings: dict[str, Any] = {}
    for spec in SETTING_SPECS:
        env_managed = is_env_managed(spec)
        if env_managed:
            source = "env"
        elif spec.key in file_values:
            source = "settings"
        else:
            source = "default"
        value = getattr(config, spec.key)
        if isinstance(value, tuple):
            value = list(value)
        settings[spec.key] = {
            "value": value,
            "source": source,
            "mutable": not env_managed,
        }
    secrets = {
        name: {"is_set": bool(getattr(config, name, None))}
        for name, _env in SECRET_FIELDS
    }
    return {"settings": settings, "secrets": secrets}
