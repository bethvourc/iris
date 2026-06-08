from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile

from iris.system import run_command, run_osascript


@dataclass(frozen=True)
class PermissionStatus:
    name: str
    status: str
    capability: str
    detail: str
    settings_hint: str


class PermissionChecker:
    def check_all(self) -> list[PermissionStatus]:
        return [
            self.check_microphone(),
            self.check_screen_recording(),
            self.check_accessibility(),
            self.check_automation(),
            self.check_music_automation(),
        ]

    def check_microphone(self) -> PermissionStatus:
        try:
            import sounddevice  # type: ignore

            devices = sounddevice.query_devices()
            has_input = any(
                device.get("max_input_channels", 0) > 0 for device in devices
            )
            if has_input:
                status = "available"
                detail = "Input audio devices are visible to Python."
            else:
                status = "missing"
                detail = "No input audio device was found."
        except Exception as exc:  # pragma: no cover - environment dependent
            status = "unknown"
            detail = f"Could not inspect microphone devices: {exc}"
        return PermissionStatus(
            name="Microphone",
            status=status,
            capability="voice input",
            detail=detail,
            settings_hint="System Settings > Privacy & Security > Microphone",
        )

    def check_screen_recording(self) -> PermissionStatus:
        if not shutil.which("screencapture"):
            return PermissionStatus(
                name="Screen Recording",
                status="missing",
                capability="screen capture",
                detail="The screencapture binary is not available.",
                settings_hint="System Settings > Privacy & Security > Screen Recording",
            )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as handle:
            path = Path(handle.name)
            result = run_command(
                ["screencapture", "-x", "-t", "png", str(path)], timeout=8
            )
            size = path.stat().st_size if path.exists() else 0
        if result.ok and size > 0:
            status = "available"
            detail = "A screenshot was captured successfully."
        else:
            status = "missing"
            detail = result.stderr or "Screenshot capture returned no image data."
        return PermissionStatus(
            name="Screen Recording",
            status=status,
            capability="screen capture",
            detail=detail,
            settings_hint="System Settings > Privacy & Security > Screen Recording",
        )

    def check_accessibility(self) -> PermissionStatus:
        script = 'tell application "System Events" to get UI elements enabled'
        result = run_osascript(script, timeout=8)
        if result.ok and result.stdout.lower() in {"true", "false"}:
            status = "available" if result.stdout.lower() == "true" else "missing"
            detail = (
                "System Events accessibility API is enabled."
                if status == "available"
                else "System Events reports UI elements are not enabled for this process."
            )
        else:
            status = "missing"
            detail = (
                result.stderr or "System Events did not allow accessibility access."
            )
        return PermissionStatus(
            name="Accessibility",
            status=status,
            capability="click, type, hotkey, and UI inspection",
            detail=detail,
            settings_hint="System Settings > Privacy & Security > Accessibility",
        )

    def check_automation(self) -> PermissionStatus:
        script = (
            'tell application "System Events"\n'
            "  set frontApp to name of first application process whose frontmost is true\n"
            "end tell\n"
            "return frontApp"
        )
        result = run_osascript(script, timeout=8)
        if result.ok and result.stdout:
            status = "available"
            detail = f"System Events returned frontmost app: {result.stdout}"
        else:
            status = "missing"
            detail = result.stderr or "System Events automation was not available."
        return PermissionStatus(
            name="Automation",
            status=status,
            capability="AppleScript app and Finder operations",
            detail=detail,
            settings_hint="System Settings > Privacy & Security > Automation",
        )

    def check_music_automation(self) -> PermissionStatus:
        result = run_osascript(
            """
try
  tell application "Music"
    if it is running then
      return "Music is running"
    end if
    return "Music is installed"
  end tell
on error errMsg
  return "error:" & errMsg
end try
""",
            timeout=8,
        )
        text = result.stdout.strip()
        if result.ok and text and not text.lower().startswith("error:"):
            status = "available"
            detail = text
        else:
            status = "missing"
            detail = _music_automation_detail(text or result.stderr)
        return PermissionStatus(
            name="Music Automation",
            status=status,
            capability="Apple Music search, playback, and metadata control",
            detail=detail,
            settings_hint="System Settings > Privacy & Security > Automation > allow this terminal app to control Music",
        )


def _music_automation_detail(detail: str) -> str:
    lowered = detail.lower()
    if (
        "not authorized" in lowered
        or "not allowed" in lowered
        or "automation" in lowered
    ):
        return (
            "macOS blocked Iris from controlling Music. Allow the terminal app "
            "running Iris to control Music in Automation settings."
        )
    return detail or "Music Automation was not available."
