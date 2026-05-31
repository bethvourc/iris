from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RiskLevel(StrEnum):
    LOW_RISK = "low_risk"
    SENSITIVE = "sensitive"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LocalAction:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    risk: RiskLevel | None = None

    def label(self) -> str:
        if self.description:
            return self.description
        if not self.args:
            return self.name
        return f"{self.name}({self.args})"
