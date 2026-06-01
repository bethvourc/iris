from __future__ import annotations

import pytest

from iris.polling import poll_until
from iris.safety import AutomationPaused, CancellationToken


def test_poll_until_returns_when_ready() -> None:
    values = iter([{"ready": False}, {"ready": False}, {"ready": True}])
    sleeps: list[float] = []
    now = 0.0

    def monotonic() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    result = poll_until(
        lambda: next(values),
        lambda value: bool(value["ready"]),
        timeout_seconds=5.0,
        interval_seconds=0.25,
        monotonic=monotonic,
        sleep=sleep,
    )

    assert result == {"ready": True}
    assert sleeps == [0.25, 0.25]


def test_poll_until_returns_last_value_on_timeout() -> None:
    calls = 0
    now = 0.0

    def monotonic() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    def call() -> int:
        nonlocal calls
        calls += 1
        return calls

    result = poll_until(
        call,
        lambda value: value >= 10,
        timeout_seconds=0.5,
        interval_seconds=0.25,
        monotonic=monotonic,
        sleep=sleep,
    )

    assert result == 3


def test_poll_until_stops_when_cancelled_during_wait() -> None:
    token = CancellationToken()
    now = 0.0

    def monotonic() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds
        token.cancel("stop polling")

    with pytest.raises(AutomationPaused, match="stop polling"):
        poll_until(
            lambda: {"ready": False},
            lambda value: bool(value["ready"]),
            timeout_seconds=5.0,
            interval_seconds=0.25,
            monotonic=monotonic,
            sleep=sleep,
            cancellation_token=token,
        )
