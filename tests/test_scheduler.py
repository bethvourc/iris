from __future__ import annotations

from iris.agent import _parse_json_list
from iris.scheduler import SchedulerService


def test_parse_json_list_handles_plain_and_fenced() -> None:
    assert _parse_json_list('["a", "b"]') == ["a", "b"]
    assert _parse_json_list('```json\n["x"]\n```') == ["x"]
    assert _parse_json_list("nonsense") == []
    assert _parse_json_list("") == []
    assert _parse_json_list('{"not": "a list"}') == []


class _FakeNotifier:
    def __init__(self) -> None:
        self.pushes: list[tuple[str, str | None]] = []

    def send_phone_push(self, message: str, *, title: str | None = None, **_kw):
        self.pushes.append((message, title))


def _bare_scheduler() -> SchedulerService:
    scheduler = object.__new__(SchedulerService)
    scheduler.notifier = _FakeNotifier()
    scheduler.announce = None
    return scheduler


def test_announce_falls_back_to_push_when_not_spoken() -> None:
    scheduler = _bare_scheduler()
    scheduler.announce = lambda _text: False
    scheduler._announce("Downloads", "a new file appeared")
    assert scheduler.notifier.pushes == [("a new file appeared", "Iris: Downloads")]


def test_announce_skips_push_when_spoken() -> None:
    scheduler = _bare_scheduler()
    spoken: list[str] = []

    def speak(text: str) -> bool:
        spoken.append(text)
        return True

    scheduler.announce = speak
    scheduler._announce("Build", "tests went green")
    assert spoken == ["Heads up: Build changed. tests went green"]
    assert scheduler.notifier.pushes == []
