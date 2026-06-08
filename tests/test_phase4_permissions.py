from __future__ import annotations

from iris.cli import _doctor_ok
from iris.control_backends import _apple_music_status
from iris.permissions import PermissionChecker
from iris.system import CommandResult


def test_music_automation_permission_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "iris.permissions.run_osascript",
        lambda _script, timeout=8: CommandResult(
            ["osascript"], 0, "Music is installed", ""
        ),
    )

    status = PermissionChecker().check_music_automation()

    assert status.name == "Music Automation"
    assert status.status == "available"
    assert "Apple Music" in status.capability


def test_music_automation_permission_reports_actionable_block(monkeypatch) -> None:
    monkeypatch.setattr(
        "iris.permissions.run_osascript",
        lambda _script, timeout=8: CommandResult(
            ["osascript"],
            0,
            "error:Not authorized to send Apple events to Music",
            "",
        ),
    )

    status = PermissionChecker().check_music_automation()

    assert status.status == "missing"
    assert "Automation settings" in status.detail
    assert "control Music" in status.settings_hint


def test_apple_music_backend_status_reports_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "iris.control_backends.shutil.which", lambda name: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        "iris.control_backends.run_osascript",
        lambda _script, timeout=5: CommandResult(["osascript"], 0, "installed", ""),
    )

    status = _apple_music_status().to_dict()

    assert status["backend_id"] == "apple_music"
    assert status["available"] is True
    assert status["detail"] == "installed"


def test_apple_music_backend_status_reports_automation_guidance(monkeypatch) -> None:
    monkeypatch.setattr(
        "iris.control_backends.shutil.which", lambda name: "/usr/bin/osascript"
    )
    monkeypatch.setattr(
        "iris.control_backends.run_osascript",
        lambda _script, timeout=5: CommandResult(
            ["osascript"],
            0,
            "error:Not authorized to send Apple events to Music",
            "",
        ),
    )

    status = _apple_music_status().to_dict()

    assert status["available"] is False
    assert "control Music" in status["detail"]
    assert "Automation" in status["metadata"]["settings"]


def test_doctor_ok_requires_music_automation_and_backend() -> None:
    base_report = {
        "permissions": [
            {"name": "Microphone", "status": "available"},
            {"name": "Screen Recording", "status": "available"},
            {"name": "Accessibility", "status": "available"},
            {"name": "Music Automation", "status": "available"},
        ],
        "control_backends": [
            {"backend_id": "accessibility", "available": True},
            {"backend_id": "apple_music", "available": True},
        ],
    }

    assert _doctor_ok(base_report) is True

    missing_music_permission = {
        **base_report,
        "permissions": [
            *base_report["permissions"][:-1],
            {"name": "Music Automation", "status": "missing"},
        ],
    }
    assert _doctor_ok(missing_music_permission) is False

    missing_music_backend = {
        **base_report,
        "control_backends": [{"backend_id": "accessibility", "available": True}],
    }
    assert _doctor_ok(missing_music_backend) is False
