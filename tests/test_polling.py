from __future__ import annotations

from iris.polling import poll_until


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
