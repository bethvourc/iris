from __future__ import annotations

import unittest

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from iris.config import IrisConfig
from iris.integrations.openai_client import OpenAIResponsesClient


class OpenAIClientParsingTests(unittest.TestCase):
    def test_computer_call_parsing_accepts_dict_output(self) -> None:
        config = IrisConfig.from_env()
        client = OpenAIResponsesClient(config)
        response = {
            "id": "resp_1",
            "output": [
                {
                    "type": "computer_call",
                    "call_id": "call_1",
                    "action": {"type": "click", "x": 1, "y": 2},
                    "pending_safety_checks": [{"id": "safety_1"}],
                }
            ],
        }
        calls = client.computer_calls(response)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].call_id, "call_1")
        self.assertEqual(calls[0].action["type"], "click")
        self.assertEqual(calls[0].pending_safety_checks[0]["id"], "safety_1")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_default_transport_avoids_openai_sdk_import(self) -> None:
        config = IrisConfig.from_env(Path("/tmp/iris"))
        client = OpenAIResponsesClient(config)
        transport = client._get_client()
        self.assertEqual(type(transport).__name__, "_OpenAIHTTPClient")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    @patch("iris.integrations.openai_client.request.urlopen")
    def test_http_transport_does_not_send_invalid_beta_header(self, urlopen: MagicMock) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
                return None

            def read(self) -> bytes:
                return json.dumps({"id": "resp_1", "output": []}).encode("utf-8")

        urlopen.return_value = FakeResponse()
        config = IrisConfig.from_env(Path("/tmp/iris"))
        transport = OpenAIResponsesClient(config)._get_client()
        transport.responses.create(model="gpt-5.5", input="hello")

        request_obj = urlopen.call_args.args[0]
        self.assertNotIn("OpenAI-beta", request_obj.headers)
        self.assertNotIn("OpenAI-Beta", request_obj.headers)


if __name__ == "__main__":
    unittest.main()
