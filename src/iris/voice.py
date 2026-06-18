from __future__ import annotations

import json
import logging
import re
import queue
import signal
import threading
import time
import base64
import uuid
from difflib import SequenceMatcher
from dataclasses import dataclass
from typing import Any

from iris.config import IrisConfig
from iris.copilot import CoPilotService
from iris.perception import ScreenAwarenessService
from iris.profile import UserProfile, infer_system_profile
from iris.router import ActionRouter
from iris.meetings import (
    complete_meeting,
    deliver_meeting_recap,
    start_silent_meeting,
)
from iris.runtime import RunOrchestrator
from iris.safety import SafetyGate
from iris.scheduler import SchedulerService
from iris.state import open_state
from iris.system import run_command
from iris.tracing import record_trace_event
from iris.voice_events import (
    ConsoleSink,
    EventSink,
    VoiceEvent,
    VoiceEventType,
    VoiceState,
    error_event,
    log_event,
)

_LOG = logging.getLogger("iris.voice")


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    input_channels: int


class RealtimeTextClient:
    def __init__(self, config: IrisConfig) -> None:
        self.config = config

    def send_text_once(self, prompt: str, timeout: float = 20) -> str:
        if not self.config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        import websocket  # type: ignore

        url = f"wss://api.openai.com/v1/realtime?model={self.config.realtime_model}"
        ws = websocket.create_connection(
            url,
            header=[f"Authorization: Bearer {self.config.openai_api_key}"],
            timeout=timeout,
        )
        try:
            ws.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "model": self.config.realtime_model,
                            "output_modalities": ["text"],
                            "instructions": (
                                f"You are {self.config.agent_name}, a natural local Mac "
                                "agent. Be calm, conversational, and specific. Keep answers "
                                "tight, but do not sound like a canned assistant."
                            ),
                        },
                    }
                )
            )
            ws.send(
                json.dumps(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": prompt}],
                        },
                    }
                )
            )
            ws.send(json.dumps({"type": "response.create"}))
            deadline = time.monotonic() + timeout
            chunks: list[str] = []
            while time.monotonic() < deadline:
                event = json.loads(ws.recv())
                event_type = event.get("type", "")
                if event_type in {"response.text.delta", "response.output_text.delta"}:
                    chunks.append(event.get("delta", ""))
                elif event_type in {"response.done", "response.output_item.done"}:
                    if chunks:
                        return "".join(chunks).strip()
                elif event_type == "error":
                    raise RuntimeError(str(event.get("error", event)))
            return "".join(chunks).strip()
        finally:
            ws.close()


class RealtimeAudioClient:
    sample_rate = 24000

    def __init__(self, config: IrisConfig) -> None:
        self.config = config

    def record_pcm16(self, seconds: float = 2.0) -> bytes:
        import sounddevice  # type: ignore

        chunks: list[bytes] = []
        input_device = _select_input_device(sounddevice)

        def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if status:
                print(f"audio warning: {status}")
            chunks.append(bytes(indata))

        with sounddevice.RawInputStream(
            samplerate=self.sample_rate,
            device=input_device.index,
            channels=1,
            dtype="int16",
            callback=callback,
        ):
            time.sleep(seconds)
        return b"".join(chunks)

    def transcribe_once(self, seconds: float = 2.0, timeout: float = 30) -> str:
        if not self.config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        audio = self.record_pcm16(seconds)
        return self.transcribe_pcm16(audio, timeout=timeout)

    def transcribe_pcm16(self, audio: bytes, timeout: float = 30) -> str:
        if not self.config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        if not audio:
            raise RuntimeError("No audio was recorded")
        import websocket  # type: ignore

        url = f"wss://api.openai.com/v1/realtime?model={self.config.realtime_model}"
        ws = websocket.create_connection(
            url,
            header=[f"Authorization: Bearer {self.config.openai_api_key}"],
            timeout=timeout,
        )
        try:
            ws.send(
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "model": self.config.realtime_model,
                            "output_modalities": ["text"],
                            "audio": {
                                "input": {
                                    "format": {
                                        "type": "audio/pcm",
                                        "rate": self.sample_rate,
                                    },
                                    "turn_detection": None,
                                }
                            },
                            "instructions": (
                                "Transcribe the user's spoken request exactly. "
                                "Do not answer the request. Output only one clean "
                                "transcript with no repeated lines."
                            ),
                        },
                    }
                )
            )
            chunk_size = 96_000
            for offset in range(0, len(audio), chunk_size):
                chunk = audio[offset : offset + chunk_size]
                ws.send(
                    json.dumps(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(chunk).decode("ascii"),
                        }
                    )
                )
            ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            ws.send(json.dumps({"type": "response.create"}))
            deadline = time.monotonic() + timeout
            chunks: list[str] = []
            while time.monotonic() < deadline:
                event = json.loads(ws.recv())
                event_type = event.get("type", "")
                if event_type in {"response.text.delta", "response.output_text.delta"}:
                    chunks.append(event.get("delta", ""))
                elif event_type in {"response.done", "response.output_item.done"}:
                    if chunks:
                        return _clean_transcript("".join(chunks))
                elif event_type == "error":
                    raise RuntimeError(str(event.get("error", event)))
            return _clean_transcript("".join(chunks))
        finally:
            ws.close()


class RealtimeSpeechSession:
    sample_rate = 24000

    def __init__(
        self,
        *,
        config: IrisConfig,
        router: ActionRouter,
        user_profile: UserProfile,
        orchestrator: RunOrchestrator | None = None,
        wake_gated: bool = True,
        event_sink: EventSink | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.orchestrator = orchestrator
        self.user_profile = user_profile
        self.wake_gated = wake_gated
        self.awake = not wake_gated
        self._sink: EventSink = event_sink or ConsoleSink()
        self.state = VoiceState.IDLE
        self._end_reason = "stopped"
        self._output: Any | None = None
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._ws: Any | None = None
        self._agent_lock = threading.Lock()
        self._agent_thread: threading.Thread | None = None
        self._pending_agent_texts: queue.Queue[str] = queue.Queue()
        self._active_agent_request = ""
        self._last_busy_notice_at = 0.0
        self._response_state_lock = threading.Lock()
        self._assistant_response_active = False
        self._pending_response_text: str | None = None
        self._last_spoken_text = ""
        self._last_printed_response_text = ""
        self._last_response_started_at = 0.0
        self._suppress_input_until = 0.0
        self._current_assistant_item_id = ""
        self._current_audio_started_at = 0.0
        self._current_audio_received_ms = 0
        self._current_audio_played_ms = 0
        self._current_response_text_chunks: list[str] = []
        self._interrupted_response_pending = False
        self._interrupted_response_at = 0.0
        self._turn_buffer_lock = threading.Lock()
        self._turn_buffer_segments: list[str] = []
        self._turn_buffer_timer: threading.Timer | None = None
        self._turn_buffer_started_at = 0.0
        self._turn_buffer_last_at = 0.0
        self._turn_buffer_interrupted_response = False
        self._pending_calls: list[dict[str, Any]] = []
        self._tool_lock = threading.Lock()
        self._meeting_active = False
        self._meeting_id = ""
        self._meeting_transcript: list[str] = []
        self._meeting_lock = threading.Lock()
        self._screen_awareness = ScreenAwarenessService(
            router.perception,
            interval_seconds=config.screenshot_interval_seconds,
        )
        self._scheduler = SchedulerService(
            config,
            perception=router.perception,
            announce=self.announce,
        )
        self._copilot = CoPilotService(
            signature=self._copilot_signature,
            read_text=self._copilot_read_text,
            advise=self._copilot_advise,
            announce=self.announce,
        )
        self._voice_trace_run_id = f"voice-{uuid.uuid4().hex}"

    def _emit(self, event: VoiceEvent) -> None:
        _LOG.debug(
            "voice.event",
            extra={"voice_event": event.type, "event_data": event.data},
        )
        try:
            self._sink.emit(event)
        except Exception:
            # A broken sink must never take down the audio session.
            _LOG.exception("voice.sink_failed", extra={"voice_event": event.type})

    def _log_line(self, text: str) -> None:
        self._emit(log_event(text))

    def _set_state(self, state: str, *, via: str | None = None, **extra: Any) -> None:
        self.state = state
        data: dict[str, Any] = {
            "state": state,
            "meeting_active": self._meeting_active,
        }
        if via:
            data["via"] = via
        data.update(extra)
        self._emit(VoiceEvent(VoiceEventType.STATE, data))

    def run(self) -> None:
        if not self.config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for live speech mode.")
        import sounddevice  # type: ignore
        import websocket  # type: ignore

        url = f"wss://api.openai.com/v1/realtime?model={self.config.realtime_model}"
        session_started_at = time.monotonic()
        self._set_state(VoiceState.CONNECTING, model=self.config.realtime_model)
        self._ws = websocket.create_connection(
            url,
            header=[f"Authorization: Bearer {self.config.openai_api_key}"],
            timeout=10,
        )
        try:
            self._ws.settimeout(None)
        except Exception:
            pass
        self._log_line("iris> websocket connected")
        self._send_json(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": self.config.realtime_model,
                    "output_modalities": ["audio"],
                    "instructions": self._instructions(),
                    "tools": self._realtime_tools(),
                    "tool_choice": "auto",
                    "audio": {
                        "input": {
                            "format": {
                                "type": "audio/pcm",
                                "rate": self.sample_rate,
                            },
                            "transcription": {
                                "model": self.config.realtime_transcription_model,
                            },
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": self._live_vad_silence_ms(),
                                "create_response": False,
                                "interrupt_response": self._barge_in_enabled(),
                            },
                        },
                        "output": {
                            "format": {
                                "type": "audio/pcm",
                                "rate": self.sample_rate,
                            },
                            "voice": self.config.voice,
                        },
                    },
                },
            }
        )

        self._log_line("iris> live speech-to-speech connected")
        if self.wake_gated:
            self._log_line(
                f"iris> say {', '.join(self.config.wake_words)} to wake me"
            )
        self._log_line("iris> say 'go to sleep' to pause, or press Ctrl+C to stop")

        receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
        receive_thread.start()
        self.router.set_screen_awareness(self._screen_awareness)
        self._screen_awareness.start()
        self._log_line(
            "iris> live screen awareness enabled "
            f"({self.config.screenshot_interval_seconds:.1f}s sampling)"
        )
        self._scheduler.start()

        input_device = _select_input_device(sounddevice)
        self._log_line(
            f"iris> microphone device: {input_device.index} ({input_device.name})"
        )

        def input_callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if status:
                self._emit(error_event("audio_warning", str(status)))
            if self._stop.is_set() or self._input_should_be_muted():
                return
            self._send_json(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(bytes(indata)).decode("ascii"),
                }
            )

        previous_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda *_args: self.stop())
        try:
            with sounddevice.RawInputStream(
                samplerate=self.sample_rate,
                device=input_device.index,
                channels=1,
                dtype="int16",
                blocksize=2400,
                callback=input_callback,
            ):
                while not self._stop.is_set() and receive_thread.is_alive():
                    time.sleep(0.05)
        finally:
            signal.signal(signal.SIGINT, previous_handler)
            self.router.set_screen_awareness(None)
            self._screen_awareness.stop()
            self._scheduler.stop()
            self._copilot.stop()
            self._summarize_session_to_memory()
            self.stop()
            self._emit(
                VoiceEvent(
                    VoiceEventType.SESSION_ENDED,
                    {
                        "reason": self._end_reason,
                        "duration_seconds": round(
                            time.monotonic() - session_started_at, 1
                        ),
                    },
                )
            )
            self._set_state(VoiceState.IDLE, via="session_end")

    def announce(self, text: str) -> bool:
        if self._stop.is_set() or self._ws is None or not self.awake:
            return False
        if self._meeting_active:
            return False
        self._respond_with_text(text)
        return True

    def _start_meeting(self, transcript: str) -> None:
        try:
            with open_state(self.config) as db:
                self._meeting_id = start_silent_meeting(
                    db, self.config, disclosure_spoken=True
                )
        except Exception as exc:
            self._respond_with_text(f"I couldn't start meeting mode: {exc}")
            return
        with self._meeting_lock:
            self._meeting_transcript = []
        self._meeting_active = True
        self._emit(
            VoiceEvent(
                VoiceEventType.MEETING,
                {"active": True, "meeting_id": self._meeting_id},
            )
        )
        disclosure = (
            "Starting meeting mode. I'll listen quietly and won't respond. "
            "Just say 'stop the meeting' when you're done and I'll send you a recap."
        )
        self._respond_with_text(disclosure)

    def _stop_meeting(self) -> None:
        self._meeting_active = False
        meeting_id = self._meeting_id
        self._meeting_id = ""
        self._emit(
            VoiceEvent(
                VoiceEventType.MEETING,
                {"active": False, "meeting_id": meeting_id},
            )
        )
        with self._meeting_lock:
            transcript = "\n".join(self._meeting_transcript).strip()
            self._meeting_transcript = []
        self._respond_with_text("Meeting ended. Putting your recap together now.")

        def worker() -> None:
            try:
                with open_state(self.config) as db:
                    result = complete_meeting(
                        db,
                        self.config,
                        meeting_id=meeting_id,
                        transcript=transcript,
                        openai_client=self.router.openai_client,
                    )
                delivered = deliver_meeting_recap(
                    self.config,
                    result,
                    draft_email=self._meeting_draft_email,
                    create_reminder=self._meeting_create_reminder,
                )
                summary = result.get("summary") if isinstance(result, dict) else {}
                recap = ""
                if isinstance(summary, dict):
                    recap = str(summary.get("summary") or "")
                reminders = int(delivered.get("reminders") or 0)
                emailed = (
                    "drafted your recap email"
                    if delivered.get("email")
                    else "saved the recap"
                )
                spoken = (
                    f"Done. I {emailed}"
                    + (
                        f" and set {reminders} reminder{'s' if reminders != 1 else ''}."
                        if reminders
                        else "."
                    )
                    + (f" In short: {recap}" if recap else "")
                )
                self._respond_with_text(spoken)
            except Exception as exc:
                self._emit(
                    error_event("meeting_recap_failed", f"meeting recap failed: {exc}")
                )
                self._respond_with_text(
                    "I saved the meeting but hit a snag building the recap."
                )

        threading.Thread(target=worker, name="iris-meeting-recap", daemon=True).start()

    def _meeting_draft_email(self, subject: str, body: str) -> None:
        from iris.email_sender import EmailService

        service = EmailService(self.config)
        if service.available:
            result = service.send(subject=subject, body=body)
            if result.ok:
                return
            self._emit(error_event("meeting_email_failed", str(result.detail)))
        self.router.agent_executor.run_tool(
            "draft_email", {"subject": subject, "body": body}
        )

    def _meeting_create_reminder(self, task: str) -> None:
        self.router.agent_executor.run_tool("reminder_create", {"title": task})

    def _copilot_signature(self) -> str:
        if self._meeting_active or not self.awake:
            return ""
        frame = self._screen_awareness.latest()
        if frame is None:
            return ""
        ctx = frame.context
        captured = getattr(frame.screenshot, "captured_at", None)
        bucket = int(captured.timestamp() // 8) if captured else 0
        return f"{ctx.active_app}|{ctx.active_window}|{bucket}"

    def _copilot_read_text(self) -> str:
        result = self.router.agent_executor.run_tool("screen_describe", {})
        return result.message if result.ok else ""

    def _copilot_advise(self, screen_text: str) -> str:
        client = self.router.openai_client
        if not getattr(client, "available", False):
            return ""
        try:
            sdk = client._get_client()
            response = sdk.responses.create(
                model=self.config.chat_model,
                instructions=(
                    "You are a proactive co-pilot watching the user's screen. If there "
                    "is a clear problem they would want help with (an error, a failed "
                    "command, a stuck or blocked state), reply with ONE short spoken "
                    "suggestion, max two sentences, no preamble. If there is nothing "
                    "worth interrupting for, reply with exactly NONE."
                ),
                input=[
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": screen_text[:4000]}],
                    }
                ],
                max_output_tokens=120,
            )
            return client.output_text(response)
        except Exception:
            return ""

    def _toggle_copilot(self, on: bool) -> None:
        if on:
            self._copilot.start()
            self._respond_with_text(
                "Co-pilot on. I'll keep an eye on your screen and speak up if I spot something."
            )
        else:
            self._copilot.stop()
            self._respond_with_text("Co-pilot off.")

    def _summarize_session_to_memory(self) -> None:
        try:
            self.router.agent_executor.summarize_session_to_memory()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
        self._clear_turn_buffer()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass

    def interrupt(self) -> bool:
        """Cancel the in-flight assistant response; the session keeps listening.

        Programmatic twin of the barge-in path (`_handle_barge_in_started`),
        for callers like the gateway that interrupt without speech. Unlike
        barge-in, the server is not cancelling for us via VAD, so we must send
        `response.cancel` ourselves. Returns True if a response was cut.
        """
        now = time.monotonic()
        with self._response_state_lock:
            if not self._assistant_response_active:
                return False
            partial_text = "".join(self._current_response_text_chunks).strip()
            if partial_text:
                self._last_spoken_text = partial_text
            self._assistant_response_active = False
            self._pending_response_text = None
            self._suppress_input_until = 0.0
            self._interrupted_response_pending = True
            self._interrupted_response_at = now
            played_ms = self._played_audio_ms_locked()
        with self._tool_lock:
            self._pending_calls.clear()
        output = getattr(self, "_output", None)
        if output is not None:
            self._stop_output_playback(output)
        self._truncate_active_assistant_audio()
        self._send_json({"type": "response.cancel"})
        self._emit(
            VoiceEvent(
                VoiceEventType.INTERRUPTED, {"at_ms": played_ms, "via": "request"}
            )
        )
        self._set_state(VoiceState.LISTENING, via="interrupt")
        self._trace_voice_event(
            "voice.interrupt.requested",
            details={"audio_end_ms": played_ms},
        )
        return True

    def _receive_loop(self) -> None:
        import sounddevice  # type: ignore

        try:
            with sounddevice.RawOutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=2400,
            ) as output:
                self._output = output
                while not self._stop.is_set() and self._ws is not None:
                    try:
                        raw = self._ws.recv()
                    except Exception as exc:
                        if _is_realtime_timeout(exc):
                            continue
                        if not self._stop.is_set():
                            self._end_reason = "error"
                            self._emit(
                                error_event(
                                    "realtime_receive_failed",
                                    f"realtime receive failed: {exc}",
                                    terminal=True,
                                )
                            )
                        return
                    if not raw:
                        continue
                    event = json.loads(raw)
                    self._handle_event(event, output)
        finally:
            self._output = None
            self._stop.set()

    def _handle_event(self, event: dict[str, Any], output) -> None:  # noqa: ANN001
        event_type = str(event.get("type", ""))
        if event_type == "session.updated":
            self._set_state(VoiceState.LISTENING, via="session_ready")
            return
        if event_type == "response.created":
            with self._response_state_lock:
                self._assistant_response_active = True
                self._last_response_started_at = time.monotonic()
                self._current_response_text_chunks = []
                self._suppress_input_until = max(
                    self._suppress_input_until,
                    time.monotonic() + 1.0,
                )
            self._set_state(VoiceState.SPEAKING)
            return
        if event_type == "input_audio_buffer.speech_started":
            played_ms = self._current_audio_played_ms
            if self._handle_barge_in_started(output):
                self._emit(
                    VoiceEvent(VoiceEventType.INTERRUPTED, {"at_ms": played_ms})
                )
                self._set_state(VoiceState.USER_SPEAKING, via="barge_in")
            elif not self._input_should_be_muted():
                self._set_state(VoiceState.USER_SPEAKING, via="speech")
            return
        if event_type == "input_audio_buffer.speech_stopped":
            if self._input_should_be_muted():
                return
            self._set_state(VoiceState.TRANSCRIBING)
            return
        if event_type == "input_audio_buffer.committed":
            return
        if event_type == "conversation.item.input_audio_transcription.completed":
            transcript = _clean_transcript(str(event.get("transcript", "")))
            if transcript:
                self._handle_transcript(transcript)
            return
        if event_type in {
            "response.audio_transcript.delta",
            "response.output_audio_transcript.delta",
            "response.output_text.delta",
        }:
            delta = str(event.get("delta") or "")
            if delta:
                with self._response_state_lock:
                    self._current_response_text_chunks.append(delta)
                self._emit(
                    VoiceEvent(VoiceEventType.ASSISTANT_DELTA, {"text": delta})
                )
            return
        if event_type == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict):
                self._note_assistant_item(item)
            return
        if event_type in {"response.audio.delta", "response.output_audio.delta"}:
            audio = event.get("delta")
            if isinstance(audio, str) and audio:
                decoded = base64.b64decode(audio)
                duration_ms = _pcm16_duration_ms(decoded, self.sample_rate)
                self._note_audio_output(event, duration_ms)
                try:
                    output.write(decoded)
                    self._note_audio_played(duration_ms)
                except Exception as exc:
                    if not self._has_interrupted_response_pending():
                        self._emit(
                            error_event(
                                "audio_playback_failed",
                                f"audio playback failed: {exc}",
                            )
                        )
            return
        if event_type in {
            "response.audio_transcript.done",
            "response.output_audio_transcript.done",
            "response.output_text.done",
        }:
            text = str(event.get("transcript") or event.get("text") or "").strip()
            if text and not self._is_duplicate_response_text(text):
                self._emit(VoiceEvent(VoiceEventType.ASSISTANT_DONE, {"text": text}))
                with self._response_state_lock:
                    self._last_spoken_text = text
                    self._last_printed_response_text = text
                    self._suppress_input_until = max(
                        self._suppress_input_until,
                        time.monotonic() + 3.0,
                    )
            return
        if event_type in {"response.output_audio.done", "response.audio.done"}:
            return
        if event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                self._record_function_call(item)
            return
        if event_type == "response.function_call_arguments.done":
            self._record_function_call(
                {
                    "name": event.get("name"),
                    "call_id": event.get("call_id"),
                    "arguments": event.get("arguments"),
                }
            )
            return
        if event_type in {"response.done", "response.cancelled"}:
            if event_type == "response.done" and self._dispatch_pending_calls():
                self._set_state(VoiceState.THINKING, via="tools")
                return
            self._mark_response_done()
            self._set_state(VoiceState.LISTENING, via="response_done")
            return
        if event_type == "error":
            error = event.get("error", {})
            code = error.get("code") if isinstance(error, dict) else ""
            self._emit(
                error_event(
                    str(code or "realtime_error"),
                    str(event.get("error", event)),
                )
            )
            if code == "conversation_already_has_active_response":
                with self._response_state_lock:
                    self._assistant_response_active = True
                    self._suppress_input_until = max(
                        self._suppress_input_until,
                        time.monotonic() + 2.0,
                    )

    def _handle_transcript(self, transcript: str) -> None:
        normalized = _normalize_wake_text(transcript)
        if _is_filler_transcript(normalized):
            self._trace_voice_event(
                "voice.turn.dropped_filler",
                details={"word_count": len(normalized.split())},
            )
        if self._should_ignore_transcript(transcript):
            return
        self._emit(
            VoiceEvent(
                VoiceEventType.USER_TRANSCRIPT,
                {"text": transcript, "final": True},
            )
        )
        lowered = transcript.lower().strip()
        self._trace_voice_event(
            "voice.turn.segment_received",
            details={
                "word_count": len(normalized.split()),
                "char_count": len(transcript),
            },
        )
        interrupted_response = self._consume_interrupted_response()
        if self._meeting_active:
            self._clear_turn_buffer()
            if _is_meeting_stop(lowered):
                self._stop_meeting()
            else:
                with self._meeting_lock:
                    self._meeting_transcript.append(transcript)
            return
        if _is_meeting_start(lowered):
            self._clear_turn_buffer()
            self._start_meeting(transcript)
            return
        if _is_sleep_command(lowered):
            self._clear_turn_buffer()
            self.awake = False
            self._respond_with_text("Going quiet. Say Iris when you need me.")
            return

        wake_detected = _contains_wake_word(lowered, self.config.wake_words)
        if self.wake_gated and wake_detected:
            self.awake = True
            self._emit(
                VoiceEvent(VoiceEventType.WAKE_DETECTED, {"source": "in_session"})
            )
            command = _strip_wake_words(transcript, self.config.wake_words).strip(
                " ,.!?"
            )
            if not command:
                self._clear_turn_buffer()
                self._respond_with_text(
                    f"I'm here, {self.user_profile.preferred_name}."
                )
                return
            transcript = command
            lowered = transcript.lower().strip()

        if self.wake_gated and not self.awake:
            return

        if self._handle_control_word(lowered):
            self._clear_turn_buffer()
            return

        instructions = (
            self._revision_interrupt_instructions()
            if interrupted_response and _is_revision_interrupt(transcript)
            else None
        )
        if self._turn_buffer_enabled():
            self._buffer_or_flush_turn(
                transcript,
                interrupted_response=interrupted_response,
                instructions=instructions,
            )
            return
        self._begin_model_response(instructions=instructions)

    def _handle_control_word(self, lowered: str) -> bool:
        word = lowered.strip(" \t\r\n.,!?")
        if any(
            phrase in lowered
            for phrase in (
                "co-pilot on",
                "copilot on",
                "watch my screen",
                "keep an eye on my screen",
            )
        ):
            self._toggle_copilot(True)
            return True
        if any(
            phrase in lowered
            for phrase in (
                "co-pilot off",
                "copilot off",
                "stop watching my screen",
                "stop watching",
            )
        ):
            self._toggle_copilot(False)
            return True
        if _is_interrupt_command(word):
            self.router.agent_executor.cancel_current("Operation cancelled by user.")
            self.router.safety_gate.kill()
            with self._tool_lock:
                self._pending_calls.clear()
            self._log_line("iris> stopping current automation after the active step")
            self._respond_with_text("Stopping that.")
            return True
        if word in {"resume", "unpause", "continue", "carry on"}:
            self.router.safety_gate.resume()
            self._respond_with_text("Okay, back on it.")
            return True
        if word in {
            "approve",
            "approved",
            "yes approve",
            "go ahead",
            "yes go ahead",
            "confirm",
            "do it",
        }:
            result = self.router.agent_executor.approve_pending()
            self._respond_with_text(result.message)
            return True
        if word in {"deny", "denied", "cancel that", "never mind", "no thanks"}:
            result = self.router.agent_executor.deny_pending()
            self._respond_with_text(result.message)
            return True
        if _is_status_question(word):
            self._respond_with_text("I'm right here and ready.")
            return True
        return False

    def _begin_model_response(self, *, instructions: str | None = None) -> None:
        with self._response_state_lock:
            if (
                self._assistant_response_active
                and self._last_response_started_at
                and time.monotonic() - self._last_response_started_at > 30.0
            ):
                self._assistant_response_active = False
            if self._assistant_response_active:
                return
            self._assistant_response_active = True
            self._last_response_started_at = time.monotonic()
            self._suppress_input_until = time.monotonic() + 3.0
        response: dict[str, Any] = {"output_modalities": ["audio"]}
        if instructions:
            response["instructions"] = instructions
        self._send_json({"type": "response.create", "response": response})

    def _handle_barge_in_started(self, output) -> bool:  # noqa: ANN001
        if not self._barge_in_enabled():
            return False
        now = time.monotonic()
        with self._response_state_lock:
            if not self._assistant_response_active:
                return False
            if (
                self._last_response_started_at
                and now - self._last_response_started_at
                < self._barge_in_grace_seconds()
            ):
                return False
            partial_text = "".join(self._current_response_text_chunks).strip()
            if partial_text:
                self._last_spoken_text = partial_text
            self._assistant_response_active = False
            self._pending_response_text = None
            self._suppress_input_until = 0.0
            self._interrupted_response_pending = True
            self._interrupted_response_at = now
            item_id = self._current_assistant_item_id
            audio_end_ms = self._played_audio_ms_locked()
        with self._tool_lock:
            self._pending_calls.clear()
        self._stop_output_playback(output)
        self._truncate_active_assistant_audio()
        self._trace_voice_event(
            "voice.interrupt.detected",
            details={
                "had_assistant_item": bool(item_id),
                "audio_end_ms": audio_end_ms,
                "pending_calls_cleared": True,
            },
        )
        return True

    def _truncate_active_assistant_audio(self) -> None:
        with self._response_state_lock:
            item_id = self._current_assistant_item_id
            audio_end_ms = self._played_audio_ms_locked()
            received_ms = self._current_audio_received_ms
        if not item_id or audio_end_ms <= 0:
            return
        self._send_json(
            {
                "type": "conversation.item.truncate",
                "item_id": item_id,
                "content_index": 0,
                "audio_end_ms": min(audio_end_ms, received_ms),
            }
        )

    def _stop_output_playback(self, output) -> None:  # noqa: ANN001
        try:
            output.abort()
        except Exception:
            try:
                output.stop()
            except Exception:
                return
        try:
            output.start()
        except Exception:
            pass

    def _note_assistant_item(self, item: dict[str, Any]) -> None:
        if item.get("type") != "message" or item.get("role") != "assistant":
            return
        item_id = str(item.get("id") or "")
        if not item_id:
            return
        with self._response_state_lock:
            self._current_assistant_item_id = item_id
            self._current_audio_started_at = 0.0
            self._current_audio_received_ms = 0
            self._current_audio_played_ms = 0

    def _buffer_or_flush_turn(
        self,
        transcript: str,
        *,
        interrupted_response: bool = False,
        instructions: str | None = None,
    ) -> None:
        now = time.monotonic()
        response_instructions: str | None = None
        should_flush = False
        with self._turn_buffer_lock:
            if (
                self._turn_buffer_segments
                and now - self._turn_buffer_last_at > self._turn_max_wait_seconds()
            ):
                self._turn_buffer_segments = []
                self._turn_buffer_started_at = 0.0
                self._turn_buffer_interrupted_response = False
            self._turn_buffer_segments.append(transcript)
            self._turn_buffer_started_at = self._turn_buffer_started_at or now
            self._turn_buffer_last_at = now
            self._turn_buffer_interrupted_response = (
                self._turn_buffer_interrupted_response or interrupted_response
            )
            combined = _join_turn_segments(self._turn_buffer_segments)
            segment_count = len(self._turn_buffer_segments)
            word_count = len(_normalize_wake_text(combined).split())
            should_flush = self._should_flush_turn_locked(combined, transcript, now)
            endpoint_reason = self._turn_endpoint_reason_locked(
                combined,
                transcript,
                now,
                should_flush=should_flush,
            )
            if should_flush:
                response_instructions = self._turn_response_instructions_locked(
                    combined, instructions
                )
                self._cancel_turn_timer_locked()
                self._reset_turn_buffer_locked()
            else:
                self._schedule_turn_flush_locked()
        if should_flush:
            self._trace_voice_event(
                "voice.turn.endpoint_reason",
                details={
                    "endpoint_reason": endpoint_reason,
                    "word_count": word_count,
                },
            )
            self._trace_voice_event(
                "voice.turn.flushed",
                details={
                    "segment_count": segment_count,
                    "endpoint_reason": endpoint_reason,
                    "word_count": word_count,
                },
            )
            self._begin_model_response(instructions=response_instructions)
        else:
            self._trace_voice_event(
                "voice.turn.endpoint_reason",
                details={
                    "endpoint_reason": endpoint_reason,
                    "word_count": len(_normalize_wake_text(transcript).split()),
                },
            )
            self._trace_voice_event(
                "voice.turn.buffered",
                details={
                    "endpoint_reason": endpoint_reason,
                    "word_count": len(_normalize_wake_text(transcript).split()),
                },
            )

    def _flush_turn_buffer(self) -> None:
        response_instructions: str | None = None
        segment_count = 0
        word_count = 0
        with self._turn_buffer_lock:
            if not self._turn_buffer_segments:
                return
            combined = _join_turn_segments(self._turn_buffer_segments)
            segment_count = len(self._turn_buffer_segments)
            word_count = len(_normalize_wake_text(combined).split())
            response_instructions = self._turn_response_instructions_locked(
                combined,
                None,
            )
            self._cancel_turn_timer_locked()
            self._reset_turn_buffer_locked()
        self._trace_voice_event(
            "voice.turn.endpoint_reason",
            details={
                "endpoint_reason": "timeout",
                "word_count": word_count,
            },
        )
        self._trace_voice_event(
            "voice.turn.flushed",
            details={
                "segment_count": segment_count,
                "endpoint_reason": "timeout",
                "word_count": word_count,
            },
        )
        self._begin_model_response(instructions=response_instructions)

    def _clear_turn_buffer(self) -> None:
        with self._turn_buffer_lock:
            self._cancel_turn_timer_locked()
            self._reset_turn_buffer_locked()

    def _should_flush_turn_locked(
        self,
        combined: str,
        latest: str,
        now: float,
    ) -> bool:
        if _is_direct_voice_command(latest):
            return True
        if now - self._turn_buffer_started_at >= self._turn_max_wait_seconds():
            return True
        if len(self._turn_buffer_segments) > 1 and not _is_partial_turn(combined):
            return True
        word_count = len(_normalize_wake_text(combined).split())
        return (
            word_count >= self._turn_min_words_for_immediate_response()
            and not _is_partial_turn(combined)
        )

    def _turn_endpoint_reason_locked(
        self,
        combined: str,
        latest: str,
        now: float,
        *,
        should_flush: bool,
    ) -> str:
        if _is_direct_voice_command(latest):
            return "direct_command"
        if now - self._turn_buffer_started_at >= self._turn_max_wait_seconds():
            return "max_wait"
        if _is_partial_turn(combined):
            return "partial_turn"
        if len(self._turn_buffer_segments) > 1:
            return "continuation_complete" if should_flush else "continuation_wait"
        return "complete" if should_flush else "waiting_for_continuation"

    def _turn_response_instructions_locked(
        self,
        combined: str,
        existing: str | None,
    ) -> str | None:
        parts: list[str] = []
        if existing:
            parts.append(existing)
        if len(self._turn_buffer_segments) > 1:
            parts.append(
                "Treat the user's recent speech fragments as one continuous turn. "
                "Do not answer earlier fragments separately."
            )
        if _is_partial_turn(combined):
            parts.append(
                "The user's latest voice turn may be incomplete. If the intent is not "
                "clear, ask one short clarifying question instead of rewriting or "
                "finishing their sentence."
            )
        if _is_meta_feedback_about_iris(combined):
            parts.append(
                "The user is giving feedback about Iris. Acknowledge the product issue "
                "directly and briefly; do not rewrite their wording unless asked."
            )
        return " ".join(parts) if parts else None

    def _schedule_turn_flush_locked(self) -> None:
        self._cancel_turn_timer_locked()
        delay = self._turn_continuation_seconds()
        elapsed = time.monotonic() - self._turn_buffer_started_at
        remaining = max(0.05, self._turn_max_wait_seconds() - elapsed)
        self._turn_buffer_timer = threading.Timer(
            min(delay, remaining),
            self._flush_turn_buffer,
        )
        self._turn_buffer_timer.daemon = True
        self._turn_buffer_timer.start()

    def _cancel_turn_timer_locked(self) -> None:
        if self._turn_buffer_timer is not None:
            self._turn_buffer_timer.cancel()
            self._turn_buffer_timer = None

    def _reset_turn_buffer_locked(self) -> None:
        self._turn_buffer_segments = []
        self._turn_buffer_started_at = 0.0
        self._turn_buffer_last_at = 0.0
        self._turn_buffer_interrupted_response = False

    def _record_function_call(self, item: dict[str, Any]) -> None:
        name = str(item.get("name") or "")
        if not name:
            return
        call_id = str(item.get("call_id") or item.get("id") or "")
        with self._tool_lock:
            if call_id and any(c["call_id"] == call_id for c in self._pending_calls):
                return
            self._pending_calls.append(
                {
                    "call_id": call_id,
                    "name": name,
                    "arguments": item.get("arguments"),
                }
            )

    def _dispatch_pending_calls(self) -> bool:
        with self._tool_lock:
            if not self._pending_calls:
                return False
            calls = self._pending_calls
            self._pending_calls = []
        with self._response_state_lock:
            self._assistant_response_active = False
        threading.Thread(
            target=self._run_calls,
            args=(calls,),
            name="iris-tool-worker",
            daemon=True,
        ).start()
        return True

    def _run_calls(self, calls: list[dict[str, Any]]) -> None:
        for call in calls:
            name = call["name"]
            call_id = call["call_id"]
            arguments = self._parse_tool_arguments(call.get("arguments"))
            self._emit(
                VoiceEvent(
                    VoiceEventType.TOOL_CALL, {"name": name, "status": "started"}
                )
            )
            if name == "look_at_screen":
                self._inject_screen_image(call_id, arguments)
                continue
            try:
                result = self.router.agent_executor.run_tool(name, arguments)
                output = result.message or (
                    "Done." if result.ok else "That didn't work."
                )
                if result.ok:
                    self._emit(
                        VoiceEvent(
                            VoiceEventType.TOOL_CALL,
                            {"name": name, "status": "done"},
                        )
                    )
                else:
                    self._emit(
                        VoiceEvent(
                            VoiceEventType.TOOL_CALL,
                            {"name": name, "status": "failed", "detail": output},
                        )
                    )
            except Exception as exc:
                output = f"The {name} tool failed: {exc}"
                self._emit(
                    VoiceEvent(
                        VoiceEventType.TOOL_CALL,
                        {"name": name, "status": "failed", "detail": str(exc)},
                    )
                )
            self._send_json(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output,
                    },
                }
            )
        with self._response_state_lock:
            self._assistant_response_active = True
            self._last_response_started_at = time.monotonic()
            self._suppress_input_until = time.monotonic() + 3.0
        self._send_json(
            {"type": "response.create", "response": {"output_modalities": ["audio"]}}
        )

    def _inject_screen_image(self, call_id: str, arguments: dict[str, Any]) -> None:
        question = (
            arguments.get("question")
            or arguments.get("prompt")
            or "Describe what is relevant on the screen."
        )
        try:
            frame = self._screen_awareness.capture_now()
            data_url = frame.screenshot.data_url()
        except Exception as exc:
            self._send_json(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": f"I couldn't capture the screen: {exc}",
                    },
                }
            )
            return
        self._send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": "Captured the current screen; answering from the image.",
                },
            }
        )
        self._send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_image", "image_url": data_url},
                        {"type": "input_text", "text": str(question)},
                    ],
                },
            }
        )

    @staticmethod
    def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    def _run_agent_async(self, transcript: str) -> None:
        if not self._agent_lock.acquire(blocking=False):
            lowered = transcript.lower().strip(" \t\r\n.,!?")
            if _is_interrupt_command(lowered):
                if self.orchestrator is not None:
                    self.orchestrator.cancel_active_run(
                        channel="voice",
                        reason="Operation cancelled by user.",
                    )
                else:
                    self.router.agent_executor.cancel_current(
                        "Operation cancelled by user."
                    )
                self.router.safety_gate.kill()
                self._clear_pending_agent_texts()
                self._log_line(
                    "iris> stopping current automation after the active step"
                )
                self._respond_with_text("Stopping that after the current step.")
                return
            if _is_status_question(lowered):
                self._respond_with_text(self._active_agent_status_message())
                return
            self._pending_agent_texts.put(transcript)
            now = time.monotonic()
            if now - self._last_busy_notice_at > 4.0:
                self._log_line("iris> queued that after the current request")
                self._last_busy_notice_at = now
            return

        def worker() -> None:
            self._active_agent_request = transcript
            try:
                self._set_state(VoiceState.THINKING, via="agent")
                result = (
                    self.orchestrator.start_run(transcript, channel="voice")
                    if self.orchestrator is not None
                    else self.router.handle_text(transcript)
                )
                self._emit(
                    VoiceEvent(
                        VoiceEventType.AGENT_RUN,
                        {
                            "run_id": getattr(result, "run_id", None),
                            "status": "done" if result.ok else "failed",
                            "summary": result.message,
                            "detail": None if result.ok else result.message,
                        },
                    )
                )
                self._respond_with_text(result.message)
            except Exception as exc:
                message = f"I hit an agent error: {exc}"
                self._emit(
                    VoiceEvent(
                        VoiceEventType.AGENT_RUN,
                        {
                            "run_id": None,
                            "status": "failed",
                            "summary": message,
                            "detail": str(exc),
                        },
                    )
                )
                self._respond_with_text(message)
            finally:
                self._active_agent_request = ""
                self._agent_lock.release()
                self._run_next_pending_agent_text()

        self._agent_thread = threading.Thread(
            target=worker,
            name="iris-agent-worker",
            daemon=True,
        )
        self._agent_thread.start()

    def _run_next_pending_agent_text(self) -> None:
        if self._stop.is_set():
            return
        try:
            next_text = self._pending_agent_texts.get_nowait()
        except queue.Empty:
            return
        threading.Timer(0.05, lambda: self._run_agent_async(next_text)).start()

    def _clear_pending_agent_texts(self) -> None:
        while True:
            try:
                self._pending_agent_texts.get_nowait()
            except queue.Empty:
                return

    def _active_agent_status_message(self) -> str:
        snapshot = (
            self.orchestrator.get_active_run(channel="voice")
            if self.orchestrator is not None
            else None
        )
        status = self.router.agent_executor.current_status()
        active = self._active_agent_request or str(
            status.get("request") or "the request"
        )
        agent_stage = str(status.get("stage") or "")
        stage = (agent_stage or (snapshot.current_step if snapshot else "")).replace(
            "_", " "
        )
        tool_name = str(status.get("tool_name") or "").replace("_", " ")
        if stage == "executing tool" and tool_name:
            return f"I'm still working on {active}. Current step: {tool_name}."
        if stage:
            return f"I'm still working on {active}. Current step: {stage}."
        return f"I'm still working on {active}."

    def _respond_with_text(self, text: str) -> None:
        if not text:
            return
        spoken_text = _spoken_runtime_result(text)
        with self._response_state_lock:
            if (
                self._assistant_response_active
                and self._last_response_started_at
                and time.monotonic() - self._last_response_started_at > 30.0
            ):
                self._assistant_response_active = False
            if self._assistant_response_active:
                self._pending_response_text = text
                return
            self._assistant_response_active = True
            self._last_response_started_at = time.monotonic()
            self._last_spoken_text = spoken_text
            self._suppress_input_until = time.monotonic() + 3.0
        self._send_json(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Turn this local Iris runtime result into natural live speech. "
                                "Stay faithful to the result, but say it like a calm person in the room, "
                                "not a status logger. Use one or two sentences, three only if the result "
                                f"needs context: {spoken_text}"
                            ),
                        }
                    ],
                },
            }
        )
        self._send_json(
            {
                "type": "response.create",
                "response": {
                    "output_modalities": ["audio"],
                    "instructions": (
                        "Speak only the local runtime result, but make it human. "
                        "Use warm, relaxed phrasing; vary the wording; do not read raw paths, JSON, "
                        "stack traces, or command syntax aloud. If something failed, say what happened "
                        "plainly and what Iris is trying or needs next."
                    ),
                },
            }
        )

    def _mark_response_done(self) -> None:
        pending: str | None = None
        with self._response_state_lock:
            self._assistant_response_active = False
            self._suppress_input_until = max(
                self._suppress_input_until,
                time.monotonic() + self._echo_suppression_seconds(),
            )
            if self._pending_response_text:
                pending = self._pending_response_text
                self._pending_response_text = None
            self._current_assistant_item_id = ""
            self._current_audio_started_at = 0.0
            self._current_audio_received_ms = 0
            self._current_audio_played_ms = 0
            self._current_response_text_chunks = []
        if pending:
            self._respond_with_text(pending)

    def _note_audio_output(self, event: dict[str, Any], duration_ms: int) -> None:
        item_id = str(event.get("item_id") or "")
        with self._response_state_lock:
            self._assistant_response_active = True
            self._last_response_started_at = (
                self._last_response_started_at or time.monotonic()
            )
            if item_id:
                self._current_assistant_item_id = item_id
            if not self._current_audio_started_at:
                self._current_audio_started_at = time.monotonic()
            self._current_audio_received_ms += duration_ms
            self._suppress_input_until = max(
                self._suppress_input_until,
                time.monotonic() + self._echo_suppression_seconds(),
            )

    def _note_audio_played(self, duration_ms: int) -> None:
        with self._response_state_lock:
            self._current_audio_played_ms = min(
                self._current_audio_received_ms,
                self._current_audio_played_ms + duration_ms,
            )

    def _played_audio_ms_locked(self) -> int:
        if not self._current_audio_started_at:
            return self._current_audio_played_ms
        wall_ms = int((time.monotonic() - self._current_audio_started_at) * 1000.0)
        return min(
            self._current_audio_received_ms,
            max(self._current_audio_played_ms, wall_ms),
        )

    def _input_should_be_muted(self) -> bool:
        with self._response_state_lock:
            if self._barge_in_enabled():
                return False
            return (
                self._assistant_response_active
                or time.monotonic() <= self._suppress_input_until
            )

    def _should_ignore_transcript(self, transcript: str) -> bool:
        normalized = _normalize_wake_text(transcript)
        if not normalized:
            return True
        if _is_filler_transcript(normalized):
            return True
        with self._response_state_lock:
            active = self._assistant_response_active
            partial_text = "".join(self._current_response_text_chunks).strip()
            last_spoken = partial_text or self._last_spoken_text
            suppress_until = self._suppress_input_until
        if active and not self._barge_in_enabled():
            return True
        if time.monotonic() <= suppress_until and _similar_text(
            transcript, last_spoken
        ):
            return True
        if active and _similar_text(transcript, last_spoken):
            return True
        return False

    def _is_duplicate_response_text(self, text: str) -> bool:
        with self._response_state_lock:
            last = self._last_printed_response_text
        return _similar_text(text, last)

    def _send_json(self, payload: dict[str, Any]) -> None:
        if self._ws is None or self._stop.is_set():
            return
        with self._send_lock:
            try:
                self._ws.send(json.dumps(payload))
            except Exception:
                self._stop.set()

    def _has_interrupted_response_pending(self) -> bool:
        with self._response_state_lock:
            return self._interrupted_response_pending

    def _consume_interrupted_response(self) -> bool:
        with self._response_state_lock:
            pending = self._interrupted_response_pending and (
                time.monotonic() - self._interrupted_response_at <= 10.0
            )
            self._interrupted_response_pending = False
            self._interrupted_response_at = 0.0
            return pending

    def _clear_interrupted_response(self) -> None:
        with self._response_state_lock:
            self._interrupted_response_pending = False
            self._interrupted_response_at = 0.0

    def _barge_in_enabled(self) -> bool:
        return bool(getattr(self.config, "barge_in_enabled", True))

    def _barge_in_grace_seconds(self) -> float:
        return max(0, int(getattr(self.config, "barge_in_grace_ms", 250))) / 1000.0

    def _echo_suppression_seconds(self) -> float:
        return max(0, int(getattr(self.config, "echo_suppression_ms", 800))) / 1000.0

    def _turn_buffer_enabled(self) -> bool:
        return bool(getattr(self.config, "turn_buffer_enabled", True))

    def _turn_continuation_seconds(self) -> float:
        return max(0, int(getattr(self.config, "turn_continuation_ms", 1200))) / 1000.0

    def _turn_max_wait_seconds(self) -> float:
        return max(1, int(getattr(self.config, "turn_max_wait_ms", 2500))) / 1000.0

    def _turn_min_words_for_immediate_response(self) -> int:
        return max(
            1,
            int(getattr(self.config, "turn_min_words_for_immediate_response", 4)),
        )

    def _live_vad_silence_ms(self) -> int:
        return max(200, int(getattr(self.config, "live_vad_silence_ms", 900)))

    def _trace_voice_event(
        self,
        event_name: str,
        *,
        status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            safe_details = _safe_trace_details(details or {})
            with open_state(self.config) as db:
                record_trace_event(
                    db,
                    run_id=self._voice_trace_run_id,
                    event_name=event_name,
                    component="voice",
                    status=status,
                    details=safe_details,
                )
        except Exception:
            return

    @staticmethod
    def _revision_interrupt_instructions() -> str:
        return (
            "The user interrupted your previous answer to revise the request. "
            "Treat the latest user utterance as a correction or refinement of the "
            "previous prompt, not as an unrelated new topic. Stop the prior answer "
            "and answer the revised request directly."
        )

    def _realtime_tools(self) -> list[dict[str, Any]]:
        schemas = self.router.agent_executor.registry.realtime_schemas()
        return [
            {
                "type": "function",
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["parameters"],
            }
            for schema in schemas
        ]

    def _memory_context(self) -> str:
        try:
            memories = self.router.agent_executor._known_memories(limit=20)
        except Exception:
            memories = []
        if not memories:
            return ""
        lines = "\n".join(f"- {item['content']}" for item in memories)
        return f"\n\nWhat you've learned about {self.user_profile.preferred_name}:\n{lines}"

    def _instructions(self) -> str:
        return f"""You are {self.config.agent_name}, a warm, capable Mac voice agent who can act on the computer.

User profile:
{self.user_profile.prompt_context()}{self._memory_context()}

How you work:
- You have tools to control this Mac: open and drive apps, browse the web, read the screen, find and open files, play media, manage calendar/reminders, draft email, run shell commands, remember things, and delegate big multi-step jobs with run_deep_task. Call them to actually do what {self.user_profile.preferred_name} asks.
- When a request needs action, call the right tool rather than describing what you would do. Chain tools when needed. For anything more than a couple of steps (research, multi-app workflows), call run_deep_task with a clear goal.
- To know what is on screen, prefer fast structured reads first: browser_get_dom for web pages, screen_describe for a quick summary. Only call look_at_screen when you genuinely need to see the pixels (images, layout, a non-text app); you will then be shown the actual screenshot to answer from.
- When {self.user_profile.preferred_name} states a durable preference or fact ("always...", "from now on...", "I prefer...", "my X is..."), call remember silently. For "what do you know about me", call recall or memory_search. For relationship questions, use memory_related; for "why do you think that", use memory_explain. For pending memory approvals, use memory_review_list and memory_confirm or memory_review_decide.
- After a tool runs, speak only what actually happened from its result. Never invent an outcome (a song that started, a message that sent). If something failed, say so plainly and what you'll try next. Never say a capability is unavailable — just use another tool.
- Ask before destructive actions, sending messages, deleting files, credentials, purchases, installs, or security/privacy changes; the runtime will also gate these and tell you when approval is needed.

Voice behavior:
- This is live speech-to-speech, not dictation. Sound present, relaxed, and conversational.
- Talk like a capable person sitting next to {self.user_profile.preferred_name}: calm, warm, lightly playful when the user is casual, never corporate.
- Use {self.user_profile.preferred_name}'s first name occasionally, not every turn. Vary your wording; avoid canned lines like "How can I help you today?".
- One or two sentences is usually right; three if the task needs context. Don't read raw URLs, paths, JSON, or tool names aloud.
- If the user says go to sleep, go quiet until the wake word is used again.
"""


class HotkeyService:
    def __init__(self, config: IrisConfig, safety_gate: SafetyGate) -> None:
        self.config = config
        self.safety_gate = safety_gate
        self.events: queue.Queue[str] = queue.Queue()
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard  # type: ignore
        except Exception:
            return False

        def toggle() -> None:
            self.events.put("toggle")

        def kill() -> None:
            self.safety_gate.kill()
            self.events.put("kill")

        self._listener = keyboard.GlobalHotKeys(
            {
                self.config.toggle_hotkey: toggle,
                self.config.kill_hotkey: kill,
            }
        )
        self._listener.start()
        return True

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()


class VoiceSession:
    def __init__(
        self,
        *,
        config: IrisConfig,
        router: ActionRouter,
        safety_gate: SafetyGate,
        user_profile: UserProfile | None = None,
        orchestrator: RunOrchestrator | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.orchestrator = orchestrator
        self.safety_gate = safety_gate
        self.user_profile = user_profile or infer_system_profile()
        self.hotkeys = HotkeyService(config, safety_gate)
        self.listening = False
        self._stop = threading.Event()
        self._voice_lock = threading.Lock()
        self._hotkey_thread: threading.Thread | None = None

    def speak(self, text: str) -> None:
        if not text:
            return
        result = run_command(["say", text], timeout=30)
        if not result.ok:
            print(text)

    def run_terminal_loop(self, *, wake_mode: bool = False) -> None:
        hotkeys_enabled = self.hotkeys.start()
        if hotkeys_enabled:
            self._hotkey_thread = threading.Thread(
                target=self._hotkey_worker,
                name="iris-hotkey-worker",
                daemon=True,
            )
            self._hotkey_thread.start()
        print(f"{self.config.agent_name} is running.")
        print(f"Hey {self.user_profile.preferred_name}.")
        print(f"Toggle hotkey: {self.config.toggle_hotkey}")
        print(f"Kill hotkey: {self.config.kill_hotkey}")
        if not hotkeys_enabled:
            print(
                "Global hotkeys unavailable; use terminal commands: kill, resume, quit."
            )
        if wake_mode:
            print(
                "Live speech-to-speech wake mode enabled. Say one of: "
                f"{', '.join(self.config.wake_words)}. Press Ctrl+C to stop."
            )
            self._run_realtime_speech_loop(wake_gated=True)
            if self._stop.is_set():
                return
            print(
                "Type a message and press Return. Type /listen for backup STT, /reset to reset chat."
            )
        else:
            print(
                "Type a message and press Return. Type /live or /wake for live "
                "speech-to-speech, /listen for backup STT, /reset to reset chat."
            )

        try:
            while not self._stop.is_set():
                try:
                    user_text = input("you> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if user_text.lower() in {"quit", "exit"}:
                    break
                if user_text.lower().startswith("/listen"):
                    self._handle_voice_once(self._parse_listen_seconds(user_text))
                    continue
                if user_text.lower() in {"/wake", "/live"}:
                    self._run_realtime_speech_loop(wake_gated=True)
                    continue
                if user_text.lower() == "/live-now":
                    self._run_realtime_speech_loop(wake_gated=False)
                    continue
                result = (
                    self.orchestrator.start_run(user_text, channel="terminal")
                    if self.orchestrator is not None
                    else self.router.handle_text(user_text)
                )
                prefix = "iris" if result.ok else "iris error"
                print(f"{prefix}> {result.message}")
                if result.ok and self.config.speak_responses:
                    self.speak(result.message[: self.config.max_response_chars])
        finally:
            self._stop.set()
            self.hotkeys.stop()

    def _hotkey_worker(self) -> None:
        while not self._stop.is_set():
            try:
                event = self.hotkeys.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if event == "toggle":
                self._handle_voice_once(self.config.listen_seconds)
            elif event == "kill":
                if self.orchestrator is not None:
                    self.orchestrator.cancel_active_run(
                        reason="Operation cancelled by hotkey."
                    )
                else:
                    self.router.agent_executor.cancel_current(
                        "Operation cancelled by hotkey."
                    )
                print("[kill switch engaged]")

    def _handle_voice_once(self, seconds: float | None = None) -> None:
        if not self._voice_lock.acquire(blocking=False):
            print("iris> already listening")
            return
        try:
            if not self.config.has_openai:
                print("iris error> OPENAI_API_KEY is required for voice input.")
                return
            listen_seconds = (
                seconds if seconds is not None else self.config.listen_seconds
            )
            print(f"iris> listening for {listen_seconds:.1f} seconds...")
            user_text = RealtimeAudioClient(self.config).transcribe_once(
                seconds=listen_seconds
            )
            print(f"you said> {user_text}")
            if not user_text:
                print("iris error> no speech detected")
                return
            result = (
                self.orchestrator.start_run(user_text, channel="voice")
                if self.orchestrator is not None
                else self.router.handle_text(user_text)
            )
            prefix = "iris" if result.ok else "iris error"
            print(f"{prefix}> {result.message}")
            if result.ok and self.config.speak_responses:
                self.speak(result.message[: self.config.max_response_chars])
        except Exception as exc:
            print(f"iris error> {exc}")
        finally:
            self._voice_lock.release()

    def _run_wake_loop(self) -> None:
        if not self.config.has_openai:
            print("iris error> OPENAI_API_KEY is required for wake-word mode.")
            return
        client = RealtimeAudioClient(self.config)
        print("iris> wake listener active")
        while not self._stop.is_set():
            try:
                heard = client.transcribe_once(
                    seconds=self.config.wake_poll_seconds,
                    timeout=max(10.0, self.config.wake_poll_seconds + 8.0),
                )
            except KeyboardInterrupt:
                print()
                return
            except Exception as exc:
                print(f"iris error> wake listener failed: {exc}")
                return
            normalized = heard.lower().strip()
            if not normalized:
                continue
            if any(word in normalized for word in self.config.wake_words):
                print(f"iris> wake word heard: {heard}")
                self._handle_voice_once(self.config.listen_seconds)
                print("iris> wake listener active")

    def _run_realtime_speech_loop(self, *, wake_gated: bool) -> None:
        try:
            RealtimeSpeechSession(
                config=self.config,
                router=self.router,
                user_profile=self.user_profile,
                orchestrator=self.orchestrator,
                wake_gated=wake_gated,
            ).run()
        except KeyboardInterrupt:
            print()
            self._stop.set()
        except Exception as exc:
            print(f"iris error> live speech failed: {exc}")
            print("iris> fallback available: type /listen or /listen 1.5")

    def _parse_listen_seconds(self, command: str) -> float:
        match = re.match(r"^/listen(?:\s+(\d+(?:\.\d+)?))?$", command.strip(), re.I)
        if not match or not match.group(1):
            return self.config.listen_seconds
        value = float(match.group(1))
        return max(0.5, min(value, 10.0))


def _clean_transcript(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) >= 2 and len(set(lines)) == 1:
        return lines[0]
    half = len(stripped) // 2
    if len(stripped) % 2 == 0 and stripped[:half].strip() == stripped[half:].strip():
        return stripped[:half].strip()
    return stripped


def _spoken_runtime_result(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    if "That needs approval before I can do it" in cleaned:
        return "That needs approval. Say approve to continue."
    cleaned = re.sub(r"`\./iris approvals`", "the approvals command", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(
        r"\brun\s+\./iris\s+\w+(?:\s+\w+)*",
        "use the matching Iris command",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(
        r"opened\s+/Users/[^\s]+/Downloads\b",
        "opened your Downloads folder",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"/Users/[^\s]+", "that local path", cleaned)
    cleaned = cleaned.replace("OPENAI_API_KEY", "the OpenAI API key")
    return cleaned


def _join_turn_segments(segments: list[str]) -> str:
    cleaned = [segment.strip(" \t\r\n") for segment in segments if segment.strip()]
    if not cleaned:
        return ""
    return re.sub(r"\s+", " ", " ".join(cleaned)).strip()


def _safe_trace_details(details: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in details.items():
        if isinstance(value, bool | int | float) or value is None:
            safe[key] = value
        elif key.endswith("_reason") or key in {"service", "surface", "action"}:
            safe[key] = str(value)[:80]
    return safe


def _pcm16_duration_ms(audio: bytes, sample_rate: int) -> int:
    if not audio or sample_rate <= 0:
        return 0
    frames = len(audio) // 2
    return int((frames / sample_rate) * 1000.0)


def _select_input_device(sounddevice_module: Any) -> AudioDevice:
    devices = sounddevice_module.query_devices()
    default_device = getattr(sounddevice_module, "default", None)
    default_index = -1
    if default_device is not None:
        raw_default = getattr(default_device, "device", (-1, -1))
        try:
            default_index = int(raw_default[0])
        except (TypeError, ValueError, IndexError):
            default_index = -1
    if 0 <= default_index < len(devices):
        device = devices[default_index]
        channels = _input_channels(device)
        if channels > 0:
            return AudioDevice(
                default_index, str(device.get("name", default_index)), channels
            )
    for index, device in enumerate(devices):
        channels = _input_channels(device)
        if channels > 0:
            return AudioDevice(index, str(device.get("name", index)), channels)
    names = ", ".join(
        str(device.get("name", index)) for index, device in enumerate(devices)
    )
    raise RuntimeError(
        "No microphone input device is available. macOS is reporting no input-capable audio devices. "
        "Connect or select a microphone in System Settings > Sound > Input, then restart Iris. "
        f"Detected devices: {names or 'none'}"
    )


def _input_channels(device: Any) -> int:
    try:
        return int(device.get("max_input_channels", 0))
    except AttributeError:
        try:
            return int(device["max_input_channels"])
        except Exception:
            return 0


def _is_filler_transcript(normalized: str) -> bool:
    compact = normalized.strip()
    if compact in {"um", "uh", "umm", "hmm", "mm", "yeah", "okay"}:
        return True
    words = compact.split()
    return (
        bool(words)
        and len(words) <= 3
        and all(
            word in {"um", "uh", "umm", "hmm", "mm", "like", "okay"} for word in words
        )
    )


def _contains_wake_word(text: str, wake_words: tuple[str, ...]) -> bool:
    normalized = _normalize_wake_text(text)
    return any(
        re.search(rf"\b{re.escape(_normalize_wake_text(word))}\b", normalized)
        for word in _wake_word_variants(wake_words)
    )


def _strip_wake_words(text: str, wake_words: tuple[str, ...]) -> str:
    cleaned = text
    for word in sorted(_wake_word_variants(wake_words), key=len, reverse=True):
        pattern = _wake_word_pattern(word)
        cleaned = re.sub(pattern, "", cleaned, count=1, flags=re.I).strip()
    return cleaned


def _wake_word_variants(wake_words: tuple[str, ...]) -> tuple[str, ...]:
    variants = set(wake_words)
    if any(word in {"iris", "hey iris"} for word in wake_words):
        variants.update({"aries", "hey aries"})
    return tuple(variants)


def _normalize_wake_text(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    normalized = re.sub(r"\bi m\b", "i am", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _wake_word_pattern(word: str) -> str:
    parts = [re.escape(part) for part in _normalize_wake_text(word).split()]
    separator = r"[\s,.;:!?-]+"
    return r"^\s*" + separator.join(parts) + r"\b[\s,.;:!?-]*"


def _similar_text(left: str, right: str) -> bool:
    normalized_left = _normalize_wake_text(left)
    normalized_right = _normalize_wake_text(right)
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    if normalized_left in normalized_right or normalized_right in normalized_left:
        return True
    return SequenceMatcher(None, normalized_left, normalized_right).ratio() >= 0.68


def _is_realtime_timeout(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return "timeout" in name or "timed out" in text


def _is_sleep_command(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "go to sleep",
            "stop listening",
            "go silent",
            "be quiet",
            "mute yourself",
        )
    )


def _is_meeting_start(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "start a meeting",
            "start the meeting",
            "start meeting",
            "meeting mode",
            "begin the meeting",
            "take notes on this meeting",
            "listen to this meeting",
        )
    )


def _is_meeting_stop(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "stop the meeting",
            "end the meeting",
            "stop meeting",
            "end meeting",
            "that's a wrap",
            "thats a wrap",
            "meeting is over",
            "meeting's over",
            "wrap up the meeting",
        )
    )


def _is_interrupt_command(text: str) -> bool:
    normalized = _normalize_wake_text(text)
    return normalized in {
        "stop",
        "cancel",
        "cancel that",
        "stop that",
        "interrupt",
        "pause automation",
        "kill",
        "kill switch",
        "never mind",
    }


def _is_revision_interrupt(text: str) -> bool:
    normalized = _normalize_wake_text(text)
    if not normalized:
        return False
    prefixes = (
        "actually",
        "wait",
        "hold on",
        "i meant",
        "i mean",
        "no i meant",
        "no actually",
        "sorry",
        "scratch that",
        "instead",
        "make it",
        "change it",
        "go back",
        "could you go back",
        "can you go back",
        "i did mean",
        "i didnt mean",
        "i did not mean",
    )
    return any(
        normalized == prefix or normalized.startswith(f"{prefix} ")
        for prefix in prefixes
    )


def _is_direct_voice_command(text: str) -> bool:
    normalized = _normalize_wake_text(text)
    if not normalized:
        return False
    if (
        _is_interrupt_command(normalized)
        or _is_status_question(normalized)
        or _is_sleep_command(normalized)
        or _is_meeting_start(normalized)
        or _is_meeting_stop(normalized)
    ):
        return True
    direct_prefixes = (
        "open ",
        "play ",
        "pause ",
        "resume ",
        "search ",
        "find ",
        "click ",
        "type ",
        "send ",
        "create ",
        "start ",
        "stop ",
        "turn ",
        "set ",
        "what ",
        "why ",
        "how ",
        "where ",
        "are you ",
        "can you ",
        "could you ",
        "would you ",
        "please ",
    )
    return normalized.startswith(direct_prefixes) and not _has_trailing_continuation(
        normalized
    )


def _is_partial_turn(text: str) -> bool:
    normalized = _normalize_wake_text(text)
    if not normalized:
        return True
    words = normalized.split()
    if _is_filler_transcript(normalized):
        return True
    if len(words) <= 2 and not _is_direct_voice_command(normalized):
        return True
    if normalized.startswith(("when i ", "when we ", "if i ", "if we ")):
        return True
    if _has_trailing_continuation(normalized):
        return True
    if (
        re.search(
            r"\b(um|uh|umm|hmm|like|okay so|so um|i mean)\b[^.?!]*$",
            normalized,
        )
        and len(words) < 10
    ):
        return True
    return False


def _has_trailing_continuation(normalized: str) -> bool:
    trailing_phrases = (
        "there needs to be",
        "there needs to be a",
        "i feel like",
        "i think",
        "i want to",
        "i need to",
        "we need to",
        "we still need to",
        "when i",
        "when we",
        "if i",
        "if we",
        "can you",
        "could you",
        "i was going to",
        "i am trying to",
    )
    trailing_words = {
        "a",
        "an",
        "the",
        "to",
        "for",
        "with",
        "about",
        "because",
        "that",
        "this",
        "like",
        "and",
        "or",
        "but",
        "so",
    }
    return (
        normalized.endswith(trailing_phrases)
        or normalized.split()[-1] in trailing_words
    )


def _is_meta_feedback_about_iris(text: str) -> bool:
    normalized = _normalize_wake_text(text)
    if not normalized:
        return False
    iris_terms = ("iris", "voice", "wake", "wakeup", "wake up", "detect", "detection")
    feedback_terms = (
        "need",
        "needs",
        "should",
        "have to",
        "has to",
        "better",
        "improve",
        "issue",
        "problem",
        "seems like",
    )
    return any(term in normalized for term in iris_terms) and any(
        term in normalized for term in feedback_terms
    )


def _is_status_question(text: str) -> bool:
    normalized = _normalize_wake_text(text)
    return normalized in {
        "what are you doing",
        "what are you working on",
        "status",
        "where are you",
        "are you still working",
    }
