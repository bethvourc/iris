from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iris.cli import cmd_init, cmd_setup
from iris.config import IrisConfig
from iris.profile import has_user_profile, load_user_profile
from iris.state import open_state


def test_setup_command_saves_profile_from_flags(tmp_path: Path) -> None:
    args = SimpleNamespace(
        project_root=str(tmp_path),
        name="Beth",
        full_name="Beth Vour",
        pronouns="she/her",
        skip_profile=False,
    )

    assert cmd_setup(args) == 0

    config = _config(tmp_path)
    with open_state(config) as db:
        profile = load_user_profile(config, db)
        assert has_user_profile(db) is True

    assert profile.preferred_name == "Beth"
    assert profile.full_name == "Beth Vour"
    assert profile.pronouns == "she/her"


def test_setup_command_requires_values_when_non_interactive(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    args = SimpleNamespace(
        project_root=str(tmp_path),
        name=None,
        full_name=None,
        pronouns=None,
        skip_profile=False,
    )

    assert cmd_setup(args) == 1
    captured = capsys.readouterr()

    assert "No setup values provided" in captured.err


def test_setup_skip_profile_confirms_inferred_profile(tmp_path: Path) -> None:
    args = SimpleNamespace(
        project_root=str(tmp_path),
        name=None,
        full_name=None,
        pronouns=None,
        skip_profile=True,
    )

    assert cmd_setup(args) == 0

    config = _config(tmp_path)
    with open_state(config) as db:
        assert has_user_profile(db) is True


def test_init_does_not_prompt_when_non_interactive(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    args = SimpleNamespace(project_root=str(tmp_path))

    assert cmd_init(args) == 0
    captured = capsys.readouterr()

    assert "Run `./iris setup` to personalize Iris." in captured.out


def _config(tmp_path: Path) -> IrisConfig:
    return IrisConfig.from_env(tmp_path)
