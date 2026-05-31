from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from iris.config import IrisConfig
from iris.notifications import NotificationService


def build_config(**overrides) -> IrisConfig:
    values = {
        "project_root": Path("/tmp/iris"),
        "openai_api_key": None,
        "google_application_credentials": None,
        "agent_name": "Iris",
        "voice": "marin",
        "toggle_hotkey": "<ctrl>+<space>",
        "kill_hotkey": "<ctrl>+<alt>+k",
        "realtime_model": "gpt-realtime",
        "chat_model": "gpt-5.2",
        "vision_model": "gpt-5.2",
        "computer_use_model": "computer-use-preview",
        "computer_use_environment": "browser",
        "groq_api_key": None,
        "fast_intent_model": "compound-mini",
        "stt_model": "whisper-large-v3-turbo",
        "realtime_transcription_model": "gpt-4o-mini-transcribe",
        "screenshot_interval_seconds": 1.0,
        "notify_provider": "ntfy",
        "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "iris-secret-topic",
        "ntfy_token": None,
        "pushover_token": None,
        "pushover_user": None,
        "notify_timeout_seconds": 30.0,
        "state_db_path": Path("/tmp/iris.sqlite3"),
        "autonomy_level": "L1",
        "meeting_consent_required": True,
        "meeting_retention_days": 30,
        "listen_seconds": 2.0,
        "wake_poll_seconds": 1.2,
        "wake_words": ("iris", "hey iris"),
        "speak_responses": False,
        "max_response_chars": 220,
    }
    values.update(overrides)
    return IrisConfig(**values)


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class NotificationTests(unittest.TestCase):
    def test_missing_topic_is_clear(self) -> None:
        service = NotificationService(build_config(ntfy_topic=None))
        result = service.send_phone_push("hello")
        self.assertFalse(result.ok)
        self.assertIn("IRIS_NTFY_TOPIC", result.detail)

    @patch("iris.notifications.urlopen", return_value=FakeResponse())
    def test_ntfy_notification_uses_configured_topic(self, fake_urlopen) -> None:
        service = NotificationService(build_config(ntfy_token="token"))
        result = service.send_phone_push("hello", title="Iris", tags="iris")

        self.assertTrue(result.ok)
        request = fake_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://ntfy.sh/iris-secret-topic")
        self.assertEqual(request.headers["Title"], "Iris")
        self.assertEqual(request.headers["Authorization"], "Bearer token")

    @patch("iris.notifications.urlopen", return_value=FakeResponse())
    def test_pushover_notification_uses_configured_credentials(self, fake_urlopen) -> None:
        service = NotificationService(
            build_config(
                notify_provider="pushover",
                pushover_token="app-token",
                pushover_user="user-key",
            )
        )
        result = service.send_phone_push("hello", title="Iris")

        self.assertTrue(result.ok)
        request = fake_urlopen.call_args.args[0]
        body = request.data.decode("utf-8")
        self.assertEqual(request.full_url, "https://api.pushover.net/1/messages.json")
        self.assertIn("token=app-token", body)
        self.assertIn("user=user-key", body)


if __name__ == "__main__":
    unittest.main()
