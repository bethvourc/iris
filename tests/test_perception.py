from __future__ import annotations

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
