from __future__ import annotations

from pathlib import Path

from iris.config import default_project_root


def test_default_project_root_uses_iris_project_root_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("IRIS_PROJECT_ROOT", str(tmp_path))

    assert default_project_root() == tmp_path.resolve()


def test_default_project_root_falls_back_to_cwd(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("IRIS_PROJECT_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert default_project_root() == tmp_path.resolve()
