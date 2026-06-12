"""Daemon-owned secret storage beneath environment overrides.

Secrets the desktop onboarding (or any client) sets via `PUT /secrets` are
persisted to `secrets.json` next to the state DB with 0600 permissions and
applied to the process environment at config load, so every existing
`os.getenv` consumer keeps working. Precedence: real environment (including
`.env`) > stored secret. Values never serialize into API responses or logs
(docs/desktop/api-contract.md §6.1).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("iris.secrets")

SECRETS_FILE_NAME = "secrets.json"
_MAX_SECRET_LENGTH = 4096

# Env var names this module injected (as opposed to values the user set via
# the real environment or .env, which must stay authoritative).
_applied_from_store: set[str] = set()


class SecretsError(Exception):
    code = "internal"


class UnknownSecretError(SecretsError):
    code = "invalid_request"


class InvalidSecretValueError(SecretsError):
    code = "invalid_request"


class EnvManagedSecretError(SecretsError):
    code = "secret_env_managed"


@dataclass(frozen=True)
class SecretSpec:
    key: str
    env_var: str


SECRET_SPECS: tuple[SecretSpec, ...] = (
    SecretSpec("openai_api_key", "OPENAI_API_KEY"),
    SecretSpec("groq_api_key", "GROQ_API_KEY"),
    SecretSpec("pushover_token", "PUSHOVER_TOKEN"),
    SecretSpec("ntfy_token", "IRIS_NTFY_TOKEN"),
    SecretSpec("resend_api_key", "RESEND_API_KEY"),
)

_SPECS_BY_KEY = {spec.key: spec for spec in SECRET_SPECS}


def secrets_path(state_db_path: Path) -> Path:
    return Path(state_db_path).parent / SECRETS_FILE_NAME


def is_env_managed_secret(spec: SecretSpec) -> bool:
    """True when the real environment (or .env) owns this secret."""
    return bool(os.getenv(spec.env_var)) and spec.env_var not in _applied_from_store


def load_stored_secrets(state_db_path: Path) -> dict[str, str]:
    """Read secrets.json, filtered to known keys. Corruption degrades to {}."""
    path = secrets_path(state_db_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        _LOG.warning("secrets.read_failed", extra={"detail": str(exc)})
        return {}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("secrets.json must contain a JSON object")
    except ValueError as exc:
        _LOG.warning("secrets.corrupt_file_ignored", extra={"detail": str(exc)})
        return {}
    return {
        key: value
        for key, value in data.items()
        if key in _SPECS_BY_KEY and isinstance(value, str) and value
    }


def apply_stored_secrets(state_db_path: Path) -> None:
    """Inject stored secrets into the environment beneath real env values.

    Called from IrisConfig.from_env so every getenv-based consumer (config,
    SDK defaults) sees them without further plumbing.
    """
    for key, value in load_stored_secrets(state_db_path).items():
        env_var = _SPECS_BY_KEY[key].env_var
        if not os.environ.get(env_var) or env_var in _applied_from_store:
            os.environ[env_var] = value
            _applied_from_store.add(env_var)


def _validate(key: str, value: Any) -> str:
    spec = _SPECS_BY_KEY.get(key)
    if spec is None:
        raise UnknownSecretError(f"unknown secret: {key!r}")
    if not isinstance(value, str) or not value.strip():
        raise InvalidSecretValueError(f"{key} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > _MAX_SECRET_LENGTH:
        raise InvalidSecretValueError(f"{key} is too long")
    if any(ch in cleaned for ch in "\r\n\x00"):
        raise InvalidSecretValueError(f"{key} contains invalid characters")
    if is_env_managed_secret(spec):
        raise EnvManagedSecretError(
            f"{key} is managed by the {spec.env_var} environment variable"
        )
    return cleaned


def update_stored_secrets(state_db_path: Path, changes: dict[str, Any]) -> None:
    """Validate and persist secrets, then apply to the live environment.

    All-or-nothing; the file is written atomically with 0600 permissions.
    Values are deliberately absent from every log line and exception.
    """
    if not changes:
        raise InvalidSecretValueError("no secrets provided")
    validated = {key: _validate(key, value) for key, value in changes.items()}

    merged = {**load_stored_secrets(state_db_path), **validated}
    path = secrets_path(state_db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(
        json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)

    for key, value in validated.items():
        env_var = _SPECS_BY_KEY[key].env_var
        os.environ[env_var] = value
        _applied_from_store.add(env_var)
    _LOG.info("secrets.updated", extra={"keys": sorted(validated)})


def reset_applied_markers_for_tests() -> None:
    _applied_from_store.clear()
