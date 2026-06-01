from __future__ import annotations

import json
import re
import queue
import signal
import threading
import time
import base64
from difflib import SequenceMatcher
from dataclasses import dataclass
from typing import Any

from iris.config import IrisConfig
from iris.perception import ScreenAwarenessService
from iris.profile import UserProfile, infer_system_profile
from iris.router import ActionRouter
from iris.safety import SafetyGate
from iris.system import run_command


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
        wake_gated: bool = True,
    ) -> None:
        self.config = config
        self.router = router
        self.user_profile = user_profile
        self.wake_gated = wake_gated
        self.awake = not wake_gated
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
        self._screen_awareness = ScreenAwarenessService(
            router.perception,
            interval_seconds=config.screenshot_interval_seconds,
        )

    def run(self) -> None:
        if not self.config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for live speech mode.")
        import sounddevice  # type: ignore
        import websocket  # type: ignore

        url = f"wss://api.openai.com/v1/realtime?model={self.config.realtime_model}"
        print(f"iris> connecting realtime model {self.config.realtime_model}...")
        self._ws = websocket.create_connection(
            url,
            header=[f"Authorization: Bearer {self.config.openai_api_key}"],
            timeout=10,
        )
        try:
            self._ws.settimeout(None)
        except Exception:
            pass
        print("iris> websocket connected")
        self._send_json(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": self.config.realtime_model,
                    "output_modalities": ["audio"],
                    "instructions": self._instructions(),
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
                                "silence_duration_ms": 500,
                                "create_response": False,
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

        print("iris> live speech-to-speech connected")
        if self.wake_gated:
            print(f"iris> say {', '.join(self.config.wake_words)} to wake me")
        print("iris> say 'go to sleep' to pause, or press Ctrl+C to stop")

        receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
        receive_thread.start()
        self.router.set_screen_awareness(self._screen_awareness)
        self._screen_awareness.start()
        print(
            "iris> live screen awareness enabled "
            f"({self.config.screenshot_interval_seconds:.1f}s sampling)"
        )

        input_device = _select_input_device(sounddevice)
        print(f"iris> microphone device: {input_device.index} ({input_device.name})")

        def input_callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if status:
                print(f"audio warning: {status}")
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
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass

    def _receive_loop(self) -> None:
        import sounddevice  # type: ignore

        try:
            with sounddevice.RawOutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=2400,
            ) as output:
                while not self._stop.is_set() and self._ws is not None:
                    try:
                        raw = self._ws.recv()
                    except Exception as exc:
                        if _is_realtime_timeout(exc):
                            continue
                        if not self._stop.is_set():
                            print(f"iris error> realtime receive failed: {exc}")
                        return
                    if not raw:
                        continue
                    event = json.loads(raw)
                    self._handle_event(event, output)
        finally:
            self._stop.set()

    def _handle_event(self, event: dict[str, Any], output) -> None:  # noqa: ANN001
        event_type = str(event.get("type", ""))
        if event_type == "session.updated":
            print("iris> session ready")
            return
        if event_type == "response.created":
            with self._response_state_lock:
                self._assistant_response_active = True
                self._last_response_started_at = time.monotonic()
                self._suppress_input_until = max(
                    self._suppress_input_until,
                    time.monotonic() + 1.0,
                )
            return
        if event_type == "input_audio_buffer.speech_started":
            if self._input_should_be_muted():
                return
            print("iris> heard speech...")
            return
        if event_type == "input_audio_buffer.speech_stopped":
            if self._input_should_be_muted():
                return
            print("iris> speech stopped, waiting for transcript...")
            return
        if event_type == "input_audio_buffer.committed":
            return
        if event_type == "conversation.item.input_audio_transcription.completed":
            transcript = _clean_transcript(str(event.get("transcript", "")))
            if transcript:
                self._handle_transcript(transcript)
            return
        if event_type in {"response.audio.delta", "response.output_audio.delta"}:
            audio = event.get("delta")
            if isinstance(audio, str) and audio:
                self._note_audio_output()
                output.write(base64.b64decode(audio))
            return
        if event_type in {
            "response.audio_transcript.done",
            "response.output_audio_transcript.done",
            "response.output_text.done",
        }:
            text = str(event.get("transcript") or event.get("text") or "").strip()
            if text and not self._is_duplicate_response_text(text):
                print(f"iris> {text}")
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
        if event_type in {"response.done", "response.cancelled"}:
            self._mark_response_done()
            return
        if event_type == "error":
            print(f"iris error> {event.get('error', event)}")
            error = event.get("error", {})
            code = error.get("code") if isinstance(error, dict) else ""
            if code == "conversation_already_has_active_response":
                with self._response_state_lock:
                    self._assistant_response_active = True
                    self._suppress_input_until = max(
                        self._suppress_input_until,
                        time.monotonic() + 2.0,
                    )

    def _handle_transcript(self, transcript: str) -> None:
        if self._should_ignore_transcript(transcript):
            return
        print(f"you> {transcript}")
        lowered = transcript.lower().strip()
        if _is_sleep_command(lowered):
            self.awake = False
            self._respond_with_text("Going quiet. Say Iris when you need me.")
            return

        wake_detected = _contains_wake_word(lowered, self.config.wake_words)
        if self.wake_gated and wake_detected:
            self.awake = True
            command = _strip_wake_words(transcript, self.config.wake_words).strip(
                " ,.!?"
            )
            if not command:
                self._respond_with_text(
                    f"I'm here, {self.user_profile.preferred_name}."
                )
                return
            transcript = command
            lowered = transcript.lower().strip()

        if self.wake_gated and not self.awake:
            return

        self._run_agent_async(transcript)

    def _run_agent_async(self, transcript: str) -> None:
        if not self._agent_lock.acquire(blocking=False):
            lowered = transcript.lower().strip(" \t\r\n.,!?")
            if _is_interrupt_command(lowered):
                self.router.agent_executor.cancel_current(
                    "Operation cancelled by user."
                )
                self.router.safety_gate.kill()
                self._clear_pending_agent_texts()
                print("iris> stopping current automation after the active step")
                self._respond_with_text("Stopping that after the current step.")
                return
            if _is_status_question(lowered):
                active = self._active_agent_request or "the last request"
                self._respond_with_text(f"I'm still working on {active}.")
                return
            self._pending_agent_texts.put(transcript)
            now = time.monotonic()
            if now - self._last_busy_notice_at > 4.0:
                print("iris> queued that after the current request")
                self._last_busy_notice_at = now
            return

        def worker() -> None:
            self._active_agent_request = transcript
            try:
                print("iris> working...")
                result = self.router.handle_text(transcript)
                if not result.ok:
                    print(f"iris error> {result.message}")
                self._respond_with_text(result.message)
            except Exception as exc:
                message = f"I hit an agent error: {exc}"
                print(f"iris error> {exc}")
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
                time.monotonic() + 3.0,
            )
            if self._pending_response_text:
                pending = self._pending_response_text
                self._pending_response_text = None
        if pending:
            self._respond_with_text(pending)

    def _note_audio_output(self) -> None:
        with self._response_state_lock:
            self._assistant_response_active = True
            self._last_response_started_at = (
                self._last_response_started_at or time.monotonic()
            )
            self._suppress_input_until = max(
                self._suppress_input_until,
                time.monotonic() + 2.5,
            )

    def _input_should_be_muted(self) -> bool:
        with self._response_state_lock:
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
            last_spoken = self._last_spoken_text
            suppress_until = self._suppress_input_until
        if active:
            return True
        if time.monotonic() <= suppress_until and _similar_text(
            transcript, last_spoken
        ):
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

    def _instructions(self) -> str:
        return f"""You are {self.config.agent_name}, a warm, conversational Mac voice agent.

User profile:
{self.user_profile.prompt_context()}

Voice behavior:
- This is live speech-to-speech, not dictation. Sound present, relaxed, and conversational.
- Talk like a capable person sitting next to {self.user_profile.preferred_name}: calm, warm, lightly playful when the user is casual, never corporate.
- Use {self.user_profile.preferred_name}'s first name occasionally, especially greetings and reassuring confirmations, but do not overuse it.
- Do not give the same generic line every time. Avoid defaulting to “How can I help you today?” after every greeting.
- For casual check-ins, answer like a person: acknowledge the mood, maybe add a small natural follow-up.
- For action results, be brief but specific: say what happened, what you see, or what you need next.
- One or two sentences is usually right. Use three if the user is explaining something or the task needs context.
- Do not be robotic, do not read raw URLs aloud, and do not narrate internal tool names unless the user asks.
- If asked to control the Mac, inspect the screen, open apps, find files, or run workflows, the local Iris runtime will execute it and provide a result.
- Do not guess what is on the screen. For screen/current tab/current window questions, wait for the local Iris runtime result.
- Ask before destructive actions, sending messages, deleting files, credentials, purchases, installs, or security/privacy changes.
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
    ) -> None:
        self.config = config
        self.router = router
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
                result = self.router.handle_text(user_text)
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
            result = self.router.handle_text(user_text)
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


def _is_status_question(text: str) -> bool:
    normalized = _normalize_wake_text(text)
    return normalized in {
        "what are you doing",
        "what are you working on",
        "status",
        "where are you",
        "are you still working",
    }
