from __future__ import annotations

from iris.cli import main


def test_invalid_top_level_command_is_human_readable(capsys) -> None:
    assert main(["live"]) == 2

    captured = capsys.readouterr()

    assert "Iris couldn't understand that command." in captured.err
    assert "Problem: `live` is not an Iris command." in captured.err
    assert "iris start --live" in captured.err
    assert "Common commands:" in captured.err
    assert "Developer detail:" in captured.err
    assert "choose from" not in captured.err
    assert "usage:" not in captured.err


def test_invalid_nested_command_suggests_valid_subcommand(capsys) -> None:
    assert main(["browser", "tabz"]) == 2

    captured = capsys.readouterr()

    assert "Problem: `tabz` is not a valid `browser` command." in captured.err
    assert "iris browser tabs" in captured.err
    assert "Run `iris browser --help` to see valid usage." in captured.err
    assert "choose from" not in captured.err


def test_missing_nested_command_lists_next_steps(capsys) -> None:
    assert main(["browser"]) == 2

    captured = capsys.readouterr()

    assert "Problem: `iris browser` needs another command." in captured.err
    assert "iris browser status" in captured.err
    assert "iris browser start-cdp" in captured.err
    assert "Developer detail:" in captured.err
