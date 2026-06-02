from __future__ import annotations

from iris.copilot import CoPilotService, looks_interesting


def test_looks_interesting() -> None:
    assert looks_interesting("Traceback (most recent call last): ValueError")
    assert looks_interesting("HTTP 500 Internal Server Error")
    assert not looks_interesting("Here is your nicely rendered document.")
    assert not looks_interesting("")


def _service(**kw):
    state = {
        "sig": "app|win|1",
        "text": "ERROR: build failed",
        "advice": "Try rerunning with --verbose.",
        "spoken": [],
    }
    service = CoPilotService(
        signature=lambda: state["sig"],
        read_text=lambda: state["text"],
        advise=lambda _t: state["advice"],
        announce=lambda text: (state["spoken"].append(text) or True),
        min_interval_seconds=kw.get("min_interval", 0.0),
    )
    return service, state


def test_tick_announces_on_interesting_change() -> None:
    service, state = _service()
    out = service.tick(now=100.0)
    assert out == "Try rerunning with --verbose."
    assert state["spoken"] == ["Try rerunning with --verbose."]


def test_tick_skips_when_signature_unchanged() -> None:
    service, state = _service()
    assert service.tick(now=100.0) is not None
    # same signature -> no second interjection
    assert service.tick(now=200.0) is None


def test_tick_skips_when_not_interesting() -> None:
    service, state = _service()
    state["text"] = "All good, nothing to see."
    assert service.tick(now=100.0) is None
    assert state["spoken"] == []


def test_tick_respects_none_and_dedupe() -> None:
    service, state = _service()
    state["advice"] = "NONE"
    assert service.tick(now=100.0) is None

    state["advice"] = "Fix the import."
    state["sig"] = "app|win|2"
    assert service.tick(now=101.0) == "Fix the import."
    # new change, same advice -> deduped
    state["sig"] = "app|win|3"
    assert service.tick(now=102.0) is None


def test_tick_throttles() -> None:
    service, _state = _service(min_interval=30.0)
    assert service.tick(now=100.0) is not None
    # signature changes again but we're still inside the throttle window
    service._last_signature = "different"
    assert service.tick(now=110.0) is None
