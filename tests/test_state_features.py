from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from iris.config import IrisConfig
from iris.memory import add_memory, list_memories
from iris.providers import ProviderRegistry
from iris.state import open_state
from iris.watchers import add_watch, run_watch_once
from iris.workflows import run_workflow


def build_config(tmp: Path) -> IrisConfig:
    return IrisConfig(
        project_root=tmp,
        openai_api_key=None,
        google_application_credentials=None,
        agent_name="Iris",
        voice="marin",
        toggle_hotkey="<ctrl>+<space>",
        kill_hotkey="<ctrl>+<alt>+k",
        realtime_model="gpt-realtime-2",
        chat_model="gpt-5.5",
        vision_model="gpt-5.5",
        computer_use_model="computer-use-preview",
        computer_use_environment="browser",
        groq_api_key=None,
        fast_intent_model="compound-mini",
        stt_model="whisper-large-v3-turbo",
        realtime_transcription_model="gpt-4o-mini-transcribe",
        screenshot_interval_seconds=1.0,
        notify_provider="pushover",
        ntfy_server="https://ntfy.sh",
        ntfy_topic=None,
        ntfy_token=None,
        pushover_token=None,
        pushover_user=None,
        notify_timeout_seconds=30.0,
        state_db_path=tmp / "iris.sqlite3",
        autonomy_level="L1",
        meeting_consent_required=True,
        meeting_retention_days=30,
        listen_seconds=2.0,
        wake_poll_seconds=1.2,
        wake_words=("iris", "hey iris"),
        speak_responses=False,
        max_response_chars=220,
    )


class StateFeatureTests(unittest.TestCase):
    def test_memory_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as handle:
            config = build_config(Path(handle))
            with open_state(config) as db:
                memory_id = add_memory(db, category="project", content="Iris uses SQLite")
                rows = list_memories(db)
        self.assertTrue(memory_id)
        self.assertEqual(rows[0]["content"], "Iris uses SQLite")

    def test_file_watch_detects_change_after_first_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as handle:
            root = Path(handle)
            config = build_config(root)
            target = root / "watched.txt"
            target.write_text("one", encoding="utf-8")
            with open_state(config) as db:
                watch_id = add_watch(db, kind="file", name="file", target=str(target))
                first = run_watch_once(db, watch_id)
                target.write_text("two", encoding="utf-8")
                second = run_watch_once(db, watch_id)
        self.assertFalse(first.changed)
        self.assertTrue(second.changed)

    def test_sensitive_workflow_creates_approval(self) -> None:
        with tempfile.TemporaryDirectory() as handle:
            config = build_config(Path(handle))
            with open_state(config) as db:
                result = run_workflow(db, "repo-debug-pr")
        self.assertEqual(result["status"], "pending_approval")
        self.assertTrue(result["approval_id"])

    def test_provider_registry_uses_configured_model_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as handle:
            config = build_config(Path(handle))
            registry = ProviderRegistry(config)
        self.assertEqual(registry.route("realtime").model, "gpt-realtime-2")
        self.assertEqual(registry.route("reasoning").model, "gpt-5.5")


if __name__ == "__main__":
    unittest.main()
