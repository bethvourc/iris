from __future__ import annotations

import time
from typing import Callable, TypeVar


T = TypeVar("T")


def poll_until(
    call: Callable[[], T],
    ready: Callable[[T], bool],
    *,
    timeout_seconds: float,
    interval_seconds: float = 0.2,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    deadline = monotonic() + max(0.0, timeout_seconds)
    interval = max(0.01, interval_seconds)
    value = call()
    while not ready(value) and monotonic() < deadline:
        sleep(min(interval, max(0.0, deadline - monotonic())))
        value = call()
    return value
