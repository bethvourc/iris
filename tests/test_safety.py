from __future__ import annotations

import unittest

from iris.actions import LocalAction, RiskLevel
from iris.safety import AutomationPaused, SafetyGate, classify_action


class SafetyTests(unittest.TestCase):
    def test_low_risk_action_is_allowed(self) -> None:
        decision = classify_action(LocalAction("open_app", {"app_name": "TextEdit"}))
        self.assertEqual(decision.risk, RiskLevel.LOW_RISK)

    def test_sensitive_action_name_is_detected(self) -> None:
        decision = classify_action(LocalAction("send_email", {"to": "a@example.com"}))
        self.assertEqual(decision.risk, RiskLevel.SENSITIVE)

    def test_blocked_action_name_is_detected(self) -> None:
        decision = classify_action(LocalAction("delete_file", {"path": "/tmp/x"}))
        self.assertEqual(decision.risk, RiskLevel.BLOCKED)

    def test_sensitive_terms_are_detected(self) -> None:
        decision = classify_action(LocalAction("type_text", {"text": "my password"}))
        self.assertEqual(decision.risk, RiskLevel.SENSITIVE)

    def test_sensitive_action_requires_confirmation(self) -> None:
        gate = SafetyGate(confirm=lambda _: False)
        with self.assertRaises(PermissionError):
            gate.allow(LocalAction("send_email", {"to": "a@example.com"}))

    def test_kill_switch_blocks_actions(self) -> None:
        gate = SafetyGate(confirm=lambda _: True)
        gate.kill()
        with self.assertRaises(AutomationPaused):
            gate.allow(LocalAction("open_app", {"app_name": "TextEdit"}))

    def test_blocked_action_raises_without_prompt(self) -> None:
        prompts: list[str] = []
        gate = SafetyGate(confirm=lambda question: prompts.append(question) or True)
        with self.assertRaises(PermissionError):
            gate.allow(LocalAction("run_shell", {"command": "rm -rf /tmp/x"}))
        self.assertEqual(prompts, [])


if __name__ == "__main__":
    unittest.main()
