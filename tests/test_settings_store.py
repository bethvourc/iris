from __future__ import annotations

import json
from pathlib import Path

import pytest

from iris.config import IrisConfig
from iris.settings_store import (
    EnvManagedSettingError,
    InvalidSettingValueError,
    UnknownSettingError,
    load_stored_settings,
    settings_path,
    update_stored_settings,
)

# Env vars that would shadow the layers under test.
LAYERED_ENV_VARS = (
    "IRIS_WAKE_WORDS",
    "IRIS_VOICE",
    "IRIS_REALTIME_MODEL",
    "IRIS_NOTIFY_PROVIDER",
    "IRIS_WAKE_WORD_ENABLED",
)


@pytest.fixture()
def state_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "state" / "iris.sqlite3"
    monkeypatch.setenv("IRIS_STATE_DB", str(db_path))
    for name in LAYERED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return db_path


def _config(tmp_path: Path) -> IrisConfig:
    return IrisConfig.from_env(tmp_path)


def test_defaults_apply_when_no_file_and_no_env(
    state_db: Path, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    assert config.voice == "marin"
    assert config.wake_words == ("iris", "hey iris")
    assert config.wake_word_enabled is False


def test_settings_file_overrides_defaults(state_db: Path, tmp_path: Path) -> None:
    update_stored_settings(
        state_db,
        {"voice": "cedar", "wake_words": ["computer"], "wake_word_enabled": True},
    )
    config = _config(tmp_path)
    assert config.voice == "cedar"
    assert config.wake_words == ("computer",)
    assert config.wake_word_enabled is True


def test_env_overrides_settings_file(
    state_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    update_stored_settings(state_db, {"voice": "cedar"})
    monkeypatch.setenv("IRIS_VOICE", "alloy")
    config = _config(tmp_path)
    assert config.voice == "alloy"


def test_update_rejects_unknown_key_without_writing(
    state_db: Path,
) -> None:
    with pytest.raises(UnknownSettingError):
        update_stored_settings(state_db, {"voice": "cedar", "nope": 1})
    assert not settings_path(state_db).exists()  # all-or-nothing


@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("wake_words", []),
        ("wake_words", "iris"),
        ("wake_words", [""]),
        ("wake_words", [1, 2]),
        ("voice", ""),
        ("voice", 7),
        ("realtime_model", None),
        ("notify_provider", "carrier-pigeon"),
        ("wake_word_enabled", "yes"),
    ],
)
def test_update_rejects_invalid_values(
    state_db: Path, key: str, bad_value: object
) -> None:
    with pytest.raises(InvalidSettingValueError):
        update_stored_settings(state_db, {key: bad_value})


def test_update_rejects_env_managed_key(
    state_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IRIS_VOICE", "alloy")
    with pytest.raises(EnvManagedSettingError):
        update_stored_settings(state_db, {"voice": "cedar"})


def test_update_rejects_empty_changes(state_db: Path) -> None:
    with pytest.raises(InvalidSettingValueError):
        update_stored_settings(state_db, {})


def test_wake_words_are_normalized_lowercase(state_db: Path) -> None:
    stored = update_stored_settings(state_db, {"wake_words": ["  Hey IRIS "]})
    assert stored["wake_words"] == ["hey iris"]


def test_updates_merge_with_existing_values(state_db: Path) -> None:
    update_stored_settings(state_db, {"voice": "cedar"})
    update_stored_settings(state_db, {"realtime_model": "gpt-realtime-3"})
    stored = load_stored_settings(state_db)
    assert stored == {"voice": "cedar", "realtime_model": "gpt-realtime-3"}


def test_write_is_atomic_and_leaves_no_temp_files(state_db: Path) -> None:
    update_stored_settings(state_db, {"voice": "cedar"})
    directory = settings_path(state_db).parent
    assert [p.name for p in directory.iterdir()] == ["settings.json"]
    # file is valid, pretty-printed JSON
    parsed = json.loads(settings_path(state_db).read_text())
    assert parsed == {"voice": "cedar"}


def test_corrupt_file_degrades_to_defaults(state_db: Path, tmp_path: Path) -> None:
    path = settings_path(state_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert load_stored_settings(state_db) == {}
    config = _config(tmp_path)
    assert config.voice == "marin"  # daemon still starts on defaults


def test_invalid_stored_values_are_ignored_not_fatal(state_db: Path) -> None:
    path = settings_path(state_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"voice": 42, "realtime_model": "good-model", "junk": True}),
        encoding="utf-8",
    )
    assert load_stored_settings(state_db) == {"realtime_model": "good-model"}
