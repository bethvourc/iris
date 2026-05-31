from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from iris.actions import RiskLevel
from iris.approvals import create_approval
from iris.config import IrisConfig
from iris.gateway import GatewayService
from iris.plugins import list_plugins, plugin_health, set_plugin_enabled
from iris.router import RouterResult
from iris.sessions import add_message, create_session, get_session, list_sessions
from iris.state import open_state
from iris.tasks import create_task, finish_task, get_task, list_tasks, request_cancel, resume_task, start_task


def build_config(root: Path) -> IrisConfig:
    return IrisConfig(
        project_root=root,
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
        state_db_path=root / "iris.sqlite3",
        autonomy_level="L1",
        meeting_consent_required=True,
        meeting_retention_days=30,
        listen_seconds=2.0,
        wake_poll_seconds=1.2,
        wake_words=("iris", "hey iris"),
        speak_responses=False,
        max_response_chars=220,
    )


class FakeRouter:
    def __init__(self, ok: bool = True, message: str = "handled") -> None:
        self.ok = ok
        self.message = message
        self.calls: list[str] = []

    def handle_text(self, text: str) -> RouterResult:
        self.calls.append(text)
        return RouterResult(self.ok, self.message, {"echo": text})


class GatewayRuntimeTests(unittest.TestCase):
    def test_sessions_store_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp))
            with open_state(config) as db:
                session_id = create_session(db, channel="gateway", title="Test")
                add_message(db, session_id=session_id, role="user", content="hello")
                add_message(db, session_id=session_id, role="assistant", content="hi")
                session = get_session(db, session_id)
                sessions = list_sessions(db)
        self.assertIsNotNone(session)
        self.assertEqual(session["channel"], "gateway")  # type: ignore[index]
        self.assertEqual(len(session["messages"]), 2)  # type: ignore[index]
        self.assertEqual(sessions[0]["session_id"], session_id)

    def test_task_lifecycle_cancel_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp))
            with open_state(config) as db:
                task_id = create_task(db, kind="message", title="Do it", input_value={"message": "do it"})
                self.assertTrue(start_task(db, task_id))
                self.assertTrue(request_cancel(db, task_id))
                self.assertTrue(finish_task(db, task_id, status="done", result_value={"ok": True}))
                cancelled = get_task(db, task_id)
                self.assertTrue(resume_task(db, task_id))
                resumed = get_task(db, task_id)
                tasks = list_tasks(db)
        self.assertEqual(cancelled["status"], "cancelled")  # type: ignore[index]
        self.assertEqual(resumed["status"], "queued")  # type: ignore[index]
        self.assertEqual(tasks[0]["task_id"], task_id)

    def test_plugin_registry_enable_disable_health(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp))
            with open_state(config) as db:
                plugins = list_plugins(db)
                self.assertTrue(set_plugin_enabled(db, "gmail", True))
                health = plugin_health(db)
                self.assertTrue(set_plugin_enabled(db, "gmail", False))
                disabled = {item["plugin_id"]: item for item in list_plugins(db)}
        self.assertIn("browser", {item["plugin_id"] for item in plugins})
        self.assertTrue({item["plugin_id"]: item for item in health}["gmail"]["enabled"])
        self.assertFalse(disabled["gmail"]["enabled"])

    def test_gateway_message_creates_session_task_and_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp))
            router = FakeRouter(message="Done.")
            gateway = GatewayService(config=config, router_factory=lambda: router)
            status, payload = gateway.handle_post(
                "/messages",
                {"message": "open the current tab", "channel": "test"},
            )
            with open_state(config) as db:
                session = get_session(db, payload["session_id"])
                task = get_task(db, payload["task_id"])
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["task_status"], "done")
        self.assertEqual(router.calls, ["open the current tab"])
        self.assertEqual(len(session["messages"]), 2)  # type: ignore[index]
        self.assertEqual(task["status"], "done")  # type: ignore[index]

    def test_gateway_task_and_approval_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = build_config(Path(temp))
            gateway = GatewayService(config=config, router_factory=lambda: FakeRouter())
            with open_state(config) as db:
                task_id = create_task(db, kind="message", title="Long")
                start_task(db, task_id)
                approval_id = create_approval(
                    db,
                    action_name="read_gmail",
                    preview="Read Gmail",
                    risk=RiskLevel.SENSITIVE,
                )
            cancel_status, cancel_payload = gateway.handle_post(f"/tasks/{task_id}/cancel", {})
            tasks_status, tasks_payload = gateway.handle_get("/tasks", {})
            approval_status, approval_payload = gateway.handle_post(
                f"/approvals/{approval_id}/approve",
                {},
            )
        self.assertEqual(cancel_status, 200)
        self.assertTrue(cancel_payload["ok"])
        self.assertEqual(tasks_status, 200)
        self.assertEqual(tasks_payload["tasks"][0]["task_id"], task_id)
        self.assertEqual(approval_status, 200)
        self.assertEqual(approval_payload["status"], "approved")


if __name__ == "__main__":
    unittest.main()
