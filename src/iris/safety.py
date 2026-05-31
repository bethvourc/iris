from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from iris.actions import LocalAction, RiskLevel


class AutomationPaused(RuntimeError):
    """Raised when automation is blocked by the kill switch."""


SENSITIVE_ACTION_NAMES = {
    "delete_file",
    "empty_trash",
    "send_message",
    "send_email",
    "purchase",
    "make_payment",
    "enter_credentials",
    "install_software",
    "change_security_settings",
    "change_privacy_settings",
    "run_shell",
    "move_file",
    "edit_file",
    "create_calendar_event",
}

BLOCKED_ACTION_NAMES = {
    "pay",
    "purchase",
    "make_payment",
    "enter_credentials",
    "change_security_settings",
    "change_privacy_settings",
    "delete_file",
    "empty_trash",
}

SENSITIVE_TERMS = {
    "delete",
    "remove",
    "trash",
    "send",
    "email",
    "message",
    "pay",
    "payment",
    "purchase",
    "buy",
    "password",
    "credential",
    "login",
    "install",
    "sudo",
    "security",
    "privacy",
    "erase",
    "format",
    "irreversible",
}

BLOCKED_SHELL_PATTERNS = {
    "rm -rf",
    "git reset --hard",
    "git clean -fd",
    "sudo ",
    "drop database",
    "truncate table",
}


@dataclass(frozen=True)
class SafetyDecision:
    risk: RiskLevel
    reason: str


def classify_action(action: LocalAction) -> SafetyDecision:
    if action.risk is not None:
        return SafetyDecision(action.risk, "explicit action risk")
    name = action.name.lower()
    text = f"{action.name} {action.description} {action.args}".lower()
    if name in BLOCKED_ACTION_NAMES:
        return SafetyDecision(RiskLevel.BLOCKED, f"{action.name} is blocked by default")
    if name == "run_shell" and any(pattern in text for pattern in BLOCKED_SHELL_PATTERNS):
        return SafetyDecision(RiskLevel.BLOCKED, "destructive shell command is blocked")
    if name in SENSITIVE_ACTION_NAMES:
        return SafetyDecision(RiskLevel.SENSITIVE, f"{action.name} is sensitive")
    for term in SENSITIVE_TERMS:
        if term in text:
            return SafetyDecision(RiskLevel.SENSITIVE, f"contains '{term}'")
    return SafetyDecision(RiskLevel.LOW_RISK, "low-risk local action")


class SafetyGate:
    def __init__(
        self,
        confirm: Callable[[str], bool] | None = None,
    ) -> None:
        self._confirm = confirm or self._terminal_confirm
        self._killed = False

    @property
    def killed(self) -> bool:
        return self._killed

    def kill(self) -> None:
        self._killed = True

    def resume(self) -> None:
        self._killed = False

    def assert_not_killed(self) -> None:
        if self._killed:
            raise AutomationPaused("Automation is paused by the kill switch.")

    def allow(self, action: LocalAction) -> SafetyDecision:
        self.assert_not_killed()
        decision = classify_action(action)
        if decision.risk == RiskLevel.SENSITIVE:
            question = (
                f"Sensitive action requires approval: {action.label()}\n"
                f"Reason: {decision.reason}\nProceed?"
            )
            if not self._confirm(question):
                raise PermissionError(f"User rejected action: {action.label()}")
        if decision.risk == RiskLevel.BLOCKED:
            raise PermissionError(f"Blocked action: {action.label()} ({decision.reason})")
        return decision

    @staticmethod
    def _terminal_confirm(question: str) -> bool:
        answer = input(f"{question} [y/N] ").strip().lower()
        return answer in {"y", "yes"}
