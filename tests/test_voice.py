from __future__ import annotations

import unittest

from pathlib import Path
from unittest.mock import patch

from iris.config import IrisConfig
from iris.profile import UserProfile
from iris.router import RouterResult
from iris.voice import (
    RealtimeSpeechSession,
    _clean_transcript,
    _contains_wake_word,
    _is_sleep_command,
    _is_realtime_timeout,
    _similar_text,
    _strip_wake_words,
)


class VoiceTests(unittest.TestCase):
    def test_clean_transcript_removes_repeated_lines(self) -> None:
        self.assertEqual(_clean_transcript("Hi Iris\nHi Iris"), "Hi Iris")

    def test_clean_transcript_removes_exact_doubled_text(self) -> None:
        self.assertEqual(_clean_transcript("Hi IrisHi Iris"), "Hi Iris")

    @patch.dict(
        "os.environ",
        {"IRIS_WAKE_WORDS": "jarvis, hey iris", "IRIS_WAKE_POLL_SECONDS": "0.8"},
        clear=False,
    )
    def test_wake_word_config_is_parsed(self) -> None:
        config = IrisConfig.from_env(Path("/tmp/iris"))
        self.assertEqual(config.wake_words, ("jarvis", "hey iris"))
        self.assertEqual(config.wake_poll_seconds, 0.8)

    def test_wake_word_helpers(self) -> None:
        words = ("iris", "hey iris")
        self.assertTrue(_contains_wake_word("hey iris describe my screen", words))
        self.assertEqual(_strip_wake_words("hey iris describe my screen", words), "describe my screen")
        self.assertTrue(_contains_wake_word("hey, iris.", words))
        self.assertEqual(_strip_wake_words("Hey, Iris.", words), "")
        self.assertTrue(_contains_wake_word("hey aries", words))

    def test_sleep_detection(self) -> None:
        self.assertTrue(_is_sleep_command("go to sleep"))

    def test_awake_transcript_routes_to_agent_loop(self) -> None:
        config = IrisConfig.from_env(Path("/tmp/iris"))
        router = FakeRouter()
        session = RealtimeSpeechSession(
            config=config,
            router=router,  # type: ignore[arg-type]
            user_profile=UserProfile("Clinton Imaro", "Clinton"),
            wake_gated=True,
        )
        spoken: list[str] = []
        session._respond_with_text = spoken.append  # type: ignore[method-assign]

        session._handle_transcript("Hey Iris open a new tab")
        self.assertIsNotNone(session._agent_thread)
        session._agent_thread.join(timeout=1.0)  # type: ignore[union-attr]

        self.assertEqual(router.calls, ["open a new tab"])
        self.assertEqual(spoken, ["done"])

    def test_wake_only_with_punctuation_does_not_enter_agent_loop(self) -> None:
        config = IrisConfig.from_env(Path("/tmp/iris"))
        router = FakeRouter()
        session = RealtimeSpeechSession(
            config=config,
            router=router,  # type: ignore[arg-type]
            user_profile=UserProfile("Clinton Imaro", "Clinton"),
            wake_gated=True,
        )
        spoken: list[str] = []
        session._respond_with_text = spoken.append  # type: ignore[method-assign]

        session._handle_transcript("Hey, Iris.")

        self.assertEqual(router.calls, [])
        self.assertEqual(spoken, ["I'm here, Clinton."])

    def test_iris_ignores_echo_while_speaking(self) -> None:
        config = IrisConfig.from_env(Path("/tmp/iris"))
        router = FakeRouter()
        session = RealtimeSpeechSession(
            config=config,
            router=router,  # type: ignore[arg-type]
            user_profile=UserProfile("Clinton Imaro", "Clinton"),
            wake_gated=True,
        )
        sent: list[dict[str, object]] = []
        session._send_json = sent.append  # type: ignore[method-assign]

        session._respond_with_text("I'm here, Clinton.")
        session._handle_transcript("I'm here, Clinton.")

        self.assertEqual(router.calls, [])

    def test_echo_similarity_handles_misheard_name(self) -> None:
        self.assertTrue(_similar_text("I'm here, Clinton.", "I am here, Quentin."))

    def test_realtime_timeout_detection(self) -> None:
        self.assertTrue(_is_realtime_timeout(TimeoutError("timed out")))
        self.assertTrue(_is_realtime_timeout(FakeTimeout("Connection timed out")))
        self.assertFalse(_is_realtime_timeout(RuntimeError("connection closed")))

    def test_queued_speech_waits_for_active_response_done(self) -> None:
        config = IrisConfig.from_env(Path("/tmp/iris"))
        router = FakeRouter()
        session = RealtimeSpeechSession(
            config=config,
            router=router,  # type: ignore[arg-type]
            user_profile=UserProfile("Clinton Imaro", "Clinton"),
            wake_gated=True,
        )
        sent: list[dict[str, object]] = []
        session._send_json = sent.append  # type: ignore[method-assign]

        session._respond_with_text("first")
        session._respond_with_text("second")
        self.assertEqual(len(sent), 2)
        session._mark_response_done()
        self.assertEqual(len(sent), 4)


class FakePerception:
    pass


class FakeRouter:
    perception = FakePerception()

    def __init__(self) -> None:
        self.calls: list[str] = []

    def set_screen_awareness(self, screen_awareness) -> None:  # noqa: ANN001
        return None

    def handle_text(self, text: str) -> RouterResult:
        self.calls.append(text)
        return RouterResult(True, "done")


class FakeTimeout(Exception):
    pass


if __name__ == "__main__":
    unittest.main()
