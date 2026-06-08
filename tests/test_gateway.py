from __future__ import annotations

from types import SimpleNamespace

from iris.gateway import GatewayService


def test_gateway_health_does_not_require_token() -> None:
    service = GatewayService(
        config=SimpleNamespace(gateway_token=None, agent_name="Iris"),
        router_factory=lambda: None,
    )

    assert service.authorize("/health", None) is True


def test_gateway_memory_browser_does_not_require_token() -> None:
    service = GatewayService(
        config=SimpleNamespace(gateway_token=None, agent_name="Iris"),
        router_factory=lambda: None,
    )

    assert service.authorize("/memory-browser", None) is True


def test_gateway_rejects_protected_routes_without_configured_token() -> None:
    service = GatewayService(
        config=SimpleNamespace(gateway_token=None, agent_name="Iris"),
        router_factory=lambda: None,
    )

    assert service.authorize("/sessions", "Bearer anything") is False


def test_gateway_requires_matching_bearer_token() -> None:
    service = GatewayService(
        config=SimpleNamespace(gateway_token="secret-token", agent_name="Iris"),
        router_factory=lambda: None,
    )

    assert service.authorize("/sessions", None) is False
    assert service.authorize("/sessions", "Basic secret-token") is False
    assert service.authorize("/sessions", "Bearer wrong-token") is False
    assert service.authorize("/sessions", "Bearer secret-token") is True
