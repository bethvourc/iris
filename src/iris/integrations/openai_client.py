from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from iris.config import IrisConfig
from iris.perception import ScreenContext, Screenshot
from iris.profile import UserProfile, infer_system_profile


@dataclass(frozen=True)
class ComputerCall:
    call_id: str
    action: dict[str, Any]
    pending_safety_checks: list[dict[str, Any]]


def _to_plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return _to_plain(value.model_dump())
    if hasattr(value, "to_dict"):
        return _to_plain(value.to_dict())
    return value


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


class OpenAIResponsesClient:
    def __init__(self, config: IrisConfig, user_profile: UserProfile | None = None) -> None:
        self.config = config
        self.user_profile = user_profile or infer_system_profile()
        self._client = None

    @property
    def available(self) -> bool:
        return self.config.has_openai

    def _get_client(self):
        if not self.config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        if self._client is None:
            if os.getenv("IRIS_OPENAI_TRANSPORT", "http").strip().lower() == "sdk":
                from openai import OpenAI  # type: ignore

                self._client = OpenAI(api_key=self.config.openai_api_key)
            else:
                self._client = _OpenAIHTTPClient(self.config.openai_api_key)
        return self._client

    def describe_screen(
        self,
        screenshot: Screenshot,
        context: ScreenContext,
        prompt: str = "Describe what is visible on this Mac screen.",
    ) -> str:
        client = self._get_client()
        context_text = (
            f"Active app: {context.active_app or 'unknown'}\n"
            f"Active window: {context.active_window or 'unknown'}\n"
            f"User request: {prompt}"
        )
        response = client.responses.create(
            model=self.config.vision_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": context_text},
                        {
                            "type": "input_image",
                            "image_url": screenshot.data_url(),
                        },
                    ],
                }
            ],
        )
        return getattr(response, "output_text", None) or self.output_text(response)

    def chat(
        self,
        *,
        message: str,
        context: ScreenContext,
        previous_response_id: str | None = None,
    ):
        client = self._get_client()
        context_text = (
            f"Active app: {context.active_app or 'unknown'}\n"
            f"Active window: {context.active_window or 'unknown'}\n"
            "Iris capabilities: screen capture, Google Vision OCR, native Mac "
            "actions, and computer-use UI navigation. Do not claim you have "
            "taken an action unless the local runtime reports that it happened. "
            "When the user is just chatting, respond naturally and briefly. "
            "When they ask Iris to do something, state what will happen or what "
            "permission/configuration is missing.\n"
            f"{self.user_profile.prompt_context()}"
        )
        kwargs: dict[str, Any] = {
            "model": self.config.chat_model,
            "instructions": (
                f"You are {self.config.agent_name}, a conversational personal "
                "Mac assistant. Keep replies short: one sentence by default, "
                "two only when needed. Do not add extra offers, filler, or long "
                "explanations. You can coordinate screen or computer actions "
                "through the local Iris runtime. Sound like a helpful person, "
                "not a dashboard."
            ),
            "input": [
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": context_text}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": message}],
                },
            ],
        }
        kwargs["max_output_tokens"] = 80
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        return client.responses.create(**kwargs)

    def create_computer_use_response(self, instruction: str, screenshot: Screenshot):
        client = self._get_client()
        content = [{"type": "input_text", "text": instruction}]
        content.append({"type": "input_image", "image_url": screenshot.data_url()})
        return client.responses.create(
            model=self.config.computer_use_model,
            tools=[self._computer_tool(screenshot)],
            input=[{"role": "user", "content": content}],
            reasoning={"summary": "concise"},
            truncation="auto",
        )

    def continue_computer_use_response(
        self,
        *,
        previous_response_id: str,
        call_id: str,
        screenshot: Screenshot,
        acknowledged_safety_checks: list[dict[str, Any]] | None = None,
    ):
        client = self._get_client()
        item: dict[str, Any] = {
            "type": "computer_call_output",
            "call_id": call_id,
            "output": {
                "type": "computer_screenshot",
                "image_url": screenshot.data_url(),
            },
        }
        if acknowledged_safety_checks:
            item["acknowledged_safety_checks"] = acknowledged_safety_checks
        return client.responses.create(
            model=self.config.computer_use_model,
            previous_response_id=previous_response_id,
            tools=[self._computer_tool(screenshot)],
            input=[item],
            truncation="auto",
        )

    def computer_calls(self, response: Any) -> list[ComputerCall]:
        calls: list[ComputerCall] = []
        for item in _get(response, "output", []) or []:
            if _get(item, "type") != "computer_call":
                continue
            plain = _to_plain(item)
            calls.append(
                ComputerCall(
                    call_id=str(plain.get("call_id", "")),
                    action=plain.get("action", {}) or {},
                    pending_safety_checks=plain.get("pending_safety_checks", []) or [],
                )
            )
        return calls

    def output_text(self, response: Any) -> str:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return str(output_text)
        chunks: list[str] = []
        for item in _get(response, "output", []) or []:
            plain = _to_plain(item)
            if plain.get("type") == "message":
                for content in plain.get("content", []) or []:
                    if content.get("type") in {"output_text", "text"}:
                        chunks.append(str(content.get("text", "")))
        return "\n".join(chunk for chunk in chunks if chunk).strip()

    def response_id(self, response: Any) -> str:
        return str(_get(response, "id", ""))

    def _computer_tool(self, screenshot: Screenshot) -> dict[str, Any]:
        return {
            "type": "computer_use_preview",
            "display_width": screenshot.width or 1024,
            "display_height": screenshot.height or 768,
            "environment": self.config.computer_use_environment,
        }


class _OpenAIHTTPClient:
    def __init__(self, api_key: str) -> None:
        self.responses = _ResponsesHTTPResource(api_key)


class _ResponsesHTTPResource:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def create(self, **kwargs: Any) -> dict[str, Any]:
        body = json.dumps(kwargs, default=str).encode("utf-8")
        req = request.Request(
            "https://api.openai.com/v1/responses",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=90) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI Responses API failed: {exc.code} {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"OpenAI Responses API connection failed: {exc}") from exc
