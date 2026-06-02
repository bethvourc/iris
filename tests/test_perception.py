from __future__ import annotations

from iris.computer import ComputerBackend
from iris.mac_controller import ActionResult
from iris.perception import ScreenAwarenessService


def test_screen_awareness_backs_off_after_stable_frames() -> None:
    service = ScreenAwarenessService(
        perception=object(),  # type: ignore[arg-type]
        interval_seconds=1.5,
        idle_interval_seconds=6.0,
        stable_after_frames=3,
    )

    assert service._next_interval(0) == 1.5
    assert service._next_interval(2) == 1.5
    assert service._next_interval(3) == 6.0


def test_screen_awareness_has_minimum_sampling_interval() -> None:
    service = ScreenAwarenessService(
        perception=object(),  # type: ignore[arg-type]
        interval_seconds=0.1,
    )

    assert service.interval_seconds == 0.5


def test_computer_backend_observe_cache_can_be_invalidated() -> None:
    perception = _Perception()
    backend = ComputerBackend(
        controller=_Controller(),  # type: ignore[arg-type]
        perception=perception,  # type: ignore[arg-type]
        observe_cache_ttl_seconds=60.0,
    )

    first = backend.observe()
    second = backend.observe()
    backend.invalidate_observation_cache()
    third = backend.observe()

    assert first.active_app == "App 1"
    assert second.active_app == "App 1"
    assert third.active_app == "App 2"


class _Controller:
    def browser_current_page(self, _browser: str) -> ActionResult:
        return ActionResult("browser_current_page", False, "unavailable")


class _ScreenContext:
    def __init__(self, active_app: str, active_window: str) -> None:
        self.active_app = active_app
        self.active_window = active_window


class _Perception:
    def __init__(self) -> None:
        self.calls = 0

    def screen_context(self) -> _ScreenContext:
        self.calls += 1
        return _ScreenContext(f"App {self.calls}", f"Window {self.calls}")
