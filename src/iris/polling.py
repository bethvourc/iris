from __future__ import annotations

import time
from typing import Callable, TypeVar

from iris.safety import CancellationToken


T = TypeVar("T")


def poll_until(
    call: Callable[[], T],
    ready: Callable[[T], bool],
    *,
    timeout_seconds: float,
    interval_seconds: float = 0.2,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    cancellation_token: CancellationToken | None = None,
) -> T:
    deadline = monotonic() + max(0.0, timeout_seconds)
    interval = max(0.01, interval_seconds)
    if cancellation_token is not None:
        cancellation_token.throw_if_cancelled()
    value = call()
    while not ready(value) and monotonic() < deadline:
        if cancellation_token is not None:
            cancellation_token.throw_if_cancelled()
        sleep(min(interval, max(0.0, deadline - monotonic())))
        if cancellation_token is not None:
            cancellation_token.throw_if_cancelled()
        value = call()
    if cancellation_token is not None:
        cancellation_token.throw_if_cancelled()
    return value
