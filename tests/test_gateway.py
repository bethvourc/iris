from __future__ import annotations

from types import SimpleNamespace

from iris.gateway import AUTH_EXEMPT_PATHS, GatewayService

# Every route the gateway dispatches (handle_get / handle_post / SSE). The
# readiness review (docs/desktop/readiness-review.md) requires auth on every
# non-exempt route; this list is the deny-by-default scan's source of truth.
# Adding a route without adding it here — or widening AUTH_EXEMPT_PATHS — fails
# the scan, forcing a conscious decision.
_PROTECTED_ROUTES = (
    # GET
    "/voice/status", "/settings", "/activity", "/sessions", "/tasks", "/runs",
    "/approvals", "/memory/facts", "/memory/related", "/memory/explain",
    "/memory/reviews", "/voice/events",
    # POST
    "/secrets", "/voice/start", "/voice/stop", "/voice/interrupt", "/messages",
    "/tasks/run-next", "/memory/maintain",
)
_EXEMPT_ROUTES = ("/health", "/dashboard", "/memory-browser")


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


def test_auth_exempt_set_is_locked() -> None:
    # Auth is exempt only for the unauthenticated health probe and the two
    # localhost HTML shells (whose data calls still require a token). Any change
    # to this set must be deliberate and reviewed — hence the exact assertion.
    assert AUTH_EXEMPT_PATHS == set(_EXEMPT_ROUTES)


def test_every_protected_route_denies_by_default() -> None:
    service = GatewayService(
        config=SimpleNamespace(gateway_token="secret-token", agent_name="Iris"),
        router_factory=lambda: None,
    )
    for route in _PROTECTED_ROUTES:
        assert service.authorize(route, None) is False, route
        assert service.authorize(route, "Bearer wrong-token") is False, route
        assert service.authorize(route, "Bearer secret-token") is True, route
    # Exempt routes are reachable without a token by design.
    for route in _EXEMPT_ROUTES:
        assert service.authorize(route, None) is True, route


def test_no_protected_route_is_silently_exempt() -> None:
    # Catches the dangerous mistake: a protected route accidentally added to the
    # exempt set would let it through without a token.
    assert not (set(_PROTECTED_ROUTES) & AUTH_EXEMPT_PATHS)
