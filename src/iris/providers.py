from __future__ import annotations

from dataclasses import dataclass

from iris.config import IrisConfig


@dataclass(frozen=True)
class ProviderHealth:
    name: str
    enabled: bool
    configured: bool
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ModelRoute:
    purpose: str
    provider: str
    model: str
    fallbacks: tuple[tuple[str, str], ...]


class ProviderRegistry:
    def __init__(self, config: IrisConfig) -> None:
        self.config = config

    def health(self) -> list[ProviderHealth]:
        return [
            ProviderHealth(
                "openai",
                True,
                self.config.has_openai,
                ("reasoning", "realtime", "vision", "computer-use", "tts"),
            ),
            ProviderHealth(
                "groq",
                bool(self.config.groq_api_key),
                bool(self.config.groq_api_key),
                ("stt", "fast-intent", "research"),
            ),
            ProviderHealth(
                "pushover",
                self.config.notify_provider == "pushover",
                bool(self.config.pushover_token and self.config.pushover_user),
                ("notification",),
            ),
            ProviderHealth(
                "ntfy",
                self.config.notify_provider == "ntfy",
                bool(self.config.ntfy_topic),
                ("notification",),
            ),
        ]

    def route(self, purpose: str) -> ModelRoute:
        if purpose == "realtime":
            return ModelRoute(
                purpose,
                "openai",
                self.config.realtime_model,
                (("mock", "mock-realtime"),),
            )
        if purpose in {"reasoning", "vision"}:
            return ModelRoute(
                purpose, "openai", self.config.chat_model, (("openai", "gpt-5.4"),)
            )
        if purpose == "computer-use":
            return ModelRoute(
                purpose,
                "openai",
                self.config.computer_use_model,
                (("mock", "mock-computer-use"),),
            )
        if purpose == "stt":
            return ModelRoute(
                purpose,
                "groq",
                self.config.stt_model,
                (("openai", self.config.realtime_model),),
            )
        if purpose in {"fast-intent", "research"}:
            return ModelRoute(
                purpose,
                "groq",
                self.config.fast_intent_model,
                (("openai", self.config.chat_model),),
            )
        if purpose == "notification":
            provider = self.config.notify_provider
            return ModelRoute(
                purpose, provider, f"{provider}-push", (("ntfy", "ntfy-push"),)
            )
        return ModelRoute(purpose, "openai", self.config.chat_model, ())
