"""Structured events emitted by live voice sessions.

The wire catalog (event types and payloads) is specified in
docs/desktop/api-contract.md §4; gateway SSE subscribers and the CLI are both
sinks for the same stream. `ConsoleSink` reproduces the exact terminal lines
`iris start --live` printed before this module existed, so CLI behavior is
unchanged by the refactor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class VoiceEventType:
    STATE = "state"
    WAKE_DETECTED = "wake_detected"
    USER_TRANSCRIPT = "user_transcript"
    ASSISTANT_DELTA = "assistant_delta"
    ASSISTANT_DONE = "assistant_done"
    INTERRUPTED = "interrupted"
    TOOL_CALL = "tool_call"
    AGENT_RUN = "agent_run"
    MEETING = "meeting"
    SESSION_ENDED = "session_ended"
    ERROR = "error"
    # Diagnostic console/info lines; clients that render state machines
    # ignore these (unknown/diagnostic types are always ignorable).
    LOG = "log"


class VoiceState:
    IDLE = "idle"
    CONNECTING = "connecting"
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    MEETING = "meeting"
    ERROR = "error"


@dataclass(frozen=True)
class VoiceEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "ts": self.ts, "data": self.data}


def error_event(code: str, message: str, *, terminal: bool = False) -> VoiceEvent:
    return VoiceEvent(
        VoiceEventType.ERROR,
        {"code": code, "message": message, "terminal": terminal},
    )


def log_event(text: str) -> VoiceEvent:
    return VoiceEvent(VoiceEventType.LOG, {"text": text})


class EventSink(Protocol):
    def emit(self, event: VoiceEvent) -> None: ...


class ConsoleSink:
    """Renders voice events as the historical `iris start --live` output.

    Every branch corresponds to a print() that used to live in voice.py; the
    strings must stay byte-identical to preserve CLI behavior.
    """

    def emit(self, event: VoiceEvent) -> None:
        data = event.data
        event_type = event.type
        if event_type == VoiceEventType.LOG:
            print(data.get("text", ""))
            return
        if event_type == VoiceEventType.STATE:
            self._emit_state(data)
            return
        if event_type == VoiceEventType.INTERRUPTED:
            print("iris> interrupted, listening...")
            return
        if event_type == VoiceEventType.USER_TRANSCRIPT:
            print(f"you> {data.get('text', '')}")
            return
        if event_type == VoiceEventType.ASSISTANT_DONE:
            print(f"iris> {data.get('text', '')}")
            return
        if event_type == VoiceEventType.TOOL_CALL:
            status = data.get("status")
            if status == "started":
                print(f"iris> · {data.get('name', '')}")
            elif status == "failed" and data.get("detail"):
                print(f"iris error> {data.get('detail')}")
            return
        if event_type == VoiceEventType.AGENT_RUN:
            if data.get("status") == "failed":
                print(f"iris error> {data.get('detail') or data.get('summary', '')}")
            return
        if event_type == VoiceEventType.MEETING:
            if data.get("active"):
                print("iris> meeting mode on (silent; say 'stop the meeting' when done)")
            return
        if event_type == VoiceEventType.ERROR:
            self._emit_error(data)
            return
        # state-stream-only events (assistant_delta, wake_detected,
        # session_ended) have no console representation.

    @staticmethod
    def _emit_state(data: dict[str, Any]) -> None:
        state = data.get("state")
        via = data.get("via")
        if state == VoiceState.CONNECTING:
            print(f"iris> connecting realtime model {data.get('model', '')}...")
        elif state == VoiceState.LISTENING and via == "session_ready":
            print("iris> session ready")
        elif state == VoiceState.USER_SPEAKING and via == "speech":
            print("iris> heard speech...")
        elif state == VoiceState.TRANSCRIBING:
            print("iris> speech stopped, waiting for transcript...")
        elif state == VoiceState.THINKING and via == "agent":
            print("iris> working...")

    @staticmethod
    def _emit_error(data: dict[str, Any]) -> None:
        code = data.get("code")
        message = data.get("message", "")
        if code == "audio_warning":
            print(f"audio warning: {message}")
        elif code == "meeting_email_failed":
            print(f"iris> meeting email send failed: {message}")
        else:
            print(f"iris error> {message}")
