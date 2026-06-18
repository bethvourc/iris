from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Iterator

import pytest

from iris.config import IrisConfig
from iris.gateway import GatewayService
from iris.secrets_store import (
    EnvManagedSecretError,
    InvalidSecretValueError,
    UnknownSecretError,
    apply_stored_secrets,
    load_stored_secrets,
    reset_applied_markers_for_tests,
    secrets_path,
    update_stored_secrets,
)

SENTINEL = "sk-secret-sentinel-3.2"
SECRET_ENV_VARS = (
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "PUSHOVER_TOKEN",
    "IRIS_NTFY_TOKEN",
    "RESEND_API_KEY",
)


@pytest.fixture()
def state_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    db_path = tmp_path / "state" / "iris.sqlite3"
    monkeypatch.setenv("IRIS_STATE_DB", str(db_path))
    for name in SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    reset_applied_markers_for_tests()
    yield db_path
    # apply_stored_secrets writes os.environ directly; scrub our vars so
    # later tests never observe sentinel values.
    for name in SECRET_ENV_VARS:
        os.environ.pop(name, None)
    reset_applied_markers_for_tests()


def test_update_persists_with_owner_only_permissions(state_db: Path) -> None:
    update_stored_secrets(state_db, {"openai_api_key": SENTINEL})

    path = secrets_path(state_db)
    assert json.loads(path.read_text()) == {"openai_api_key": SENTINEL}
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["secrets.json"]


def test_stored_secret_reaches_config_via_environment(
    state_db: Path, tmp_path: Path
) -> None:
    update_stored_secrets(state_db, {"openai_api_key": SENTINEL})
    config = IrisConfig.from_env(tmp_path)
    assert config.openai_api_key == SENTINEL
    assert config.has_openai is True


def test_real_environment_wins_over_stored_secret(
    state_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    update_stored_secrets(state_db, {"groq_api_key": "stored-value"})
    reset_applied_markers_for_tests()
    os.environ.pop("GROQ_API_KEY", None)
    monkeypatch.setenv("GROQ_API_KEY", "env-value")

    config = IrisConfig.from_env(tmp_path)
    assert config.groq_api_key == "env-value"


def test_env_managed_secret_rejects_update(
    state_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-owned")
    with pytest.raises(EnvManagedSecretError):
        update_stored_secrets(state_db, {"openai_api_key": SENTINEL})


def test_store_applied_secret_remains_updatable(state_db: Path) -> None:
    # After our own apply() the env var is set, but the store still owns it.
    update_stored_secrets(state_db, {"openai_api_key": "first"})
    update_stored_secrets(state_db, {"openai_api_key": "second"})
    assert load_stored_secrets(state_db) == {"openai_api_key": "second"}
    assert os.environ["OPENAI_API_KEY"] == "second"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("nope", "x"),
        ("openai_api_key", ""),
        ("openai_api_key", "   "),
        ("openai_api_key", 42),
        ("openai_api_key", "bad\nnewline"),
        ("openai_api_key", "x" * 5000),
    ],
)
def test_invalid_updates_rejected_without_writing(
    state_db: Path, key: str, value: object
) -> None:
    with pytest.raises((UnknownSecretError, InvalidSecretValueError)):
        update_stored_secrets(state_db, {key: value})
    assert not secrets_path(state_db).exists()


def test_corrupt_file_degrades_to_empty(state_db: Path) -> None:
    path = secrets_path(state_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    assert load_stored_secrets(state_db) == {}
    apply_stored_secrets(state_db)  # must not raise


# ------------------------------------------------------------- gateway


@pytest.fixture()
def service(
    state_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> GatewayService:
    monkeypatch.setenv("IRIS_GATEWAY_TOKEN", "secrets-test-token")
    config = IrisConfig.from_env(tmp_path)
    return GatewayService(config=config, router_factory=lambda: None)


def test_put_secrets_requires_auth(service: GatewayService) -> None:
    assert service.authorize("/secrets", None) is False
    assert service.authorize("/secrets", "Bearer secrets-test-token") is True


def test_put_secrets_flips_is_set_and_never_echoes_value(
    service: GatewayService,
) -> None:
    _, before = service.handle_get("/settings", {})
    assert before["secrets"]["openai_api_key"] == {"is_set": False}

    status, payload = service.handle_put("/secrets", {"openai_api_key": SENTINEL})
    assert status == 200
    assert payload["secrets"]["openai_api_key"] == {"is_set": True}
    assert SENTINEL not in json.dumps(payload)

    # the reloaded gateway config can now start voice sessions
    assert service.config.openai_api_key == SENTINEL


def test_put_secrets_unknown_key_envelope(service: GatewayService) -> None:
    status, payload = service.handle_put("/secrets", {"theme": "dark"})
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert SENTINEL not in json.dumps(payload)


def test_put_secrets_env_managed_returns_409(
    service: GatewayService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-owned")
    status, payload = service.handle_put("/secrets", {"openai_api_key": SENTINEL})
    assert status == 409
    assert payload["error"]["code"] == "secret_env_managed"
    assert "OPENAI_API_KEY" in payload["error"]["message"]


def test_put_secrets_is_audit_logged_by_key_only(service: GatewayService) -> None:
    from iris.audit import list_audit
    from iris.state import open_state

    service.handle_put("/secrets", {"openai_api_key": SENTINEL})
    with open_state(service.config) as db:
        entries = list_audit(db, limit=5)
    entry = next(e for e in entries if e["tool"] == "secrets.update")
    serialized = json.dumps(entry)
    assert "openai_api_key" in serialized
    assert SENTINEL not in serialized
